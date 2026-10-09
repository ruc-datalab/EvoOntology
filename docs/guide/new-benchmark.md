<p align="center">
  English | <a href="new-benchmark.zh-CN.md">简体中文</a>
</p>

# Integrating a New Benchmark

A benchmark environment is a `benchmarks/<name>/` package that provides data loading, task execution and scoring, an evaluation adapter, and configuration. Use the shared `build-ontology` skill to construct its initial ontology layer. `benchmarks/bird/` provides a complete integration reference.

## Required components

| Component | Responsibility |
| --- | --- |
| `data/` or a scenario loader | Load train/validation/test items with IDs from disk |
| `run_agent.py` + `run_evaluation.py` | Run the Agent, score each item, and persist results |
| `evolution_adapter.py` (`EvolutionAdapter`) | Connect the loader and rollout to the evolution lifecycle |
| `configs/*.yaml` | Model, MCP, semantic switch, and evaluation parameters |
| `build-ontology` skill | Initial ontology-layer construction method |

The only core contract is an adapter class in `evolution_adapter.py` implementing `evaluate(subject: str, cases=None, output_hint=None) -> dict` and returning `{"metrics": {...}, "cases": [...], "artifact_paths": [...]}`. `metrics` is the minimum input for a gate decision; `cases` and `artifact_paths` support diagnosis and audit.

## Step 1 — Create the package

```bash
mkdir -p benchmarks/my_benchmark
touch benchmarks/my_benchmark/__init__.py
```

## Step 2 — Load data

Use a `data/` directory or scenario loader to load items with IDs from disk. BIRD uses `benchmarks/bird/data/` and `DATASET_PATHS` in `config.py`. Each item includes at least `question_id`, `question`, `db_id`, and `gold_sql` for rollout and scoring.

## Step 3 — Implement rollout and scoring

`run_agent.py` runs an item against a chosen semantic version. `run_evaluation.py` executes results and scores them against ground truth. Keep scoring here, not in the adapter: the adapter only orchestrates execution and normalizes results.

Both experimental conditions share one Agent, tool set, and runner, differing only in configuration:

- `configs/baseline.yaml`: native tools only;
- `configs/ontology.yaml`: additionally connects semantic MCP and exposes `browse_semantics` and `resolve_semantics`.

## Step 4 — Implement the adapter

Create `benchmarks/my_benchmark/evolution_adapter.py`:

```python
import json
import subprocess
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent


class MyBenchmarkAdapter:
    def __init__(self, config_path: str, **kwargs):
        self.config_path = config_path

    def evaluate(self, subject, cases=None, output_hint=None):
        output = output_hint or str(DIR / "results" / "evolution" / subject)
        completed = subprocess.run(
            [sys.executable, str(DIR / "run_evaluation.py"),
             "--config", self.config_path,
             "--semantic-version", subject,
             "--output", output],
            cwd=str(DIR), capture_output=True, text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr[-500:])
        data = json.loads((Path(output) / "results.json").read_text(encoding="utf-8"))
        return {
            "metrics": data.get("metrics", {}),
            "cases": data.get("results", []),
            "artifact_paths": [str(Path(output) / "results.json")],
        }
```

The adapter must use only the standard library so the evolution core can import it without installing benchmark-heavy dependencies such as the OpenAI SDK, `requests`, or `torch`. Use those dependencies only in the `run_agent.py` or `run_evaluation.py` subprocess.

## Step 5 — Register the benchmark

Add one lazy-loading entry to `_BUILTINS` in `benchmarks/registry.py`:

```python
_BUILTINS = {
    ...
    "my_benchmark": (
        "my_benchmark.evolution_adapter",
        "MyBenchmarkAdapter",
        "My benchmark description",
    ),
}
```

Verify it:

```bash
python -m benchmarks list
python -m benchmarks resolve my_benchmark
```

`Unknown benchmark environment 'my_benchmark'` means the environment was not registered.

## Step 6 — Configure both conditions

Keep `benchmarks/my_benchmark/configs/baseline.yaml` and `configs/ontology.yaml` identical except for `semantic.enabled` and whether `mcp_servers` includes semantic MCP. Generate the semantic workspace with `build-ontology`; do not ship a prebuilt workspace.

## Step 7 — Run

First run `build-ontology` to generate `ontology_v0`, then execute both conditions:

```bash
python benchmarks/my_benchmark/run_evaluation.py --config configs/baseline.yaml
python benchmarks/my_benchmark/run_evaluation.py --config configs/ontology.yaml
```

During evolution, the skill constructs the adapter through `benchmarks.registry.get("my_benchmark")(…)`, calls `evaluate()` for the Parent and Candidate, and passes both results to `EvaluationGate` for the Accept/Reject decision.
