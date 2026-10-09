<p align="center">
  <a href="new-benchmark.md">English</a> | 简体中文
</p>

# 接入一个新的 Benchmark

一个 benchmark 环境是一个 `benchmarks/<name>/` 包，提供数据加载、任务执行与评分、评测 adapter
和配置。初始本体层通过共用的 `build-ontology` skill 构建；`benchmarks/bird/` 可作为完整接入参考。

## 接入组件

| 组件 | 职责 |
| --- | --- |
| `data/` 或场景加载器 | 从磁盘加载 train/val/test item |
| `run_agent.py` + `run_evaluation.py` | 在 item 上运行 Agent、逐条评分、落盘结果 |
| `evolution_adapter.py`（`EvolutionAdapter`） | 把 loader + rollout 接入进化生命周期 |
| `configs/*.yaml` | 模型、MCP、语义开关、评测参数 |
| `build-ontology` skill | 初始本体层的构建方法 |

核心契约只有一条：`evolution_adapter.py` 里的适配器类实现
`evaluate(subject: str, cases=None, output_hint=None) -> dict`，返回
`{"metrics": {...}, "cases": [...], "artifact_paths": [...]}`。`metrics` 是门控决策的最小要求；
`cases` 和 `artifact_paths` 用于诊断与审计。

## Step 1 — 创建包

```bash
mkdir -p benchmarks/my_benchmark
touch benchmarks/my_benchmark/__init__.py
```

## Step 2 — 数据加载

用一个 `data/` 目录（或场景加载器）负责从磁盘加载带 `id` 的 item。BIRD 的例子是
`benchmarks/bird/data/` + `config.py` 的 `DATASET_PATHS`：每个 item 至少有 `question_id` / `question` /
`db_id` / `gold_sql`，供 rollout 与评分使用。

## Step 3 — rollout + 评分

`run_agent.py` 负责「用某个语义版本在 item 上跑出结果」，`run_evaluation.py` 负责「执行结果 + 对照
Ground Truth 评分」。评分逻辑放在这里，而不是放在 adapter 里——adapter 只做编排与结果规约。

两条实验条件共用一个 Agent / 工具 / Runner，只有配置不同：

- `configs/baseline.yaml`：只有原生工具；
- `configs/ontology.yaml`：额外挂语义 MCP，暴露 `browse_semantics` / `resolve_semantics`。

## Step 4 — 实现 adapter

`benchmarks/my_benchmark/evolution_adapter.py`：

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

adapter 必须 stdlib-only，这样进化核心可以导入它而不用安装 benchmark 的重依赖（OpenAI SDK、
`requests`、`torch` 等）。重型依赖只在 `run_agent.py` / `run_evaluation.py` 子进程里使用。

## Step 5 — 注册

在 `benchmarks/registry.py` 的 `_BUILTINS` 里加一行，保持懒加载：

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

验证：

```bash
python -m benchmarks list
python -m benchmarks resolve my_benchmark
```

若得到 `Unknown benchmark environment 'my_benchmark'`，说明忘了注册。

## Step 6 — 配置

`benchmarks/my_benchmark/configs/baseline.yaml` 与 `configs/ontology.yaml` 保持两条条件一致，只切换
`semantic.enabled` 与 `mcp_servers` 里是否挂语义 MCP。语义 workspace 用 `build-ontology` 生成，不随仓库
预置。

## Step 7 — 运行

先运行 `build-ontology` 生成 `ontology_v0`，再跑两条条件：

```bash
python benchmarks/my_benchmark/run_evaluation.py --config configs/baseline.yaml
python benchmarks/my_benchmark/run_evaluation.py --config configs/ontology.yaml
```

进化时由 skill 通过 `benchmarks.registry.get("my_benchmark")(...)` 构造 adapter，分别对 Parent 与
Candidate 调用 `evaluate()`，再交给 `EvaluationGate` 做 Accept/Reject 决策。
