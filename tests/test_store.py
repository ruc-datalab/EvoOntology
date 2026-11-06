"""Core store tests: save / load / active version / candidate publish / rollback."""

import json

import pytest

from evoontology import SemanticStore, ensure_workspace

SAMPLE = {
    "terms": [{"id": "t1", "name": "net_income", "type": "metric", "definition": "net income"}],
    "mappings": [{"id": "m1", "term_id": "t1", "table": "financials", "column": "net_income"}],
    "relations": [],
    "constraints": [{"id": "c1", "target": "t1", "severity": "warn", "description": "exclude refunds"}],
    "evidence": [{"id": "e1", "source": "schema", "query": "PRAGMA table_info(financials)"}],
}


def _write_v0(root):
    SemanticStore.save_version(str(root), "ontology_v0", SAMPLE)
    SemanticStore.set_active(str(root), "ontology_v0")


def test_save_load_roundtrip(tmp_path):
    ensure_workspace(str(tmp_path))
    _write_v0(tmp_path)
    store = SemanticStore.load(str(tmp_path))
    assert store.version == "ontology_v0"
    assert store.counts() == {
        "terms": 1, "mappings": 1, "relations": 0, "constraints": 1, "evidence": 1,
    }
    assert store.terms["t1"].name == "net_income"
    assert store.mappings["m1"].table == "financials"


def test_active_version(tmp_path):
    ensure_workspace(str(tmp_path))
    _write_v0(tmp_path)
    assert SemanticStore.active_version(str(tmp_path)) == "ontology_v0"


def test_legacy_version_field(tmp_path):
    ensure_workspace(str(tmp_path))
    _write_v0(tmp_path)
    # rewrite active.json using the legacy "version" field
    (tmp_path / "active.json").write_text(json.dumps({"version": "ontology_v0"}), encoding="utf-8")
    assert SemanticStore.active_version(str(tmp_path)) == "ontology_v0"


def test_candidate_publish_and_promote(tmp_path):
    ensure_workspace(str(tmp_path))
    _write_v0(tmp_path)

    candidate = {k: v for k, v in SAMPLE.items()}
    candidate["terms"] = SAMPLE["terms"] + [{"id": "t2", "name": "gross_margin", "type": "metric"}]
    SemanticStore.save_version(str(tmp_path), "v0-c1", candidate)

    # before promote, active is still parent
    assert SemanticStore.active_version(str(tmp_path)) == "ontology_v0"

    new_version = SemanticStore.promote(str(tmp_path), "v0-c1", "ontology_v1")
    assert new_version == "ontology_v1"
    assert SemanticStore.active_version(str(tmp_path)) == "ontology_v1"
    store = SemanticStore.load(str(tmp_path))
    assert "t2" in store.terms


def test_rollback_keeps_parent(tmp_path):
    ensure_workspace(str(tmp_path))
    _write_v0(tmp_path)

    candidate = {k: v for k, v in SAMPLE.items()}
    candidate["terms"] = SAMPLE["terms"] + [{"id": "t3", "name": "bad", "type": "metric"}]
    SemanticStore.save_version(str(tmp_path), "v0-c1", candidate)

    # reject = simply do not promote; active.json stays at parent
    assert SemanticStore.active_version(str(tmp_path)) == "ontology_v0"
    store = SemanticStore.load(str(tmp_path))
    assert "t3" not in store.terms


def test_list_versions(tmp_path):
    ensure_workspace(str(tmp_path))
    _write_v0(tmp_path)
    SemanticStore.save_version(str(tmp_path), "v0-c1", SAMPLE)
    assert SemanticStore.list_versions(str(tmp_path)) == ["ontology_v0", "v0-c1"]


def test_missing_active_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        SemanticStore.load(str(tmp_path))


@pytest.mark.parametrize(
    "bad_version",
    ["../escape", "..\\escape", "/absolute", "C:\\absolute", ".", ".."],
)
def test_version_identifiers_must_be_single_path_components(tmp_path, bad_version):
    with pytest.raises(ValueError, match="single path component"):
        SemanticStore.save_version(tmp_path, bad_version, SAMPLE)
    with pytest.raises(ValueError, match="single path component"):
        SemanticStore.load_version(tmp_path, bad_version)
    with pytest.raises(ValueError, match="single path component"):
        SemanticStore.set_active(tmp_path, bad_version)


def test_candidate_and_published_identifiers_are_contained(tmp_path):
    SemanticStore.save_version(tmp_path, "candidate", SAMPLE)
    with pytest.raises(ValueError, match="single path component"):
        SemanticStore.publish(tmp_path, "../candidate", "ontology_v1")
    with pytest.raises(ValueError, match="single path component"):
        SemanticStore.publish(tmp_path, "candidate", "../ontology_v1")


def test_store_remains_naming_agnostic(tmp_path):
    version = "候选 草稿 1"
    SemanticStore.save_version(tmp_path, version, SAMPLE)
    SemanticStore.set_active(tmp_path, version)
    assert SemanticStore.load(tmp_path).version == version


def test_save_validates_and_serializes_every_family_before_writing(tmp_path):
    bad_type = dict(SAMPLE)
    bad_type["mappings"] = "not a list"
    with pytest.raises(TypeError):
        SemanticStore.save_version(tmp_path, "bad-type", bad_type)
    assert not (tmp_path / "versions" / "bad-type").exists()

    bad_json = {family: list(items) for family, items in SAMPLE.items()}
    bad_json["evidence"] = [{"value": object()}]
    with pytest.raises(TypeError):
        SemanticStore.save_version(tmp_path, "bad-json", bad_json)
    assert not (tmp_path / "versions" / "bad-json").exists()


def test_save_writes_each_record_file_without_leaving_temporaries(tmp_path):
    SemanticStore.save_version(tmp_path, "draft", SAMPLE)
    version_dir = tmp_path / "versions" / "draft"
    assert json.loads((version_dir / "relations.json").read_text(encoding="utf-8")) == []
    assert not list(version_dir.glob("*.tmp"))
    # An existing version directory is never rewritten: different content must
    # raise instead of silently replacing the record files (issue #6).
    changed = {family: list(items) for family, items in SAMPLE.items()}
    changed["relations"] = [{"id": "r1", "source": "t1", "target": "t1", "type": "related"}]
    with pytest.raises(FileExistsError):
        SemanticStore.save_version(tmp_path, "draft", changed)
    assert json.loads((version_dir / "relations.json").read_text(encoding="utf-8")) == []
    assert not list(version_dir.glob("*.tmp"))
