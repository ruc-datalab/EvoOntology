<p align="center">
  <a href="USAGE.md">English</a> | 简体中文
</p>

# EvoOntology 产品化使用指南

本文档说明如何实际使用 EvoOntology。产品化把散落在三个 benchmark 里的通用能力抽取为
一个**核心包** `evoontology/`（确定性能力），并配两个自包含插件：

- `evoontology/` —— 与 benchmark 无关的产品运行时：ontology store / runtime(MCP) /
  trajectory / trigger / evaluation / evolution 生命周期 / validate 门禁。
- `plugins/` —— Claude Code 与 Codex 插件均提供 `build-ontology`、`evolve-ontology`、
  `explore-ontology` skills，以及 MCP；Claude Code 另带 Session Start 提醒。两者内置同一份 core 副本。

产品最终形态 = 一个核心包（含 validate 门禁）+ 三个 skills，无 CLI。智能分析全在
skill，Python 只做「运行时 + 最小确定性校验 + 进化生命周期状态机」。默认**零配置**：
不要求用户填写 workspace 路径、Evaluation Mode、Judge 模型或 Trigger 参数。

---

## 1. 安装

请选择正在使用的客户端。Claude Code 与 Codex 通过 Marketplace 安装，无需 clone 仓库、创建虚拟环境或单独运行 `pip install`；WorkBuddy 没有 Marketplace，改为从 clone 接入。

### Claude Code

```bash
claude plugin marketplace add ruc-datalab/EvoOntology
claude plugin marketplace list
claude plugin install evoontology@evoontology
claude plugin list
```

### Codex

```bash
codex plugin marketplace add ruc-datalab/EvoOntology
codex plugin marketplace list
codex plugin add evoontology-codex@evoontology
codex plugin list
```

Marketplace 添加成功不等于插件已安装；请以最后一条 `plugin list` 显示 installed/enabled
为准。安装或更新后新建会话，再调用对应客户端的 `build-ontology` skill。

### WorkBuddy

```bash
git clone https://github.com/ruc-datalab/EvoOntology.git
cd EvoOntology
python3 -m venv .venv && .venv/bin/pip install -e .
plugins/workbuddy/install.sh
```

安装脚本会把三个 skill 拷入 `~/.workbuddy/skills/`，并把 `evo-semantic` MCP server 注册进
`~/.workbuddy/mcp.json`。两者都用绝对路径，因为 WorkBuddy 不对该文件做变量展开。重启会话后生效；
手动步骤、工作区选择与平台注意事项见 [WorkBuddy 集成](plugins/workbuddy/README.zh-CN.md)。

---

## 2. 一个 workspace 长什么样

workspace 默认是项目根的 `.evoontology/`，首次运行 `build-ontology` 时自动创建：

```
.evoontology/
├── project.json         # mode / data source / workload / evaluator / boundary
├── active.json          # {"active_version": "ontology_v0"}
├── versions/            # 所有版本（正式 ontology_vN + 候选 vN-cK），每版本 5 个 JSON
├── trajectories/        # 每个任务一条 JSON trajectory
├── evolution/           # 每个进化 run 一个目录 run_N/
│   └── run_N/
│       ├── run.json                 # 状态 / Parent / 当前 Candidate / 轮次 / 冻结预算
│       ├── trajectory-sources.json  # 用户确认的轨迹来源记录
│       ├── rounds.jsonl             # 每轮一行摘要
│       └── evaluations/             # 正式 Parent/Candidate 评估摘要
└── state.json           # Trigger checkpoint 与阈值
```

每版本下是 5 个记录文件，对应五类对象：Term / Mapping / Relation / Constraint /
Evidence。轨迹由 Data Agent 运行时（benchmark adapter 侧）在每次任务结束时追加到
`trajectories/`。

Workspace 分阶段初始化：Step 0 确认后写 `project.json`；初始版本保存并通过
语义 MCP 的 `validate_semantics`（`version` 传 `ontology_v0`）后，才写 `active.json` 和
`state.json`。Core 默认解析 `<project-root>/.evoontology/`，benchmark 可显式传入其他路径。

