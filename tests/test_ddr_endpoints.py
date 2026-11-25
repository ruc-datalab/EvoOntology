"""Exercise DDR endpoint configuration at the HTTP request boundaries."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from benchmarks.ddr_10k import config, evaluate
from benchmarks.ddr_10k.agent.providers import vllm_provider

MESSAGES = [{"role": "user", "content": "hello"}]
CHAT_RESPONSE = {"choices": [{"message": {"content": "FINISH"}}]}
ENDPOINTS = [
    ("https://host", 0, "https://host/v1"),
    ("https://host/", 0, "https://host/v1"),
    ("https://host/v1", 0, "https://host/v1"),
    ("https://host/v1/", 0, "https://host/v1"),
    ("https://host/proxy", 0, "https://host/proxy/v1"),
    ("https://host/proxy/v1/", 0, "https://host/proxy/v1"),
    ("https://host/proxy/v1/", 8001, "https://host:8001/proxy/v1"),
    ("https://host:8000/proxy/v1/", 8001, "https://host:8001/proxy/v1"),
    ("https://[::1]:8000/proxy/v1/", 8001, "https://[::1]:8001/proxy/v1"),
]


def async_context(value):
    context = MagicMock()
    context.__aenter__.return_value = value
    return context


@pytest.mark.parametrize("base_url,port,api", ENDPOINTS)
def test_provider_requests_and_request_log(monkeypatch, base_url, port, api):
    session = MagicMock()
    session.get.return_value = async_context(
        Mock(status=200, json=AsyncMock(return_value={"data": [{"id": "model", "probe": True}]}))
    )
    session.post.return_value = async_context(
        Mock(status=200, json=AsyncMock(return_value=CHAT_RESPONSE))
    )
    monkeypatch.setattr(vllm_provider.aiohttp, "ClientSession", lambda **kwargs: async_context(session))
    log = Mock()
    monkeypatch.setattr(vllm_provider, "log_llm_call", log)

    async def request():
        provider = vllm_provider.VLLMProvider(base_url, model="model", port=port)
        assert (await provider._get_model_info())["probe"]
        assert await provider._check_function_calling_support()
        assert await provider.generate_response(MESSAGES) == "FINISH"
        assert (await provider.generate_response_with_tools(MESSAGES, []))["finish"]

        provider._supports_function_calling = False
        assert (await provider.generate_response_with_tools(MESSAGES, []))["finish"]
        gemma = vllm_provider.VLLMProvider(base_url, model="gemma", port=port)
        assert (await gemma.generate_response_with_tools(MESSAGES, []))["finish"]

    asyncio.run(request())
    session.get.assert_called_once_with(api + "/models")
    assert [c.args[0] for c in session.post.call_args_list] == [api + "/chat/completions"] * 5
    assert session.post.call_args_list[0].kwargs["json"]["tools"][0]["function"]["name"] == "test_function"
    assert [c.kwargs["input_data"]["url"] for c in log.call_args_list] == [api + "/chat/completions"] * 4


@pytest.fixture
def evaluator_cli(monkeypatch, tmp_path):
    for env in ("VLLM_BASE_URL", "VLLM_PORT", "DDR_AGENT_BASE_URL", "DDR_EVALUATOR_BASE_URL", "DDR_LOG_LEVEL"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(config, "_config", None)
    monkeypatch.setitem(sys.modules, "config", config)
    monkeypatch.setitem(sys.modules, "evaluate", evaluate)
    path = Path(config.__file__).with_name("run_evaluation.py")
    spec = importlib.util.spec_from_file_location("ddr_evaluation_cli", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    post = Mock(return_value=Mock(json=Mock(return_value=CHAT_RESPONSE)))
    session = Mock(post=post)
    monkeypatch.setattr(evaluate.base_evaluator.requests, "Session", lambda: session)

    def run(base_url, port, independent="", cli_url=None):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            f"provider:\n  vllm_base_url: '{base_url}'\n  vllm_port: {port}\n"
            f"evaluation:\n  provider: vllm\n  base_url: '{independent}'\n"
            "scenarios:\n  10k:\n    qa_file: qa.json\n    log_dir: logs\n",
            encoding="utf-8",
        )
        argv = [str(path), "--scenario", "10k", "--config", str(cfg), "--api-key", "test"]
        if cli_url:
            argv.extend(["--base-url", cli_url])
        monkeypatch.setattr(sys, "argv", argv)

        def check_request(evaluator, **kwargs):
            assert evaluator.call_vllm_api(MESSAGES) == "FINISH"

        monkeypatch.setattr(evaluate.UnifiedEvaluator, "run_evaluation", check_request)
        runner.main()
        assert post.call_count == 1
        return post.call_args.args[0]

    return run


@pytest.mark.parametrize("base_url,port,api", ENDPOINTS)
def test_evaluator_posts_to_the_same_endpoint(evaluator_cli, base_url, port, api):
    assert evaluator_cli(base_url, port) == api + "/chat/completions"


@pytest.mark.parametrize("env_url,cli_url,expected", [
    (None, None, "https://config-judge:8001/judge/v1/chat/completions"),
    ("https://env-judge/path", None, "https://env-judge:8001/path/v1/chat/completions"),
    ("https://env-judge/path", "https://cli-judge/v1", "https://cli-judge:8001/v1/chat/completions"),
])
def test_evaluator_base_url_precedence(evaluator_cli, monkeypatch, env_url, cli_url, expected):
    monkeypatch.setenv("DDR_AGENT_BASE_URL", "https://agent-env/agent")
    monkeypatch.setenv("VLLM_PORT", "8001")
    if env_url:
        monkeypatch.setenv("DDR_EVALUATOR_BASE_URL", env_url)
    assert evaluator_cli("https://agent-config", 8000, "https://config-judge/judge/v1/", cli_url) == expected


def test_url_components_with_proxy_and_port():
    base = "https://name:token@[::1]:8000/proxy/v1/?route=blue#tag"
    overridden = config.override_url_port(base, 8001)
    assert config.openai_compatible_endpoint(overridden, "models") == (
        "https://name:token@[::1]:8001/proxy/v1/models?route=blue#tag"
    )


def test_evaluator_keeps_schemeless_local_port_override(evaluator_cli):
    assert evaluator_cli("localhost/proxy/v1", 8001) == "http://localhost:8001/proxy/v1/chat/completions"
