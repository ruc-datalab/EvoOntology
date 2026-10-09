<p align="center">
  <a href="README.md">English</a> | 简体中文
</p>

# Benchmarks

三个 benchmark 环境，用于验证 EvoOntology 的 Build → Use → Record → Evolve → Evaluate 闭环。
每个环境是一个自包含包，通过一个 `EvolutionAdapter` 接入进化循环。

| 环境 | 基准 | 任务类型 | 语义 workspace |
| --- | --- | --- | --- |
| `bird/` | BIRD | text-to-SQL | 每数据库一个 `.evoontology/<db_id>/` |
| `ddr_10k/` | DDR-10K | 自主数据分析 | 一个 `.evoontology/` |
| `insightbench/` | InsightBench | 迭代分析 / 代码生成 | 一个 `.evoontology/` |

## 统一接入契约

每个 benchmark 环境提供数据加载、任务执行与评分、评测 adapter 和配置；初始本体层由共用的
`build-ontology` skill 构建：

| 组件 | 职责 |
| --- | --- |
| `data/` 或场景加载器 | 从磁盘加载带 `id` 的 train/val/test item |
| benchmark 的 runner 与 evaluator（`run_agent.py` / `run_evaluation.py`，或 InsightBench 的 `main.py`） | 运行 Agent、逐条评分、落盘结果 |
| `evolution_adapter.py`（`EvolutionAdapter`） | 把 loader + rollout 接入进化生命周期 |
| `configs/baseline.yaml` + `configs/ontology.yaml` | 模型、MCP、语义开关、评测参数 |
| 插件 `build-ontology` skill | 初始本体层构建方法 |

核心契约只有一条：adapter 实现
`evaluate(subject, cases=None, output_hint=None) -> {"metrics", "cases", "artifact_paths"}`。
adapter 必须 stdlib-only；benchmark 的重依赖（OpenAI SDK、`requests`、`torch` 等）只在
runner 或 evaluator 子进程里使用。

## 统一发现入口

`benchmarks/registry.py` 按名称注册环境，并按需加载 adapter 类：

```bash
python -m benchmarks list           # 列出已注册环境
python -m benchmarks resolve bird   # 解析 bird 的 adapter 类
```

在代码里构造 adapter：

```python
from benchmarks import get

adapter = get("bird")(config_path="configs/ontology.yaml", dataset="minidev")
result = adapter.evaluate(subject="ontology_v0")
```

接入一个新的环境见 [`docs/guide/new-benchmark.zh-CN.md`](../docs/guide/new-benchmark.zh-CN.md)。

## 复用 EvoOntology Core

三个 benchmark 的确定性能力统一由仓库根 `evoontology` 包提供，不重复维护：

| 能力 | 模块 | 接入方式 |
| --- | --- | --- |
| Ontology 存储 / 版本管理 | `evoontology.ontology.store.SemanticStore` | `save_version` / `publish` / `set_active` |
| Semantic Runtime | `evoontology.runtime.runtime.SemanticLayer` | `browse` / `resolve` / `manifest` |
| Semantic MCP | `evoontology.runtime.mcp_server` | `python -m evoontology.runtime.mcp_server --store <workspace>` |
| Trajectory 记录 | `evoontology.trajectory.TrajectoryStore` | `append` |
| Evolution 生命周期 | `evoontology.evolution.EvolutionSession` | Run 状态机 |
| Evolution 适配 | `evoontology.evolution.EvolutionAdapter` | 各 benchmark 实现 `evaluate()` |
| Evaluation 调度 | `evoontology.evaluation.EvaluationGate` | `decide_gt` / `decide_judge` |

每个 benchmark 目录下的 Data Agent、Native Tools、Runner、Evaluator 为该 benchmark 特有实现。
`tceo/` 保留 benchmark 特有的 Binder、scope 与 manifest adapter，active-version 选择与五类 JSON
文件门禁统一调用 `SemanticStore.load_records()`。

## 数据准备

仓库不提供 benchmark 原始大型数据、预构建 `ontology_v0` 与预构建 evolved ontology。各 benchmark
的 README 说明官方数据准备方式；准备后运行 `build-ontology` 构建自己的 ontology。BIRD 自带一个
`formula_1` 最小示例（数据库 + 语义 workspace）可用于离线 smoke test。
