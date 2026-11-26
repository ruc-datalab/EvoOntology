import json
import sqlite3

import pytest

from evoontology.ontology.store import SemanticStore
from evoontology.runtime import ops
from evoontology.runtime.tools import OPERATIONS, TOOLS
from evoontology.visualization.renderer import visualize
from evoontology.workflow import ProjectWorkflow, build_experience


@pytest.fixture
def workflow(tmp_path):
    db = tmp_path / "sales.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute("create table sales (region text, revenue integer)")
        connection.executemany("insert into sales values (?,?)", [("East", 12), ("West", 8)])
    flow = ProjectWorkflow(tmp_path / ".evoontology")
    flow.configure({"schema_version": 1, "mode": "rolling_trajectory",
                    "data_source": str(db), "workload_source": {"type": "project_workload"},
                    "evaluation": {"type": "llm_judge"}, "boundary": {"strategy": "rolling_trajectory"}})
    records = {"terms": [{"id": "t1", "name": "revenue", "definition": "Recorded revenue"}],
               "mappings": [{"id": "m1", "term_id": "t1", "table": "sales", "column": "revenue"}],
               "relations": [], "constraints": [], "evidence": []}
    SemanticStore.save_version(flow.root, "ontology_v0", records)
    SemanticStore.set_active(flow.root, "ontology_v0")
    return flow


def test_priority_dedup_scope_and_generated_provenance(workflow):
    source = workflow.status()["project"]["data_source"]
    result = workflow.prepare(
        questions=[{"question": " Revenue? ", "topics": ["sales"]}],
        history=[{"question": "Revenue", "source_ref": "current project", "data_source": source},
                 {"question": "Other project?", "source_ref": "elsewhere", "data_source": "other"}],
        generated=[{"question": "Revenue by region?", "topics": ["region"], "evidence_refs": ["sales.region"]}],
        topics=["sales", "region", "time"])
    assert result["counts"] == {"user": 1, "history": 0, "generated": 1}
    assert result["coverage_gaps"] == ["time"]
    assert len(result["questions"][0]["provenance"]) == 2
    assert result["notes"]


def test_generated_questions_need_evidence(workflow):
    with pytest.raises(ValueError, match="evidence_refs"):
        workflow.prepare(generated=[{"question": "What is churn?"}])
    assert not (workflow.root / "workload" / "questions.json").exists()


def test_explicit_scope_excludes_old_questions_without_deleting_history(workflow):
    workflow.prepare(questions=[{"question": "Old request"}],
                     generated=[{"question": "Old exploration", "evidence_refs": ["schema"]}])
    result = workflow.prepare(questions=[{"question": "Only this"}], supplied_only=True)
    assert [q["question"] for q in result["questions"]] == ["Only this"]
    assert workflow.status()["question_count"] == 3


def test_fixed_split_does_not_collect_unregistered_questions(workflow):
    project = workflow.status()["project"]
    project.update(mode="fixed_split", boundary={"construction_question_ids": ["q1"]})
    workflow.configure(project, replace=True)
    with pytest.raises(ValueError, match="boundary"):
        workflow.prepare(questions=[{"id": "secret", "question": "Heldout"}])
    result = workflow.prepare(questions=[{"id": "q1", "question": "Allowed"}],
                              generated=[{"question": "Ignored"}])
    assert [q["id"] for q in result["questions"]] == ["q1"]


def test_real_sqlite_execution_recording_and_idempotence(workflow):
    q = workflow.prepare(questions=[{"question": "Total revenue?"}])["questions"][0]
    task = workflow.start_task(q["id"])
    assert workflow.status()["running_tasks"][0]["task_id"] == task["task_id"]
    result = workflow.execute_query(task["task_id"], "select sum(revenue) from sales", "sql1")
    assert result["result"]["rows"] == [[20]]
    assert workflow.execute_query(task["task_id"], "select sum(revenue) from sales", "sql1")["status"] == "already_recorded"
    finished = workflow.finish_task(task["task_id"], "20")
    assert len(finished["events"]) == 1
    assert finished["ontology_version"] == "ontology_v0"
    assert workflow.finish_task(task["task_id"], "20") == finished
    assert not workflow.status()["running_tasks"]
    assert (workflow.root / "trajectories" / (task["task_id"] + ".json")).is_file()


