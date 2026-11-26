"""Read-only ontology visualization: all ontology versions -> one HTML explorer.

Pipeline::

    ontology versions
        -> per-version content / tool views + shared schema view
        -> render_html (template.html + vendored cytoscape.min.js)
        -> <workspace>/visualizations/ontology-layer-explorer.html

The module is strictly read-only for semantic state: records are loaded through
``SemanticStore`` and filesystem changes are confined to generated HTML under
``visualizations/``. ``active.json``, versions, trajectories, and evolution
records are never modified.
"""

from __future__ import annotations

import dataclasses
import json
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..ontology.models import Constraint, Evidence, Mapping, Relation, Term
from ..ontology.store import SemanticStore
from ..runtime.runtime import SemanticLayer
from ..runtime.tools import TOOLS
from ..workspace import (
    PathLike,
    resolve_workspace_for_version,
    validate_path_component,
)
from ..workflow import build_experience

ACTIVE = "active"
VISUALIZATIONS_DIRNAME = "visualizations"
VISUALIZATION_FILENAME = "ontology-layer-explorer.html"
LEGACY_VISUALIZATION_FILENAMES = ("index.html", "semantic-layer-explorer.html")
CYTOSCAPE_VERSION = "3.30.2"

#: The five controlled relation values defined by ``ontology-schema.md``.
RELATION_TYPES = (
    "association",
    "hierarchy",
    "composition",
    "equivalence",
    "derivation",
)

_PACKAGE_DIR = Path(__file__).resolve().parent

_SCHEMA_OBJECTS = (
    ("Term", Term, "Reusable analytical concept: business concept, metric, entity, dimension, or category."),
    ("Mapping", Mapping, "Grounds a Term to physical structures in the underlying data environment."),
    ("Constraint", Constraint, "Conditions required for correct interpretation and usage of semantic objects."),
    ("Evidence", Evidence, "Reproducible observations from the data environment that support semantic claims."),
)

_RELATION_SEMANTICS = {
    "association": "Concept-level relatedness that does not fit a stronger relation type.",
    "hierarchy": "Directed broader/narrower (parent/child) structure between Terms.",
    "composition": "Directed part-of relationship between Terms.",
    "equivalence": "Symmetric same-meaning relationship between Terms.",
    "derivation": "Directed relationship where one Term is computed from another.",
}

_DIRECTED_RELATION_ROLES = {
    "hierarchy": ("parent_broader", "child_narrower"),
    "composition": ("whole_parent", "part_child"),
    "derivation": ("input_base", "derived_result"),
}

_REFERENCE_RULES = (
    ("grounded_by", "Term", "Mapping", "A Mapping grounds its Term to database structures (table, column, path)."),
    ("constrained_by", "Term / Mapping", "Constraint", "A Constraint governs how its target object must be interpreted."),
    ("supported_by", "Term / Mapping / Relation", "Evidence", "Evidence supports the semantic claim of the object it is attached to."),
)


# ---- public API -------------------------------------------------------------


