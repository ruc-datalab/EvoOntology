#!/usr/bin/env python3
"""Implementation for the bird.run_evaluation module."""

import asyncio
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # project root -> evoontology package

from agent.models import EvalResult
from config import DB_DIR, RESULTS_DIR, SEMANTIC_LAYER_DIR, ExperimentConfig, get_dataset_config

VES_EVAL_RUNS = 4


def _kill_process_tree(pid: int, timeout: int = 10):
    """Kill a process and all its child processes (cross-platform).

    On Windows, uses taskkill /T. On Unix, sends SIGKILL to the process group.
    """
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=timeout,
            )
        else:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def _time_sql(db_path: str, sql: str, runs: int = VES_EVAL_RUNS) -> float:
    """Implement time sql."""
    if not sql:
        return 0.0
    times = []
    for _ in range(runs):
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            t0 = time.perf_counter()
            conn.execute(sql).fetchall()
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
        except Exception:
            conn.close()
            return 0.0
        conn.close()
    warm = times[1:]
    return sum(warm) / len(warm)


def evaluate_sql(pred_sql: str, gold_sql: str, db_path: str) -> tuple:
    """Evaluate sql."""
    if not pred_sql:
        return (False, 0.0)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        try:
            cursor = conn.execute(pred_sql)
            pred_rows = [tuple(row) for row in cursor.fetchall()]
        except Exception:
            conn.close()
            return (False, 0.0)

        try:
            cursor = conn.execute(gold_sql)
            gold_rows = [tuple(row) for row in cursor.fetchall()]
        except Exception:
            conn.close()
            return (False, 0.0)

        conn.close()

        # Compare as multisets: sorting raises TypeError on rows that mix NULL
        # with values (or ints with text), which scored correct answers as wrong.
        ex = Counter(pred_rows) == Counter(gold_rows)

        if not ex:
            return (False, 0.0)


        pred_time = _time_sql(db_path, pred_sql)
        gold_time = _time_sql(db_path, gold_sql)

        if pred_time <= 0:
            return (True, 0.0)

        ves = gold_time / pred_time
        return (True, ves)

    except Exception:
        return (False, 0.0)


