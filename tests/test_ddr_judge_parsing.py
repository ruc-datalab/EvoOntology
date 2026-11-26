"""Regression tests for parsing DDR-10K judge replies that are not valid JSON."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def evaluator():
    pytest.importorskip("pandas")
    pytest.importorskip("requests")
    from benchmarks.ddr_10k.evaluate import UnifiedEvaluator

    return UnifiedEvaluator(scenario="10k", provider="vllm", vllm_url="http://judge.invalid")


@pytest.mark.parametrize("reply,expected", [
    # Truncated JSON (e.g. the judge hit max_tokens mid-reasoning).
    ('{"context_quality": "INCORRECT_INFO", "reasoning": "The context says revenue fell', "INCORRECT_INFO"),
    ('{"context_quality": "CORRECT_INFO", "reasoning": "unlike INCORRECT_INFO cases', "CORRECT_INFO"),
    # JSON preceded by prose.
    ('Here is my verdict: {"context_quality": "INSUFFICIENT_INFO"} trailing', "INSUFFICIENT_INFO"),
    # Plain prose.
    ("The messages contradict the answer, so the verdict is INCORRECT_INFO.", "INCORRECT_INFO"),
    ("Verdict: CORRECT_INFO", "CORRECT_INFO"),
    ("I cannot decide.", "INSUFFICIENT_INFO"),
])
def test_fallback_keeps_the_judge_verdict(evaluator, reply, expected):
    result = evaluator._parse_context_quality_result(reply, "q", "gt", "", "message_wise")
    assert result["context_quality"] == expected
