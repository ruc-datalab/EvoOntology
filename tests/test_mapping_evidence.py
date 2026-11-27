"""Regression tests for mapping evidence fallback in models and validation."""

from evoontology import SemanticLayer, SemanticStore, ensure_workspace
from evoontology.ontology.models import Mapping
from evoontology.validate import validate


def _sample_records(mapping):
    return {
        "terms": [{"id": "t1", "name": "revenue", "type": "metric", "definition": "Revenue"}],
        "mappings": [mapping],
        "relations": [],
        "constraints": [],
        "evidence": [{"id": "e1", "source": "schema", "query": "PRAGMA table_info(orders)"}],
    }


def test_mapping_from_dict_falls_back_to_evidence_field():
    without_validation = Mapping.from_dict(
        {"id": "m1", "term_id": "t1", "table": "orders", "evidence": ["e1"]}
    )
    with_empty_validation = Mapping.from_dict(
        {
            "id": "m2",
            "term_id": "t1",
            "table": "orders",
            "validation": {"description": "checked", "confidence": "high"},
            "evidence": ["e1"],
        }
    )
    with_source_refs = Mapping.from_dict(
        {
            "id": "m3",
            "term_id": "t1",
            "table": "orders",
            "evidence": {"source_refs": ["e1"]},
        }
    )
    prefer_validation = Mapping.from_dict(
        {
            "id": "m4",
            "term_id": "t1",
            "table": "orders",
            "validation": {"evidence": ["e_val"]},
            "evidence": ["e1"],
        }
    )
    prefer_evidence_refs = Mapping.from_dict(
        {
            "id": "m5",
            "term_id": "t1",
            "table": "orders",
            "evidence_refs": ["e_ref"],
            "validation": {"evidence": ["e_val"]},
            "evidence": ["e1"],
        }
    )

    assert without_validation.evidence_refs == ["e1"]
    assert with_empty_validation.evidence_refs == ["e1"]
    assert with_source_refs.evidence_refs == ["e1"]
    assert prefer_validation.evidence_refs == ["e_val"]
    assert prefer_evidence_refs.evidence_refs == ["e_ref"]


def test_resolve_includes_mapping_evidence_from_evidence_field(tmp_path):
    ensure_workspace(tmp_path)
    records = _sample_records(
        {
            "id": "m1",
            "term_id": "t1",
            "table": "orders",
            "column": "amount",
            "validation": {"confidence": "high"},
            "evidence": ["e1"],
        }
    )
    SemanticStore.save_version(tmp_path, "ontology_v0", records)
    SemanticStore.set_active(tmp_path, "ontology_v0")

    layer = SemanticLayer.load(str(tmp_path))
    resolved = layer.resolve(["revenue"])["results"][0]

    assert resolved["mappings"][0]["evidence_refs"] == ["e1"]
    assert [ev["id"] for ev in resolved.get("evidence", [])] == ["e1"]


def test_validate_checks_mapping_evidence_field(tmp_path):
    ensure_workspace(tmp_path)
    records = _sample_records(
        {
            "id": "m1",
            "term_id": "t1",
            "table": "orders",
            "validation": {"confidence": "high"},
            "evidence": ["e_missing"],
        }
    )
    SemanticStore.save_version(tmp_path, "ontology_v0", records)

    report = validate(str(tmp_path), version="ontology_v0")

    assert report["passed"] is False
    assert any("mapping m1: unresolved evidence ref 'e_missing'" in err for err in report["errors"])
