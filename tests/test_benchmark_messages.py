"""Regression tests for the initial task's position in benchmark conversations."""

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
