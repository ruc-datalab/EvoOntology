"""Regression tests: version directories are immutable on every write path.

`save_version` must enforce the same immutability rule as `publish`: an
existing version directory is never rewritten (identical content is a safe
retry, different content raises ``FileExistsError``). See issue #6.
"""

import json

import pytest

from evoontology import SemanticStore
from evoontology.runtime import ops

SAMPLE = {
    "terms": [{"id": "t1", "name": "Concept A"}],
    "mappings": [],
    "relations": [],
    "constraints": [],
    "evidence": [],
}
TAMPERED = {
    "terms": [{"id": "t1", "name": "Concept A TAMPERED"}],
    "mappings": [],
    "relations": [],
    "constraints": [],
    "evidence": [],
}


def _terms(ws, version):
    terms_file = ws / "versions" / version / "terms.json"
    return json.loads(terms_file.read_text(encoding="utf-8"))


def test_save_version_rejects_published_version_with_different_content(tmp_path):
    SemanticStore.save_version(str(tmp_path), "cand_a", SAMPLE)
    SemanticStore.publish(str(tmp_path), "cand_a", "ontology_v0")

    with pytest.raises(FileExistsError):
        SemanticStore.save_version(str(tmp_path), "ontology_v0", TAMPERED)

    assert _terms(tmp_path, "ontology_v0") == SAMPLE["terms"]


def test_save_version_rejects_existing_candidate_with_different_content(tmp_path):
    SemanticStore.save_version(str(tmp_path), "cand_a", SAMPLE)

    with pytest.raises(FileExistsError):
        SemanticStore.save_version(str(tmp_path), "cand_a", TAMPERED)

    assert _terms(tmp_path, "cand_a") == SAMPLE["terms"]


def test_save_version_identical_content_is_safe_retry(tmp_path):
    first = SemanticStore.save_version(str(tmp_path), "cand_a", SAMPLE)
    second = SemanticStore.save_version(str(tmp_path), "cand_a", SAMPLE)

    assert first == second
    assert _terms(tmp_path, "cand_a") == SAMPLE["terms"]


def test_save_version_partial_directory_is_rejected(tmp_path):
    SemanticStore.save_version(str(tmp_path), "cand_a", SAMPLE)
    (tmp_path / "versions" / "cand_a" / "terms.json").unlink()

    with pytest.raises(FileExistsError):
        SemanticStore.save_version(str(tmp_path), "cand_a", SAMPLE)


def test_publish_guard_unaffected(tmp_path):
    SemanticStore.save_version(str(tmp_path), "cand_a", SAMPLE)
    SemanticStore.save_version(str(tmp_path), "cand_b", TAMPERED)

    assert SemanticStore.publish(str(tmp_path), "cand_a", "ontology_v0") == "ontology_v0"
    # Identical content stays a safe retry...
    assert SemanticStore.publish(str(tmp_path), "cand_a", "ontology_v0") == "ontology_v0"
    # ...different content is rejected and leaves the official version intact.
    with pytest.raises(FileExistsError):
        SemanticStore.publish(str(tmp_path), "cand_b", "ontology_v0")
    assert _terms(tmp_path, "ontology_v0") == SAMPLE["terms"]


def test_mcp_save_version_surfaces_immutability_error(tmp_path):
    ws = str(tmp_path / ".evoontology")

    assert ops.execute("save_version", {"workspace": ws, "version": "ontology_v0", "records": SAMPLE})["status"] == "ok"
    with pytest.raises(FileExistsError):
        ops.execute("save_version", {"workspace": ws, "version": "ontology_v0", "records": TAMPERED})

    stored = json.loads(
        (tmp_path / ".evoontology" / "versions" / "ontology_v0" / "terms.json").read_text(encoding="utf-8")
    )
    assert stored == SAMPLE["terms"]
