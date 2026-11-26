"""Regression tests for extracting the final SQL from a BIRD agent reply."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SQL = "SELECT name\nFROM t\nWHERE x = 1"
CTE = "WITH c AS (\n  SELECT a FROM t\n)\nSELECT a FROM c"


@pytest.mark.parametrize("reply,expected", [
    (f"FINISH: {SQL}", SQL),
    (f"FINISH: {SQL}\n\nThis query filters on x.", SQL),
    (f"**FINISH:** {SQL};", SQL),
    ("FINISH: SELECT 1", "SELECT 1"),
    (f"FINISH:\n```sql\n{SQL};\n```", SQL),
    (f"FINISH: ```sql\n{SQL}\n```\n\nDone.", SQL),
    (f"FINISH: {CTE}", CTE),
])
def test_extract_sql(reply, expected):
    pytest.importorskip("httpx")
    from benchmarks.bird.agent.data_agent import BIRDReActAgent

    assert BIRDReActAgent._extract_sql(reply) == expected
