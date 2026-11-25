#!/usr/bin/env python3
"""
Autonomous Data Analysis Agent - Refactored Version
An agent that autonomously explores and analyzes data through conversation with the environment
"""

import asyncio
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent directory to Python path to allow imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import our modules
# Import LLM providers
from agent.llm_providers import LLMProvider, create_llm_provider, log_api_stats_summary
from agent.models import AgentMessage, AutonomousSession, MCPServerConfig
from agent.prompt_manager import PromptManager
from agent.ui_components import (
    print_agent_turn,
    print_completion,
    print_environment_turn,
    print_error,
    print_exploration_start,
    print_log_info,
    print_session_start,
    print_turn_header,
    print_warning,
)
from tool_server.mcp_client import MCPClientManager

# Tool categories for insight generation control
# Only evidence-gathering tools produce insights; navigation/semantic tools do not.
NAVIGATION_TOOLS = {
    "describe_table",
    "get_database_info",
    "resolve_semantics",
    "browse_semantics",
}

EVIDENCE_TOOLS = {
    "execute_query",
    "execute_python",
}

# Global variables for logging configuration
LOG_DIR = None
LOG_TIMESTAMP = None

def setup_simple_logging(log_dir: str):
    """Setup simple logging with specified directory"""
    global LOG_DIR, LOG_TIMESTAMP

    # Set global variables
    LOG_DIR = log_dir
    LOG_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Ensure logs directory exists
    logs_path = Path(log_dir)
    logs_path.mkdir(parents=True, exist_ok=True)


