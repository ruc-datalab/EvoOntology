"""Regression test: BIRD scoring must not run a predicted query forever."""

import sqlite3
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def test_slow_predicted_sql_is_interrupted(tmp_path, monkeypatch):
    pytest.importorskip("dotenv")
    pytest.importorskip("yaml")
    from benchmarks.bird import run_evaluation

    db_path = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (a INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(400)])
    conn.commit()
    conn.close()

    monkeypatch.setattr(run_evaluation, "SQL_EXEC_TIMEOUT", 0.2, raising=False)
    slow = "SELECT COUNT(*) FROM t a, t b, t c WHERE a.a + b.a + c.a = -1"

    start = time.monotonic()
    assert run_evaluation.evaluate_sql(slow, "SELECT 1", str(db_path)) == (False, 0.0)
    assert time.monotonic() - start < 1.5
