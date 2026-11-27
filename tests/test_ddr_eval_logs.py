"""Regression tests for loading DDR-10K agent logs for evaluation."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def test_evaluator_scores_the_latest_run_in_a_retried_log_dir(tmp_path):
    pytest.importorskip("pandas")
    pytest.importorskip("requests")
    from benchmarks.ddr_10k.evaluate import UnifiedEvaluator

    # A retry without --overwrite leaves the failed attempt next to the new one.
    company = tmp_path / "company_123"
    company.mkdir()
    for ts, label, completed in [("20260101_100000", "failed", False), ("20260101_110000", "retry", True)]:
        (company / f"insights_{ts}.csv").write_text(f"insight\n{label} insight\n", encoding="utf-8")
        (company / f"session_stats_{ts}.json").write_text(
            json.dumps({"final_summary": f"{label} summary", "completed": completed}), encoding="utf-8"
        )

    # An unfinished later run may have written insights without a summary.
    (company / "insights_20260101_120000.csv").write_text("insight\nunfinished\n", encoding="utf-8")

    evaluator = UnifiedEvaluator(scenario="10k", provider="vllm", vllm_url="http://judge.invalid")
    entity = evaluator.load_logs_data(str(tmp_path))["123"]

    assert entity["insights_file"] == "insights_20260101_110000.csv"
    assert entity["message_wise_insights_list"] == ["retry insight"]
    assert entity["session_stats_file"] == "session_stats_20260101_110000.json"
    assert entity["chat_wise_context"] == "retry summary"


def test_latest_attempt_without_insights_does_not_reuse_older_csv(tmp_path):
    pytest.importorskip("pandas")
    pytest.importorskip("requests")
    from benchmarks.ddr_10k.evaluate import UnifiedEvaluator

    company = tmp_path / "company_123"
    company.mkdir()
    (company / "insights_20260101_100000.csv").write_text("insight\nold insight\n", encoding="utf-8")
    (company / "session_stats_20260101_110000.json").write_text(
        json.dumps({"final_summary": "retry summary", "completed": True}), encoding="utf-8"
    )

    evaluator = UnifiedEvaluator(scenario="10k", provider="vllm", vllm_url="http://judge.invalid")
    entity = evaluator.load_logs_data(str(tmp_path))["123"]

    assert entity["session_stats_file"] == "session_stats_20260101_110000.json"
    assert entity["chat_wise_context"] == "retry summary"
    assert entity["insights_file"] == ""
    assert entity["message_wise_insights_list"] == []
