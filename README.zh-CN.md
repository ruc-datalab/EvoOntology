<p align="center">
  <img src="assets/logo.png" alt="EvoOntology" width="68%">
</p>

<h1 align="center">EvoOntology：面向 Data Agent 的自进化 Ontology Layer</h1>

<p align="center">
  <a href="https://arxiv.org/abs/2609.15779"><img src="https://img.shields.io/badge/arXiv-2609.15779-b31b1b.svg?logo=arXiv" alt="arXiv"></a>
  <a href="https://github.com/ruc-datalab/EvoOntology"><img src="https://img.shields.io/badge/MCP-compatible-7c3aed.svg" alt="MCP compatible"></a>
  <a href="https://github.com/ruc-datalab/EvoOntology/tree/master/plugins/evoontology-codex"><img src="https://img.shields.io/badge/Plug--in-codex-white.svg" alt="Codex plugin"></a>
  <a href="https://github.com/ruc-datalab/EvoOntology/tree/master/plugins/claude-code"><img src="https://img.shields.io/badge/Plug--in-claude--code-orange.svg" alt="Claude Code plugin"></a>
  <a href="https://github.com/ruc-datalab/EvoOntology/tree/master/plugins/workbuddy"><img src="https://img.shields.io/badge/Plug--in-workbuddy-1f6feb.svg" alt="WorkBuddy integration"></a>
</p>

<p align="center">
  <a href="https://github.com/ruc-datalab/EvoOntology/stargazers"><img src="https://img.shields.io/github/stars/ruc-datalab/EvoOntology?style=flat&amp;logo=github&amp;color=3941EA" alt="GitHub stars"></a>
  <a href="https://github.com/ruc-datalab/EvoOntology/forks"><img src="https://img.shields.io/github/forks/ruc-datalab/EvoOntology?style=flat&amp;logo=github&amp;color=3941EA" alt="GitHub forks"></a>
  <a href="https://hitscounter.dev/history?url=https%3A%2F%2Fgithub.com%2Fruc-datalab%2FEvoOntology"><img src="https://hitscounter.dev/api/hit?url=https%3A%2F%2Fgithub.com%2Fruc-datalab%2FEvoOntology&amp;label=Views&amp;icon=graph-up&amp;color=%233941ea&amp;message=&amp;style=flat&amp;tz=Asia%2FShanghai" alt="Page views: today / total"></a>
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README.zh-CN.md"><strong>简体中文</strong></a>
</p>

<p align="center">
  <a href="#user-content--demo">演示视频</a> · <a href="#user-content--快速开始">快速开始</a> · <a href="#user-content--社区分享与解读">社区分享</a> · <a href="#user-content--star-趋势">Star 趋势</a>
</p>

