"""Claude Code plugin launch configuration.

Claude Code does not support a ``cwd`` field for plugin MCP servers and starts
them in the user's project, so the server must be launched by absolute path
(as the Codex plugin already does) rather than with ``python -m`` from the
plugin root. Many systems (e.g. macOS) also ship no bare ``python``.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "claude-code"


def _server_config():
    config = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
    return config["mcpServers"]["evo-semantic"]


def test_mcp_server_does_not_depend_on_working_directory():
    server = _server_config()

    assert "cwd" not in server
    assert server["command"].startswith("${CLAUDE_PLUGIN_ROOT}/")


def test_mcp_launcher_ships_with_plugin():
    launcher = _server_config()["command"].replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN))

    assert Path(launcher).is_file()
    assert Path(launcher + ".cmd").is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX launcher")
def test_mcp_launcher_serves_from_a_foreign_working_directory(tmp_path):
    launcher = PLUGIN / "scripts" / "launch_evo_semantic_mcp"
    env = dict(os.environ, EVO_ONTOLOGY_PYTHON=sys.executable)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "1"}},
    }

    proc = subprocess.run(
        [str(launcher)],
        cwd=str(tmp_path),
        env=env,
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
    )

    response = json.loads(proc.stdout.splitlines()[0])
    assert response["result"]["serverInfo"]["name"] == "evo-semantic-mcp", proc.stderr


def test_reminder_hook_does_not_require_bare_python():
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    command = hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]

    assert "python3" in command