async def run_single_question(question_data: dict, config: ExperimentConfig,
                              db_id: str, llm_kwargs: dict,
                              mcp_client_class, agent_class, runtime_class,
                              verbose: bool = False,
                              save_traces: bool = False,
                              record_trajectories: bool = False,
                              split: str = "",
                              gold_lookup: dict | None = None) -> tuple:
    """Run single question."""
    question_id = question_data["question_id"]
    question = question_data["question"]
    difficulty = question_data.get("difficulty", "unknown")
    gold_sql = question_data.get("SQL", "")

    if not gold_sql and gold_lookup:
        gold_sql = gold_lookup.get(str(question_id), "")
    db_path = str(DB_DIR / db_id / f"{db_id}.sqlite")

    if not Path(db_path).exists():
        return (EvalResult(
            question_id=question_id, db_id=db_id, question=question,
            difficulty=difficulty, condition=config.condition,
            pred_sql="", gold_sql=gold_sql, ex=False, turns=0,
            error=f"Database does not exist: {db_path}",
        ), None)


    mcp_configs = []
    semantic_store_path = ""
    if config.semantic.enabled:
        semantic_store_path = config.semantic.store_path or str(
            SEMANTIC_LAYER_DIR / db_id
        )
    semantic_version = config.semantic.version if config.semantic.enabled else ""
    for s in config.mcp_servers:
        server_args = list(s.get("args", []))
        serves_semantics = str(s.get("module", "")).endswith(("semantic_mcp", "sqlite_mcp"))
        for i, val in enumerate(server_args):
            if val == "" or val is None:
                if i > 0 and server_args[i - 1] == "--db-path":
                    server_args[i] = db_path
                elif i > 0 and server_args[i - 1] == "--store":
                    server_args[i] = semantic_store_path
                elif i > 0 and server_args[i - 1] == "--db-id":
                    server_args[i] = db_id
                elif i > 0 and server_args[i - 1] == "--semantic-store":
                    server_args[i] = semantic_store_path if semantic_store_path else ""
                elif i > 0 and server_args[i - 1] == "--version" and serves_semantics:
                    server_args[i] = semantic_version
        if serves_semantics and semantic_version and "--version" not in server_args:
            server_args.extend(["--version", semantic_version])

        mcp_configs.append({
            "name": s["name"],
            "module": s.get("module", ""),
            "args": server_args,
            "description": s.get("description", ""),
        })


    semantic_manifest = ""
    if config.semantic.enabled:
        store_path = config.semantic.store_path or str(SEMANTIC_LAYER_DIR / db_id)
        if Path(store_path).exists():
            try:
                layer = runtime_class(store_path, version=config.semantic.version)
                semantic_manifest = layer.manifest(db_id=db_id)
            except Exception:
                pass


    from agent.llm_providers import create_llm_provider
    llm = create_llm_provider(**llm_kwargs)


    mcp_client = mcp_client_class()
    await mcp_client.connect_to_servers(mcp_configs)


    agent = agent_class(
        llm_provider=llm,
        mcp_client=mcp_client,
        max_turns=config.agent.max_turns,
        semantic_manifest=semantic_manifest,
        verbose=verbose,
        turn_timeout=90,
    )

    trace = None
    try:
        session = await agent.start_session(
            question=question, db_id=db_id, db_path=db_path,
        )
        await agent.run()

        pred_sql = session.pred_sql
        ex, ves = evaluate_sql(pred_sql, gold_sql, db_path)

        if save_traces or record_trajectories:
            trace = agent.export_trace()

        if record_trajectories and config.semantic.enabled and trace:
            from trajectory_recorder import record_trajectory

            record_trajectory(
                semantic_store_path,
                db_id=db_id,
                question=question,
                question_id=question_id,
                split=split,
                messages=trace.get("messages", []),
                final_answer=pred_sql,
                task_status="completed",
            )

        return (EvalResult(
            question_id=question_id, db_id=db_id, question=question,
            difficulty=difficulty, condition=config.condition,
            pred_sql=pred_sql, gold_sql=gold_sql, ex=ex, ves=ves,
            turns=session.total_turns,
            semantic_tool_calls=agent.semantic_call_count,
        ), trace)

    except Exception as e:
        if save_traces or record_trajectories:
            try:
                trace = agent.export_trace()
            except Exception:
                pass
        if record_trajectories and config.semantic.enabled and trace:
            try:
                from trajectory_recorder import record_trajectory

                record_trajectory(
                    semantic_store_path,
                    db_id=db_id,
                    question=question,
                    question_id=question_id,
                    split=split,
                    messages=trace.get("messages", []),
                    final_answer="",
                    task_status="failed",
                    errors=[str(e)],
                )
            except Exception:
                pass
        return (EvalResult(
            question_id=question_id, db_id=db_id, question=question,
            difficulty=difficulty, condition=config.condition,
            pred_sql="", gold_sql=gold_sql, ex=False, ves=0.0, turns=0,
            error=str(e),
        ), trace)
    finally:
        await mcp_client.close()


def compute_metrics(results: list) -> dict:
    """Compute metrics."""
    total = len(results)
    correct = sum(1 for r in results if r["ex"])
    overall_ex = correct / total if total > 0 else 0
    overall_ves = sum(r.get("ves", 0.0) for r in results) / total if total > 0 else 0

    by_diff = {}
    for diff in ["simple", "moderate", "challenging"]:
        subset = [r for r in results if r["difficulty"] == diff]
        s_total = len(subset)
        s_correct = sum(1 for r in subset if r["ex"])
        s_ves = sum(r.get("ves", 0.0) for r in subset) / s_total if s_total else 0
        by_diff[diff] = {
            "total": s_total,
            "correct": s_correct,
            "ex": s_correct / s_total if s_total else 0,
            "ves": s_ves,
        }

    by_db = {}
    for r in results:
        db = r["db_id"]
        if db not in by_db:
            by_db[db] = {"total": 0, "correct": 0, "ves_sum": 0.0}
        by_db[db]["total"] += 1
        if r["ex"]:
            by_db[db]["correct"] += 1
        by_db[db]["ves_sum"] += r.get("ves", 0.0)
    for db in by_db:
        info = by_db[db]
        info["ex"] = info["correct"] / info["total"]
        info["ves"] = info["ves_sum"] / info["total"]
        del info["ves_sum"]

    turns_list = [r["turns"] for r in results if r["turns"] > 0]
    avg_turns = sum(turns_list) / len(turns_list) if turns_list else 0

    return {
        "condition": results[0]["condition"] if results else "",
        "total": total,
        "correct": correct,
        "overall_ex": overall_ex,
        "overall_ves": overall_ves,
        "avg_turns": avg_turns,
        "by_difficulty": by_diff,
        "by_database": by_db,
    }


