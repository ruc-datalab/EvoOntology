"""Regression tests for the initial task's position in benchmark conversations."""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def test_bird_keeps_initial_task_before_tool_history():
    pytest.importorskip("httpx")
    from benchmarks.bird.agent.data_agent import BIRDReActAgent
    from benchmarks.bird.agent.models import AgentMessage, AgentSession

    agent = BIRDReActAgent(llm_provider=None, mcp_client=None)
    agent.session = AgentSession(
        session_id="s1",
        task="Find the fastest lap time.",
        db_id="formula_1",
        db_path="/tmp/formula_1.sqlite",
        messages=[
            AgentMessage(role="system", content="system prompt"),
            AgentMessage(
                role="agent",
                content="Let me inspect the schema.",
                tool_call={"tool": "get_database_info", "arguments": {}},
            ),
            AgentMessage(role="environment", content="Tool result", tool_result={"tables": ["lapTimes"]}),
        ],
    )

    messages = agent._prepare_llm_messages()
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool"]
    assert agent.session.task in messages[1]["content"]


def test_ddr_keeps_initial_task_before_tool_history(monkeypatch):
    pytest.importorskip("rich")
    pytest.importorskip("mcp")
    monkeypatch.syspath_prepend(str(REPO_ROOT / "benchmarks" / "ddr_10k"))
    from benchmarks.ddr_10k.agent.data_agent import AutonomousDataAgent
    from benchmarks.ddr_10k.agent.models import AgentMessage, AutonomousSession

    class _DummyProvider:
        def get_provider_name(self) -> str:
            return "openai"

    agent = AutonomousDataAgent(llm_provider=_DummyProvider(), mcp_configs=[])
    agent.session = AutonomousSession(
        session_id="s1",
        start_time=datetime.now(),
        task="Analyze revenue growth.",
        messages=[
            AgentMessage(role="system", content="system prompt"),
            AgentMessage(
                role="agent",
                content="Querying revenue.",
                tool_call={"tool": "execute_query", "arguments": {"query": "SELECT 1"}},
            ),
            AgentMessage(role="environment", content="Tool result", tool_result={"rows": [[1]]}),
        ],
        available_tools={},
        mcp_servers={},
    )

    messages = agent._prepare_llm_messages()
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool"]
    assert agent.session.task in messages[1]["content"]


def assert_task_prefix(agent, messages):
    initial = {"role": "user", "content": f"Please start working on the task: {agent.session.task}"}
    assert messages[1] == initial
    assert messages.count(initial) == 1


@pytest.mark.parametrize("benchmark,provider", [
    ("bird", None),
    ("ddr", "openai"),
    ("ddr", "gemini3"),
    ("ddr", "vllm"),
])
def test_initial_task_survives_repeated_message_rebuilds(benchmark, provider, monkeypatch):
    if benchmark == "bird":
        from benchmarks.bird.agent.data_agent import BIRDReActAgent
        from benchmarks.bird.agent.models import AgentMessage, AgentSession

        agent = BIRDReActAgent(llm_provider=None, mcp_client=None)
        agent.session = AgentSession(
            session_id="s", task="Find the answer.", db_id="fixture", db_path="unused",
        )
    else:
        monkeypatch.syspath_prepend(str(REPO_ROOT / "benchmarks" / "ddr_10k"))
        from benchmarks.ddr_10k.agent.data_agent import AutonomousDataAgent
        from benchmarks.ddr_10k.agent.models import AgentMessage, AutonomousSession

        class Provider:
            def get_provider_name(self):
                return provider

        agent = AutonomousDataAgent(llm_provider=Provider(), mcp_configs=[])
        agent.session = AutonomousSession(
            session_id="s", start_time=datetime.now(), task="Find the answer.",
            messages=[], available_tools={}, mcp_servers={},
        )

    agent.session.messages = [AgentMessage(role="system", content="System prompt")]
    messages = agent._prepare_llm_messages()
    assert [message["role"] for message in messages] == ["system", "user"]
    assert_task_prefix(agent, messages)

    prior_ids = []
    for turn in (1, 2):
        agent.session.messages.extend([
            AgentMessage(
                role="agent", content=f"Calling tool {turn}", timestamp=f"turn-{turn}",
                tool_call={"tool": "query", "arguments": {"turn": turn}, "thought_signature": "token"},
            ),
            AgentMessage(role="environment", content=f"Result {turn}", tool_result={"turn": turn}),
            AgentMessage(role="environment", content=f"Hint {turn}"),
        ])
        messages = agent._prepare_llm_messages()
        assert_task_prefix(agent, messages)
        result_role = "user" if provider == "vllm" else "tool"
        assert [message["role"] for message in messages] == (
            ["system", "user"] + ["assistant", result_role, "user"] * turn
        )
        ids = []
        for earlier_turn in range(1, turn + 1):
            start = 2 + (earlier_turn - 1) * 3
            assistant, result, hint = messages[start:start + 3]
            assert assistant["content"] == f"Calling tool {earlier_turn}"
            assert hint == {"role": "user", "content": f"Hint {earlier_turn}"}
            if provider == "vllm":
                assert result == {"role": "user", "content": f"Result {earlier_turn}"}
                assert "tool_calls" not in assistant
                continue
            call, = assistant["tool_calls"]
            ids.append(call["id"])
            assert call["function"]["name"] == "query"
            assert json.loads(call["function"]["arguments"]) == {"turn": earlier_turn}
            assert result["tool_call_id"] == call["id"]
            assert json.loads(result["content"]) == {"turn": earlier_turn}
            if provider == "gemini3":
                assert call["thought_signature"] == "token"
                assert result["name"] == "query"
        assert ids[:len(prior_ids)] == prior_ids
        assert len(set(ids)) == len(ids)
        prior_ids = ids
