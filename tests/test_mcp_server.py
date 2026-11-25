"""Protocol checks for the dependency-free semantic MCP server."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from evoontology import SemanticStore


def test_stdio_mcp_initialize_and_list_tools(tmp_path):
    workspace = tmp_path / ".evoontology"
    SemanticStore.save_version(
        workspace,
        "ontology_v0",
        {family: [] for family in ("terms", "mappings", "relations", "constraints", "evidence")},
    )
    SemanticStore.set_active(workspace, "ontology_v0")

    requests = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                }
            ),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            "",
        ]
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evoontology.runtime.mcp_server",
            "--store",
            str(workspace),
        ],
        input=requests,
        text=True,
        capture_output=True,
        check=True,
    )

    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert responses[0]["result"]["protocolVersion"] == "2025-06-18"
    tool_names = [tool["name"] for tool in responses[1]["result"]["tools"]]
    assert tool_names[:2] == ["browse_semantics", "resolve_semantics"]
    assert "validate_semantics" in tool_names
    assert "accept_evolution" in tool_names


def test_resources_templates_list_is_empty(tmp_path):
    workspace = tmp_path / ".evoontology"
    requests = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "resources/templates/list"}) + "\n"
    completed = subprocess.run(
        [sys.executable, "-m", "evoontology.runtime.mcp_server", "--store", str(workspace)],
        input=requests,
        text=True,
        capture_output=True,
        check=True,
    )
    response = json.loads(completed.stdout.splitlines()[0])
    assert response["result"] == {"resourceTemplates": []}


def test_parse_error_reports_null_id_and_keeps_serving(tmp_path):
    """A malformed line must never poison the id of a later request."""
    workspace = tmp_path / ".evoontology"
    lines = [
        "{this is not json",
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}),
        "",
    ]
    completed = subprocess.run(
        [sys.executable, "-m", "evoontology.runtime.mcp_server", "--store", str(workspace)],
        input="\n".join(lines),
        text=True,
        capture_output=True,
        check=True,
    )
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert len(responses) == 2
    assert responses[0]["id"] is None
    assert responses[0]["error"]["code"] == -32700
    assert responses[1] == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_chinese_arguments_survive_non_utf8_host_locale(tmp_path):
    """Requests carrying UTF-8 CJK text must get a real reply even when the
    server inherits a legacy console code page (no PYTHONIOENCODING/PYTHONUTF8).

    Regression: Codex spawns plugin MCP servers with the ambient Windows
    environment, where stdin/stdout default to the console code page (GBK on
    zh-CN hosts). The garbled decode used to drop requests silently, leaving
    the client waiting until its 300s timeout.
    """
    workspace = tmp_path / "语义工作区" / ".evoontology"
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_versions", "arguments": {"workspace": str(workspace)}},
        },
        ensure_ascii=False,
    )
    # Run by file path (as the plugin launcher does): this must work from a
    # cwd that does not contain the evoontology package.
    server_script = Path(__file__).resolve().parents[1] / "evoontology" / "runtime" / "mcp_server.py"
    proc = subprocess.Popen(
        [sys.executable, str(server_script)],
        cwd=str(tmp_path),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = proc.communicate(
            (request + "\n").encode("utf-8"), timeout=30
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        raise AssertionError("server never answered a UTF-8 CJK request")
    responses = [json.loads(line) for line in stdout.decode("utf-8").splitlines() if line.strip()]
    assert len(responses) == 1, stderr.decode("utf-8", "replace")
    payload = responses[0]
    assert payload["id"] == 3
    assert "result" in payload, payload
    text = payload["result"]["content"][0]["text"]
    assert json.loads(text)["status"] == "ok"


@pytest.mark.parametrize("data_source", [
    "/absolute/path/sales.sqlite",
    {"type": "sqlite", "path": "/absolute/path/sales.sqlite"},
    {"type": "host", "connection": {"name": "warehouse", "extra": [1]}},
])
def test_stdio_workflow_input_schema_and_legacy_calls(tmp_path, data_source):
    workspace = tmp_path / ".evoontology"
    records = {family: [] for family in ("mappings", "relations", "constraints", "evidence")}
    records["terms"] = [{"id": "t1", "name": "Revenue", "definition": "Recorded revenue"}]
    SemanticStore.save_version(workspace, "ontology_v0", records)

    project = {
        "version": 1,
        "mode": "rolling_trajectory",
        "data_source": data_source,
        "workload_source": {"type": "project_workload", "extra": ["seed"]},
        "evaluation": {"type": "llm_judge", "extensions": {"temperature": 0}},
        "boundary": {"strategy": "rolling_trajectory", "extra": {"limit": 10}},
        "owner": "sample",
    }
    configure = "configure_ontology_project"
    prepare = "prepare_ontology_workload"
    annotate = "annotate_ontology_version"
    requests = [{"jsonrpc": "2.0", "id": 0, "method": "tools/list"}]

    def tool(name, **arguments):
        requests.append({
            "jsonrpc": "2.0", "id": len(requests), "method": "tools/call",
            "params": {"name": name, "arguments": {"workspace": str(workspace), **arguments}},
        })

    tool(configure, project=project)
    tool(configure, replace=True, project={
        **project, "schema_version": 1, "version": {"ignored": 99},
    })
    tool(configure, replace=True, project={**project, "schema_version": 99})
    tool(prepare,
         questions=["What were total sales?", {"id": "question2", "question": "Monthly sales?"}],
         history=["No source", {"question": "No source ref", "data_source": data_source},
                  {"question": "Last year sales?", "data_source": data_source,
                   "source_ref": "prior report", "recorded_at": None, "topics": ["sales"]}],
         generated=[{"question": "Sales by region?", "evidence_refs": ["sales.region"]}])
    tool(prepare, generated=[{"question": "Missing evidence"}])
    tool(prepare, generated=["Also missing evidence"])
    links = [{"question_id": "question2", "reason": "Optional object IDs"},
             {"question_id": "question2", "object_ids": ["t1"]}]
    tool(annotate, version="ontology_v0", summary="Verified links", limitations=[], links=links)
    tool(configure, replace=True, project={
        **project, "mode": "fixed_split", "boundary": {"construction_question_ids": ["in_split"]},
    })
    tool(prepare, questions=["Outside the fixed split"])
    tool(prepare, questions=[{"id": "in_split", "question": "Fixed split question"}],
         history=["Ignored in fixed mode"], generated=[{"question": "Also ignored in fixed mode"}])

    completed = subprocess.run(
        [sys.executable, "-m", "evoontology.runtime.mcp_server", "--store", str(workspace)],
        input="".join(json.dumps(req) + "\n" for req in requests),
        text=True, encoding="utf-8", capture_output=True, check=True, timeout=30,
    )
    replies = [json.loads(line) for line in completed.stdout.splitlines()]
    assert [reply["id"] for reply in replies] == list(range(len(requests)))
    schemas = {entry["name"]: entry["inputSchema"]["properties"] for entry in replies[0]["result"]["tools"]}
    project_schema = schemas[configure]["project"]
    assert set(project_schema["required"]) == set(project) - {"owner", "version"}
    properties = project_schema["properties"]
    assert properties["schema_version"]["const"] == 1
    assert "const" not in properties["version"]
    assert {"required": ["schema_version"]} in project_schema["anyOf"]
    assert {"required": ["version"], "properties": {"version": {"type": "integer", "const": 1}}} in project_schema["anyOf"]
    assert properties["mode"]["enum"] == ["fixed_split", "rolling_trajectory"]
    assert "construction_question_ids" in properties["boundary"]["properties"]
    assert properties["evaluation"].get("additionalProperties", True)
    assert properties["boundary"].get("additionalProperties", True)
    for name in ("questions", "history", "generated"):
        schema = schemas[prepare][name]
        assert schema["type"] == "array"
        variants = {item["type"]: item for item in schema["items"]["anyOf"]}
        assert set(variants) == {"string", "object"}
        assert variants["object"]["required"] == ["question"]
        assert set(variants["object"]["properties"]) == {
            "question", "id", "data_source", "source_ref", "evidence_refs", "topics", "recorded_at", "purpose",
        }
    link_schema = schemas[annotate]["links"]["items"]
    assert link_schema["required"] == ["question_id"]
    assert link_schema["properties"]["object_ids"]["default"] == []
    assert link_schema.get("additionalProperties", True)
    assert schemas["record_ontology_task_event"]["arguments"] == {"type": "object"}

    def payload(index, *, error=False):
        reply = replies[index]["result"]
        assert reply["isError"] is error, reply
        return json.loads(reply["content"][0]["text"])

    normalized = {key: value for key, value in project.items() if key != "version"}
    normalized["schema_version"] = 1
    assert payload(1)["project"] == normalized
    assert payload(2)["project"] == normalized
    assert "schema_version must be 1" in payload(3, error=True)["message"]
    workload = payload(4)
    assert workload["counts"] == {"user": 2, "history": 1, "generated": 1}
    assert len(workload["notes"]) == 2
    assert workload["questions"][0]["origin"] == "user"
    by_question = {q["question"]: q for q in workload["questions"]}
    assert by_question["What were total sales?"]["id"].startswith("q_")
    assert by_question["Last year sales?"]["provenance"][0]["source_ref"] == "prior report"
    assert by_question["Sales by region?"]["provenance"][0]["evidence_refs"] == ["sales.region"]
    for index in (5, 6):
        assert "require evidence_refs" in payload(index, error=True)["message"]
    assert payload(7)["links"] == links
    assert payload(8)["project"]["mode"] == "fixed_split"
    assert "construction boundary" in payload(9, error=True)["message"]
    assert [q["id"] for q in payload(10)["questions"]] == ["in_split"]