def visualize(
    workspace: Optional[PathLike] = None,
    version: str = ACTIVE,
    open_browser: bool = True,
) -> Path:
    """Render all ontology versions to one standalone HTML file and return its path.

    ``version="active"`` follows ``active.json``; an explicit version selects
    the initially visible version without touching ``active.json``. Every
    version under ``versions/`` is embedded so the page can switch and compare
    versions offline. Containers and project roots resolve to one matching nested
    workspace. The stable output is
    ``<resolved-workspace>/visualizations/ontology-layer-explorer.html``.
    """
    root = resolve_workspace_for_version(workspace, version=version)
    if not root.is_dir():
        raise FileNotFoundError("EvoOntology workspace not initialized.")
    selected = resolve_version(root, version)
    active_version: Optional[str] = None
    try:
        active_version = SemanticStore.active_version(root)
    except (FileNotFoundError, ValueError):
        active_version = None

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    schema_snapshot = build_schema_view()
    available_versions: List[str] = []
    unloadable: List[str] = []
    version_data: Dict[str, Any] = {}
    for version_name in SemanticStore.list_versions(root):
        try:
            store = load_ontology(root, version_name)
        except Exception as exc:
            # One unloadable version (for example an incomplete or rejected
            # Candidate) must not hide every other version from the explorer.
            if version_name == selected:
                raise
            unloadable.append(f"version {version_name!r} skipped: could not be loaded ({exc})")
            continue
        available_versions.append(version_name)
        content = build_content_elements(store)
        version_data[version_name] = {
            "meta": {
                "version": version_name,
                "active": version_name == active_version,
                "counts": store.counts(),
                "warnings": content["warnings"],
                "generated_at": generated_at,
            },
            "content": {"nodes": content["nodes"], "edges": content["edges"]},
            "schema": schema_snapshot,
            "tools": build_tool_view(store),
            "experience": build_experience(root, version_name),
        }
    for entry in version_data.values():
        entry["meta"]["warnings"].extend(unloadable)

    data = {
        "meta": {
            "initial_version": selected,
            "active_version": active_version,
            "versions": available_versions,
            "generated_at": generated_at,
        },
        "versions": version_data,
    }

    out_dir = root / VISUALIZATIONS_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / VISUALIZATION_FILENAME
    out_path.write_text(render_html(data), encoding="utf-8")
    for legacy_filename in LEGACY_VISUALIZATION_FILENAMES:
        legacy_path = out_dir / legacy_filename
        if legacy_path.exists():
            legacy_path.unlink()

    if open_browser:
        try:
            webbrowser.open(out_path.resolve().as_uri())
        except Exception:
            pass  # visualization output stays valid without a browser
    return out_path


# ---- pipeline steps ---------------------------------------------------------


def resolve_version(root: PathLike, version: Union[str, None]) -> str:
    """Resolve ``"active"`` through ``active.json``; check explicit versions."""
    requested = str(version or ACTIVE).strip() or ACTIVE
    root = Path(root)
    if requested == ACTIVE:
        try:
            return SemanticStore.active_version(root)
        except (FileNotFoundError, ValueError) as exc:
            raise FileNotFoundError("No active ontology version.") from exc
    requested = validate_path_component(requested, label="version")
    if not (root / "versions" / requested).is_dir():
        raise FileNotFoundError(f"Ontology version '{requested}' not found.")
    return requested


def load_ontology(root: PathLike, version: str) -> SemanticStore:
    """Load one version read-only (never changes ``active.json``)."""
    return SemanticStore.load_version(root, version)


