<p align="center">
  English | <a href="README.zh-CN.md">简体中文</a>
</p>

# EvoOntology Documentation

EvoOntology maintains a versioned ontology layer for Data Agents. Build and Evolve skills construct and update the layer; the deterministic core handles storage, runtime access, and the evolution lifecycle. Candidate updates are compared with the Parent within a round budget and evaluation boundary, and only Accept publishes a new version.

## Documentation

- [Architecture overview](architecture.md) — the ontology-layer structure, closed loop, module responsibilities, two modes, and benchmark integration.
- [Integrating a new benchmark](guide/new-benchmark.md) — data loading, task execution and scoring, the adapter contract, configuration, and initial ontology construction.
- [Product guide](../USAGE.md) — installation, workspace layout, the evolution loop, and an end-to-end workflow.