class AutonomousDataAgent:
    """Autonomous data analysis agent that explores data through conversation with environment"""

    def __init__(
        self, llm_provider: LLMProvider, mcp_configs: List[MCPServerConfig],
        auto_finish: bool = True, semantic_manifest: str = "",
        semantic_layer = None, semantic_tools: list = None, split: str = "",
    ):
        self.llm_provider = llm_provider
        self.mcp_configs = mcp_configs
        self.auto_finish = auto_finish
        self.session: Optional[AutonomousSession] = None
        self._semantic_layer = semantic_layer
        self._semantic_tools = semantic_tools or []
        self._split = split

        # Initialize managers
        self.mcp_manager = MCPClientManager()
        self.prompt_manager = PromptManager(
            auto_finish, semantic_manifest=semantic_manifest,
        )

        # Initialize logging data structures
        self.message_stats = []  # For individual message token/timing stats
        self.session_stats = {  # For overall session statistics
            'total_messages': 0,
            'total_agent_messages': 0,
            'total_user_messages': 0,
            'total_insight_messages': 0,
            'total_prompt_tokens': 0,
            'total_completion_tokens': 0,
            'total_tokens': 0,
            'total_tool_calls': 0,
            'successful_tool_calls': 0,
            'failed_tool_calls': 0,
            'total_time': 0,
            'start_time': None,
            'end_time': None
        }

    async def start_autonomous_session(self, task: str) -> AutonomousSession:
        """Start a new autonomous exploration session"""
        session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Initialize session statistics
        self.session_stats['start_time'] = datetime.now().isoformat()

        # Connect to all MCP servers
        await self.mcp_manager.connect_to_servers(self.mcp_configs)

        # Get available tools from all servers
        available_tools = await self.mcp_manager.get_available_tools()

        # Create session
        self.session = AutonomousSession(
            session_id=session_id,
            start_time=datetime.now(),
            task=task,
            messages=[],
            available_tools=available_tools,
            mcp_servers={config.name: config.description for config in self.mcp_configs}
        )

        # Add system message with task
        # Regenerate manifest with the exposed semantic tools
        if self._semantic_layer and self._semantic_tools:
            self.prompt_manager.semantic_manifest = self._semantic_layer.manifest(
                exposed_tools=self._semantic_tools,
            )
        system_msg = AgentMessage("system", self.prompt_manager.build_system_prompt_with_task(task))
        self.session.messages.append(system_msg)

        print(f"Started autonomous session: {session_id}")
        print(f"Task: {task}")

        # Print session start info
        print_session_start(self.session, self.llm_provider, self.mcp_manager.mcp_sessions, self.auto_finish)

        return self.session

    async def run_autonomous_exploration(self, max_turns: int = 20) -> Dict[str, Any]:
        """Run autonomous exploration until task completion"""
        if not self.session:
            raise Exception("No active session")

        print_exploration_start(self.session.task, max_turns)

        turn = 0
        while turn < max_turns and not self.session.completed:
            turn += 1
            print_turn_header(turn)

            # Agent reasoning and tool call
            agent_response = await self._agent_turn()
            if not agent_response:
                # No response from agent
                print_warning("Agent provided no response")
                # Retry up to 3 times if no response and not completed
                retries = 0
                while retries < 3 and not self.session.completed and not agent_response:
                    retries += 1
                    print_warning(f"Retrying due to no response (attempt {retries}/3)")
                    agent_response = await self._agent_turn()
                    if agent_response and agent_response.get("completion"):
                        print("Completion detected during retry - stopping exploration")
                        self.session.completed = True
                        break
                if not agent_response and not self.session.completed:
                    print_warning("No response after 3 retries - forcing final summary and completion")
                    await self._generate_final_summary_on_turn_limit()
                    self.session.completed = True
                    break
                if self.session.completed:
                    break
                # Fall through to normal handling if we obtained a response
            elif isinstance(agent_response, dict) and agent_response.get("completion"):
                # Agent wants to complete the task
                print("Main loop received completion signal - stopping exploration")
                self.session.completed = True
                break
            elif isinstance(agent_response, dict) and agent_response.get("no_tool_call"):
                # Agent provided reasoning without tool call or had an error - continue to next turn
                if agent_response.get("error"):
                    print(f"Warning: Agent had an error: {agent_response['error']}")
                # Retry up to 3 times to elicit a tool call or finish
                retries = 0
                took_action = False
                while retries < 3 and not self.session.completed and not took_action:
                    retries += 1
                    print_warning(f"Retrying due to no tool call (attempt {retries}/3)")

                    # Add a prompt to guide the agent before retrying
                    guidance_msg = AgentMessage(
                        role="environment",
                        content="Your message should either contain a function call or use 'FINISH' in your message to end the task."
                    )
                    self.session.messages.append(guidance_msg)

                    retry_response = await self._agent_turn()
                    if not retry_response:
                        continue
                    if isinstance(retry_response, dict) and retry_response.get("completion"):
                        print("Completion detected during retry - stopping exploration")
                        self.session.completed = True
                        took_action = True
                        break
                    if isinstance(retry_response, dict) and "tool" in retry_response:
                        # Execute tool for the retry response
                        await self._environment_turn(retry_response)
                        took_action = True
                        break
                    # If still no tool call, loop to retry
                if self.session.completed:
                    break
                if not took_action:
                    print_warning("No tool call after 3 retries - forcing final summary and completion")
                    await self._generate_final_summary_on_turn_limit()
                    self.session.completed = True
                    break
                # If action was taken, continue to next turn
                continue

            # Environment response (tool execution)
            # Only execute tool if agent_response is a valid tool call
            if isinstance(agent_response, dict) and "tool" in agent_response:
                await self._environment_turn(agent_response)
            else:
                # This shouldn't happen, but just in case
                print(f"Error: Unexpected agent_response format for tool execution: {agent_response}")
                continue

            # No need to check for completion here since we check the finish field directly
            # in the _agent_turn method

        # If reached turn limit without completion, generate final summary
        if not self.session.completed and turn >= max_turns:
            print(f"Turn limit ({max_turns}) reached. Generating final summary...")
            await self._generate_final_summary_on_turn_limit()
            self.session.completed = True

        # Save logging data
        self._save_chat_messages_csv()
        self._save_message_stats_csv()
        self._save_session_stats_json()

        # Generate results
        results = {
            "session_id": self.session.session_id,
            "task": self.session.task,
            "completed": self.session.completed,
            "total_turns": turn,
            "conversation": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                    "tool_call": msg.tool_call,
                    "tool_result": msg.tool_result
                }
                for msg in self.session.messages if msg.role != "system"
            ]
        }

        if self._semantic_layer is not None:
            from evoontology import TrajectoryStore, from_message_trace

            root_dir = getattr(self._semantic_layer.store, "root_dir", "")
            if root_dir:
                final_answer = self._get_latest_agent_message() or ""
                TrajectoryStore(root_dir).append(from_message_trace(
                    task_id=self.session.session_id,
                    question=self.session.task,
                    ontology_version=self._semantic_layer.version,
                    split=self._split,
                    messages=results["conversation"],
                    final_answer=final_answer,
                    task_status="completed",
                ))

        print_completion(turn, "Session completed. Check logs for insights and statistics.")

        return results

    def _get_latest_agent_message(self) -> Optional[str]:
        """Get the latest agent message content"""
        for msg in reversed(self.session.messages):
            if msg.role == "agent":
                return msg.content
        return None

    def _get_final_agent_summary(self) -> Optional[str]:
        """Get the final agent summary (FINISH message) if available"""
        finish_messages = []
        for msg in self.session.messages:
            if msg.role == "agent" and msg.content and self._has_finish_marker(msg.content):
                finish_messages.append(msg.content.strip())

        # Return the last FINISH message found
        if finish_messages:
            return finish_messages[-1]

        # Fallback: return the last agent message content if no explicit FINISH found
        agent_messages = [m.content for m in self.session.messages if m.role == "agent" and m.content]
        if agent_messages:
            return agent_messages[-1].strip()
        return None

    def _get_final_summary_from_csv(self) -> Optional[str]:
        """Get the final agent summary from CSV file if not found in messages"""
        if not LOG_DIR:
            return None

        messages_file = Path(LOG_DIR) / f"chat_messages_{LOG_TIMESTAMP}.csv"
        if not messages_file.exists():
            return None

        try:
            finish_messages = []
            with open(messages_file, 'r', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    if row.get('role') == 'agent' and row.get('content'):
                        content = row['content'].strip()
                        if self._has_finish_marker(content):
                            finish_messages.append(content)

            # Return the last FINISH message found
            if finish_messages:
                return finish_messages[-1]

        except Exception as e:
            print(f"Error reading final summary from CSV: {e}")

        return None

    async def _generate_final_summary_on_turn_limit(self):
        """Generate final summary when turn limit is reached using prompt manager"""
        if not self.session:
            return

        # Convert AgentMessage objects to standard chat message list format
        chat_messages = []
        for msg in self.session.messages:
            if msg.role != "system":
                chat_msg = {
                    "role": msg.role,
                    "content": msg.content
                }
                if msg.tool_call:
                    chat_msg["tool_call"] = msg.tool_call
                if msg.tool_result:
                    chat_msg["tool_result"] = msg.tool_result
                chat_messages.append(chat_msg)

        # Use prompt manager to build prompts with chat message list
        system_prompt = self.prompt_manager.get_final_summary_system_prompt()
        final_summary_prompt = self.prompt_manager.build_final_summary_prompt(chat_messages)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": final_summary_prompt}
        ]

        try:
            start_time = time.time()
            response = await self.llm_provider.generate_response(messages, max_tokens=16384)
            generation_time = time.time() - start_time

            # Add the forced summary as an agent message
            forced_summary_msg = AgentMessage(
                role="agent",
                content=f"FINISH: {response}"
            )
            self.session.messages.append(forced_summary_msg)

            # Record message stats for final summary generation
            self._record_message_stats(
                "assistant", response, generation_time=generation_time
            )

            print(f"Generated final summary: {response[:100]}...")

        except Exception as e:
            print(f"Error generating final summary: {e}")
            # Add error message as agent message
            error_msg = AgentMessage(
                role="agent",
                content=f"FINISH: Error generating final summary: {e}"
            )
            self.session.messages.append(error_msg)

    async def _agent_turn(self) -> Optional[Dict[str, Any]]:
        """Agent's turn: reasoning + tool call using native tool calling"""
        # Prepare conversation history for LLM
        llm_messages = self._prepare_llm_messages()

        # Convert MCP tools to provider format
        mcp_tools = self._prepare_mcp_tools_for_llm()

        # Get agent response with native tool calling
        start_time = time.time()
        response = await self.llm_provider.generate_response_with_tools(llm_messages, mcp_tools)
        generation_time = time.time() - start_time

        # Extract content, thinking, and tool calls
        content = response.get("content", "")
        thinking = response.get("thinking", "")  # Extract thinking content
        tool_calls = response.get("tool_calls", [])
        finish = response.get("finish", False)  # Get the finish flag

        # Extract token usage if available
        usage = response.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

        # Accept only an explicit line-level FINISH marker.  Ordinary planning
        # text such as "let me finish the analysis" must not terminate a run.
        textual_finish = self._has_finish_marker(content)

        if finish or textual_finish:
            # Agent wants to finish - add message and return completion signal
            print(f"FINISH signal detected: {content[:100]}...")
            print_agent_turn(content, {}, thinking)

            agent_msg = AgentMessage(
                role="agent",
                content=content
            )
            self.session.messages.append(agent_msg)

            # Record message stats
            self._record_message_stats(
                "assistant", content, prompt_tokens, completion_tokens, total_tokens, generation_time
            )

            # Return completion signal - this should immediately stop the main loop
            print("Returning completion signal to main loop")
            return {"content": content, "completion": True}

        # Only process tool calls if not finishing
        if not tool_calls:
            # No tool calls, just reasoning/completion
            if content:
                agent_msg = AgentMessage(
                    role="agent",
                    content=content
                )
                self.session.messages.append(agent_msg)

                # Record message stats
                self._record_message_stats(
                    "assistant", content, prompt_tokens, completion_tokens, total_tokens, generation_time
                )

                # Display agent's response
                print_agent_turn(content, {}, thinking)

                # Return the content so main loop can check for completion
                return {"content": content, "no_tool_call": True}

            print_warning("Agent provided no content and no tool call")
            # Add a prompt to guide the agent
            guidance_msg = AgentMessage(
                role="environment",
                content="Your message should either contain a function call or use 'FINISH' in your message to end the task."
            )
            self.session.messages.append(guidance_msg)
            return None

        # Use the first tool call
        first_tool_call = tool_calls[0]

        # Convert to our internal format
        tool_call = self._convert_provider_tool_call(first_tool_call)
        if not tool_call:
            print_error("Agent provided invalid tool call")
            return {"error": "Failed to convert tool call", "no_tool_call": True}

        # Check for format errors and return them to agent
        if "error" in tool_call:
            error_msg = tool_call["error"]
            print_error(f"Tool call format error: {error_msg}")

            # Create environment message with format error
            env_msg = AgentMessage(
                role="environment",
                content=f"ERROR: {error_msg}. Please provide a properly formatted tool call.",
                tool_result={"error": error_msg}
            )
            self.session.messages.append(env_msg)

            # Return error signal to continue the conversation
            return {"error": error_msg, "no_tool_call": True}

        # Create agent message with original LLM output content
        agent_msg = AgentMessage(
            role="agent",
            content=content,  # Keep original LLM output as-is
            tool_call=tool_call
        )
        self.session.messages.append(agent_msg)

        # Record message stats
        self._record_message_stats(
            "assistant", content, prompt_tokens, completion_tokens, total_tokens, generation_time
        )

        # Display agent's reasoning and tool call
        print_agent_turn(content, tool_call, thinking)

        return tool_call

    async def _environment_turn(self, tool_call: Dict[str, Any]) -> str:
        """Environment's turn: execute tool and return result"""
        # Validate tool_call has required fields
        if not isinstance(tool_call, dict) or "tool" not in tool_call:
            error_msg = f"Invalid tool_call format: {tool_call}"
            print(f"Error: {error_msg}")
            self.session_stats['failed_tool_calls'] += 1
            return f"ERROR: {error_msg}"

        # Execute the tool
        start_time = time.time()
        result = await self.mcp_manager.execute_tool(tool_call["tool"], tool_call.get("arguments", {}))
        execution_time = time.time() - start_time

        # Update tool call statistics
        self.session_stats['total_tool_calls'] += 1
        if isinstance(result, dict) and result.get('error'):
            self.session_stats['failed_tool_calls'] += 1
        else:
            self.session_stats['successful_tool_calls'] += 1

        # Format result as environment response
        env_response = f"Tool execution result: {result}"

        # Create environment message
        env_msg = AgentMessage(
            role="environment",
            content=env_response,
            tool_result=result
        )
        self.session.messages.append(env_msg)

        # Record message stats for environment response
        self._record_message_stats("user", env_response, execution_time=execution_time)

        # Display environment response
        print_environment_turn(result)

        # Generate insight only for evidence-gathering tools (execute_query, execute_python).
        # Navigation and semantic tools must NOT produce insights.
        tool_name = tool_call.get("tool", "")
        if tool_name in EVIDENCE_TOOLS:
            await self._generate_and_save_insight()

        return env_response

    def _prepare_llm_messages(self) -> List[Dict[str, Any]]:
        """Prepare messages for LLM call with tool call history support"""

        # Detect provider type
        provider_name = self.llm_provider.get_provider_name().lower()
        supports_structured_tools = any(name in provider_name for name in ["openai", "gemini", "gemini3", "anthropic", "minimax"])
        is_gemini3 = "gemini3" in provider_name

        # Keep the initial task before the history to avoid restarting after tool results.
        messages = [
            {"role": "system", "content": self.prompt_manager.build_system_prompt_with_task(self.session.task)},
            {"role": "user", "content": f"Please start working on the task: {self.session.task}"},
        ]

        for i, msg in enumerate(self.session.messages):
            if msg.role != "system":
                # Convert agent/environment roles to assistant/user for LLM
                llm_role = "assistant" if msg.role == "agent" else "user"

                # For supported providers, process tool calls specially
                if supports_structured_tools:
                    if msg.role == "agent":
                        # Agent message
                        message = {"role": llm_role, "content": msg.content}

                        # Add tool calls if present
                        if msg.tool_call:
                            tool_call_entry = {
                                "id": f"call_{abs(hash(f'{msg.timestamp}_{i}'))}",
                                "type": "function",
                                "function": {
                                    "name": msg.tool_call.get("tool", ""),
                                    "arguments": json.dumps(msg.tool_call.get("arguments", {}))
                                }
                            }
                            # Include thought_signature for Gemini 3
                            if is_gemini3 and msg.tool_call.get("thought_signature"):
                                tool_call_entry["thought_signature"] = msg.tool_call.get("thought_signature")
                            message["tool_calls"] = [tool_call_entry]

                        messages.append(message)

                    elif msg.role == "environment":
                        # Environment message with tool result
                        if msg.tool_result and messages and messages[-1].get("tool_calls"):
                            # Add tool response message (OpenAI/Anthropic/MiniMax format)
                            if "openai" in provider_name or "anthropic" in provider_name or "minimax" in provider_name:
                                tool_response_msg = {
                                    "role": "tool",
                                    "tool_call_id": messages[-1]["tool_calls"][0]["id"],
                                    "name": messages[-1]["tool_calls"][0]["function"]["name"],
                                    "content": json.dumps(msg.tool_result) if not isinstance(msg.tool_result, str) else str(msg.tool_result)
                                }
                                messages.append(tool_response_msg)
                            # For Gemini, add tool response with same structure for _convert_messages_to_contents
                            elif "gemini" in provider_name:
                                tool_response_msg = {
                                    "role": "tool",
                                    "tool_call_id": messages[-1]["tool_calls"][0]["id"],
                                    "name": messages[-1]["tool_calls"][0]["function"]["name"],
                                    "content": json.dumps(msg.tool_result) if not isinstance(msg.tool_result, str) else str(msg.tool_result)
                                }
                                messages.append(tool_response_msg)
                            else:
                                # Fallback for other providers
                                message = {"role": llm_role, "content": msg.content}
                                messages.append(message)
                        else:
                            # Regular environment message without tool result
                            message = {"role": llm_role, "content": msg.content}
                            messages.append(message)
                else:
                    # For providers that don't support structured tools, use simple format
                    message = {"role": llm_role, "content": msg.content}
                    messages.append(message)

        return messages

    def _prepare_mcp_tools_for_llm(self) -> List[Dict[str, Any]]:
        """Convert MCP tools to a flat list for LLM provider."""
        tools_list = []

        if self.session and self.session.available_tools:
            for server_name, tools in self.session.available_tools.items():
                for tool_name, tool_info in tools.items():
                    tool_dict = {
                        "name": tool_name,
                        "description": tool_info.get("description", ""),
                        "inputSchema": tool_info.get("inputSchema", {})
                    }
                    tools_list.append(tool_dict)

        return tools_list

    def _convert_provider_tool_call(self, provider_tool_call: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Convert provider-specific tool call to our internal format"""
        if "function" in provider_tool_call:
            function = provider_tool_call["function"]
            tool_name = function.get("name", "")
            arguments_str = function.get("arguments", "{}")

            # Parse arguments if they're a string - NO AUTO CORRECTION
            if isinstance(arguments_str, str):
                try:
                    arguments = json.loads(arguments_str)
                except json.JSONDecodeError as e:
                    # Return error instead of empty dict
                    print(f"Error: Invalid JSON in tool call arguments: {e}")
                    return {"error": f"Tool call format error: Invalid JSON in arguments - {e}"}
            else:
                arguments = arguments_str

            # Validate tool name is not empty
            if not tool_name:
                return {"error": "Tool call format error: Missing tool name"}

            return {
                "tool": tool_name,
                "arguments": arguments,
                "thought_signature": provider_tool_call.get("thought_signature")  # Gemini 3 specific
            }

        return {"error": "Tool call format error: Missing 'function' field"}

    @staticmethod
    def _is_discovery_sql(sql: str) -> bool:
        """Check if SQL is a schema/catalog discovery query that should NOT generate insight.

        Discovery queries: DESCRIBE, SELECT DISTINCT (catalog probing),
        COUNT(*) without fact_value filter (availability probing).
        """
        sql_upper = sql.upper().strip()
        # DESCRIBE queries
        if sql_upper.startswith("DESCRIBE") or sql_upper.startswith("PRAGMA"):
            return True
        # SELECT DISTINCT — catalog probing (fact_name listing, year enumeration, etc.)
        if "SELECT DISTINCT" in sql_upper:
            return True
        # COUNT(*) without fact_name filter — availability probing
        if "COUNT(*)" in sql_upper and "FACT_NAME" not in sql_upper:
            return True
        return False

    @staticmethod
    def _has_finish_marker(content: str) -> bool:
        """Return True only for an explicit ``FINISH:`` line marker."""
        return bool(re.search(
            r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?FINISH\s*:",
            str(content or ""),
        ))

    @staticmethod
    def _query_text(arguments: Dict[str, Any]) -> str:
        """Read the SQL text from the real MCP argument, with legacy fallback."""
        return str(arguments.get("query") or arguments.get("sql") or "")

    async def _generate_and_save_insight(self):
        """Generate insight for the latest tool execution and save to CSV.

        Skips insight generation for discovery SQL (DESCRIBE, SELECT DISTINCT, COUNT(*) probes)
        per goal.txt: only analytical queries with real data results generate insights.
        """
        if len(self.session.messages) < 2:
            return

        # Get the last two messages (assistant with tool call, then user with result)
        last_messages = self.session.messages[-2:]
        if len(last_messages) != 2 or last_messages[0].role != "agent" or last_messages[1].role != "environment":
            return

        assistant_msg = last_messages[0]
        user_msg = last_messages[1]

        # Skip insight generation for discovery SQL
        if assistant_msg.tool_call:
            sql = self._query_text(assistant_msg.tool_call.get("arguments") or {})
            if sql and self._is_discovery_sql(sql):
                return

        # Extract fact_names from tool result for logging
        fact_names_queried = ""
        if assistant_msg.tool_call and user_msg.tool_result:
            fact_names_queried = self._extract_fact_names_from_tool_result(
                assistant_msg.tool_call, user_msg.tool_result
            )

        # Create insight generation prompt using prompt manager
        insight_prompt = self.prompt_manager.build_insight_prompt(
            assistant_msg.content,
            user_msg.content,
            self.session.task
        )

        messages = [
            {"role": "system", "content": self.prompt_manager.get_insight_system_prompt()},
            {"role": "user", "content": insight_prompt}
        ]

        try:
            start_time = time.time()
            insight = await self.llm_provider.generate_response(messages, max_tokens=512)
            insight_time = time.time() - start_time

            # Save insight to CSV with fact_names and concept
            self._save_insight_to_csv(
                timestamp=datetime.now().isoformat(),
                assistant_message=assistant_msg.content,
                user_message=user_msg.content,
                insight=insight,
                fact_names_queried=fact_names_queried,
                concept=""
            )

            # Record stats for insight generation
            self._record_message_stats("insight_generation", insight, generation_time=insight_time)

            print(f"Generated insight: {insight[:100]}...")
        except Exception as e:
            print(f"Error: Failed to generate insight: {e}")

    def _extract_fact_names_from_tool_result(self, tool_call: Dict[str, Any], tool_result: Any) -> str:
        """Extract fact_names from SQL tool call result for logging.

        Returns comma-separated fact_name string, or empty string.
        Two paths:
        1. Result has 'fact_name' column → extract values
        2. Result columns themselves are fact_names → match against known set
        """
        import re as _re
        fact_names = set()

        if not isinstance(tool_result, dict):
            return ""

        cols = tool_result.get("cols", [])
        data = tool_result.get("data", [])

        # Path 1: 'fact_name' column present
        fn_idx = None
        for i, col in enumerate(cols):
            if col == "fact_name":
                fn_idx = i
                break

        if fn_idx is not None:
            for row_data in data:
                if fn_idx < len(row_data) and row_data[fn_idx]:
                    fact_names.add(str(row_data[fn_idx]))
            return ",".join(sorted(fact_names))

        # Path 2: cols themselves may be fact_names
        # Check if any col looks like a known XBRL fact_name (PascalCase pattern)
        xbrl_pattern = _re.compile(r'^[A-Z][a-zA-Z0-9]+$')
        for col in cols:
            if xbrl_pattern.match(col) and len(col) > 3:
                fact_names.add(col)

        return ",".join(sorted(fact_names)) if fact_names else ""

    def _save_insight_to_csv(self, timestamp: str, assistant_message: str, user_message: str, insight: str,
                              fact_names_queried: str = "", concept: str = ""):
        """Save insight to CSV file"""
        if not LOG_DIR:
            return

        insights_file = Path(LOG_DIR) / f"insights_{LOG_TIMESTAMP}.csv"
        file_exists = insights_file.exists()

        with open(insights_file, 'a', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['timestamp', 'assistant_message', 'user_message', 'insight',
                          'fact_names_queried', 'concept']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            if not file_exists:
                writer.writeheader()

            writer.writerow({
                'timestamp': timestamp,
                'assistant_message': assistant_message,
                'user_message': user_message,
                'insight': insight,
                'fact_names_queried': fact_names_queried,
                'concept': concept
            })

    def _record_message_stats(self, role: str, content: str, prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0, generation_time: float = 0, execution_time: float = 0):
        """Record statistics for a message"""
        stats = {
            'timestamp': datetime.now().isoformat(),
            'role': role,
            'content_length': len(content),
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens,
            'total_tokens': total_tokens,
            'generation_time': generation_time,
            'execution_time': execution_time
        }

        self.message_stats.append(stats)

        # Update session stats (counts and timing only, not token accumulation)
        self.session_stats['total_messages'] += 1
        if role == 'assistant':
            self.session_stats['total_agent_messages'] += 1
        elif role == 'user':
            self.session_stats['total_user_messages'] += 1
        elif role == 'insight_generation':
            self.session_stats['total_insight_messages'] += 1

        # Don't accumulate token counts here - they will be calculated correctly in _save_session_stats_json
        # Only accumulate timing information
        self.session_stats['total_time'] += generation_time + execution_time

    def _save_chat_messages_csv(self):
        """Save complete chat messages to CSV"""
        if not LOG_DIR or not self.session:
            return

        messages_file = Path(LOG_DIR) / f"chat_messages_{LOG_TIMESTAMP}.csv"

        with open(messages_file, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['timestamp', 'role', 'content', 'tool_call', 'tool_result']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for msg in self.session.messages:
                if msg.role != "system":  # Skip system messages
                    writer.writerow({
                        'timestamp': msg.timestamp if msg.timestamp else '',
                        'role': msg.role,
                        'content': msg.content,
                        'tool_call': json.dumps(msg.tool_call) if msg.tool_call else '',
                        'tool_result': json.dumps(msg.tool_result) if msg.tool_result else ''
                    })

    def _save_message_stats_csv(self):
        """Save individual message statistics to CSV"""
        if not LOG_DIR:
            return

        stats_file = Path(LOG_DIR) / f"message_stats_{LOG_TIMESTAMP}.csv"

        with open(stats_file, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['timestamp', 'role', 'content_length', 'prompt_tokens', 'completion_tokens', 'total_tokens', 'generation_time', 'execution_time']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for stats in self.message_stats:
                writer.writerow(stats)

    def _save_session_stats_json(self):
        """Save overall session statistics to JSON"""
        if not LOG_DIR:
            return

        # Calculate final statistics
        self.session_stats['end_time'] = datetime.now().isoformat()
        if self.session_stats['start_time']:
            start = datetime.fromisoformat(self.session_stats['start_time'])
            end = datetime.fromisoformat(self.session_stats['end_time'])
            self.session_stats['session_duration'] = (end - start).total_seconds()

        # Calculate success rate
        if self.session_stats['total_tool_calls'] > 0:
            self.session_stats['tool_success_rate'] = self.session_stats['successful_tool_calls'] / self.session_stats['total_tool_calls']
        else:
            self.session_stats['tool_success_rate'] = 0

        # Calculate final token statistics from the last assistant call
        # This represents the complete conversation history tokens, not cumulative duplicates
        assistant_messages = [stats for stats in self.message_stats if stats['role'] == 'assistant']
        if assistant_messages:
            # Use the last assistant call which contains the complete conversation history
            last_call = assistant_messages[-1]
            final_prompt_tokens = last_call['prompt_tokens']

            # Sum all completion tokens generated during the session
            total_completion_tokens = sum(msg['completion_tokens'] for msg in assistant_messages)

            # Calculate true total tokens
            final_total_tokens = final_prompt_tokens + total_completion_tokens

            # Update session stats with corrected values
            self.session_stats['total_prompt_tokens'] = final_prompt_tokens
            self.session_stats['total_completion_tokens'] = total_completion_tokens
            self.session_stats['total_tokens'] = final_total_tokens

        # Add session info
        if self.session:
            self.session_stats['session_id'] = self.session.session_id
            self.session_stats['task'] = self.session.task
            self.session_stats['completed'] = self.session.completed

            # Add final agent summary if available
            final_summary = self._get_final_agent_summary()
            if final_summary:
                self.session_stats['final_summary'] = final_summary
            else:
                # Try to get final summary from CSV file if not found in messages
                final_summary_from_csv = self._get_final_summary_from_csv()
                if final_summary_from_csv:
                    self.session_stats['final_summary'] = final_summary_from_csv

        stats_file = Path(LOG_DIR) / f"session_stats_{LOG_TIMESTAMP}.json"

        with open(stats_file, 'w', encoding='utf-8') as jsonfile:
            json.dump(self.session_stats, jsonfile, indent=2, ensure_ascii=False)

    async def close(self):
        """Close agent and connections"""
        await self.mcp_manager.close()
        log_api_stats_summary()
        print("Autonomous data agent closed")

async def main():
    """Main function"""
    import argparse
    import sys

    # Force UTF-8 stdout to avoid UnicodeEncodeError on Windows (GBK default)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    # Add parent directory to path to allow importing config
    sys.path.append(str(Path(__file__).parent.parent))
    from config import Config, get_config

    parser = argparse.ArgumentParser(description="Autonomous Data Analysis Agent")
    parser.add_argument("--llm-provider",
                       choices=["openai", "anthropic", "gemini", "vllm", "minimax", "gemini3"],
                       help="LLM provider to use (overrides config)")
    parser.add_argument("--api-key", help="API key for the LLM provider")
    parser.add_argument("--model", help="Model name to use")
    parser.add_argument("--base-url", help="Base URL for LLM Backend provider")
    parser.add_argument("--port", type=int, help="Port number for LLM Backend provider")

    # Task specification
    parser.add_argument("--task", required=True, help="Task for the agent to accomplish")
    parser.add_argument("--max-turns", type=int, help="Maximum number of turns")
    parser.add_argument("--no-auto-finish", action="store_true", help="Disable automatic finishing")

    # MCP server configuration
    parser.add_argument("--sql-server", help="Path to SQL MCP server script")
    parser.add_argument("--data-path", help="Data path for MCP servers")
    parser.add_argument("--code-server", help="Path to Code MCP server script")
    parser.add_argument("--semantic-server", help="Path to TCEO semantic MCP server script")
    parser.add_argument("--semantic-store", help="Path to the EvoOntology workspace")
    parser.add_argument("--semantic-version", default="",
                        help="Explicit semantic version to load (default: active)")
    parser.add_argument("--servers", help="JSON file with MCP server configurations")

    # Config file arguments
    parser.add_argument("--config", help="Path to config.yaml file")
    parser.add_argument("--scenario", help="Scenario name (mimic, 10k, globem)")
    parser.add_argument("--split", default="",
                        help="Split label recorded in the trajectory (e.g. train/test)")

    # Logging configuration
    parser.add_argument("--log-dir", default="./logs", help="Directory for log files")

    args = parser.parse_args()

    # Setup simple logging with specified directory
    setup_simple_logging(args.log_dir)

    # Load configuration
    config = get_config(args.config)

    # Resolve parameters from config (agent uses provider section)
    provider_name = args.llm_provider or config.provider.default_provider
    model_name = args.model or config.provider.default_model
    base_url = args.base_url or config.provider.vllm_base_url
    api_key = args.api_key or Config.resolve_api_key(config.provider.api_key_env)
    port = args.port or config.provider.vllm_port
    max_turns = args.max_turns or config.agent.max_turns or 20

    # Create LLM provider
    try:
        llm_provider = create_llm_provider(
            provider_name,
            api_key=api_key,
            model=model_name,
            base_url=base_url,
            port=port
        )
        print(f"Using LLM provider: {llm_provider.get_provider_name()}")
    except Exception as e:
        print(f"Error: Failed to create LLM provider: {e}")
        return

    # Configure MCP servers
    mcp_configs = []

    if args.servers:
        # Load from JSON file
        try:
            with open(args.servers, 'r') as f:
                servers_data = json.load(f)
            for server in servers_data:
                mcp_configs.append(MCPServerConfig(
                    name=server["name"],
                    script_path=server["script_path"],
                    description=server["description"]
                ))
        except Exception as e:
            print(f"Error: Failed to load servers config: {e}")
            return
    else:
        # Use command line arguments
        if args.sql_server:
            # Build arguments for SQL server
            sql_args = []
            if args.data_path:
                sql_args.extend(["--data-path", args.data_path])
            if args.config:
                sql_args.extend(["--config", args.config])
            if args.scenario:
                sql_args.extend(["--scenario", args.scenario])

            mcp_configs.append(MCPServerConfig(
                name="sql",
                script_path=args.sql_server,
                description="SQL database query and analysis",
                args=sql_args
            ))

        if args.code_server:
            # Build arguments for Code server
            code_args = []
            if args.data_path:
                code_args.extend(["--data-path", args.data_path])
            if args.config:
                code_args.extend(["--config", args.config])
            if args.scenario:
                code_args.extend(["--scenario", args.scenario])

            mcp_configs.append(MCPServerConfig(
                name="code",
                script_path=args.code_server,
                description="Python code execution and file analysis",
                args=code_args
            ))

        if args.semantic_server:
            if not args.semantic_store:
                print("Error: --semantic-store is required when --semantic-server is enabled")
                return
            semantic_server_args = ["--store", args.semantic_store]
            if args.semantic_version:
                semantic_server_args += ["--version", args.semantic_version]
            mcp_configs.append(MCPServerConfig(
                name="semantic",
                script_path=args.semantic_server,
                description="TCEO semantic definitions, physical mappings, and analysis constraints",
                args=semantic_server_args,
            ))

    if not mcp_configs:
        print("Error: No MCP servers configured. Use --sql-server, --code-server, --semantic-server, or --servers")
        return

    # Create agent
    auto_finish = not args.no_auto_finish  # Default is True, --no-auto-finish sets it to False
    semantic_manifest = ""
    semantic_layer_obj = None
    semantic_tools_list = []
    if args.semantic_store:
        try:
            from tceo.runtime import DDRSemanticLayer
            layer = DDRSemanticLayer.load(args.semantic_store, version=args.semantic_version or None)
            exposed_tools = [
                item.strip()
                for item in os.getenv(
                    "DDR_SEMANTIC_TOOLS", "browse_semantics,resolve_semantics"
                ).split(",")
                if item.strip()
            ]
            include_fn = os.getenv("DDR_MANIFEST_FACT_NAMES", "1") != "0"
            semantic_manifest = layer.manifest(
                exposed_tools=exposed_tools, include_fact_names=include_fn,
            )
            semantic_layer_obj = layer
            semantic_tools_list = exposed_tools
        except Exception as e:
            print(f"Error: Failed to load ontology layer: {e}")
            return
    agent = AutonomousDataAgent(
        llm_provider=llm_provider,
        mcp_configs=mcp_configs,
        auto_finish=auto_finish,
        semantic_manifest=semantic_manifest,
        semantic_layer=semantic_layer_obj,
        semantic_tools=semantic_tools_list,
        split=args.split,
    )

    if not auto_finish:
        print_warning("Auto-finish is disabled. Agent will continue until max turns are reached.")
        print("Auto-finish disabled - agent will not finish automatically")

    try:
        # Start autonomous session
        await agent.start_autonomous_session(args.task)

        # Run autonomous exploration
        await agent.run_autonomous_exploration(max_turns=max_turns)

        # Print completion summary
        print(f"Exploration completed in {len([m for m in agent.session.messages if m.role == 'agent'])} turns")
        print(f"Generated {len(agent.message_stats)} message statistics entries")
        print(f"Tool success rate: {agent.session_stats.get('tool_success_rate', 0):.2%}")

        print_log_info(LOG_TIMESTAMP, args.log_dir)

    except KeyboardInterrupt:
        print_warning("Exploration interrupted by user")
    except Exception as e:
        print(f"Error: Agent execution failed: {e}")
        print_error(f"Agent execution failed: {e}")
    finally:
        await agent.close()

if __name__ == "__main__":
    asyncio.run(main())
