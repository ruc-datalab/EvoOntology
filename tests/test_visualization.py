"""Focused tests for the read-only visualization module (Phase 1 plan §16)."""

import json
from pathlib import Path

import pytest

from evoontology import SemanticStore, ensure_workspace, save_project
from evoontology.visualization import VISUALIZATION_FILENAME, visualize
from evoontology.visualization.renderer import (
    build_content_elements,
    build_schema_view,
    build_tool_view,
    resolve_version,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _project(mode: str) -> dict:
    project = {
        "schema_version": 1,
        "mode": mode,
        "data_source": "financial_database",
        "workload_source": "seed_workload",
        "evaluation": {"type": "llm_judge"} if mode == "rolling_trajectory"
        else {"type": "external_evaluator", "adapter": "bird"},
        "boundary": {"strategy": "rolling_trajectory"} if mode == "rolling_trajectory"
        else {"direction": "A_to_B", "evolution_split": "ev", "validation_split": "val"},
    }
    return project


def _records(broken: bool = False) -> dict:
    records = {
        "terms": [
            {"id": "labor_cost", "name": "Labor Cost", "type": "metric",
             "definition": "Employee-related operating expenses",
             "aliases": ["labour cost"], "evidence": ["evidence_001"]},
            {"id": "operating_cost", "name": "Operating Cost", "type": "metric",
             "definition": "All operating expenses"},
        ],
        "mappings": [
            {"id": "mapping_labor_cost", "term_id": "labor_cost",
             "database_source": "financial_database", "table": "expense_statement",
             "column": "labor_expense", "aggregation_semantics": "sum of labor expenses",
             "grain": "annual", "evidence_refs": ["evidence_001"]},
        ],
        "relations": [
            {"id": "rel_cost_composition", "source": "operating_cost",
             "relation_type": "composition", "target": "labor_cost",
             "connection_condition": "labor expense is part of operating cost",
             "evidence": ["evidence_rel_only"]},
        ],
        "constraints": [
            {"id": "constraint_annual", "target": "labor_cost",
             "constraint_type": "scope", "severity": "warning",
             "trigger_keywords": ["annual"], "description": "Use annual grain",
             "evidence": ["evidence_con_only"]},
        ],
        "evidence": [
            {"id": "evidence_001", "source": "financial_database",
             "query": "SELECT DISTINCT category FROM expenses",
             "result": "labor_expense exists", "validation_method": "schema verification"},
            {"id": "evidence_rel_only", "source": "financial_database",
             "query": "SELECT COUNT(*) FROM expense_statement",
             "result": "composition supported", "validation_method": "schema verification"},
            {"id": "evidence_con_only", "source": "financial_database",
             "query": "SELECT grain FROM expense_statement LIMIT 1",
             "result": "annual grain", "validation_method": "schema verification"},
        ],
    }
    if broken:
        records["relations"].append(
            {"id": "rel_broken", "source": "labor_cost", "relation_type": "association",
             "target": "missing_term"}
        )
        records["mappings"].append(
            {"id": "mapping_orphan", "term_id": "missing_term", "table": "ghost_table"}
        )
        records["constraints"].append(
            {"id": "constraint_orphan", "target": "missing_term", "constraint_type": "unit"}
        )
        records["terms"][1]["evidence"] = ["missing_evidence"]
    return records


@pytest.fixture
def workspace(tmp_path):
    ws = ensure_workspace(tmp_path)
    save_project(_project("fixed_split"), ws)
    SemanticStore.save_version(ws, "ontology_v0", _records())
    SemanticStore.set_active(ws, "ontology_v0")
    return ws


# ---- 1. minimal Content conversion -----------------------------------------

def test_minimal_content_conversion(workspace):
    content = build_content_elements(SemanticStore.load(workspace))
    families = {node["data"]["family"] for node in content["nodes"]}
    assert families == {"term", "mapping", "constraint", "evidence"}

    edges = {edge["data"].get("kind") or edge["data"].get("relation_type")
             for edge in content["edges"]}
    assert {"composition", "grounded_by", "constrained_by", "supported_by"} <= edges
    assert not content["warnings"]


def test_compare_payload_excludes_derived_reference_false_positives(tmp_path):
    """Compare signatures represent records, not backlinks or derived edges."""
    ws = ensure_workspace(tmp_path)
    save_project(_project("fixed_split"), ws)
    base_records = _records()
    next_records = _records()
    next_records["mappings"] = [*next_records["mappings"], {
        "id": "mapping_operating_cost",
        "term_id": "operating_cost",
        "database_source": "financial_database",
        "table": "expense_statement",
        "column": "operating_expense",
    }]
    SemanticStore.save_version(ws, "ontology_v0", base_records)
    SemanticStore.save_version(ws, "candidate_v1", next_records)

    before = build_content_elements(SemanticStore.load_version(ws, "ontology_v0"))
    after = build_content_elements(SemanticStore.load_version(ws, "candidate_v1"))
    before_terms = {n["data"]["record_id"]: n["data"] for n in before["nodes"]
                    if n["data"]["family"] == "term"}
    after_terms = {n["data"]["record_id"]: n["data"] for n in after["nodes"]
                   if n["data"]["family"] == "term"}

    # The rendered backlink list changes, but the Term record itself does not.
    assert before_terms["operating_cost"]["detail"] != after_terms["operating_cost"]["detail"]
    assert before_terms["operating_cost"]["comparison"] == after_terms["operating_cost"]["comparison"]
    assert all("comparison" not in edge["data"] for edge in after["edges"]
               if edge["data"]["family"] == "reference")
    assert all("comparison" in edge["data"] for edge in after["edges"]
               if edge["data"]["family"] == "relation")


# ---- 2. Relation records render as edges, not nodes ------------------------

def test_relation_records_render_as_edges_not_nodes(workspace):
    store = SemanticStore.load(workspace)
    content = build_content_elements(store)
    node_ids = {node["data"]["id"] for node in content["nodes"]}
    for relation_id in store.relations:
        assert relation_id not in node_ids
    relation_edges = [edge["data"] for edge in content["edges"]
                       if edge["data"]["family"] == "relation"]
    assert [edge["record_id"] for edge in relation_edges] == list(store.relations)


# ---- 3. Mapping grounding creates no virtual nodes -------------------------

def test_mapping_grounding_creates_no_virtual_nodes(workspace):
    store = SemanticStore.load(workspace)
    content = build_content_elements(store)
    record_ids = set(store.terms) | set(store.mappings) | set(store.constraints) | set(store.evidence)
    for node in content["nodes"]:
        assert node["data"]["record_id"] in record_ids
    for virtual in ("expense_statement", "labor_expense", "financial_database"):
        assert virtual not in {node["data"]["record_id"] for node in content["nodes"]}


# ---- broken references: warn, never fabricate -------------------------------

def test_broken_references_warn_without_fabricating(tmp_path):
    ws = ensure_workspace(tmp_path)
    save_project(_project("fixed_split"), ws)
    SemanticStore.save_version(ws, "ontology_v0", _records(broken=True))
    SemanticStore.set_active(ws, "ontology_v0")

    content = build_content_elements(SemanticStore.load(ws))
    assert len(content["warnings"]) >= 4
    assert any("rel_broken" in warning for warning in content["warnings"])

    node_ids = {node["data"]["id"] for node in content["nodes"]}
    relation_edge_ids = {edge["data"]["id"] for edge in content["edges"]
                         if edge["data"]["family"] == "relation"}
    for edge in content["edges"]:
        assert edge["data"]["source"] in node_ids | relation_edge_ids
        assert edge["data"]["target"] in node_ids
    assert "missing_term" not in node_ids


def test_every_evidence_node_is_connected(workspace):
    """每个 Evidence 节点必须挂进图谱：Term / Mapping / Constraint / Relation
    的 evidence 引用都要渲染成 supported_by 连线，不允许出现孤立证据节点。"""
    content = build_content_elements(SemanticStore.load(workspace))
    evidence_nodes = [node["data"]["id"] for node in content["nodes"]
                      if node["data"]["family"] == "evidence"]
    assert len(evidence_nodes) == 3
    degree = {}
    for edge in content["edges"]:
        for endpoint in (edge["data"]["source"], edge["data"]["target"]):
            degree[endpoint] = degree.get(endpoint, 0) + 1
    for node_id in evidence_nodes:
        assert degree.get(node_id, 0) >= 1, f"{node_id} is disconnected"


def test_relation_evidence_edges_are_renderable(workspace):
    """Cytoscape 不支持边挂到边上：source 为关系（rel:*）的 supported_by 边
    必须由模板重锚到隐形中点锚节点（mid:* / family=anchor）并随关系边中点同步，
    否则关系证据会被静默丢弃、退化成孤立节点。"""
    html = visualize(workspace=workspace, open_browser=False).read_text(encoding="utf-8")
    assert "familyByNode[anchorId] = 'anchor'" in html, "anchor nodes must be created for rel:* sources"
    assert "function syncAnchors()" in html, "anchors must track the relation edge midpoint"
    assert 'node[family="anchor"]' in html, "anchors must be excluded from layout/overlap passes"
    assert "get('lang')" in html, "language must be overridable via ?lang= for verification"


def test_content_edges_use_only_schema_defined_families(tmp_path):
    """Content 图只允许论文定义的 Semantic Relation 与 Structural Reference。

    同表落地不是 schema reference rule，不能为视觉连通性凭空生成第三类边。
    """
    records = _records()
    records["terms"].append({
        "id": "labor_cost_label", "name": "Labor Cost Label", "type": "dimension",
    })
    records["mappings"].append({
        "id": "mapping_labor_cost_label", "term_id": "labor_cost_label",
        "database_source": "financial_database", "table": "expense_statement",
        "column": "labor_label",
    })
    ws = ensure_workspace(tmp_path)
    save_project(_project("fixed_split"), ws)
    SemanticStore.save_version(ws, "ontology_v0", records)

    content = build_content_elements(SemanticStore.load_version(ws, "ontology_v0"))
    assert {edge["data"]["family"] for edge in content["edges"]} <= {
        "relation", "reference",
    }
    assert not any(edge["data"].get("kind") == "same_table"
                   for edge in content["edges"])
    reference_kinds = {
        edge["data"].get("kind") for edge in content["edges"]
        if edge["data"]["family"] == "reference"
    }
    assert reference_kinds <= {"grounded_by", "constrained_by", "supported_by"}
    assert any(edge["data"].get("kind") == "grounded_by" and
               "term:labor_cost_label" in (edge["data"]["source"], edge["data"]["target"])
               for edge in content["edges"])


def test_isolated_term_does_not_gain_a_fabricated_semantic_relation(tmp_path):
    """没有 Relation 记录的 Term 可通过结构引用接入图谱，但不能伪造语义边。"""
    records = _records()
    records["terms"].append({
        "id": "labor_cost_label", "name": "Labor Cost Label", "type": "dimension",
    })
    records["mappings"].append({
        "id": "mapping_labor_cost_label", "term_id": "labor_cost_label",
        "database_source": "financial_database", "table": "expense_statement",
        "column": "labor_label",
    })
    ws = ensure_workspace(tmp_path)
    save_project(_project("fixed_split"), ws)
    SemanticStore.save_version(ws, "ontology_v0", records)

    content = build_content_elements(SemanticStore.load_version(ws, "ontology_v0"))
    semantic_edges = [edge["data"] for edge in content["edges"]
                      if edge["data"]["family"] == "relation"]
    assert not any("term:labor_cost_label" in (edge["source"], edge["target"])
                   for edge in semantic_edges)
    assert any(edge["data"].get("kind") == "grounded_by" and
               edge["data"]["source"] == "term:labor_cost_label"
               for edge in content["edges"])


def test_term_nodes_show_semantics_only(workspace):
    """Term 卡片只展示语义名称；物理表列仍保留在 Mapping 与详情中。"""
    content = build_content_elements(SemanticStore.load(workspace))
    term = next(node["data"] for node in content["nodes"]
                if node["data"]["record_id"] == "labor_cost")
    assert term["label"] == "Labor Cost"
    assert "sublabel" not in term


# ---- 4/5. version resolution ------------------------------------------------

def test_active_version_resolves_active_json(workspace):
    assert resolve_version(workspace, "active") == "ontology_v0"
    output = visualize(workspace=workspace, open_browser=False)
    assert output == workspace / "visualizations" / VISUALIZATION_FILENAME
    assert output.is_file()


def test_visualize_discovers_one_workspace_at_arbitrary_nested_depth(tmp_path):
    container = tmp_path / ".evoontology"
    nested = container / "database_a" / "team_workspace"
    SemanticStore.save_version(nested, "ontology_v0", _records())
    SemanticStore.set_active(nested, "ontology_v0")

    output = visualize(workspace=container, open_browser=False)

    assert output == nested / "visualizations" / VISUALIZATION_FILENAME
    assert output.is_file()
    assert not (container / "visualizations").exists()


def test_visualize_accepts_project_root_containing_nested_workspace(tmp_path):
    project_root = tmp_path / "project"
    nested = project_root / ".evoontology" / "database_a" / "workspace_a"
    SemanticStore.save_version(nested, "ontology_v0", _records())
    SemanticStore.set_active(nested, "ontology_v0")

    output = visualize(workspace=project_root, open_browser=False)

    assert output == nested / "visualizations" / VISUALIZATION_FILENAME


def test_visualize_uses_explicit_version_to_disambiguate_nested_workspaces(tmp_path):
    container = tmp_path / ".evoontology"
    first = container / "database_a"
    second = container / "database_b" / "workspace_b"
    SemanticStore.save_version(first, "ontology_v0", _records())
    SemanticStore.set_active(first, "ontology_v0")
    SemanticStore.save_version(second, "ontology_v1", _records())
    SemanticStore.set_active(second, "ontology_v1")

    output = visualize(
        workspace=container, version="ontology_v1", open_browser=False
    )

    assert output == second / "visualizations" / VISUALIZATION_FILENAME


def test_visualize_rejects_ambiguous_nested_workspaces(tmp_path):
    container = tmp_path / ".evoontology"
    candidates = [container / "database_a", container / "database_b" / "workspace_b"]
    for candidate in candidates:
        SemanticStore.save_version(candidate, "ontology_v0", _records())
        SemanticStore.set_active(candidate, "ontology_v0")

    with pytest.raises(ValueError) as exc_info:
        visualize(workspace=container, open_browser=False)

    message = str(exc_info.value)
    assert "Multiple EvoOntology workspaces" in message
    assert all(str(candidate) in message for candidate in candidates)
    assert "Pass the exact workspace path" in message


def test_visualize_ignores_nested_active_pointer_without_target_version(tmp_path):
    container = tmp_path / ".evoontology"
    broken = ensure_workspace(container / "broken")
    (broken / "active.json").write_text(
        json.dumps({"active_version": "missing_version"}), encoding="utf-8"
    )
    valid = container / "database_a" / "workspace_a"
    SemanticStore.save_version(valid, "ontology_v0", _records())
    SemanticStore.set_active(valid, "ontology_v0")

    output = visualize(workspace=container, open_browser=False)

    assert output == valid / "visualizations" / VISUALIZATION_FILENAME


def test_explicit_version_renders_without_changing_active(workspace):
    SemanticStore.save_version(workspace, "ontology_v1", _records())
    active_before = json.loads((workspace / "active.json").read_text(encoding="utf-8"))

    output = visualize(workspace=workspace, version="ontology_v1", open_browser=False)

    assert output.name == VISUALIZATION_FILENAME
    assert output.is_file()
    html = output.read_text(encoding="utf-8")
    assert '"ontology_v0"' in html and '"ontology_v1"' in html
    assert '"initial_version":"ontology_v1"' in html
    active_after = json.loads((workspace / "active.json").read_text(encoding="utf-8"))
    assert active_after == active_before == {"active_version": "ontology_v0"}


def test_visualize_replaces_legacy_outputs(workspace):
    legacy_paths = [
        workspace / "visualizations" / "index.html",
        workspace / "visualizations" / "semantic-layer-explorer.html",
    ]
    legacy_paths[0].parent.mkdir(parents=True)
    for legacy in legacy_paths:
        legacy.write_text("stale visualization", encoding="utf-8")

    output = visualize(workspace=workspace, open_browser=False)

    assert output.name == VISUALIZATION_FILENAME
    assert output.is_file()
    assert all(not legacy.exists() for legacy in legacy_paths)


def test_one_html_contains_version_switch_and_compare(workspace):
    records_v1 = _records()
    records_v1["terms"] = [dict(item) for item in records_v1["terms"]]
    records_v1["terms"][0]["definition"] = "Changed definition"
    records_v1["terms"].append({"id": "profit", "name": "Profit", "type": "metric"})
    SemanticStore.save_version(workspace, "ontology_v1", records_v1)

    html = visualize(workspace=workspace, open_browser=False).read_text(encoding="utf-8")

    assert 'id="version-select"' in html
    assert 'id="btn-compare"' in html
    assert 'id="compare-version-select"' in html
    assert 'id="cy-compare"' in html
    assert "function switchVersion(" in html
    assert "function computeDiff(" in html
    assert "diff-added" in html and "diff-removed" in html and "diff-changed" in html
    assert 'class="compare-icon"' in html
    assert "'version.activeSuffix'" not in html
    assert "fillVersionSelect(compareSelect, selectedCompare, currentVersion)" in html
    assert 'id="chk-highlight-diff"' in html
    assert "function buildSchemaCompareView(" in html
    assert "function buildToolCompareView(" in html
    assert "function semanticEntries(" in html
    assert "if (d.family !== 'relation' || !d.comparison) return" in html
    assert "function showDiffDetails(" in html
    assert "chip.addEventListener('click', function () { showDiffDetails" in html
    assert "'width': 1.8, 'line-style': 'dotted', 'line-color': '#8f9bad'" in html
    assert "isList ? 'value-chip' : 'kv-text'" in html
    assert html.count('"schema":{"object_types"') == 2
    assert "function layoutTermHierarchy(" in html
    assert "function fitGraphToCenter(" in html
    assert "runLayout(true);" in html
    assert "function chooseCoreTerm(" in html
    assert "function buildTermAffinities(" in html
    assert "function visibleOwnedObjectCount(" in html
    assert "function satelliteBranchKey(" in html
    assert "function satelliteLayerRadius(" in html
    assert "function positionOnAssignedLayer(" in html
    assert "setLayeredPosition(node, radius, angle + nudge);" in html
    assert "termLayout.sectors[node.id()]" in html
    assert "var sharedGroups = {};" in html
    assert "function familyRingOffset(" in html
    assert "randomize: true" not in html
    assert "if (!layoutTermHierarchy())" in html
    assert "family === 'evidence' ? 375" in html
    assert "function avoidEdgeNodeCrossings(" in html
    assert "function settleGraphGeometry(" in html
    assert "'target-arrow-shape': 'triangle'" not in html
    assert "'target-arrow-shape': 'none'" in html
    assert "function stableManifestSignature(" in html
    assert "item.signature == null ? item.raw : item.signature" in html
    assert "Ontology layer version:|Objects:|Active constraints:" in html
    assert 'edge[kind="same_table"]' not in html
    assert "Same-table grounding" not in html
    assert "同表落地" not in html
    assert "switch between Chinese and English" in html
    assert "Switch between CN and English" not in html
    assert "Switch between 中文 and English" not in html
    assert "可在右上角或本指南中切换中文和英文" in html
    assert "右上角或本指南弹窗内可在 中文 与 英文 之间切换" not in html
    assert "var LANG_KEY" not in html
    assert "localStorage.getItem(LANG_KEY)" not in html
    assert "localStorage.setItem(LANG_KEY" not in html
    assert "return 'en'; // every new page open defaults to English" in html
    assert "'compare.added': '新增 {n} 项'" in html
    assert "'compare.removed': '删除 {n} 项'" in html
    assert "'compare.changed': '修改 {n} 项'" in html
    assert "'compare.details': '{status}明细'" in html
    assert "'filters.title': '显示内容'" in html
    assert "'action.fit': '适配视图'" in html
    assert "'search.matches': '找到 {n} 项'" in html
    assert "'status.warnings.title': '点击查看无法解析并已跳过的引用'" in html
    assert "'schema.relationTypes.belongsTo': '适用字段'" in html
    assert "'tool.manifest': '本体层清单（Layer Manifest）'" in html
    assert "'intro.text': '这是 Agent-first 本体层版本 {version} 的只读视图" in html
    assert "'welcome.subtitle': '探索面向 Data Agent 构建的 Agent-first 自进化本体层'" in html
    assert "该层围绕 Agent 的工作负载构建，通过 Agent 的 MCP 工具接口按需访问" in html
    assert "'intro.text': 'Read-only view of agent-first ontology-layer version {version}" in html
    assert "'welcome.subtitle': 'Explore an agent-first, self-evolving ontology layer for data agents'" in html
    assert "The layer is built around the agent\\'s workload, queried on demand through the agent\\'s MCP tool interface" in html
    assert "#version-select { width: 220px; max-width: 220px; font-size: 13px; }" in html
    assert "#compare-version-select { width: 210px; max-width: 210px; font-size: 13px; }" in html


def test_version_selectors_distinguish_published_layers_from_candidates(workspace):
    SemanticStore.save_version(workspace, "v0-c1", _records())

    html = visualize(workspace=workspace, open_browser=False).read_text(encoding="utf-8")

    assert "function versionKind(version)" in html
    assert "return /^(?:ontology|semantic)_v\\d+$/.test(version) ? 'published' : 'candidate';" in html
    assert "var group = document.createElement('optgroup');" in html
    assert "group.label = t('version.group.' + kind);" in html
    assert "option.textContent = versionOptionLabel(version);" in html
    assert "'version.group.published': 'Published ontology layers'" in html
    assert "'version.group.candidate': 'Evolution candidates'" in html
    assert "'version.kind.published': 'Published'" in html
    assert "'version.kind.candidate': 'Candidate'" in html
    assert "'version.group.published': '已发布本体层'" in html
    assert "'version.group.candidate': '进化候选版本'" in html
    assert "Version selectors group published ontology layers separately from evolution candidates" in html
    assert "版本选择器将已发布本体层与进化候选版本分组显示" in html


def test_bilingual_controls_use_stable_responsive_layout(workspace):
    html = visualize(workspace=workspace, open_browser=False).read_text(encoding="utf-8")

    assert 'class="header-context"' in html
    assert 'class="header-tools"' in html
    assert "grid-template-columns: minmax(0, 1fr) max-content;" in html
    assert "@media (max-width: 1360px)" in html
    assert "#intro-text { flex: 1; min-width: 0; line-height: 1.5; overflow-wrap: anywhere; }" in html
    assert "'search.placeholder': '搜索语义对象'" in html
    assert "'search.placeholder': 'Search semantic objects'" in html
    assert "#search-status:empty { display: none; }" in html
    assert ".kv-row { display: grid;" in html
    assert "grid-template-columns: minmax(96px, .38fr) minmax(0, 1fr);" in html
    assert ".detail-actions { display: flex; gap: 8px; flex-wrap: wrap;" in html
    assert ".welcome-list li { display: grid; grid-template-columns: 7px minmax(0, 1fr);" in html
    assert "#btn-compare-close { justify-self: end;" in html
    assert "#btn-reset { min-width: 112px; }" in html


def test_welcome_guide_covers_current_controls(workspace):
    html = visualize(workspace=workspace, open_browser=False).read_text(encoding="utf-8")

    assert "'welcome.compare.title': '版本与对比'" in html
    assert "本体内容、Schema 层和 Tool 层均支持并排对比" in html
    assert "点击新增、删除或修改统计可查看具体差异" in html
    assert "'welcome.tips.title': '浏览与操作'" in html
    assert "“适配视图”只缩放并居中当前可见内容" in html
    assert "“不再自动显示”只关闭后续开屏" in html
    assert "'welcome.compare.title': 'Versions and comparison'" in html
    assert "Ontology Content, Schema Layer and Tool Layer all support side-by-side comparison" in html
    assert "Highlight differences only changes visual emphasis" in html
    assert "'welcome.tips.title': 'Explore and control'" in html
    assert "Fit view only zooms and centers the currently visible content" in html
    assert "Don\\'t show this again only disables automatic opening" in html


def test_restore_initial_is_the_only_layout_recovery_control(workspace):
    html = visualize(workspace=workspace, open_browser=False).read_text(encoding="utf-8")
    assert 'id="btn-layout"' not in html
    assert "action.layout" not in html
    assert "layoutDirty" not in html
    assert "setLayoutDirty" not in html
    assert "updateLayoutAction" not in html
    reset_body = html.split("function resetView()", 1)[1].split(
        "/* ================= graph: interactions", 1
    )[0]
    assert "familyVisible = { mapping: true, constraint: true, evidence: true };" in reset_body
    assert "input.value = '';" in reset_body
    assert "cy.elements(':selected').unselect();" in reset_body
    assert "runLayout(true);" in reset_body
    assert "'action.reset': '恢复初始'" in html
    assert "'action.reset': 'Restore initial'" in html


# ---- 6. generated HTML is offline and self-contained ------------------------

def test_generated_html_is_offline(workspace):
    html = visualize(workspace=workspace, open_browser=False).read_text(encoding="utf-8")
    assert "Copyright (c) 2016-2024, The Cytoscape Consortium." in html
    assert "3.30.2" in html
    assert "Labor Cost" in html and "mapping_labor_cost" in html
    assert "window.EVO_DATA" in html
    for marker in ("<script src", "cdn.jsdelivr", "unpkg.com", "cdnjs."):
        assert marker not in html


# ---- 7. initial build without evolution history -----------------------------

def test_initial_build_without_evolution_renders(workspace):
    html = visualize(workspace=workspace, open_browser=False).read_text(encoding="utf-8")
    assert "Labor Cost" in html
    assert '"evolution"' not in html


# ---- 8. both project modes render -------------------------------------------

def test_fixed_split_and_rolling_trajectory_both_render(tmp_path):
    for index, mode in enumerate(("fixed_split", "rolling_trajectory")):
        ws = ensure_workspace(tmp_path / mode)
        save_project(_project(mode), ws)
        SemanticStore.save_version(ws, "ontology_v0", _records())
        SemanticStore.set_active(ws, "ontology_v0")
        output = visualize(workspace=ws, open_browser=False)
        assert output.is_file(), f"{mode} failed to render"
        assert "Labor Cost" in output.read_text(encoding="utf-8")


# ---- schema / tool views ------------------------------------------------------

def test_schema_view_reflects_core_model():
    schema = build_schema_view()
    names = [object_type["name"] for object_type in schema["object_types"]]
    assert names == ["Term", "Mapping", "Constraint", "Evidence"]
    term_fields = next(t["fields"] for t in schema["object_types"] if t["name"] == "Term")
    assert {"id", "name", "type", "definition", "scope", "aliases"} <= set(term_fields)
    assert [r["name"] for r in schema["relation_types"]] == [
        "association", "hierarchy", "composition", "equivalence", "derivation"]
    assert {rule["name"] for rule in schema["reference_rules"]} == {
        "grounded_by", "constrained_by", "supported_by"}
    assert "source" in schema["relation_record"]["fields"]
    relation_types = {item["name"]: item for item in schema["relation_types"]}
    assert (relation_types["hierarchy"]["source_role"],
            relation_types["hierarchy"]["target_role"]) == (
                "parent_broader", "child_narrower")
    assert (relation_types["composition"]["source_role"],
            relation_types["composition"]["target_role"]) == (
                "whole_parent", "part_child")
    assert (relation_types["derivation"]["source_role"],
            relation_types["derivation"]["target_role"]) == (
                "input_base", "derived_result")
    assert "source_role" not in relation_types["association"]
    assert "source_role" not in relation_types["equivalence"]


def test_legend_counts_follow_rendered_graph_elements(tmp_path):
    """图例统计必须按实际渲染进图谱的元素（DATA.content）汇总。

    存在坏引用时，记录会被跳过且不进入图谱；此时图例若继续按版本记录总数
    （meta.counts）统计就会虚高，与图中可见对象不一致。
    """
    ws = ensure_workspace(tmp_path)
    save_project(_project("fixed_split"), ws)
    SemanticStore.save_version(ws, "ontology_v0", _records(broken=True))
    SemanticStore.set_active(ws, "ontology_v0")
    html = visualize(workspace=ws, open_browser=False).read_text(encoding="utf-8")
    assert "nodeCountByFamily" in html, "legend must count rendered nodes per family"
    assert "relationEdgeCount" in html, "legend must count rendered relation edges"
    assert "counts[family + 's']" not in html, "legend must not use store record counts"


def test_schema_view_nests_relation_types_under_relation(workspace):
    """语义关系类型是 Relation 记录的 relation_type 字段取值，必须嵌套在
    Relation 小节内展示，而不是与 Relation 记录并列的独立小节。"""
    html = visualize(workspace=workspace, open_browser=False).read_text(encoding="utf-8")
    assert "Relation（语义关系）" in html
    assert "relation_type 的受控取值" in html
    assert "'语义关系类型'" not in html, "relation types must not stay a standalone section"
    assert ".link-chip::before" not in html, "navigable values must not show a misleading arrow"
    assert "function relationDirectionLabel(" in html
    assert "'schema.direction.flow': 'source（{source}）→ target（{target}）'" in html
    assert "'schema.role.parent_broader': '父级 / 上位概念'" in html
    assert "'schema.role.whole_parent': 'whole / parent'" in html
    assert '"source_role":"input_base","target_role":"derived_result"' in html
    assert "[t('schema.direction'), relationDirectionLabel(item)]" in html


def test_tool_view_uses_real_registry(workspace):
    tools = build_tool_view(SemanticStore.load(workspace))
    assert [tool["name"] for tool in tools["tools"]] == [
        "browse_semantics", "resolve_semantics"]
    assert all(tool["description"] for tool in tools["tools"])
    assert "Ontology layer version: ontology_v0" in tools["manifest"]


# ---- plugin smoke test ---------------------------------------------------------

def test_plugin_entry_points_call_same_core_api():
    claude_skill = (REPO_ROOT / "plugins" / "claude-code" /
                    "skills" / "explore-ontology" / "SKILL.md")
    codex_skill = (REPO_ROOT / "plugins" / "evoontology-codex" /
                   "skills" / "explore-ontology" / "SKILL.md")
    assert claude_skill.is_file() and codex_skill.is_file()

    claude_text = claude_skill.read_text(encoding="utf-8")
    codex_text = codex_skill.read_text(encoding="utf-8")
    assert "visualize_ontology" in claude_text
    assert "visualize_ontology" in codex_text
    forbidden_invocation = "python -m evoontology.visualization"
    assert "Do not run" in claude_text and forbidden_invocation in claude_text
    assert "Do not run" in codex_text and forbidden_invocation in codex_text
    assert "name: explore-ontology" in claude_text
    assert "name: explore-ontology" in codex_text


# ---- read-only guarantee --------------------------------------------------------

def test_visualize_is_read_only(workspace):
    def snapshot():
        state = {}
        for path in sorted(workspace.rglob("*")):
            if path.is_file() and "visualizations" not in path.parts:
                state[path.relative_to(workspace).as_posix()] = path.read_bytes()
        return state

    before = snapshot()
    visualize(workspace=workspace, open_browser=False)
    visualize(workspace=workspace, version="ontology_v0", open_browser=False)
    assert snapshot() == before


# ---- error behavior --------------------------------------------------------------

def test_unloadable_version_does_not_break_the_explorer(workspace):
    broken = Path(workspace) / "versions" / "v0-c1"
    broken.mkdir()
    (broken / "terms.json").write_text("[]", encoding="utf-8")

    path = visualize(workspace=workspace, open_browser=False)

    html = path.read_text(encoding="utf-8")
    start = html.index("window.EVO_DATA = ") + len("window.EVO_DATA = ")
    data = json.loads(html[start:html.index(";\n</script>", start)])
    assert "v0-c1" not in data["meta"]["versions"]
    assert "v0-c1" not in data["versions"]
    active = data["meta"]["active_version"]
    assert any("v0-c1" in warning for warning in data["versions"][active]["meta"]["warnings"])
    with pytest.raises(FileNotFoundError):
        visualize(workspace=workspace, version="v0-c1", open_browser=False)


def test_missing_workspace_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="workspace not initialized"):
        visualize(workspace=tmp_path / "nowhere", open_browser=False)


def test_missing_active_version_error(tmp_path):
    ws = ensure_workspace(tmp_path)
    save_project(_project("fixed_split"), ws)
    SemanticStore.save_version(ws, "ontology_v0", _records())
    with pytest.raises(FileNotFoundError, match="No active ontology version"):
        visualize(workspace=ws, open_browser=False)


def test_missing_explicit_version_error(workspace):
    with pytest.raises(FileNotFoundError, match="Ontology version 'ontology_v9' not found"):
        visualize(workspace=workspace, version="ontology_v9", open_browser=False)
    assert not (workspace / "visualizations" / "ontology_v9.html").exists()
