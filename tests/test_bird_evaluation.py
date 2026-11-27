"""Regression tests for BIRD execution-accuracy scoring."""

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def run_evaluation():
    pytest.importorskip("dotenv")
    pytest.importorskip("yaml")
    from benchmarks.bird import run_evaluation

    return run_evaluation


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "t.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (a, b TEXT)")
    conn.executemany("INSERT INTO t VALUES (?, ?)", [(1, "x"), (None, "y"), (2, None)])
    conn.commit()
    conn.close()
    return str(path)


@pytest.mark.parametrize("sql", [
    "SELECT a, b FROM t",
    "SELECT COALESCE(a, b) FROM t",
])
def test_matching_results_with_null_or_mixed_types_count_as_correct(run_evaluation, db_path, sql):
    ex, _ = run_evaluation.evaluate_sql(sql, sql, db_path)
    assert ex is True


def test_row_order_is_ignored_but_rows_must_match(run_evaluation, db_path):
    gold = "SELECT a, b FROM t ORDER BY b"
    assert run_evaluation.evaluate_sql("SELECT a, b FROM t ORDER BY b DESC", gold, db_path)[0] is True
    assert run_evaluation.evaluate_sql("SELECT a, b FROM t WHERE a IS NOT NULL", gold, db_path)[0] is False