def _make_entry(result) -> dict:
    """Create entry."""
    return {
        "question_id": result.question_id,
        "db_id": result.db_id,
        "question": result.question,
        "difficulty": result.difficulty,
        "condition": result.condition,
        "pred_sql": result.pred_sql,
        "gold_sql": result.gold_sql,
        "ex": result.ex,
        "ves": result.ves,
        "turns": result.turns,
        "error": result.error,
        "semantic_tool_calls": result.semantic_tool_calls,
    }


def _save_trace(trace: dict, traces_dir: Path, qid: str):
    """Save trace."""
    traces_dir.mkdir(parents=True, exist_ok=True)
    with open(traces_dir / f"{qid}.json", "w", encoding="utf-8") as f:
        json.dump(trace, f, ensure_ascii=False, indent=2)


def _flush_results(run_dir: Path, db_id: str, condition: str,
                   model: str, db_results: list, all_results: list):
    """Implement flush results."""
    db_dir = run_dir / db_id
    db_dir.mkdir(parents=True, exist_ok=True)
    with open(db_dir / "results.json", "w", encoding="utf-8") as f:
        json.dump({
            "db_id": db_id, "condition": condition,
            "model": model,
            "db_metrics": compute_metrics(db_results),
            "results": db_results,
        }, f, ensure_ascii=False, indent=2)
    with open(run_dir / "all_results.json", "w", encoding="utf-8") as f:
        json.dump({
            "condition": condition, "model": model,
            "metrics": compute_metrics(all_results),
            "results": all_results,
        }, f, ensure_ascii=False, indent=2)


# =========================================================================
# Durable single-question worker helpers
# =========================================================================

def _worker_file_stem(db_id: str, question_id: object) -> str:
    return f"{db_id}_{question_id}".replace("/", "_").replace("\\", "_")


def _worker_error_entry(question: dict, db_id: str, condition: str, message: str) -> dict:
    return {
        "question_id": question["question_id"],
        "db_id": db_id,
        "question": question["question"],
        "difficulty": question.get("difficulty", "?"),
        "condition": condition,
        "pred_sql": "",
        "gold_sql": question.get("SQL", ""),
        "ex": False,
        "ves": 0.0,
        "turns": 0,
        "error": message,
        "semantic_tool_calls": 0,
    }


def _load_worker_result(result_path: Path) -> dict:
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    entry = payload.get("entry")
    required_fields = {"question_id", "db_id", "pred_sql", "ex", "turns", "error"}
    if not isinstance(entry, dict) or not required_fields.issubset(entry):
        raise ValueError("worker result is incomplete")
    return payload


