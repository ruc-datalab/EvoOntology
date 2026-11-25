#!/usr/bin/env python3
"""Implementation for the bird.agent.data_agent module."""

import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import AgentMessage, AgentSession
from .prompt_manager import PromptManager
from .llm_providers import LLMProvider, create_llm_provider

AGENT_DIR = Path(__file__).resolve().parent

SEMANTIC_TOOLS = {"browse_semantics", "resolve_semantics"}


class BIRDReActAgent:

    def __init__(self, llm_provider: LLMProvider,
                 mcp_client,
                 max_turns: int = 15,
                 semantic_manifest: str = "",
                 verbose: bool = False,
                 turn_timeout: int = 60):
        self.llm_provider = llm_provider
        self.mcp_client = mcp_client
        self.max_turns = max_turns
        self.verbose = verbose
        self.turn_timeout = turn_timeout
        self.prompt_manager = PromptManager(
            max_turns=max_turns,
            semantic_manifest=semantic_manifest,
        )
        self.session: Optional[AgentSession] = None
        self._semantic_call_count = 0
        self._total_turns = 0

    async def start_session(self, question: str, db_id: str,
                            db_path: str) -> AgentSession:
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        available_tools = await self.mcp_client.get_available_tools()

        self.session = AgentSession(
            session_id=session_id,
            task=question,
            db_id=db_id,
            db_path=db_path,
            available_tools=available_tools,
        )

        system_prompt = self.prompt_manager.build_system_prompt(
            question=question, db_path=db_path,
        )
        self.session.messages.append(AgentMessage(
            role="system", content=system_prompt,
        ))

        return self.session

    async def run(self) -> AgentSession:
        if not self.session:
            raise RuntimeError("Call start_session() first")

        turn = 0
        while turn < self.max_turns and not self.session.completed:
            turn += 1
            self._total_turns = turn

            agent_response = await self._safe_agent_turn()
            if agent_response is None:
                retries = 0
                while retries < 3 and agent_response is None:
                    retries += 1
                    self.session.messages.append(AgentMessage(
                        role="environment",
                        content=self.prompt_manager.get_guidance_no_response(),
                    ))
                    agent_response = await self._safe_agent_turn()
                if agent_response is None:
                    self._vprint(f"  [WARN] No response after three retries; stopping")
                    self.session.completed = True
                    break

            if agent_response.get("finish"):
                sql = self._extract_sql(agent_response.get("content", ""))
                self.session.pred_sql = sql
                self.session.completed = True
                self._vprint(f"  [FINISH] {sql[:120]}{'...' if len(sql) > 120 else ''}")
                break

            if agent_response.get("tool_call"):
                await self._environment_turn(agent_response["tool_call"])
            else:
                retries = 0
                took_action = False
                while retries < 3 and not took_action:
                    retries += 1
                    self.session.messages.append(AgentMessage(
                        role="environment",
                        content=self.prompt_manager.get_guidance_no_tool_call(),
                    ))
                    resp = await self._safe_agent_turn()
                    if resp is None:
                        continue
                    if resp.get("finish"):
                        sql = self._extract_sql(resp.get("content", ""))
                        self.session.pred_sql = sql
                        self.session.completed = True
                        took_action = True
                        break
                    if resp.get("tool_call"):
                        await self._environment_turn(resp["tool_call"])
                        took_action = True
                        break
                if self.session.completed:
                    break
                if not took_action:
                    self._vprint(f"  [WARN] No tool call after three retries; stopping")
                    self.session.completed = True
                    break

        if not self.session.completed:
            self._vprint(f"  [WARN] Reached max_turns={self.max_turns}; making one final attempt to obtain SQL")
            await self._force_finish()

        self.session.total_turns = turn
        return self.session

    # =========================================================================
    # Agent Turn (with timeout + retry)
    # =========================================================================

    async def _safe_agent_turn(self) -> Optional[Dict[str, Any]]:
        """Wrap _agent_turn with per-turn timeout and retry."""
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                return await asyncio.wait_for(
                    self._agent_turn(), timeout=self.turn_timeout,
                )
            except asyncio.TimeoutError:
                if attempt < max_retries:
                    self._vprint(f"  [WARN] Turn timeout ({self.turn_timeout}s), retry {attempt+1}/{max_retries}")
                else:
                    self._vprint(f"  [ERROR] Turn failed after {max_retries} retries")
                    raise
        return None

    async def _agent_turn(self) -> Optional[Dict[str, Any]]:
        llm_messages = self._prepare_llm_messages()
        tools = self._prepare_tools()

        response = await self.llm_provider.generate_response_with_tools(
            llm_messages, tools,
        )

        content = response.get("content", "")
        tool_calls = response.get("tool_calls", [])
        finish = response.get("finish", False)

        if self.verbose:

            preview = content[:200].replace("\n", "\\n") if content else "(empty)"
            has_tc = len(tool_calls) > 0
            self._vprint(f"  [Turn {self._total_turns}] content={preview}")
            self._vprint(f"  [Turn {self._total_turns}] tool_calls={len(tool_calls)} finish={finish}")
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    self._vprint(f"  [Turn {self._total_turns}]   -> {fn.get('name', '?')}({fn.get('arguments', '')[:100]})")

        if finish or self._has_finish_marker(content):
            self.session.messages.append(AgentMessage(
                role="agent", content=content,
            ))
            return {"content": content, "finish": True}

        if tool_calls:
            tc = tool_calls[0]
            tool_name = tc.get("function", {}).get("name", "")
            try:
                arguments = json.loads(tc["function"].get("arguments", "{}"))
            except json.JSONDecodeError:
                arguments = {}

            self.session.messages.append(AgentMessage(
                role="agent",
                content=content,
                tool_call={"tool": tool_name, "arguments": arguments},
            ))

            self._vprint(f"  [Turn {self._total_turns}] {tool_name}({json.dumps(arguments, ensure_ascii=False)[:100]})")

            if tool_name in SEMANTIC_TOOLS:
                self._semantic_call_count += 1

            return {"content": content, "tool_call": {"tool": tool_name, "arguments": arguments}}

        if content:
            self.session.messages.append(AgentMessage(
                role="agent", content=content,
            ))
            return {"content": content}

        return None

    # =========================================================================
    # Environment Turn
    # =========================================================================

    async def _environment_turn(self, tool_call: dict):
        tool_name = tool_call.get("tool", "")
        arguments = tool_call.get("arguments", {})

        result = await self.mcp_client.execute_tool(tool_name, arguments)
        result_text = json.dumps(result, ensure_ascii=False, default=str)

        if self.verbose:
            preview = result_text[:300].replace("\n", "\\n")
            self._vprint(f"  [Observe] {tool_name} -> {preview}{'...(truncated)' if len(result_text) > 300 else ''}")

        self.session.messages.append(AgentMessage(
            role="environment",
            content=f"Tool '{tool_name}' result:\n{result_text}",
            tool_result=result,
        ))

    # =========================================================================
    # Force Finish
    # =========================================================================

    async def _force_finish(self):
        self.session.messages.append(AgentMessage(
            role="environment",
            content=(
                "You have reached the maximum number of turns. "
                "Please output your best SQL query attempt now, even if "
                "you are not fully confident. Use the format:\n"
                "FINISH: SELECT ..."
            ),
        ))

        llm_messages = self._prepare_llm_messages()
        tools = []
        response = await self.llm_provider.generate_response_with_tools(
            llm_messages, tools,
        )

        content = response.get("content", "")
        sql = self._extract_sql(content)
        if sql:
            self.session.pred_sql = sql
        self.session.messages.append(AgentMessage(
            role="agent", content=content,
        ))
        self.session.completed = True

    # =========================================================================
    # Trace Export (for self-evolution)
    # =========================================================================

    def export_trace(self) -> dict:
        """Implement export trace."""
        if not self.session:
            return {}

        messages_serialized = []
        for msg in self.session.messages:
            entry = {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp,
            }
            if msg.tool_call:
                entry["tool_call"] = msg.tool_call
            if msg.tool_result is not None:
                entry["tool_result"] = json.dumps(msg.tool_result, ensure_ascii=False, default=str)
            messages_serialized.append(entry)

        return {
            "session_id": self.session.session_id,
            "task": self.session.task,
            "db_id": self.session.db_id,
            "total_turns": self.session.total_turns,
            "pred_sql": self.session.pred_sql,
            "completed": self.session.completed,
            "messages": messages_serialized,
        }

    # =========================================================================
    # Helpers
    # =========================================================================

    def _vprint(self, msg: str):
        """Implement vprint."""
        if self.verbose:
            print(msg)

    def _prepare_llm_messages(self) -> List[Dict[str, Any]]:
        # Keep the initial task before the history to avoid restarting after tool results.
        api_messages = [
            {"role": "system", "content": msg.content}
            for msg in self.session.messages
            if msg.role == "system"
        ]
        api_messages.append({
            "role": "user",
            "content": f"Please start working on the task: {self.session.task}",
        })

        for msg in self.session.messages:
            if msg.role == "agent":
                entry = {"role": "assistant", "content": msg.content or ""}
                if msg.tool_call:
                    tc = msg.tool_call
                    entry["tool_calls"] = [{
                        "id": f"call_{hash(msg.timestamp) & 0xFFFFFFFF:08x}",
                        "type": "function",
                        "function": {
                            "name": tc["tool"],
                            "arguments": json.dumps(tc.get("arguments", {})),
                        },
                    }]
                api_messages.append(entry)

            elif msg.role == "environment":
                if msg.tool_result:
                    prev = api_messages[-1] if api_messages else None
                    tc_id = prev["tool_calls"][0]["id"] if (
                        prev and prev.get("tool_calls")
                    ) else "call_unknown"

                    api_messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps(msg.tool_result, ensure_ascii=False, default=str),
                    })
                else:
                    api_messages.append({"role": "user", "content": msg.content})

        return api_messages

    def _prepare_tools(self) -> List[Dict[str, Any]]:
        tools = []
        if self.session:
            for server_name, server_tools in self.session.available_tools.items():
                for tool_name, tool_info in server_tools.items():
                    tools.append({
                        "name": tool_name,
                        "description": tool_info.get("description", ""),
                        "inputSchema": tool_info.get("inputSchema", {}),
                    })
        return tools

    @classmethod
    def _has_finish_marker(cls, content: str) -> bool:
        """Detect FINISH marker, handling markdown bold formatting."""
        normalized = cls._normalize_finish_marker(str(content or ""))
        return bool(re.search(
            r"(?im)^\s*(?:#{1,6}\s*)?FINISH\s*:",
            normalized,
        ))

    # Regex to normalize markdown bold/formatting around FINISH marker.
    # Handles: **FINISH:**, **FINISH**:, **FINISH** :, FINISH:**
    _FINISH_NORMALIZE_RE = re.compile(
        r'\*{1,2}\s*FINISH\s*\*{0,2}\s*:\s*\*{0,2}',
        re.IGNORECASE,
    )

    @classmethod
    def _normalize_finish_marker(cls, content: str) -> str:
        """Strip markdown bold from around FINISH: so extraction regexes work."""
        return cls._FINISH_NORMALIZE_RE.sub('FINISH:', content)

    @staticmethod
    def _extract_sql(content: str) -> str:
        if not content:
            return ""

        # Normalize markdown bold around FINISH: "**FINISH:**" → "FINISH:"
        # deepseek-v4-flash frequently outputs **FINISH:** SQL which breaks
        # the regex below because * after : doesn't match \s*
        normalized = BIRDReActAgent._normalize_finish_marker(content or "")





        match = re.search(
            r'(?im)FINISH\s*:\s*(SELECT\s+.+?)(?:\n\n|\n\s*\n|\Z)',
            normalized,
            re.DOTALL,
        )
        if not match:

            match = re.search(
                r'(?im)FINISH\s*:\s*([^\n]+)',
                normalized,
            )
        if not match:
            return ""

        sql = match.group(1).strip()
        # Strip code fences and inline code backticks
        sql = re.sub(r'^```(?:sql)?\s*', '', sql)
        sql = re.sub(r'\s*```$', '', sql)
        sql = re.sub(r'^`+\s*', '', sql)
        sql = re.sub(r'\s*`+$', '', sql)
        # Strip markdown bold artifacts (defense-in-depth for edge cases)
        sql = re.sub(r'^\*\*\s*', '', sql)
        sql = re.sub(r'\s*\*\*$', '', sql)
        sql = sql.rstrip(";").strip()

        return sql

    @property
    def semantic_call_count(self) -> int:
        return self._semantic_call_count