def test_sqlite_query_denies_mutations_and_attachments(workflow):
    q = workflow.prepare(questions=[{"question": "Revenue?"}])["questions"][0]
    task = workflow.start_task(q["id"])
    for i, sql in enumerate(["delete from sales", "attach ':memory:' as extra", "pragma writable_schema=ON"]):
        assert workflow.execute_query(task["task_id"], sql, str(i))["status"] == "error"
    result = workflow.execute_query(task["task_id"], "select count(*) from sales", "count")
    assert result["result"]["rows"] == [[2]]


def test_cannot_finish_an_unexecuted_task(workflow):
    q = workflow.prepare(questions=[{"question": "Revenue?"}])["questions"][0]
    task = workflow.start_task(q["id"])
    with pytest.raises(ValueError, match="observed native"):
        workflow.finish_task(task["task_id"], "invented")
    workflow.finish_task(task["task_id"], "No access", status="interrupted")


def test_switching_data_source_keeps_other_sources_questions(workflow, tmp_path):
    project = workflow.status()["project"]
    first = workflow.prepare(questions=["Revenue?", "Top regions?"])["questions"]
    other_db = tmp_path / "other.sqlite"
    sqlite3.connect(other_db).close()
    workflow.configure({**project, "data_source": str(other_db)}, replace=True)
    workflow.prepare(questions=["Revenue?", "Churn rate?"])
    assert workflow.status()["question_count"] == 4

    workflow.configure(project, replace=True)
    result = workflow.prepare()
    assert {q["id"] for q in result["questions"]} == {q["id"] for q in first}
    task = workflow.start_task(first[0]["id"])
    assert task["data_source"] == project["data_source"]


def test_task_ids_cannot_escape_workspace(workflow):
    with pytest.raises(ValueError, match="ID must"):
        workflow.finish_task("../../escape", "")


def test_synthetic_execution_never_becomes_real_history(workflow):
    q = workflow.prepare(generated=[{"question": "Revenue?", "evidence_refs": ["sales.revenue"]}])["questions"][0]
    task = workflow.start_task(q["id"])
    workflow.execute_query(task["task_id"], "select sum(revenue) from sales", "one")
    workflow.finish_task(task["task_id"], "20")
    result = workflow.prepare()
    assert result["counts"] == {"user": 0, "history": 0, "generated": 1}


def test_question_links_validated_and_snapshot_preserved(workflow):
    q = workflow.prepare(questions=[{"question": "Revenue?"}])["questions"][0]
    with pytest.raises(ValueError, match="nonexistent"):
        workflow.annotate("ontology_v0", "", [], [{"question_id": q["id"], "object_ids": ["missing"]}])
    workflow.annotate("ontology_v0", "Ready", ["No time dimension"], [{"question_id": q["id"], "object_ids": ["t1", "m1"]}])
    workflow.prepare(questions=[{"question": "New unrelated question"}])
    report = build_experience(workflow.root, "ontology_v0")
    assert len(report["questions"]) == 1
    assert report["questions"][0]["tasks"] == []
    html = visualize(workflow.root, open_browser=False).read_text(encoding="utf-8")
    assert "No time dimension" in html
    assert "New unrelated question" not in html


def test_publication_survives_presentation_failure(workflow, monkeypatch):
    (workflow.root / "active.json").unlink()
    def fail(**kwargs):
        raise OSError("browser unavailable")
    monkeypatch.setattr(ops, "_visualize", fail)
    result = ops.execute("publish_ontology_build", {"workspace": str(workflow.root)})
    assert result["status"] == "published"
    assert result["presentation"]["status"] == "presentation_failed"
    assert SemanticStore.active_version(workflow.root) == "ontology_v0"
    with pytest.raises(ValueError, match="already exists"):
        ops.execute("publish_ontology_build", {"workspace": str(workflow.root)})