> **作者：** [Meiduo Chong](https://github.com/MeiduoChong)、[Shaolei Zhang](https://zhangshaolei1998.github.io/)<sup>*</sup>、[Ju Fan](https://iir.ruc.edu.cn/~fanj/)、[Xiaoyong Du](https://info.ruc.edu.cn/jsky/szdw/ajxjgcx/jsjkxyjsx1/js2/7374b0a3f58045fc9543703ccea2eb9c.htm)<br>
> 中国人民大学<br>

EvoOntology 用于弥合异构表格、文件和数据库上的 **agent-data gap**。它通过 MCP 工具提供版本化的 **Ontology Layer**，基于真实 workload 和数据证据完成构建，并根据实际执行轨迹持续适配。

## 🧭 为什么需要 EvoOntology

- **原始数据缺少显式语义。** 表名、字段、文件路径和零散观测通常无法完整说明指标口径、实体关系与业务约束，Agent 需要反复推断，容易产生语义错误。
- **静态语义层难以持续扩展。** 人工构建和维护依赖专家投入；数据与 workload 变化后容易过时，完整注入 prompt 的方式也会带来不断增长的上下文开销。
- **Agent 需要能够持续适配的语义。** EvoOntology 围绕真实 workload 构建 Ontology Layer，由 Agent 按需查询，并依据执行行为在受控评估下持续进化。

<p align="center">
  <img src="assets/evoontology-overview.png" alt="使用与不使用 EvoOntology 的 Data Agent 对比" width="92%">
</p>

<p align="center"><strong>面向 Data Agent 的 Agent-first、自进化 Ontology Layer。</strong></p>

## 🎬 Demo

Codex 和 Claude Code 插件可以基于您的数据构建并进化 Ontology Layer；WorkBuddy 通过自身的接入方式复用同一套 skill 与运行时。

https://github.com/user-attachments/assets/e15f4acd-7161-4ae1-ba41-f2f3ea05488b

## ✨ 核心亮点

### 🎯 我们解决的问题

- **降低语义不确定性。** 显式组织领域概念、数据映射、关系与约束，避免 Agent 仅凭原始数据猜测业务含义。
- **减少重复的数据探索。** 在任务间复用经过数据验证的知识，使 Agent 能聚焦相关数据，而不必每次重新理解整个环境。
- **降低语义维护成本。** 根据 workload 和 Agent 行为持续适配 Ontology Layer，同时保证更新可检查、可比较、可回滚。

### 🧩 设计亮点

| 设计原则 | 核心要点 |
| --- | --- |
| **主动访问** | Agent 通过 MCP 工具仅获取当前步骤所需的语义，无需注入完整 Ontology Layer。 |
| **基于证据构建** | 围绕 workload 构建 ontology，并只提交经过底层数据验证的语义对象。 |
| **定向进化** | 根据交互轨迹诊断问题，并在相互关联的 Content、Schema 和 Tool Layer 中进行局部更新。 |
| **门控版本演进** | Candidate 只有在配对评估中可复现地优于 Parent，才会发布为下一版本。 |
| **Agent 集成** | 通过插件将 ontology workspace 和 MCP runtime 直接接入受支持的 Agent。 |

## ⚙️ 工作原理

EvoOntology 把 Ontology Layer 视为可训练的 Agent 状态，而不是模型权重。Builder 根据 workload 和底层数据构建有证据支撑的语义对象；Evolution Agent 再利用历史交互提出局部更新，并将每个 Candidate 与 Parent 配对评估。

<p align="center">
  <img src="assets/evoontology-framework.png" alt="EvoOntology 构建与进化框架" width="100%">
</p>

### 🧠 Ontology Layer

Ontology Layer 由三个相互关联的层组成，分别定义语义知识、表示规则和运行时访问方式：

| 层 | 作用 |
| --- | --- |
| **Content Layer** | 类型化语义图，包含 Term、Mapping、Constraint 和 Evidence 四类节点。Semantic Relation 连接 Term；Structural Reference 连接 Term 与 Mapping，并将 Constraint 或 Evidence 挂接到其约束或支撑的对象上。 |
| **Schema Layer** | 定义四类节点的字段、允许使用的 Semantic Relation 类型，以及合法的 Structural Reference 模式，从而确定 Ontology Layer 的表达边界。 |
| **Tool Layer** | 通过 `browse_semantics`、`resolve_semantics` 和简洁的 session manifest 向 Agent 暴露 Ontology Layer。会话初始化时只注入 manifest；具体记录及其关联对象均按需检索。 |

<p align="center">
  <a href="assets/ontology-layers/content-layer.png"><img src="assets/ontology-layers/content-layer.png" alt="EvoOntology 可视化界面的 Content Layer" width="96%"></a><br>
  <sub><strong>Content Layer：</strong>查看已落地的概念、映射、约束、证据及其关系。</sub>
</p>

<table>
  <tr>
    <td width="50%" align="center">
      <a href="assets/ontology-layers/schema-layer.png"><img src="assets/ontology-layers/schema-layer.png" alt="EvoOntology 可视化界面的 Schema Layer" width="100%"></a><br>
      <sub><strong>Schema Layer：</strong>查看对象类型、字段和受控关系规则。</sub>
    </td>
    <td width="50%" align="center">
      <a href="assets/ontology-layers/tool-layer.png"><img src="assets/ontology-layers/tool-layer.png" alt="EvoOntology 可视化界面的 Tool Layer" width="100%"></a><br>
      <sub><strong>Tool Layer：</strong>查看 MCP 工具和简洁的运行时 manifest。</sub>
    </td>
  </tr>
</table>

<p align="center"><sub>点击任意截图可查看完整分辨率。</sub></p>

### 🔄 生命周期

1. **Build** — 从 workload 提取候选概念，在原始数据源中验证，并发布 `ontology_v0`。
2. **Use** — Data Agent 按需查询 Ontology Layer，同时记录工具交互和任务结果。
3. **Evolve** — 诊断重复出现的行为，将问题归因到 Content、Tool 或 Schema，并生成局部 Candidate 补丁。
4. **Evaluate** — 在相同数据、Agent、解码配置和交互预算下比较 Parent 与 Candidate。
5. **Publish or reject** — 通过门控的 Candidate 发布为 `ontology_vN+1`；否则保留 Parent，并把结果用于下一轮。

## 🚀 快速开始

Claude Code 与 Codex 直接从 GitHub Marketplace 安装插件，无需 clone 仓库、创建虚拟环境或单独执行 `pip install`；WorkBuddy 没有插件市场，改为从 clone 接入。

### 🤖 Claude Code

```bash
claude plugin marketplace add ruc-datalab/EvoOntology
claude plugin install evoontology@evoontology
claude plugin list
```

新建会话后运行：

```text
/evoontology:build-ontology
/evoontology:evolve-ontology
/evoontology:explore-ontology
```

### 🤖 Codex

```bash
codex plugin marketplace add ruc-datalab/EvoOntology
codex plugin add evoontology-codex@evoontology
codex plugin list
```

新建 thread 后，让 Codex 使用：

```text
$build-ontology
$evolve-ontology
$explore-ontology
```

Codex 自动按用户需求、相关项目历史和有依据的探索补足问题，不要求预先准备问题或轨迹文件。构建和进化结束后自动展示结果。SQLite 支持内置只读回放，其他数据源通过宿主工具执行并记录实际观察。详见 [Codex 插件](plugins/evoontology-codex/README.md)。

### 🖥️ WorkBuddy

WorkBuddy 没有插件市场，而是加载用户级 skill 与 MCP server。环境准备一次，之后运行安装脚本：

```bash
cd EvoOntology
python3 -m venv .venv && .venv/bin/pip install -e .
plugins/workbuddy/install.sh
```

重启会话后，让 agent 使用 `build-ontology`、`evolve-ontology` 或 `explore-ontology`。安装脚本会把三个 skill 拷入 `~/.workbuddy/skills/`，并以绝对路径把 `evo-semantic` MCP server 注册进 `~/.workbuddy/mcp.json`。手动步骤、工作区选择与平台注意事项见 [WorkBuddy 集成](plugins/workbuddy/README.zh-CN.md)。

三种接入方式使用相同的 Build / Evolve / Explore 工作流名称，仅调用方式与接入细节遵循各自平台约定。


构建完成后，Data Agent 可以直接调用 `browse_semantics` 和 `resolve_semantics`，无需额外配置 Ontology Layer。完整流程和数据边界请见[使用指南](USAGE.md)。

## 🔧 使用模式

| 模式 | 适用场景 | 评估边界 |
| --- | --- | --- |
| `fixed_split` | 具有固定问题集和 Ground Truth 的 benchmark | Construction 数据用于 Build 和诊断；Validation Reserve 只用于最终 gate。 |
| `rolling_trajectory` | 没有固定测试集的生产 workload 或冷启动项目 | 每个 checkpoint 后持续积累新轨迹，再用独立抽样任务或 LLM Judge 对 Candidate 进行门控。 |

两种模式共用同一套 `.evoontology/` workspace、`ontology_vN` 版本、checkpoint 和 Parent/Candidate 生命周期。

## 📊 性能表现

在四个 backbone 的分析子集上，Builder 构建的**初始 Ontology Layer**相比**无 Ontology Layer 的 ReAct**已获得提升；经过自进化后，**EvoOntology** 在三个 benchmark 上均进一步提升。

| Benchmark | 主要指标 | ReAct（无 Ontology Layer） | 初始 Ontology Layer | EvoOntology | 相对 ReAct 提升 |
| --- | --- | ---: | ---: | ---: | ---: |
| DDR-Bench（10-K） | Trajectory-Wise | 69.5 | 81.8 | **89.5** | **+20.0** |
| InsightBench | Insight | 53.2 | 54.0 | **54.2** | **+1.0** |
| BIRD | Execution Accuracy（EX） | 63.6 | 68.7 | **72.4** | **+8.8** |

<p align="center"><sub>结果采用<a href="https://arxiv.org/abs/2609.15779">论文</a>中的四 backbone 分析子集：GPT-5.5、GPT-5.6-sol、Claude-Sonnet-5 和 Claude-Opus-4.8。DDR-Bench 数值直接取自表 2、8；InsightBench 和 BIRD 数值为图 3 中四个分数的单小数均值，并与正文报告的分阶段提升一致。完整的六 backbone 结果和评估协议见表 1、3、4。</sub></p>

### 🧪 评估环境

EvoOntology 包含三个互补的、自包含的 Data Agent 评估环境：

| Benchmark | 任务 | 目录 |
| --- | --- | --- |
| BIRD | 面向真实数据库的 text-to-SQL | [`benchmarks/bird/`](benchmarks/bird/) |
| DDR-10K | 面向异构金融数据的开放式研究 | [`benchmarks/ddr_10k/`](benchmarks/ddr_10k/) |
| InsightBench | 迭代式业务分析和洞察生成 | [`benchmarks/insightbench/`](benchmarks/insightbench/) |

每个环境都实现 `EvolutionAdapter`，并保留原生 rollout 和评估协议。运行 `python -m benchmarks list` 可查看已注册环境；接入新环境请见[新增 Benchmark](docs/guide/new-benchmark.md)。

## 🗂️ 仓库结构

| 路径 | 职责 |
| --- | --- |
| [`assets/`](assets/) | README 媒体、框架图和 Ontology Layer 界面截图。 |
| [`evoontology/`](evoontology/) | 确定性核心：ontology store、runtime/MCP、trajectory、trigger、evaluation、evolution state、validation 和 visualization。 |
| [`plugins/`](plugins/) | 自包含的 Claude Code 与 Codex 插件（含 Build、Evolve、Visualize skills），以及复用同一套 skill 与语义 MCP 运行时的 WorkBuddy 集成。 |
| [`benchmarks/`](benchmarks/) | BIRD、DDR-10K 和 InsightBench 评估环境。 |
| [`docs/`](docs/) | 架构与 benchmark 接入文档。 |
| [`scripts/`](scripts/) | 将 core 同步到插件的工具。 |

## 📚 文档

- [使用指南](USAGE.md) — 安装、workspace、生命周期、配置和端到端流程。
- [架构说明](docs/architecture.md) — 模块边界、进化状态机和评估模式。
- [新增 Benchmark](docs/guide/new-benchmark.md) — adapter、data loader、rollout、配置和 seed skill 契约。
- [Claude Code 插件](plugins/claude-code/README.md)、[Codex 插件](plugins/evoontology-codex/README.md)与 [WorkBuddy 集成](plugins/workbuddy/README.zh-CN.md) — 各客户端的安装与使用方式。

## 🌐 社区分享与解读

感谢社区对 EvoOntology 的分享与讨论。

| 来源 | 内容 |
| --- | --- |
| [Gorden Sun · X](https://x.com/Gorden_Sun/status/2100846451375141145) | 介绍 EvoOntology 如何帮助数据 Agent 理解业务语义。 |
| [Bloss0m](https://www.bloss0m.com/paper-reading/50-evoontology-self-evolving-ontology/) | 围绕语义层、MCP 接口与受控进化的独立论文解读。 |

## ⭐ Star 趋势

<p align="center">
  <a href="https://www.star-history.com/#ruc-datalab/EvoOntology&amp;Date">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=ruc-datalab/EvoOntology&amp;type=Date&amp;theme=dark">
      <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=ruc-datalab/EvoOntology&amp;type=Date">
      <img src="https://api.star-history.com/svg?repos=ruc-datalab/EvoOntology&amp;type=Date" alt="EvoOntology Star History" width="100%">
    </picture>
  </a>
</p>

<sub>Star 与 Fork 统计对应官方仓库 ruc-datalab/EvoOntology。访问徽章显示今日 / 累计图片请求次数（北京时间），从接入后开始累计，受图片缓存影响，不等同于独立访客人数。</sub>

## 🖋 引用

如果本项目对您有帮助，请引用：

```bibtex
@misc{chong2026evoontologyselfevolvingontologylayer,
      title={EvoOntology: A Self-Evolving Ontology Layer for Data Agents},
      author={Meiduo Chong and Shaolei Zhang and Ju Fan and Xiaoyong Du},
      year={2026},
      eprint={2609.15779},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2609.15779},
}
```

## 📄 License

本项目使用 [MIT License](LICENSE)。Copyright © Meiduo Chong。