### 选择 mode

- `fixed_split`：用于已有固定问题集、GT 和评测边界的 benchmark。Construction Pool 用于
  Build/诊断，Validation Reserve 只用于最终 Gate。优先复用官方划分，不额外生成随机 Fold。
- `rolling_trajectory`：用于真实业务或冷启动。先用 seed workload 初始化，之后按 checkpoint
  持续收集新的 task trajectory；没有 GT 时通过独立任务抽样和 LLM Judge 比较 Parent/Candidate，
  不需要强行划分 Fold A/B。

mode 在 Step 0 确认后写入 `project.json`，后续 Build 和 Evolve 共用，避免每轮重新判断。

---

## 3. Skill 入口

| 工作流 | Claude Code | Codex | 语义 |
| --- | --- | --- | --- |
| Build | `/evoontology:build-ontology` | `$build-ontology` | 构建并发布 `ontology_v0` |
| Evolve | `/evoontology:evolve-ontology` | `$evolve-ontology` | 诊断→归因→补丁→Parent/Candidate gate→发布 |
| Explore | `/evoontology:explore-ontology` | `$explore-ontology` | 只读浏览问题、证据、结果与版本差异 |

三个入口均由 agent 按 skill 执行，不是 Python 确定性操作。
版本命名与切换约定见 `plugins/claude-code/docs/versioning.zh-CN.md`（正式 `ontology_vN`、
候选 `vN-cK`，accept 映射 `vN-cK` → `ontology_vN+1`）。

---

## 4. 进化闭环：EvolutionSession

每次 `evolve-ontology` 对应一个 Run，由核心包的 `EvolutionSession` 状态机托管。Skill 决定
「改什么、为什么改」，Session 保证 run 不会以错误方式结束：

```
running ──Reject──▶ running（同一 run 内设计下一个 Candidate）
running ──Accept──▶ accepted（发布新版本、推进 checkpoint）
running ──预算耗尽/用户中断/数据缺失/评估不可靠──▶ incomplete
```

### 新 run 开始时

1. **恢复优先**：若已有未结束的 run，resume 它而不是新开；
2. **冻结数据**：fixed_split 复用已持久化的训练/验证子集；rolling_trajectory 从
   checkpoint 之后收集合格轨迹、冻结批次并切分 Evolution Pool / Validation Reserve；
3. **确认预算**：向用户说明本次计划使用的轮数（默认 8），确认后冻结进 `run.json`；
   resume 同一 run 沿用已确认预算；预算耗尽后如需加轮数，必须再次确认；
4. **确认轨迹来源**：来源或范围未定时，向用户说明每条来源的路径、内容范围、时间与
   用途并确认，确认后写入 `run_N/trajectory-sources.json`。新 run 默认复用最近一次 run
   的来源记录并验证路径仍有效，仅当来源新增、失效或范围变化时重新确认。找不到轨迹时，
   先跑 Parent baseline，再据评测结果、错误和反例开始诊断。

### 循环内

- 诊断 → 归因 → 补丁：沿 **Content / Tool / Schema** 三个维度选择主要机制，一个
  Candidate 验证一个主要假设；改动必须可溯源到目标维度、可回滚到 Parent；
- 评估：Candidate 以自己的存储版本参评（`--semantic-version`），比较期间不修改
  `active.json`；有 GT 走绝对评分，无 GT 走 LLM Judge 匿名 A/B；
- **Reject 不是终点**：写 `rounds.jsonl` 摘要、更新归因与 problem map，然后设计下一个
  Candidate；不推进 checkpoint、不结束 run；
- **Accept 结束搜索**：进入收尾。

### 收尾（Finalize）

