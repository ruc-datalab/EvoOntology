"""Lazy benchmark-environment registry.

Each benchmark registers a stdlib-only adapter class that implements
``evoontology.evolution.EvolutionAdapter``. The adapter is imported lazily so
optional benchmark dependencies (OpenAI SDK,
``requests``, ``torch``, ...) are never pulled in at discovery time.

Task execution and scoring live inside each benchmark's runner and evaluator
entry points, which the adapter drives as subprocesses. The adapter normalizes
their results for the evolution lifecycle.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Dict, List

AdapterFactory = Callable[..., Any]

#: name -> (adapter module, adapter class, short description)
_BUILTINS: Dict[str, tuple] = {
    "bird": (
        "bird.evolution_adapter",
        "BirdEvolutionAdapter",
        "BIRD text-to-SQL benchmark",
    ),
    "ddr_10k": (
        "ddr_10k.evolution_adapter",
        "DDREvolutionAdapter",
        "DDR-10K autonomous data-analysis benchmark",
    ),
    "insightbench": (
        "insightbench.evolution_adapter",
        "InsightBenchEvolutionAdapter",
        "InsightBench iterative analysis / code-generation benchmark",
    ),
}

_REGISTRY: Dict[str, dict] = {}


def register(
    name: str,
    adapter: Any,
    description: str = "",
    *,
    module: str = "",
    cls: str = "",
) -> None:
    """Register an adapter class (or lazy factory) under ``name``."""
    name = str(name).strip()
    if not name:
        raise ValueError("benchmark name must be non-empty")
    if name in _REGISTRY:
        raise ValueError(f"benchmark already registered: {name}")
    _REGISTRY[name] = {
        "adapter": adapter,
        "module": module or getattr(adapter, "__module__", ""),
        "cls": cls or getattr(adapter, "__name__", ""),
        "description": str(description or ""),
    }


def _lazy(module: str, cls: str) -> AdapterFactory:
    def build(**kwargs: Any) -> Any:
        adapter_cls = getattr(
            importlib.import_module(f".{module}", package=__package__), cls
        )
        return adapter_cls(**kwargs)

    return build


def get(name: str) -> AdapterFactory:
    """Return a callable that constructs the named benchmark adapter."""
    return _entry(name)["adapter"]


def adapter_class(name: str) -> Any:
    """Return the concrete adapter class for ``name`` (importing it lazily)."""
    entry = _entry(name)
    if entry["module"]:
        return getattr(importlib.import_module(entry["module"]), entry["cls"])
    return entry["adapter"]


def list_adapters() -> List[str]:
    """Return the registered benchmark environment names, sorted."""
    return sorted(_REGISTRY)


def describe(name: str) -> str:
    """Return the human-readable description of a benchmark environment."""
    return _entry(name)["description"]


def _entry(name: str) -> dict:
    name = str(name).strip()
    entry = _REGISTRY.get(name)
    if entry is None:
        available = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(
            f"Unknown benchmark environment {name!r}. Available: {available}"
        )
    return entry


def _register_builtins() -> None:
    for name, (module, cls, description) in _BUILTINS.items():
        register(
            name,
            _lazy(module, cls),
            description,
            module=f"{__package__}.{module}",
            cls=cls,
        )


_register_builtins()
