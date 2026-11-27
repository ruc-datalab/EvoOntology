#!/usr/bin/env python3
"""Subprocess worker for BIRD parallel batch evaluation.

Each worker runs a single question in its own process so that a timeout,
exception, or model failure cannot contaminate the parent's event loop or
MCP client state. The parent writes a job JSON file and reads back a result
JSON file (see ``run_evaluation._run_worker_subprocess``).
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.bird.agent.data_agent import BIRDReActAgent
from benchmarks.bird.config import ExperimentConfig
from benchmarks.bird.run_evaluation import _make_entry, run_single_question
from benchmarks.bird.tceo.runtime import BIRDSemanticLayer
from benchmarks.bird.tool_server.mcp_client import MCPClientManager


def _write_result(result_path: str, entry: dict, trace: dict | None) -> None:
    payload = {"entry": entry}
    if trace is not None:
        payload["trace"] = trace
    out = Path(result_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="BIRD single-question worker")
    parser.add_argument("--job", required=True, help="Path to job JSON file")
    parser.add_argument("--result", required=True, help="Path to result JSON file")
    args = parser.parse_args()

    job = json.loads(Path(args.job).read_text(encoding="utf-8"))

    config = ExperimentConfig.from_yaml(job["config_path"])
    if job.get("semantic_version"):
        config.semantic.version = job["semantic_version"]

    llm_kwargs = dict(job.get("llm_kwargs", {}))
    api_key = os.getenv(job.get("api_key_env", "BIRD_AGENT_API_KEY"), "")
    if api_key:
        llm_kwargs["api_key"] = api_key

    question = job["question"]
    db_id = job["db_id"]
    save_traces = job.get("save_traces", False)
    record_trajectories = job.get("record_trajectories", False)
    split = job.get("split", "")

    async def _run() -> tuple:
        result, trace = await run_single_question(
            question, config, db_id, llm_kwargs,
            MCPClientManager, BIRDReActAgent, BIRDSemanticLayer,
            verbose=False,
            save_traces=save_traces,
            record_trajectories=record_trajectories,
            split=split,
            gold_lookup=None,
        )
        return _make_entry(result), trace

    entry, trace = asyncio.run(_run())
    _write_result(args.result, entry, trace)


if __name__ == "__main__":
    main()