def build_content_elements(store: SemanticStore) -> Dict[str, Any]:
    """Convert the Content Layer into Cytoscape nodes/edges.

    Node families: Term / Mapping / Constraint / Evidence. Edge families:
    Semantic Relations (Relation records between Terms) and Structural
    References (grounded_by / constrained_by / supported_by). Broken
    references are reported as warnings; nothing is fabricated.
    """
    warnings: List[str] = []
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    used_ids: set = set()

    mappings_by_term: Dict[str, List[str]] = {}
    for mapping in store.mappings.values():
        if mapping.term_id:
            mappings_by_term.setdefault(mapping.term_id, []).append(mapping.id)
    relations_by_term: Dict[str, List[str]] = {}
    for relation in store.relations.values():
        for end in {relation.source, relation.target}:
            if end:
                relations_by_term.setdefault(end, []).append(relation.id)
    constraints_by_target: Dict[str, List[str]] = {}
    for constraint in store.constraints.values():
        if constraint.target:
            constraints_by_target.setdefault(constraint.target, []).append(constraint.id)
    term_names = {t.id: (t.name or t.id) for t in store.terms.values()}

    def _nid(family: str, record_id: str) -> str:
        # Prefix ids so record ids that collide with Object.prototype keys
        # (e.g. a Term named "constructor") cannot poison Cytoscape internals.
        return f"{family}:{record_id}"

    def _add_node(record_id: str, family: str, label: str, search: List[str],
                  detail: List[List[Any]], comparison: Dict[str, Any],
                  extra: Optional[Dict[str, Any]] = None) -> None:
        if record_id in used_ids:
            warnings.append(f"duplicate object id skipped: {record_id!r}")
            return
        used_ids.add(record_id)
        data = {
            "id": _nid(family, record_id),
            "record_id": record_id,
            "family": family,
            "label": label,
            "search": sorted({s.lower() for s in search if s}),
            "detail": detail,
            "comparison": comparison,
        }
        if extra:
            data.update(extra)
        nodes.append({"data": data})

    def _add_edge(edge_id: str, family: str, source: str, target: str,
                  extra: Dict[str, Any]) -> None:
        data = {"id": edge_id, "family": family, "source": source, "target": target}
        data.update(extra)
        edges.append({"data": data})

    for term in store.terms.values():
        _add_node(term.id, "term", term.name or term.id,
                  [term.name, term.id, *term.aliases],
                  _pairs([
                      ("Name", term.name),
                      ("ID", term.id),
                      ("Type", term.type),
                      ("Definition", term.definition),
                      ("Scope", term.scope),
                      ("Aliases", term.aliases),
                      ("Mappings", sorted(mappings_by_term.get(term.id, []))),
                      ("Relations", sorted(relations_by_term.get(term.id, []))),
                      ("Constraints", sorted(constraints_by_target.get(term.id, []))),
                      ("Evidence", term.evidence_refs),
                      ("Lifecycle", term.lifecycle_state),
                  ]), dataclasses.asdict(term),
                  {"definition": term.definition or ""})

    for mapping in store.mappings.values():
        grounding = ".".join(part for part in (mapping.table, mapping.column) if part)
        _add_node(mapping.id, "mapping", grounding or mapping.id,
                  [mapping.id, grounding, mapping.table, mapping.column,
                   term_names.get(mapping.term_id, "")],
                  _pairs([
                      ("ID", mapping.id),
                      ("Related Term", term_names.get(mapping.term_id, "") if mapping.term_id else ""),
                      ("Data source", mapping.database_source),
                      ("Table", mapping.table),
                      ("Column", mapping.column),
                      ("Semantic Filter", mapping.semantic_filter),
                      ("Aggregation", mapping.aggregation_semantics),
                      ("Grain", mapping.grain),
                      ("Validation", mapping.validation),
                      ("Confidence", mapping.confidence_level),
                      ("Evidence", mapping.evidence_refs),
                      ("Lifecycle", mapping.lifecycle_state),
                  ]), dataclasses.asdict(mapping))

    for constraint in store.constraints.values():
        _add_node(constraint.id, "constraint", constraint.constraint_type or constraint.id,
                  [constraint.id, constraint.constraint_type, constraint.description],
                  _pairs([
                      ("ID", constraint.id),
                      ("Target", constraint.target),
                      ("Type", constraint.constraint_type),
                      ("Trigger Keywords", constraint.trigger_keywords),
                      ("Severity", constraint.severity),
                      ("Scope", constraint.scope),
                      ("Confidence", constraint.confidence_level),
                      ("Description", constraint.description),
                      ("Evidence", constraint.evidence_refs),
                      ("Lifecycle", constraint.lifecycle_state),
                  ]), dataclasses.asdict(constraint))

    for evidence in store.evidence.values():
        _add_node(evidence.id, "evidence", evidence.id,
                  [evidence.id, evidence.source],
                  _pairs([
                      ("ID", evidence.id),
                      ("Source", evidence.source),
                      ("Query", evidence.query),
                      ("Result", evidence.result),
                      ("Validation Method", evidence.validation_method),
                      ("Timestamp", evidence.timestamp),
                  ]), dataclasses.asdict(evidence))

    # Semantic Relation edges: Relation records between Terms.
    for relation in store.relations.values():
        if relation.source in store.terms and relation.target in store.terms:
            relation_type = relation.relation_type or "association"
            _add_edge(f"rel:{relation.id}", "relation",
                      _nid("term", relation.source), _nid("term", relation.target), {
                "relation_type": relation_type,
                "record_id": relation.id,
                "label": relation_type,
                "detail": _pairs([
                    ("ID", relation.id),
                    ("Type", relation.relation_type),
                    ("Source", term_names.get(relation.source, relation.source)),
                    ("Target", term_names.get(relation.target, relation.target)),
                    ("Connection", relation.connection_condition),
                    ("Description", relation.description),
                    ("Evidence", relation.evidence_refs),
                    ("Lifecycle", relation.lifecycle_state),
                ]),
                "comparison": dataclasses.asdict(relation),
            })
        else:
            warnings.append(
                f"relation {relation.id}: unresolved term reference, edge skipped "
                f"(source={relation.source!r}, target={relation.target!r})"
            )

    # Structural Reference edges: induced by schema-defined object references.
    for mapping in store.mappings.values():
        if not mapping.term_id:
            continue
        if mapping.term_id in store.terms:
            _add_edge(f"ref:grounded_by:{mapping.term_id}:{mapping.id}", "reference",
                      _nid("term", mapping.term_id), _nid("mapping", mapping.id), {
                          "kind": "grounded_by",
                          "detail": _pairs([
                              ("Reference", "grounded_by"),
                              ("Term", term_names.get(mapping.term_id, mapping.term_id)),
                              ("Mapping", mapping.id),
                              ("Grounding", ".".join(p for p in (mapping.table, mapping.column) if p)),
                          ]),
                      })
        else:
            warnings.append(
                f"mapping {mapping.id}: unresolved term_id {mapping.term_id!r}, reference skipped"
            )

    for constraint in store.constraints.values():
        if not constraint.target:
            continue
        if constraint.target in store.terms or constraint.target in store.mappings:
            target_family = "term" if constraint.target in store.terms else "mapping"
            _add_edge(f"ref:constrained_by:{constraint.target}:{constraint.id}", "reference",
                      _nid(target_family, constraint.target), _nid("constraint", constraint.id), {
                          "kind": "constrained_by",
                          "detail": _pairs([
                              ("Reference", "constrained_by"),
                              ("Object", constraint.target),
                              ("Constraint", constraint.id),
                              ("Severity", constraint.severity),
                              ("Description", constraint.description),
                          ]),
                      })
        else:
            warnings.append(
                f"constraint {constraint.id}: unresolved target {constraint.target!r}, reference skipped"
            )

    evidence_ids = set(store.evidence)
    for owner in (*store.terms.values(), *store.mappings.values()):
        for ref in owner.evidence_refs:
            if ref in evidence_ids:
                owner_family = "term" if owner.id in store.terms else "mapping"
                _add_edge(f"ref:supported_by:{owner.id}:{ref}", "reference",
                          _nid(owner_family, owner.id), _nid("evidence", ref), {
                              "kind": "supported_by",
                              "detail": _pairs([
                                  ("Reference", "supported_by"),
                                  ("Object", owner.id),
                                  ("Evidence", ref),
                              ]),
                          })
            else:
                warnings.append(
                    f"{owner.id}: unresolved evidence reference {ref!r}, reference skipped"
                )

    for constraint in store.constraints.values():
        for ref in constraint.evidence_refs:
            if ref in evidence_ids:
                _add_edge(f"ref:supported_by:con:{constraint.id}:{ref}", "reference",
                          _nid("constraint", constraint.id), _nid("evidence", ref), {
                              "kind": "supported_by",
                              "detail": _pairs([
                                  ("Reference", "supported_by"),
                                  ("Object", constraint.id),
                                  ("Evidence", ref),
                              ]),
                          })
            else:
                warnings.append(
                    f"constraint {constraint.id}: unresolved evidence reference {ref!r}, "
                    "reference skipped"
                )

    for relation in store.relations.values():
        if relation.source not in store.terms or relation.target not in store.terms:
            continue  # unresolved endpoints already warned above
        for ref in relation.evidence_refs:
            if ref in evidence_ids:
                _add_edge(f"ref:supported_by:rel:{relation.id}:{ref}", "reference",
                          f"rel:{relation.id}", _nid("evidence", ref), {
                              "kind": "supported_by",
                              "owner_terms": [_nid("term", relation.source),
                                              _nid("term", relation.target)],
                              "detail": _pairs([
                                  ("Reference", "supported_by"),
                                  ("Object", relation.id),
                                  ("Evidence", ref),
                              ]),
                          })
            else:
                warnings.append(
                    f"relation {relation.id}: unresolved evidence reference {ref!r}, "
                    "reference skipped"
                )

    return {"nodes": nodes, "edges": edges, "warnings": warnings}


