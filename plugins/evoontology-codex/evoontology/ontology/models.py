"""Canonical serialized record types for the productized ontology layer.

The five record types follow ``ontology-schema.md``: Term, Mapping, Relation,
Constraint, and Evidence. Sample stores serialize ``evidence`` as a list of
evidence ids and keep mapping evidence under ``validation.evidence``; both
shapes are accepted so cross-record references survive loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


def _evidence_refs(data: Dict[str, Any]) -> List[str]:
    """Return evidence ids from a dict or list ``evidence`` field."""
    raw = data.get("evidence", [])
    if isinstance(raw, dict):
        return [str(e) for e in raw.get("source_refs", [])]
    if isinstance(raw, list):
        return [str(e) for e in raw]
    return []


def _mapping_evidence_refs(data: Dict[str, Any]) -> List[str]:
    """Return mapping evidence ids from ``evidence_refs`` / ``validation.evidence`` / ``evidence``."""
    refs = data.get("evidence_refs", [])
    if refs:
        return [str(e) for e in refs]
    validation = data.get("validation", {})
    if isinstance(validation, dict):
        val_evidence = validation.get("evidence", [])
        if isinstance(val_evidence, list) and val_evidence:
            return [str(e) for e in val_evidence]
    return _evidence_refs(data)


def _lifecycle_state(data: Dict[str, Any]) -> str:
    """Read an optional nested ``lifecycle.state``, defaulting to active."""
    lifecycle = data.get("lifecycle", {})
    if isinstance(lifecycle, dict):
        return str(lifecycle.get("state", "active"))
    return "active"


def _confidence_level(data: Dict[str, Any]) -> str:
    """Read ``confidence`` as either a string ("high") or a nested object."""
    raw = data.get("confidence", "medium")
    if isinstance(raw, dict):
        return str(raw.get("level", "medium"))
    return str(raw or "medium")


_SEVERITY_NORMALIZE = {"warning": "warn", "block": "block", "info": "info", "warn": "warn"}


@dataclass(frozen=True)
class Term:
    id: str
    name: str = ""
    type: str = ""
    definition: str = ""
    scope: str = ""
    aliases: List[str] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    lifecycle_state: str = "active"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Term":
        return cls(
            id=data["id"],
            name=str(data.get("name", data.get("label", ""))),
            type=str(data.get("type", data.get("kind", ""))),
            definition=str(data.get("definition", "")),
            scope=str(data.get("scope", "")),
            aliases=list(data.get("aliases", [])),
            evidence_refs=_evidence_refs(data),
            lifecycle_state=_lifecycle_state(data),
        )


@dataclass(frozen=True)
class Mapping:
    id: str
    term_id: str = ""
    database_source: str = ""
    table: str = ""
    column: str = ""
    semantic_filter: str = ""
    aggregation_semantics: str = ""
    grain: str = ""
    validation: str = ""
    evidence_refs: List[str] = field(default_factory=list)
    lifecycle_state: str = "active"
    confidence_level: str = "medium"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Mapping":
        validation = data.get("validation", "")
        confidence_raw = data.get("confidence")
        if isinstance(validation, dict):
            validation_text = str(validation.get("description", ""))
            if validation.get("confidence") is not None:
                confidence_raw = validation.get("confidence")
        else:
            validation_text = str(validation or "")
        return cls(
            id=data["id"],
            term_id=str(data.get("term_id", data.get("target_term", ""))),
            database_source=str(data.get("database_source", "")),
            table=str(data.get("table", "")),
            column=str(data.get("column", "")),
            semantic_filter=str(data.get("semantic_filter", data.get("filter", ""))),
            aggregation_semantics=str(data.get("aggregation_semantics", "")),
            grain=str(data.get("grain", "")),
            validation=validation_text,
            evidence_refs=_mapping_evidence_refs(data),
            lifecycle_state=_lifecycle_state(data),
            confidence_level=_confidence_level({"confidence": confidence_raw}),
        )


@dataclass(frozen=True)
class Relation:
    id: str
    source: str = ""
    relation_type: str = ""
    target: str = ""
    connection_condition: str = ""
    description: str = ""
    evidence_refs: List[str] = field(default_factory=list)
    lifecycle_state: str = "active"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Relation":
        return cls(
            id=data["id"],
            source=str(data.get("source", data.get("from_term", ""))),
            relation_type=str(data.get("relation_type", data.get("type", ""))),
            target=str(data.get("target", data.get("to_term", ""))),
            connection_condition=str(data.get("connection_condition", "")),
            description=str(data.get("description", "")),
            evidence_refs=_evidence_refs(data),
            lifecycle_state=_lifecycle_state(data),
        )


@dataclass(frozen=True)
class Constraint:
    id: str
    target: str = ""
    constraint_type: str = ""
    trigger_keywords: List[str] = field(default_factory=list)
    severity: str = "warn"
    scope: str = ""
    confidence_level: str = "medium"
    description: str = ""
    evidence_refs: List[str] = field(default_factory=list)
    lifecycle_state: str = "active"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Constraint":
        severity = str(data.get("severity", "warn")).lower()
        return cls(
            id=data["id"],
            target=str(data.get("target", data.get("term_id", ""))),
            constraint_type=str(data.get("constraint_type", data.get("type", ""))),
            trigger_keywords=list(data.get("trigger_keywords", [])),
            severity=_SEVERITY_NORMALIZE.get(severity, "warn"),
            scope=str(data.get("scope", "")),
            confidence_level=_confidence_level(data),
            description=str(data.get("description", "")),
            evidence_refs=_evidence_refs(data),
            lifecycle_state=_lifecycle_state(data),
        )


@dataclass(frozen=True)
class Evidence:
    id: str
    source: str = ""
    query: str = ""
    result: str = ""
    validation_method: str = ""
    timestamp: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Evidence":
        return cls(
            id=data["id"],
            source=str(data.get("source", "")),
            query=str(data.get("query", "")),
            result=str(data.get("result", "")),
            validation_method=str(data.get("validation_method", "")),
            timestamp=str(data.get("timestamp", "")),
        )
