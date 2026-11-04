"""Runtime tests: browse / resolve / manifest, including the uninitialized case."""

from evoontology import SemanticLayer, SemanticStore, ensure_workspace

SAMPLE = {
    "terms": [
        {"id": "t1", "name": "net_income", "type": "metric", "definition": "net income", "aliases": ["profit"]},
        {"id": "t2", "name": "region", "type": "dimension", "definition": "sales region"},
    ],
    "mappings": [
        {"id": "m1", "term_id": "t1", "table": "financials", "column": "net_income"},
        {"id": "m2", "term_id": "t2", "table": "sales", "column": "region"},
    ],
    "relations": [{"id": "r1", "source": "t2", "target": "t1", "relation_type": "derivation"}],
    "constraints": [{"id": "c1", "target": "t1", "severity": "warn", "description": "exclude refunds"}],
    "evidence": [{"id": "e1", "source": "schema", "query": "PRAGMA table_info(financials)"}],
}


def _init(tmp_path):
    ensure_workspace(str(tmp_path))
    SemanticStore.save_version(str(tmp_path), "ontology_v0", SAMPLE)
    SemanticStore.set_active(str(tmp_path), "ontology_v0")


def test_manifest(tmp_path):
    _init(tmp_path)
    layer = SemanticLayer.load(str(tmp_path))
    manifest = layer.manifest()
    assert "ontology_v0" in manifest
    assert "browse_semantics" in manifest
    assert "resolve_semantics" in manifest


def test_browse(tmp_path):
    _init(tmp_path)
    layer = SemanticLayer.load(str(tmp_path))
    result = layer.browse(query="net income", kind="term")
    assert result["status"] == "ok"
    assert result["matched_total"] == 1
    assert result["items"][0]["name"] == "net_income"


def test_browse_needs_query(tmp_path):
    _init(tmp_path)
    layer = SemanticLayer.load(str(tmp_path))
    result = layer.browse(query="")
    assert result["status"] == "needs_query"


def test_browse_ignores_stopwords_when_ranking(tmp_path):
    records = {
        "terms": [
            {
                "id": "paid_share",
                "name": "fully paid percentage",
                "type": "metric",
                "definition": "percentage of the loan amount",
            },
            {
                "id": "approval_date",
                "name": "loan approval date",
                "type": "dimension",
                "definition": "date when a loan was approved",
            },
        ],
        "mappings": [],
        "relations": [],
        "constraints": [],
        "evidence": [],
    }
    ensure_workspace(str(tmp_path))
    SemanticStore.save_version(str(tmp_path), "ontology_v0", records)
    SemanticStore.set_active(str(tmp_path), "ontology_v0")

    result = SemanticLayer.load(str(tmp_path)).browse(
        query="How many loans were approved in the period?", kind="term"
    )

    assert result["items"][0]["id"] == "approval_date"
    assert all(
        token not in {"how", "many", "were", "in", "the"}
        for item in result["items"]
        for token in item["match_rationale"]["overlap_tokens"]
    )


def test_resolve(tmp_path):
    _init(tmp_path)
    layer = SemanticLayer.load(str(tmp_path))
    result = layer.resolve(mentions=["net_income"])
    assert result["results"][0]["status"] == "resolved"
    resolved = result["results"][0]
    assert resolved["term"]["name"] == "net_income"
    assert len(resolved["mappings"]) == 1
    assert len(resolved["constraints"]) == 1


def test_resolve_unresolved(tmp_path):
    _init(tmp_path)
    layer = SemanticLayer.load(str(tmp_path))
    result = layer.resolve(mentions=["nonexistent"])
    assert result["results"][0]["status"] == "unresolved"


def test_resolve_does_not_confuse_substrings(tmp_path):
    records = {
        "terms": [
            {"id": "male", "name": "male client", "type": "category"},
            {"id": "female", "name": "female client", "type": "category"},
            {"id": "issue", "name": "loan issue", "type": "category"},
            {"id": "issued", "name": "statement issued", "type": "category"},
        ],
        "mappings": [],
        "relations": [],
        "constraints": [],
        "evidence": [],
    }
    ensure_workspace(str(tmp_path))
    SemanticStore.save_version(str(tmp_path), "ontology_v0", records)
    SemanticStore.set_active(str(tmp_path), "ontology_v0")
    layer = SemanticLayer.load(str(tmp_path))

    male = layer.resolve(mentions=["male"])["results"][0]
    issued = layer.resolve(mentions=["issued"])["results"][0]

    assert male["term"]["id"] == "male"
    assert issued["term"]["id"] == "issued"


def test_uninitialized(tmp_path):
    layer = SemanticLayer.load(str(tmp_path))
    assert layer.version == "uninitialized"
    assert "uninitialized" in layer.manifest()
    assert layer.browse(query="x")["status"] == "ok"
    assert layer.browse(query="x")["items"] == []


