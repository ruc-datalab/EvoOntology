"""Versioned ontology store: load, save, publish candidates, and switch active version.

A store root (workspace) contains ``active.json`` (``{"active_version": "<v>"}``)
and a ``versions/<v>/`` directory holding the five serialized record files:
``terms.json``, ``mappings.json``, ``relations.json``, ``constraints.json``, and
``evidence.json``. ``active.json`` also accepts the legacy ``version`` field for
backward compatibility with pre-``.evoontology`` workspaces.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Union

from ..workspace import ensure_workspace, resolve_workspace, validate_path_component
from .models import Constraint, Evidence, Mapping, Relation, Term

PathLike = Union[str, Path]

# The five serialized record files under ``versions/<v>/``.
VERSION_FILES = (
    "terms.json",
    "mappings.json",
    "relations.json",
    "constraints.json",
    "evidence.json",
)

_FAMILIES = {
    "terms": ("terms.json", Term),
    "relations": ("relations.json", Relation),
    "mappings": ("mappings.json", Mapping),
    "constraints": ("constraints.json", Constraint),
    "evidence": ("evidence.json", Evidence),
}


class SemanticStore:
    """In-memory view of the semantic version selected by ``active.json``."""

    def __init__(
        self,
        version: str,
        terms: Dict[str, Term],
        relations: Dict[str, Relation],
        mappings: Dict[str, Mapping],
        constraints: Dict[str, Constraint],
        evidence: Dict[str, Evidence],
        root_dir: str = "",
    ):
        self.version = version
        self.terms = terms
        self.relations = relations
        self.mappings = mappings
        self.constraints = constraints
        self.evidence = evidence
        self.root_dir = root_dir

    # ---- loading -----------------------------------------------------------

    @classmethod
    def load(cls, path: Optional[PathLike] = None) -> "SemanticStore":
        """Load the semantic version selected by ``active.json`` under ``path``."""
        workspace = resolve_workspace(path)
        return cls.load_version(workspace, cls.active_version(workspace))

    @classmethod
    def load_version(cls, path: Optional[PathLike], version: str) -> "SemanticStore":
        """Load a named version without changing ``active.json``."""
        workspace = resolve_workspace(path)
        version, records = cls.load_records(workspace, version=version)
        loaded = {}
        for family, (filename, model) in _FAMILIES.items():
            loaded[family] = {
                item["id"]: model.from_dict(item) for item in records[family]
            }
        return cls(version=version, root_dir=str(workspace), **loaded)

    @classmethod
    def load_records(
        cls, path: Optional[PathLike], version: Optional[str] = None
    ) -> tuple[str, Dict[str, list]]:
        """Load raw records for benchmark-specific thin adapters.

        This is the canonical implementation of active-version selection and
        required-file checking. Benchmark adapters may construct richer local
        model objects without reimplementing the on-disk store contract.
        """
        workspace = resolve_workspace(path)
        selected_version = validate_path_component(
            version or cls.active_version(workspace), label="version"
        )
        version_dir = workspace / "versions" / selected_version
        missing = [
            filename for filename, _ in _FAMILIES.values()
            if not (version_dir / filename).is_file()
        ]
        if missing:
            raise FileNotFoundError(
                f"Semantic version {selected_version!r} is missing files: {missing}"
            )
        return selected_version, {
            family: _load_json(version_dir / filename)
            for family, (filename, _) in _FAMILIES.items()
        }

    @staticmethod
    def active_version(path: Optional[PathLike] = None) -> str:
        """Return the active version name without loading records."""
        store_dir = resolve_workspace(path)
        active_file = store_dir / "active.json"
        if not active_file.is_file():
            raise FileNotFoundError(f"Missing ontology index: {active_file}")
        active = _load_json(active_file)
        version = str(active.get("active_version") or active.get("version") or "").strip()
        if not version:
            raise ValueError(f"Missing active_version in {active_file}")
        return validate_path_component(version, label="active version")

    # ---- writing -----------------------------------------------------------

    @classmethod
    def save_version(
        cls, path: Optional[PathLike], version: str, records: Dict[str, list]
    ) -> str:
        """Write a version directory holding the five record files.

        ``records`` maps each family name (``terms`` / ``mappings`` /
        ``relations`` / ``constraints`` / ``evidence``) to a list of raw record
        dicts. An existing version directory is never rewritten, mirroring the
        immutability rule :meth:`publish` enforces: identical content makes
        the call a safe retry, different content raises ``FileExistsError``.
        Returns the absolute path of the version directory.
        """
        version = validate_path_component(version, label="version")
        serialized = {}
        for family, (filename, _) in _FAMILIES.items():
            items = records.get(family, [])
            if not isinstance(items, list):
                raise TypeError(f"records[{family!r}] must be a list")
            serialized[filename] = json.dumps(items, ensure_ascii=False, indent=2)

        root = ensure_workspace(path)
        version_dir = root / "versions" / version
        if version_dir.exists():
            for filename, payload in serialized.items():
                destination = version_dir / filename
                if not destination.is_file() or destination.read_bytes() != payload.encode("utf-8"):
                    raise FileExistsError(
                        f"Version already exists with different content: {version_dir}"
                    )
            return str(version_dir)
        version_dir.mkdir(parents=True)
        for filename, payload in serialized.items():
            destination = version_dir / filename
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(destination)
        return str(version_dir)

    @classmethod
    def set_active(cls, path: Optional[PathLike], version: str) -> None:
        """Point ``active.json`` at ``version``."""
        version = validate_path_component(version, label="version")
        root = ensure_workspace(path)
        version_dir = root / "versions" / version
        if not version_dir.is_dir():
            raise FileNotFoundError(f"Semantic version missing: {version_dir}")
        active_file = root / "active.json"
        active_file.write_text(
            json.dumps({"active_version": version}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def promote(
        cls, path: Optional[PathLike], candidate_version: str, new_version: str
    ) -> str:
        """Accept a candidate: copy it to ``new_version`` and make it active.

        The candidate must already exist under ``versions/<candidate_version>/``
        (written via :meth:`save_version`). Rejects are simply "do not promote":
        ``active.json`` keeps pointing at the parent. Returns the new version name.
        """
        candidate_version = validate_path_component(
            candidate_version, label="candidate version"
        )
        new_version = validate_path_component(new_version, label="new version")
        root = resolve_workspace(path)
        src = root / "versions" / candidate_version
        if not src.is_dir():
            raise FileNotFoundError(f"Candidate version missing: {src}")
        dst = root / "versions" / new_version
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        cls.set_active(str(root), new_version)
        return new_version

    @classmethod
    def publish(cls, path: Optional[PathLike], candidate_version: str, new_version: str) -> str:
        """Publish a candidate as a new official version without overwriting.

        Unlike :meth:`promote`, an existing ``new_version`` is never replaced:
        identical content makes the call a safe retry, different content raises
        ``FileExistsError``. Returns the published version name.
        """
        candidate_version = validate_path_component(
            candidate_version, label="candidate version"
        )
        new_version = validate_path_component(new_version, label="new version")
        root = resolve_workspace(path)
        src = root / "versions" / candidate_version
        if not src.is_dir():
            raise FileNotFoundError(f"Candidate version missing: {src}")
        dst = root / "versions" / new_version
        if dst.exists():
            if not _same_version_content(src, dst):
                raise FileExistsError(f"Official version already exists: {dst}")
        else:
            shutil.copytree(src, dst)
        cls.set_active(str(root), new_version)
        return new_version

    @staticmethod
    def list_versions(path: Optional[PathLike] = None) -> List[str]:
        """Return the names of all versions present under ``versions/``."""
        versions_dir = resolve_workspace(path) / "versions"
        if not versions_dir.is_dir():
            return []
        return sorted(p.name for p in versions_dir.iterdir() if p.is_dir())

    # ---- lookup ------------------------------------------------------------

    def get(self, semantic_id: str):
        """Return an object from any semantic record family."""
        return (
            self.terms.get(semantic_id)
            or self.relations.get(semantic_id)
            or self.mappings.get(semantic_id)
            or self.constraints.get(semantic_id)
            or self.evidence.get(semantic_id)
        )

    def counts(self) -> Dict[str, int]:
        return {
            "terms": len(self.terms),
            "mappings": len(self.mappings),
            "relations": len(self.relations),
            "constraints": len(self.constraints),
            "evidence": len(self.evidence),
        }


def _same_version_content(src: Path, dst: Path) -> bool:
    """True when both version directories hold identical record files."""
    if not dst.is_dir():
        return False
    for filename in VERSION_FILES:
        src_file = src / filename
        dst_file = dst / filename
        if not src_file.is_file() or not dst_file.is_file():
            return False
        if src_file.read_bytes() != dst_file.read_bytes():
            return False
    return True


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)