def test_running_evaluation_and_reserved_cases_never_embedded(workflow):
    args = {"workspace": str(workflow.root)}
    ops.execute("start_evolution_run", {**args, "parent_version": "ontology_v0", "profile": "quick"})
    ops.execute("record_evolution_evaluation", {**args, "subject": "ontology_v0", "result": {
        "metrics": {"score": 0.5}, "cases": [{"question": "reserved secret"}], "provenance": "synthetic"}})
    assert build_experience(workflow.root, "ontology_v0")["runs"][0]["evaluations"] == []
    ops.execute("mark_evolution_incomplete", {**args, "reason": "user_interrupted"})
    report = build_experience(workflow.root, "ontology_v0")
    assert report["runs"][0]["budget"]["max_rounds"] == 2
    assert report["runs"][0]["evaluations"][0]["provenance"] == "synthetic"
    assert "reserved secret" not in json.dumps(report)


def test_all_advertised_operations_have_handlers():
    assert all(tool["name"] in ops._HANDLERS for tool in OPERATIONS)


def test_semantic_tool_schemas_accept_workspace_and_version():
    for tool in TOOLS:
        properties = tool["inputSchema"]["properties"]
        assert "workspace" in properties
        assert "version" in properties


def test_mcp_semantic_tools_can_target_an_explicit_workspace(workflow, tmp_path):
    from evoontology.runtime.mcp_server import SemanticMCPServer

    server = SemanticMCPServer(str(tmp_path / "uninitialized"))
    response = server.dispatch(
        "tools/call",
        {
            "name": "browse_semantics",
            "arguments": {
                "query": "revenue",
                "workspace": str(workflow.root.parent),
                "version": "ontology_v0",
            },
        },
    )

    payload = json.loads(response["content"][0]["text"])
    assert not response["isError"]
    assert payload["version"] == "ontology_v0"
    assert payload["workspace"] == str(workflow.root.resolve())
    assert payload["items"][0]["id"] == "t1"


def test_mcp_refreshes_active_version_without_server_restart(workflow):
    from evoontology.runtime.mcp_server import SemanticMCPServer
    server = SemanticMCPServer(str(workflow.root))
    records = {"terms": [{"id": "new", "name": "margin", "definition": "Margin"}],
               "mappings": [], "relations": [], "constraints": [], "evidence": []}
    SemanticStore.save_version(workflow.root, "ontology_v1", records)
    SemanticStore.set_active(workflow.root, "ontology_v1")
    response = server.dispatch("tools/call", {"name": "browse_semantics", "arguments": {"query": "margin"}})
    assert not response["isError"]
    assert "margin" in response["content"][0]["text"]
    assert server.layer.version == "ontology_v1"


def test_pinned_task_uses_candidate_without_activating_it(workflow):
    q = workflow.prepare(questions=[{"question": "Margin?"}])["questions"][0]
    records = {"terms": [{"id": "new", "name": "margin", "definition": "Candidate only"}],
               "mappings": [], "relations": [], "constraints": [], "evidence": []}
    SemanticStore.save_version(workflow.root, "candidate", records)
    task = workflow.start_task(q["id"], "candidate")
    result = workflow.resolve_task(task["task_id"], ["margin"])
    assert "Candidate only" in json.dumps(result)
    assert SemanticStore.active_version(workflow.root) == "ontology_v0"


def test_initial_publish_validates_before_activation(workflow):
    (workflow.root / "active.json").unlink()
    path = workflow.root / "versions" / "ontology_v0" / "terms.json"
    path.write_text("not JSON", encoding="utf-8")
    with pytest.raises(ValueError, match="validation failed"):
        ops.execute("publish_ontology_build", {"workspace": str(workflow.root), "open_browser": False})
    assert not (workflow.root / "active.json").exists()


def test_browser_failure_reports_html_without_claiming_opened(workflow, monkeypatch):
    monkeypatch.setattr(ops.webbrowser, "open", lambda url: False)
    result = ops.execute("visualize_ontology", {"workspace": str(workflow.root)})
    assert result["status"] == "ok"
    assert not result["opened_in_browser"]
    assert result["warning"]
    assert result["html_path"]