def test_execute_dispatch(tmp_path):
    _init(tmp_path)
    layer = SemanticLayer.load(str(tmp_path))
    result = layer.execute("browse_semantics", {"query": "region", "kind": "term"})
    assert result["matched_total"] == 1


def test_resolve_by_semantic_id(tmp_path):
    _init(tmp_path)
    layer = SemanticLayer.load(str(tmp_path))
    result = layer.resolve(mentions=["t1"])
    resolved = result["results"][0]
    assert resolved["status"] == "resolved"
    assert resolved["query_type"] == "semantic_id"
    assert resolved["term"]["name"] == "net_income"
    assert len(resolved["mappings"]) == 1


def test_resolve_expands_evidence(tmp_path):
    records = {
        "terms": [
            {
                "id": "term_revenue",
                "name": "revenue",
                "type": "metric",
                "definition": "total revenue",
                "aliases": ["sales revenue"],
                "evidence_refs": ["ev_revenue"],
            }
        ],
        "mappings": [
            {
                "id": "map_revenue",
                "term_id": "term_revenue",
                "table": "financials",
                "column": "revenue",
                "evidence_refs": ["ev_revenue"],
            }
        ],
        "relations": [],
        "constraints": [
            {
                "id": "con_revenue",
                "target": "term_revenue",
                "severity": "warn",
                "description": "exclude refunds",
                "evidence_refs": ["ev_revenue"],
            }
        ],
        "evidence": [
            {
                "id": "ev_revenue",
                "source": "schema",
                "query": "PRAGMA table_info(financials)",
                "result": "revenue exists",
                "validation_method": "schema check",
                "timestamp": "2026-08-20T00:00:00Z",
            }
        ],
    }
    ensure_workspace(str(tmp_path))
    SemanticStore.save_version(str(tmp_path), "ontology_v0", records)
    SemanticStore.set_active(str(tmp_path), "ontology_v0")
    layer = SemanticLayer.load(str(tmp_path))
    result = layer.resolve(mentions=["revenue"])
    evidence = result["results"][0]["evidence"]
    assert [item["id"] for item in evidence] == ["ev_revenue"]
    assert evidence[0]["query"] == "PRAGMA table_info(financials)"


def test_resolve_context_disambiguates(tmp_path):
    records = {
        "terms": [
            {"id": "t_net", "name": "net_income", "type": "metric",
             "definition": "net income", "aliases": ["profit"]},
            {"id": "t_gross", "name": "gross_profit", "type": "metric",
             "definition": "gross profit before costs", "aliases": ["profit"]},
        ],
        "mappings": [],
        "relations": [],
        "constraints": [],
        "evidence": [],
    }
    ensure_workspace(str(tmp_path))
    SemanticStore.save_version(str(tmp_path), "ontology_v0", records)
    SemanticStore.set_active(str(tmp_path), "ontology_v0")
    layer = SemanticLayer.load(str(tmp_path))
    without_context = layer.resolve(mentions=["profit"])
    assert without_context["results"][0]["status"] == "ambiguous"
    with_context = layer.resolve(mentions=["profit"], context="net income")
    assert with_context["results"][0]["status"] == "resolved"
    assert with_context["results"][0]["term"]["name"] == "net_income"


def test_resolve_returns_mapping_targeted_constraints(tmp_path):
    records = {
        "terms": [
            {"id": "t1", "name": "net_income", "type": "metric", "definition": "net income"},
            {"id": "t2", "name": "region", "type": "dimension", "definition": "sales region"},
        ],
        "mappings": [
            {"id": "m1", "term_id": "t1", "table": "financials", "column": "net_income"},
            {"id": "m_old", "term_id": "t1", "table": "legacy", "column": "net_income",
             "lifecycle": {"state": "deprecated"}},
            {"id": "m2", "term_id": "t2", "table": "sales", "column": "region"},
        ],
        "relations": [],
        "constraints": [
            {"id": "c_term", "target": "t1", "severity": "warn", "description": "exclude refunds"},
            {"id": "c_map", "target": "m1", "severity": "warn",
             "description": "values are in thousands", "evidence": ["e_map"]},
            {"id": "c_old", "target": "m_old", "severity": "warn", "description": "legacy units"},
            {"id": "c_other", "target": "m2", "severity": "warn", "description": "region codes"},
        ],
        "evidence": [
            {"id": "e_map", "source": "schema", "query": "SELECT MAX(net_income) FROM financials"},
        ],
    }
    ensure_workspace(str(tmp_path))
    SemanticStore.save_version(str(tmp_path), "ontology_v0", records)
    SemanticStore.set_active(str(tmp_path), "ontology_v0")
    result = SemanticLayer.load(str(tmp_path)).resolve(mentions=["net_income"])["results"][0]
    targets = {c["id"]: c.get("target") for c in result.get("constraints", [])}
    assert targets == {"c_term": "t1", "c_map": "m1"}
    assert [e["id"] for e in result.get("evidence", [])] == ["e_map"]