def build_schema_view() -> Dict[str, Any]:
    """Describe the current Schema Layer from the Core model definitions."""
    return {
        "object_types": [
            {"name": name, "description": description,
             "fields": [f.name for f in dataclasses.fields(model)]}
            for name, model, description in _SCHEMA_OBJECTS
        ],
        "relation_record": {
            "name": "Relation",
            "description": "Serialized form of a Semantic Relation edge between Terms.",
            "fields": [f.name for f in dataclasses.fields(Relation)],
        },
        "relation_types": [
            {
                "name": name,
                "directed": name in _DIRECTED_RELATION_ROLES,
                "description": _RELATION_SEMANTICS[name],
                **({
                    "source_role": _DIRECTED_RELATION_ROLES[name][0],
                    "target_role": _DIRECTED_RELATION_ROLES[name][1],
                } if name in _DIRECTED_RELATION_ROLES else {}),
            }
            for name in RELATION_TYPES
        ],
        "reference_rules": [
            {"name": name, "from": source, "to": target, "description": description}
            for name, source, target, description in _REFERENCE_RULES
        ],
    }


def build_tool_view(store: SemanticStore) -> Dict[str, Any]:
    """Describe the current Tool Layer from the real runtime/tool registry."""
    layer = SemanticLayer(store)
    tools = []
    for spec in TOOLS:
        schema = spec.get("inputSchema", {})
        required = set(schema.get("required", []))
        tools.append({
            "name": spec.get("name", ""),
            "description": spec.get("description", ""),
            "parameters": [
                f"{name}{'*' if name in required else ''}"
                for name in schema.get("properties", {})
            ],
        })
    return {"manifest": layer.manifest(), "tools": tools}


def render_html(data: Dict[str, Any]) -> str:
    """Inline Cytoscape.js, CSS, data, and interaction JS into one HTML file."""
    template = _read_package_text("template.html")
    cytoscape_js = _read_package_text(Path("vendor") / "cytoscape.min.js")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")  # keep </script> safe inside JSON
    title = "EvoOntology · Ontology Layer Explorer"
    return (
        template
        .replace("__TITLE__", title)
        .replace("__CYTOSCAPE_JS__", cytoscape_js)
        .replace("__DATA_JSON__", payload)
    )


# ---- internals --------------------------------------------------------------


def _pairs(items) -> List[List[Any]]:
    """Build detail-panel rows, hiding absent fields."""
    pairs: List[List[Any]] = []
    for key, value in items:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple)):
            if not value:
                continue
            value = [str(item) for item in value]
        pairs.append([key, value])
    return pairs


def _read_package_text(relative: Union[str, Path]) -> str:
    return (_PACKAGE_DIR / relative).read_text(encoding="utf-8")
