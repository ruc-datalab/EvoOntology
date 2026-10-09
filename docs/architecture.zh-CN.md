<p align="center">
  <a href="architecture.md">English</a> | 简体中文
</p>

# 架构总览

## 核心思想

EvoOntology 维护一个版本化的本体层，供 Data Agent 通过语义 MCP 工具查询。Content Layer 保存有数据证据支撑的概念及其关系；Schema Layer 定义允许的表示结构；Tool Layer 通过 manifest 和按需 browse/resolve 操作提供访问。Build / Evolve skills 根据任务证据构建初始本体层并提出局部更新；确定性 Python 核心负责存储、运行时访问、轨迹、校验和进化生命周期。每次进化在冻结的轮次预算与评测边界内比较 Parent 和 Candidate，只有 Accept 决策才发布下一个本体版本。

```
自然语言问题 ──▶ Data Agent（Claude Code / Codex / benchmark harness）
                    │ MCP: browse_semantics / resolve_semantics
                    ▼
              EvoOntology 本体层 ontology_vN
                    │
                    │ evolve-ontology
                    ▼
        EvolutionSession：冻结预算与数据 → 诊断 → 归因 → 补丁 → 评估门控
                    │
                    ▼
        trajectories/ + evaluations/ → Accept 发布 ontology_vN+1 / Reject 下一轮 / Incomplete 保持 Parent
```

## 模块划分

| 目录 | 职责 |
| --- | --- |
| `evoontology/` | 确定性核心包：ontology store、runtime/MCP、trajectory、trigger、evaluation、evolution 状态机、validate 门禁 |
| `plugins/claude-code/` | Claude Code 插件：`build-ontology`/`evolve-ontology`/`explore-ontology` skills、`.mcp.json`、Session Start 提醒 hook |
| `plugins/evoontology-codex/` | Codex 插件：`AGENTS.md`、`build-ontology`/`evolve-ontology`/`explore-ontology` skill、`.mcp.json` |
| `benchmarks/` | 三个 benchmark 环境（bird / ddr_10k / insightbench），每个环境实现一个 `EvolutionAdapter` |
| `scripts/` | `sync_plugin_core.py`（把根 core 同步到两个插件） |
| `docs/` | 架构与接入文档 |

核心包只提供确定性能力；Build / Evolve 的智能放在 skill 里，Python 只做「运行时 + 最小确定性校验 +
进化生命周期状态机」。

## 进化闭环

1. **Build**：`build-ontology` 按 workload 探针 → 证据落地，产出并发布 `ontology_v0`。
2. **Use**：Data Agent 通过语义 MCP `browse_semantics` / `resolve_semantics` 做概念 grounding。
3. **Record**：任务轨迹以 Tool Call 粒度写入 `trajectories/`（不存思维链）。
4. **Evolve**：达到触发条件后，`evolve-ontology` 在 `EvolutionSession` 内循环诊断 → 归因 → 补丁 → 门控。
5. **Evaluate**：`EvaluationGate` 用 GT 绝对评分或 LLM Judge A/B 比较 Parent/Candidate。

状态机规则：

```text
running ──Reject──▶ running（同一 run 下一轮 Candidate）
running ──Accept──▶ accepted（发布新版本、切 active、推进 checkpoint）
running ──预算耗尽/外部阻断──▶ incomplete（不发布、不推进）
```

只有 Accept 或合法的 Incomplete 是终态；Reject 只是下一轮的输入。

## 两种 mode

项目在 Build Step 0 确定并写入 `.evoontology/project.json` 的 `mode`：

- **`fixed_split`**：有固定问题集、Ground Truth 和评测边界的 benchmark。Construction Pool 用于
  Build/诊断，Validation Reserve 只用于最终 Gate，不能回流到构建、诊断或补丁生成。
- **`rolling_trajectory`**：没有固定测试集的真实业务或冷启动项目。seed workload 初始化 `ontology_v0`，
  上线后的任务持续写入 `trajectories/`，达到阈值后冻结一批，用独立抽样任务或 LLM Judge 完成 Gate。

两种 mode 共用同一套 workspace、版本与 checkpoint 机制，区别只在 workload 如何进入构建、进化与评估。

## benchmark 接入形式

每个 benchmark 是一个自包含环境，通过一个 `EvolutionAdapter` 接入进化循环。adapter 评测指定语义版本并返回统一格式的结果，使进化核心无需依赖 benchmark 内部实现即可比较 Parent 和 Candidate：

- `evolution_adapter.py`：`evaluate(subject, cases, output_hint)` → `{metrics, cases, artifact_paths}`；
- benchmark 的 runner 与 evaluator（如 `run_agent.py` / `run_evaluation.py`，或 InsightBench 的 `main.py`）：执行任务、评分并落盘产物；
- `data/`（或场景加载器）：加载任务条目与数据划分；
- `configs/*.yaml`：baseline / semantic 两条实验条件；
- 插件里的 `build-ontology` skill：根据 workload 与数据证据构建初始本体层。

`benchmarks/registry.py` 按环境名称注册 adapter 工厂，并延迟导入 adapter 类；`python -m benchmarks` 用于列出环境与解析 adapter。
接入细节见 [接入一个新的 Benchmark](guide/new-benchmark.zh-CN.md)。
