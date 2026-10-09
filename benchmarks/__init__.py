"""Benchmark environments for the EvoOntology evolution loop.

Each benchmark under this package is a self-contained environment that plugs
into the evolution loop through a single ``EvolutionAdapter`` that evaluates
semantic versions and returns normalized results. See ``benchmarks/registry.py``
for the discovery/registration layer and ``docs/guide/new-benchmark.md`` for the full
integration contract.
"""

from .registry import (
    adapter_class,
    describe,
    get,
    list_adapters,
    register,
)

__all__ = [
    "adapter_class",
    "describe",
    "get",
    "list_adapters",
    "register",
]