Accept 后：确定性校验 → 发布为 `ontology_vN+1`（不覆盖已有正式版本）→ 更新
`active.json` → 推进一次 checkpoint → run 标记 `accepted`。
Incomplete 不发布、不推进；同一批次在下次 run 重试。`missing_data` /
`unreliable_evaluation` / `external_block` 这类判断性停止，需先在同一 run 内正式 Reject
至少 `min_rejects_before_incomplete`（默认 2）个候选；`user_interrupted` 与
`missing_permissions` 才立即停止。最终报告必须基于 session 终态与落盘记录，不依赖对话记忆。

---

## 5. 配置（零配置）

产品默认零配置，无 `config.yaml`。用户需要调整时直接告诉 Claude / Codex（例如「以后每
60 个任务提醒我一次」），由 agent 更新 `state.json` 内部状态，不改配置文件。

- 进化触发默认：checkpoint 后新增 ≥ 30 个 task，或距 checkpoint ≥ 7 天。首次 checkpoint
  是 `ontology_v0` 发布时间；**只有正式 Gate 的 Accept 推进 checkpoint**（Reject 在同一
  run 内继续循环，Incomplete 不推进）。
- 评估协议自动选择：benchmark 提供 Evaluator（Ground Truth）时走 GT；否则走 LLM Judge
  （见 `plugins/claude-code/docs/evaluation-protocol.zh-CN.md`）。

---

## 6. MCP 接入

插件通过 `.mcp.json` 以模块形式 spawn 服务，client 自动拉起、无需手动起服。默认
workspace 为当前项目的 `.evoontology/`（零配置）。WorkBuddy 则在用户级
`~/.workbuddy/mcp.json` 中注册同一个 server，并以绝对路径指定 `--store`；详见
[WorkBuddy 集成](plugins/workbuddy/README.zh-CN.md)。

接入后 Data Agent 可见：

- 工具 `browse_semantics(query, kind, limit)` —— 发现相关概念；
- 工具 `resolve_semantics(mentions, context)` —— 解析概念到 grounding 的 mapping +
  关联的 relation / constraint / evidence；
- 资源 `evo-semantic://session-manifest` —— 会话开始时读取的简洁说明。

同一个 `evo-semantic` 服务还向 Build / Evolve / Visualize 暴露确定性操作
（`validate_semantics`、`visualize_ontology`、`evolution_status`、版本辅助与进化会话
工具），因此插件-only 安装无需在用户项目里运行 `python -m evoontology...`。

这两个工具返回的是元数据与指引，数据库查询与 Python 执行仍由 benchmark 原生工具负责。

---

## 7. validate 门禁（agent 自动）

`build-ontology`、`evolve-ontology` 发布新版本前，agent 会自动调用语义 MCP 的
`validate_semantics` 工具做确定性门禁（JSON 合法 / 引用完整 / 可加载），用户无需手动执行。
validate 只做结构校验，不做数据库语义校验（表字段存在 / Mapping 可执行 / Evidence 可复现
是 Builder 探索阶段已做的事）。

---

## 8. 一个最小端到端流程

```bash
# 1. 按第 1 节通过 Claude Code 或 Codex Marketplace 安装插件

# 2. 触发构建 ontology_v0（Claude Code / Codex）
/evoontology:build-ontology
$build-ontology

# 3. Data Agent 通过 MCP 接入（.mcp.json 声明，client 自动 spawn，无需手动起服）

# 4. 触发进化（Claude Code / Codex，或等待轨迹达到阈值后的提醒）
/evoontology:evolve-ontology
$evolve-ontology   # agent 用语义 MCP 的进化工具循环 Candidate；
                   # Accept 时经 accept_evolution 校验、发布、更新 active.json 并推进 checkpoint
```

agent 发布前会自动调用 `validate_semantics` 做门禁。

---

## 9. 边界（一期不做）

Web UI / SaaS / 多租户 / 消息队列 / 常驻 worker / 多 Candidate 并行 / 自动循环 / 高频改
schema 均不在本版范围。无人值守全自动进化需要常驻后台 worker，一期只做「检测 + 提醒」，
由人触发。
