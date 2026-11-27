"""Project-scoped workload preparation and observable task execution records.

The host agent supplies analytical questions and executes native data tools. This
module owns provenance, deduplication, resumable tasks and evidence presentation;
it never invents questions, answers, executions or evaluation scores.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from .ontology.store import SemanticStore
from .trajectory.trajectory import TrajectoryStore, now_iso, truncate_result
from .workspace import load_project, resolve_workspace, save_project


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("ID must contain only letters, digits, dots, underscores or hyphens")
    return value


def source_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def question_key(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split()).rstrip("?？。.!！")


class ProjectWorkflow:
    def __init__(self, workspace):
        self.root = resolve_workspace(workspace)

    def configure(self, project: dict, replace: bool = False) -> dict:
        path = self.root / "project.json"
        if path.exists() and not replace:
            return {"status": "reused", "project": load_project(self.root)}
        save_project(project, self.root)
        return {"status": "configured", "project": load_project(self.root)}

    def prepare(self, questions=None, history=None, generated=None, *,
                include_history=True, supplied_only=False, limit=12, topics=None) -> dict:
        project = load_project(self.root)
        source = project["data_source"]
        fixed = project["mode"] == "fixed_split"
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        path = self.root / "workload" / "questions.json"
        saved = read_json(path, {"questions": []})["questions"]
        existing = [q for q in saved if source_key(q.get("data_source")) == source_key(source)]
        # Questions recorded for other data sources stay in the saved workload.
        other_sources = [q for q in saved if source_key(q.get("data_source")) != source_key(source)]
        stored = existing
        if supplied_only:
            existing = []
            include_history = False
            generated = []
        if not include_history:
            existing = [q for q in existing if q["origin"] != "history"]
        candidates = [(q, "user") for q in questions or []]
        skipped = []
        if fixed:
            # Imported fixed-split IDs must be explicitly enumerated at setup.
            # Do not discover history or synthesize tasks across a benchmark boundary.
            # Stored question IDs are strings; registered IDs may be integers.
            allowed = {str(i) for i in project["boundary"].get("construction_question_ids", [])}
            if candidates and not allowed:
                raise ValueError("Register boundary.construction_question_ids before importing fixed-split questions")
            for q, _ in candidates:
                if not isinstance(q, dict) or q.get("id") is None or str(q["id"]) not in allowed:
                    raise ValueError("Question is outside the registered construction boundary")
            existing = [q for q in existing if q["id"] in allowed]
            skipped.append("fixed_split: automatic history and generated questions disabled")
        else:
            if include_history:
                candidates += [(q, "history") for q in history or []]
                for p in sorted((self.root / "trajectories").glob("*.json")):
                    t = read_json(p)
                    if t.get("split") in {"validation", "heldout", "test"}:
                        continue
                    if source_key(t.get("data_source")) != source_key(source):
                        continue
                    candidates.append(({"question": t.get("question"), "data_source": source,
                                        "source_ref": str(p), "recorded_at": t.get("recorded_at"),
                                        "evidence_refs": [str(p)],
                                        "topics": t.get("topics", [])},
                                       "generated" if t.get("origin") == "generated" else "history"))
            candidates += [(q, "generated") for q in generated or []]
        merged = {question_key(q["question"]): q for q in existing}
        stored_by_key = {question_key(q["question"]): q for q in stored}
        priority = {"user": 0, "history": 1, "generated": 2}
        for raw, origin in candidates:
            if isinstance(raw, str):
                raw = {"question": raw}
            if not isinstance(raw, dict) or not str(raw.get("question") or "").strip():
                raise ValueError("Each question needs non-empty question text")
            if raw.get("purpose", "construction") != "construction":
                raise ValueError("Reserved validation/heldout questions cannot enter the construction workload")
            if origin == "history" and (not raw.get("source_ref") or
                    source_key(raw.get("data_source")) != source_key(source)):
                skipped.append("history question skipped: missing provenance or different data source")
                continue
            if origin == "generated" and not raw.get("evidence_refs"):
                raise ValueError("Generated questions require evidence_refs from inspected data metadata")
            key = question_key(raw["question"])
            if not key:
                raise ValueError("Question text contains no usable content")
            prior = merged.get(key)
            kept = stored_by_key.get(key)
            if prior is None and kept and (raw.get("id") in (None, "") or str(raw["id"]) == kept["id"]):
                # A stored question excluded from this selection keeps its ID
                # and provenance when it is supplied again. An explicit new ID
                # (such as a registered fixed-split ID) still replaces it.
                prior = merged[key] = {**kept, "provenance": list(kept["provenance"])}
            provenance = {"origin": origin, "source_ref": raw.get("source_ref", "current request"),
                          "evidence_refs": raw.get("evidence_refs", [])}
            if prior:
                if provenance not in prior["provenance"]:
                    prior["provenance"].append(provenance)
                if priority[origin] < priority[prior["origin"]]:
                    prior["origin"] = origin
                continue
            raw_id = raw.get("id")
            # An explicit ID of 0 (BIRD question IDs start at 0) is still an ID.
            qid = (str(raw_id) if raw_id not in (None, "")
                   else "q_" + hashlib.sha256(key.encode()).hexdigest()[:16])
            safe_id(qid)
            if any(q["id"] == qid and question_key(q["question"]) != key for q in stored):
                raise ValueError("Question ID already belongs to different text")
            if any(q["id"] == qid for q in merged.values()):
                raise ValueError("Question ID already belongs to different text")
            merged[key] = {"id": qid, "question": raw["question"].strip(), "origin": origin,
                           "data_source": source, "provenance": [provenance],
                           "topics": list(raw.get("topics", [])),
                           "recorded_at": raw.get("recorded_at") or now_iso(),
                           "purpose": "construction"}
        ordered = sorted(merged.values(), key=lambda q: q["recorded_at"], reverse=True)
        ordered.sort(key=lambda q: priority[q["origin"]])
        # Retain all user questions; fill remaining slots with diverse topics first.
        selected = [q for q in ordered if q["origin"] == "user"]
        covered = {t for q in selected for t in q["topics"]}
        rest = [q for q in ordered if q["origin"] != "user"]
        for origin in ("history", "generated"):
            pool = [q for q in rest if q["origin"] == origin]
            while pool and len(selected) < limit:
                best = max(pool, key=lambda q: len(set(q["topics"]) - covered))
                pool.remove(best)
                selected.append(best)
                covered.update(best["topics"])
        # An explicit scope restriction affects this selection, not saved history.
        catalog = {question_key(q["question"]): q for q in stored}
        catalog.update({question_key(q["question"]): q for q in ordered})
        write_json(path, {"questions": other_sources + list(catalog.values()), "updated_at": now_iso()})
        return {"status": "ready" if selected else "needs_questions", "questions": selected,
                "counts": {o: sum(q["origin"] == o for q in selected) for o in priority},
                "coverage_gaps": sorted(set(topics or []) - covered), "notes": skipped,
                "next_action": "Explore data metadata and supply grounded generated questions for uncovered needs"
                               if not selected or set(topics or []) - covered else "Execute or build from this workload"}

    def start_task(self, question_id: str, version: str = "active", batch_id: str = "") -> dict:
        project = load_project(self.root)
        questions = read_json(self.root / "workload" / "questions.json", {"questions": []})["questions"]
        matches = [q for q in questions if q["id"] == question_id]
        question = next((q for q in matches if source_key(q["data_source"]) == source_key(project["data_source"])),
                        matches[0] if matches else None)
        if question is None:
            raise ValueError("Unknown question_id; prepare the workload first")
        if source_key(question["data_source"]) != source_key(project["data_source"]):
            raise ValueError("Question belongs to a different data source")
        if version == "active":
            version = SemanticStore.active_version(self.root)
        safe_id(version)
        SemanticStore.load_version(self.root, version)
        task = {"task_id": "task_" + uuid.uuid4().hex, "question_id": question_id,
                "question": question["question"], "data_source": project["data_source"],
                "origin": question["origin"], "topics": question["topics"],
                "ontology_version": version, "batch_id": batch_id,
                "split": "construction", "task_status": "running", "started_at": now_iso(),
                "events": [], "semantic_calls": [], "native_tool_calls": [], "errors": []}
        write_json(self.root / "tasks" / (task["task_id"] + ".json"), task)
        return task

    def record_event(self, task_id: str, tool: str, arguments: dict, result: Any,
                     event_id: str, error: bool = False) -> dict:
        path = self.root / "tasks" / (safe_id(task_id) + ".json")
        task = read_json(path)
        if task is None:
            raise ValueError("Unknown task")
        if task["task_status"] != "running":
            raise ValueError("Task is already finished")
        if not tool.strip() or not event_id.strip():
            raise ValueError("tool and event_id are required")
        event = {"event_id": event_id, "tool": tool, "input": arguments,
                 **truncate_result(result), "error": error}
        prior = next((e for e in task["events"] if e["event_id"] == event_id), None)
        if prior:
            if prior != event:
                raise ValueError("event_id already has a different result")
            return {"status": "already_recorded", "task_id": task_id}
        task["events"].append(event)
        family = "semantic_calls" if tool in {"browse_semantics", "resolve_semantics"} else "native_tool_calls"
        task[family].append(event)
        if error:
            task["errors"].append({"event_id": event_id, "tool": tool})
        write_json(path, task)
        return {"status": "recorded", "task_id": task_id, "events": len(task["events"])}

    def finish_task(self, task_id: str, final_answer: Any, status="completed") -> dict:
        if status not in {"completed", "failed", "interrupted"}:
            raise ValueError("Unsupported task status")
        path = self.root / "tasks" / (safe_id(task_id) + ".json")
        task = read_json(path)
        if task is None:
            raise ValueError("Unknown task")
        if task["task_status"] != "running":
            if task["task_status"] != status or task["final_answer"] != final_answer:
                raise ValueError("Task already finished with different output")
            return task
        if status == "completed" and not any(not e.get("error") for e in task["native_tool_calls"]):
            raise ValueError("A completed data task needs at least one observed native tool result")
        task.update(task_status=status, final_answer=final_answer, recorded_at=now_iso())
        TrajectoryStore(self.root).append(task)
        write_json(path, task)
        return task

    def status(self) -> dict:
        project = load_project(self.root)
        tasks = [read_json(p) for p in sorted((self.root / "tasks").glob("*.json"))]
        return {"project": project, "running_tasks": [t for t in tasks if t["task_status"] == "running"],
                "completed_tasks": sum(t["task_status"] == "completed" for t in tasks),
                "question_count": len(read_json(self.root / "workload" / "questions.json", {"questions": []})["questions"])}

    def execute_query(self, task_id: str, query: str, event_id: str) -> dict:
        """Built-in bounded SQLite replay; other environments use host tools."""
        task = read_json(self.root / "tasks" / (safe_id(task_id) + ".json"))
        if not task or task["task_status"] != "running":
            raise ValueError("A running task is required")
        source = task["data_source"]
        database = source.get("path", "") if isinstance(source, dict) else source
        path = Path(str(database)).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise ValueError("Built-in replay needs an absolute SQLite data_source path; otherwise use host native tools")
        existing = next((e for e in task["events"] if e["event_id"] == event_id), None)
        if existing:
            if existing["tool"] != "sqlite_query" or existing["input"] != {"query": query}:
                raise ValueError("event_id already used by another query")
            return {"status": "already_recorded", "event": existing}
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        if hasattr(connection, "setlimit"):
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
        # Read-only URI prevents writes; authorizer also prohibits attachments,
        # pragmas and extension loading. Progress limit bounds expensive scans.
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                   sqlite3.SQLITE_RECURSIVE}
        def authorize(action, arg1, arg2, database_name, trigger):
            if action == sqlite3.SQLITE_FUNCTION and str(arg2).lower() == "load_extension":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
        connection.set_authorizer(authorize)
        calls = 0
        def progress():
            nonlocal calls
            calls += 1
            return int(calls > 1000)
        connection.set_progress_handler(progress, 1000)
        try:
            cursor = connection.execute(query)
            rows = cursor.fetchmany(101)
            # Normalize binary values without inventing textual contents.
            result = {"columns": [c[0] for c in cursor.description or []],
                      "rows": [[v.hex() if isinstance(v, bytes) else v for v in row] for row in rows[:100]],
                      "truncated": len(rows) > 100}
            error = False
        except sqlite3.Error as exc:
            result = {"error": str(exc)}
            error = True
        finally:
            connection.close()
        self.record_event(task_id, "sqlite_query", {"query": query}, result, event_id, error)
        return {"status": "error" if error else "ok", "result": result}

    def resolve_task(self, task_id: str, mentions: list, context: str = "") -> dict:
        from .runtime.runtime import SemanticLayer
        task = read_json(self.root / "tasks" / (safe_id(task_id) + ".json"))
        if not task or task["task_status"] != "running":
            raise ValueError("A running task is required")
        layer = SemanticLayer.load(str(self.root), version=task["ontology_version"])
        arguments = {"mentions": mentions, "context": context}
        result = layer.execute("resolve_semantics", arguments)
        self.record_event(task_id, "resolve_semantics", arguments, result, "semantic_" + uuid.uuid4().hex)
        return result

    def annotate(self, version: str, summary: str, limitations: list, links: list) -> dict:
        safe_id(version)
        store = SemanticStore.load_version(self.root, version)
        known = set()
        for family in (store.terms, store.mappings, store.relations, store.constraints, store.evidence):
            known.update(family)
        questions = read_json(self.root / "workload" / "questions.json", {"questions": []})["questions"]
        if links:
            # Other data sources' questions stay saved and may share an ID.
            source = source_key(load_project(self.root)["data_source"])
            questions = [q for q in questions if source_key(q.get("data_source")) == source]
        question_ids = {q["id"] for q in questions}
        for link in links:
            if link.get("question_id") not in question_ids:
                raise ValueError("Unknown linked question")
            if not set(link.get("object_ids", [])) <= known:
                raise ValueError("Question link references nonexistent semantic objects")
        report = {"summary": summary, "limitations": limitations, "links": links,
                  "questions": [q for q in questions if q["id"] in {link["question_id"] for link in links}],
                  "recorded_at": now_iso(), "version": version}
        write_json(self.root / "reports" / (version + ".json"), report)
        return report


def build_experience(root: Path, version: str) -> dict:
    """Read-only report; no inferred scores or validation contents from open runs."""
    report = read_json(root / "reports" / (safe_id(version) + ".json"), {})
    questions = report.get("questions", [])
    links = {link["question_id"]: link for link in report.get("links", [])}
    trajectories = [read_json(p) for p in sorted((root / "trajectories").glob("*.json"))]
    trajectories.sort(key=lambda t: t.get("recorded_at", ""))
    task_versions = {version}
    for p in (root / "evolution").glob("run_*/run.json"):
        run = read_json(p)
        if run.get("accepted_version") == version:
            task_versions.add(run.get("current_candidate"))
    public_tasks = [t for t in trajectories if t.get("ontology_version") in task_versions
                    and t.get("split") not in {"validation", "heldout", "test"}]
    tasks_by_question = {}
    for task in public_tasks:
        key = source_key(task.get("data_source")), task.get("question_id")
        tasks_by_question.setdefault(key, []).append(
            {k: task.get(k) for k in ("task_id", "task_status", "final_answer", "errors")}
        )
    report["questions"] = []
    for question in questions:
        qid = question["id"]
        tasks = tasks_by_question.get((source_key(question.get("data_source")), qid), [])
        if qid in links or tasks:
            report["questions"].append({
                **question,
                "object_ids": links.get(qid, {}).get("object_ids", []),
                "tasks": tasks,
            })
    report["runs"] = []
    for path in sorted((root / "evolution").glob("run_*/run.json"),
                       key=lambda p: int(p.parent.name.split("_")[-1])):
        run = read_json(path)
        if version not in {run.get("parent_version"), run.get("current_candidate"), run.get("accepted_version")}:
            continue
        entry = {k: run.get(k) for k in ("run_id", "status", "parent_version", "current_candidate", "accepted_version", "end_reason", "round", "budget")}
        # Validation remains isolated from candidate design. Only terminal-run
        # aggregate metrics are embedded; task examples come from public replays.
        entry["evaluations"] = []
        if run.get("status") in {"accepted", "incomplete"}:
            for p in sorted((path.parent / "evaluations").glob("*.json")):
                evaluation = read_json(p)
                entry["evaluations"].append({k: evaluation.get(k) for k in ("subject", "role", "round", "metrics", "provenance")})
        report["runs"].append(entry)
    return report