def _run_worker_subprocess(job_path: Path, result_path: Path, timeout: int,
                           api_key_env: str, api_key: str | None) -> None:
    worker_path = Path(__file__).resolve().parent / "evaluation_worker.py"
    worker_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if api_key:
        worker_env[api_key_env] = api_key
    process = subprocess.Popen(
        [sys.executable, str(worker_path), "--job", str(job_path), "--result", str(result_path)],
        cwd=str(Path(__file__).resolve().parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=worker_env,
    )
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process.pid)
        process.wait(timeout=10)
        raise TimeoutError(f"Timeout after {timeout}s")

    if process.returncode != 0:
        raise RuntimeError(f"Worker exited with code {process.returncode}")


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="BIRD batch evaluation")
    parser.add_argument("--config", required=True, help="YAML configuration file")
    parser.add_argument("--split-dir", default="",
                        help="Directory of per-database question JSONs to run "
                             "(train or test split; defaults to the --dataset test split)")
    parser.add_argument("--dataset", default="minidev",
                        choices=["minidev", "dev"],
                        help="Dataset selection (minidev=500 questions, dev=1534 questions)")
    parser.add_argument("--output", default=str(RESULTS_DIR), help="Results output directory")
    parser.add_argument("--db-ids", default="", help="Comma-separated database IDs (default: all)")
    parser.add_argument("--limit", type=int, default=0, help="Limit the number of questions for debugging")
    parser.add_argument("--llm-provider", default="", help="LLM provider")
    parser.add_argument("--model", default="", help="Model name")
    parser.add_argument("--api-key", default="", help="API key")
    parser.add_argument("--base-url", default="", help="API base URL")
    parser.add_argument("--verbose", action="store_true", help="Print detailed interactions")
    parser.add_argument("--save-traces", action="store_true", help="Save complete execution traces")
    parser.add_argument(
        "--record-trajectories", action="store_true",
        help="Write normalized evolution trajectories to each semantic workspace",
    )
    parser.add_argument("--parallel", type=int, default=None,
                        help="Number of parallel workers (default: 24)")
    parser.add_argument("--timeout", type=int, default=180, help="Per-question timeout in seconds (default: 180)")
    parser.add_argument("--max-retries", type=int, default=2,
                        help="Per-question retries (default: 2; triggered by timeout, exception, or EX=False)")
    parser.add_argument("--semantic-version", default="",
                        help="Explicit semantic version to evaluate "
                             "(overrides semantic.version in the config)")
    parser.add_argument("--resume", default="",
                        help="Resume from a previous run directory and skip completed questions")
    args = parser.parse_args()

    config = ExperimentConfig.from_yaml(args.config)
    if args.semantic_version:
        config.semantic.version = args.semantic_version

    llm_kwargs = {
        "provider_type": args.llm_provider or config.agent.provider,
        "model": args.model or config.agent.model,
        "temperature": config.agent.temperature,
    }
    api_key = args.api_key or os.getenv(config.agent.api_key_env)
    base_url = args.base_url or config.agent.base_url
    if api_key:
        llm_kwargs["api_key"] = api_key
    if base_url:
        llm_kwargs["base_url"] = base_url
    parallel = args.parallel if args.parallel is not None else 8

    ds_config = get_dataset_config(args.dataset)
    split_dir = Path(args.split_dir) if args.split_dir else ds_config["split_dir"]
    if not split_dir.exists():
        print(f"❌ Split directory does not exist: {split_dir}")
        return
    split_name = split_dir.name
    print(f"Dataset: {args.dataset}")

    test_files = sorted(split_dir.glob("*.json"))
    if args.db_ids:
        allowed = set(args.db_ids.split(","))
        test_files = [f for f in test_files if f.stem in allowed]

    print(f"Configuration: {args.config}")
    print(f"Condition: {config.condition}")
    print(f"LLM: {llm_kwargs['provider_type']}/{llm_kwargs['model']}")
    print(f"Question files: {len(test_files)} items")
    print(f"Output: {args.output}")
    if args.verbose:
        print("Verbose: ON")
    if args.save_traces:
        print("Save traces: ON")

    from agent.data_agent import BIRDReActAgent
    from tceo.runtime import BIRDSemanticLayer
    from tool_server.mcp_client import MCPClientManager


    gold_lookup: dict[str, str] = {}
    questions_file = ds_config.get("questions")
    if questions_file and Path(questions_file).exists():
        with open(questions_file, "r", encoding="utf-8") as f:
            all_questions = json.load(f)
        gold_lookup = {str(q["question_id"]): q.get("SQL", "") for q in all_questions}
        print(f"Gold SQL lookup: {len(gold_lookup)} records")


    completed_ids = set()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.resume:
        run_dir = Path(args.resume).resolve()
        if not run_dir.exists():
            print(f"❌ Resume directory does not exist: {run_dir}")
            return
        resume_file = run_dir / "all_results.json"
        if resume_file.exists():
            with open(resume_file, "r", encoding="utf-8") as f:
                prev = json.load(f)
            for r in prev.get("results", []):
                if not r.get("error"):
                    completed_ids.add(r["question_id"])
            print(f"Resume: {len(completed_ids)} questions already completed successfully; skipping")
        else:
            print("[WARN] No all_results.json found in the resume directory; starting from scratch")
    else:
        run_dir = (Path(args.output) / config.condition / timestamp).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    all_results = []
    question_count = 0


    if completed_ids and (run_dir / "all_results.json").exists():
        with open(run_dir / "all_results.json", "r", encoding="utf-8") as f:
            prev = json.load(f)
        all_results = [r for r in prev.get("results", []) if not r.get("error")]
        print(f"Loaded {len(all_results)} existing results")

    if parallel <= 1:
        # =====================================================================

        # =====================================================================
        for test_file in test_files:
            with open(test_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            db_id = data["db_id"]
            questions = data["questions"]
            db_results = []
            print(f"\n{'='*60}")
            print(f"[{db_id}] {len(questions)} questions")

            for i, q in enumerate(questions):
                if args.limit and question_count >= args.limit:
                    break

                qid = q["question_id"]
                if qid in completed_ids:
                    continue
                difficulty = q.get("difficulty", "?")
                if args.verbose:
                    print(f"\n{'─'*50}")
                    print(f"[{i+1}/{len(questions)}] #{qid} ({difficulty})")
                    print(f"Q: {q['question']}")
                    print(f"{'─'*50}")
                else:
                    print(f"  [{i+1}/{len(questions)}] #{qid} ({difficulty})", end=" ")

                start_time = time.time()
                result = None
                trace = None

                for attempt in range(args.max_retries + 1):
                    try:
                        result, trace = await asyncio.wait_for(
                            run_single_question(
                                q, config, db_id, llm_kwargs,
                                MCPClientManager, BIRDReActAgent, BIRDSemanticLayer,
                                verbose=args.verbose,
                                save_traces=args.save_traces,
                                record_trajectories=args.record_trajectories,
                                split=split_name,
                                gold_lookup=gold_lookup,
                            ),
                            timeout=args.timeout,
                        )

                        break
                    except asyncio.TimeoutError:
                        if attempt == args.max_retries:
                            result = EvalResult(
                                question_id=q["question_id"],
                                db_id=q["db_id"],
                                question=q["question"],
                                difficulty=q.get("difficulty", "?"),
                                condition=config.condition,
                                pred_sql="",
                                gold_sql=q.get("SQL", ""),
                                ex=False,
                                turns=0,
                                error=f"Timeout after {args.timeout}s",
                            )
                        else:
                            print(f"    [Retry {attempt+1}/{args.max_retries}] Timeout, "
                                  f"cooldown 5s before retry...")
                            await asyncio.sleep(5)
                    except Exception as e:
                        if attempt == args.max_retries:
                            result = EvalResult(
                                question_id=q["question_id"],
                                db_id=q["db_id"],
                                question=q["question"],
                                difficulty=q.get("difficulty", "?"),
                                condition=config.condition,
                                pred_sql="",
                                gold_sql=q.get("SQL", ""),
                                ex=False,
                                turns=0,
                                error=f"Unexpected: {e}",
                            )
                        else:
                            print(f"    [Retry {attempt+1}/{args.max_retries}] {e}, "
                                  f"cooldown 2s...")
                            await asyncio.sleep(2)

                elapsed = time.time() - start_time

                status = "✅" if result.ex else "❌"
                print(f"{status} turns={result.turns} [{elapsed:.0f}s]")
                if result.error:
                    print(f"    Error: {result.error[:100]}")

                entry = _make_entry(result)
                db_results.append(entry)
                all_results.append(entry)

                if args.save_traces and trace:
                    _save_trace(trace, run_dir / db_id / "traces", qid)

                _flush_results(run_dir, db_id, config.condition,
                               llm_kwargs["model"], db_results, all_results)
                question_count += 1

            if args.limit and question_count >= args.limit:
                break
    else:
        # =====================================================================


        # =====================================================================
        if args.verbose:
            print("[INFO] In parallel mode, worker output is written to separate result files")
        if parallel > 8:
            print(f"[WARN] Concurrency {parallel} is high and may trigger API rate limits")

        all_questions = []
        db_results_map = {}
        for test_file in test_files:
            with open(test_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            db_id = data["db_id"]

            db_results_map[db_id] = []
            if completed_ids:
                db_file = run_dir / db_id / "results.json"
                if db_file.exists():
                    with open(db_file, "r", encoding="utf-8") as f:
                        prev_db = json.load(f)
                    db_results_map[db_id] = [
                        r for r in prev_db.get("results", []) if not r.get("error")
                    ]
            for q in data["questions"]:
                if q["question_id"] in completed_ids:
                    continue
                all_questions.append((q, db_id))

        if args.limit and args.limit < len(all_questions):
            all_questions = all_questions[:args.limit]
        total_questions = len(all_questions)
        print(f"\nParallel mode: {parallel} worker threads for {total_questions} questions\n")

        jobs_dir = run_dir / "worker_jobs"
        worker_results_dir = run_dir / "worker_results"

        def _run_one(q: dict, db_id: str) -> tuple[dict, dict | None, float]:
            start_time = time.time()
            worker_question = dict(q)
            if not worker_question.get("SQL"):
                worker_question["SQL"] = gold_lookup.get(str(worker_question["question_id"]), "")

            stem = _worker_file_stem(db_id, worker_question["question_id"])
            job_path = jobs_dir / f"{stem}.json"
            result_path = worker_results_dir / f"{stem}.json"
            job_path.parent.mkdir(parents=True, exist_ok=True)
            job_path.write_text(json.dumps({
                "question": worker_question,
                "db_id": db_id,
                "condition": config.condition,
                "config_path": str(Path(args.config).resolve()),
                "llm_kwargs": {k: v for k, v in llm_kwargs.items() if k != "api_key"},
                "api_key_env": config.agent.api_key_env,
                "save_traces": args.save_traces,
                "record_trajectories": args.record_trajectories,
                "split": split_name,
                "semantic_version": config.semantic.version,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

            payload = None
            last_error = "Unknown worker failure"
            for attempt in range(args.max_retries + 1):
                result_path.unlink(missing_ok=True)
                try:
                    _run_worker_subprocess(
                        job_path, result_path, args.timeout,
                        config.agent.api_key_env, llm_kwargs.get("api_key"),
                    )
                    payload = _load_worker_result(result_path)
                    entry = payload["entry"]
                    if not entry.get("error"):
                        return entry, payload.get("trace"), time.time() - start_time
                    last_error = entry["error"]
                except Exception as exc:
                    last_error = str(exc)

                if attempt < args.max_retries:
                    time.sleep(2 ** attempt)

            return (
                _worker_error_entry(worker_question, db_id, config.condition, last_error),
                payload.get("trace") if payload else None,
                time.time() - start_time,
            )

        with ThreadPoolExecutor(max_workers=parallel) as executor:
            futures = {
                executor.submit(_run_one, q, db_id): (q, db_id)
                for q, db_id in all_questions
            }
            for done_count, future in enumerate(as_completed(futures), 1):
                q, db_id = futures[future]
                try:
                    entry, trace, elapsed = future.result()
                except Exception as exc:
                    entry = _worker_error_entry(q, db_id, config.condition, str(exc))
                    trace = None
                    elapsed = 0.0

                status = "✅" if entry.get("ex") else "❌"
                all_results.append(entry)
                db_results_map[db_id].append(entry)
                print(f"  [{done_count}/{total_questions}] #{entry['question_id']} "
                      f"({db_id}) {status} turns={entry.get('turns', 0)} [{elapsed:.0f}s]")
                if entry.get("error"):
                    print(f"    Error: {entry['error'][:100]}")
                _flush_results(run_dir, db_id, config.condition,
                               llm_kwargs["model"], db_results_map[db_id], all_results)
                if args.save_traces and trace:
                    _save_trace(trace, run_dir / db_id / "traces", entry["question_id"])

    metrics = compute_metrics(all_results)



    summary_file = run_dir / "summary.md"
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write("# BIRD Evaluation Summary\n\n")
        f.write(f"- **Condition**: {config.condition}\n")
        f.write(f"- **Model**: {llm_kwargs['model']}\n")
        f.write(f"- **Date**: {timestamp}\n\n")
        f.write("## Overall\n\n")
        f.write(f"- **EX**: {metrics['overall_ex']:.2%} "
                f"({metrics['correct']}/{metrics['total']})\n")
        f.write(f"- **VES**: {metrics['overall_ves']:.4f}\n")
        f.write(f"- **Avg Turns**: {metrics['avg_turns']:.1f}\n\n")
        f.write("## By Difficulty\n\n")
        f.write("| Difficulty | EX | VES | Correct/Total |\n")
        f.write("|------------|-----|------|---------------|\n")
        for diff, info in metrics["by_difficulty"].items():
            if info["total"] > 0:
                f.write(f"| {diff} | {info['ex']:.2%} | {info['ves']:.4f} | "
                        f"{info['correct']}/{info['total']} |\n")
        f.write("\n## By Database\n\n")
        f.write("| Database | EX | VES | Correct/Total |\n")
        f.write("|----------|-----|------|---------------|\n")
        for db, info in sorted(metrics["by_database"].items()):
            f.write(f"| {db} | {info['ex']:.2%} | {info['ves']:.4f} | "
                    f"{info['correct']}/{info['total']} |\n")

    print(f"\n{'='*60}")
    print(f"Evaluation completed: {config.condition}")
    print(f"Overall EX: {metrics['overall_ex']:.2%} "
          f"({metrics['correct']}/{metrics['total']})")
    print(f"Overall VES: {metrics['overall_ves']:.4f}")
    print(f"Output: {run_dir}")
    print("  - summary.md | all_results.json")
    for db, info in sorted(metrics["by_database"].items()):
        if info["total"] > 0:
            print(f"  - {db}/results.json", end="")
            if args.save_traces:
                trace_count = info["total"]
                print(f" | {db}/traces/ ({trace_count} questions)")
            else:
                print()


if __name__ == "__main__":
    asyncio.run(main())
