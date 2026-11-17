<p align="center">
  <a href="README.md">English</a> | 简体中文
</p>

# EvoOntology — WorkBuddy 集成

WorkBuddy 从 `~/.workbuddy/skills/` 读取**用户级 skill**，从 `~/.workbuddy/mcp.json` 读取 **MCP server**。它没有插件市场，也不做 `${PLUGIN_ROOT}` 变量替换，因此本集成不走插件清单，而是把同一套三个 skill 与同一个语义 MCP 运行时通过上述两个路径接入。

三个 skill 与 [`plugins/claude-code/skills/`](../claude-code/skills/) 逐字节一致，本目录直接复用而非另存一份，差异只在接入方式。

## 组件

| 组件 | 位置 | 作用 |
| --- | --- | --- |
| Build / Evolve / Explore skill | `~/.workbuddy/skills/{build,evolve,explore}-ontology/` | 从 `plugins/claude-code/skills/` 拷贝 |
| 语义 MCP server | `~/.workbuddy/mcp.json` → `evo-semantic` | 绝对路径 stdio 条目，见 [`mcp.example.json`](mcp.example.json) |
| 确定性核心 | `pip install -e .` | 语义 MCP 运行时、store、trajectory、trigger、评测 gate |

## 前置条件

| 条件 | 说明 |
| --- | --- |
| Python 3.10+ | 已在 CPython 3.12.12 / macOS 上验证 |
| 仓库 clone | clone 的绝对路径会被写入 `mcp.json`，请保持稳定 |
| OpenAI 兼容端点 | 仅在运行 benchmark agent 时需要 |
| DDR-10K 数据 | 仅 `benchmarks/ddr_10k` 运行需要，见 [数据布局](#数据布局) |

## 安装

直接运行 [`install.sh`](install.sh) 完成全部步骤，或按下列步骤手动执行。

### 1. 创建环境

```bash
git clone https://github.com/ruc-datalab/EvoOntology.git
cd EvoOntology
python3 -m venv .venv
.venv/bin/pip install -e .
```

核心包不声明任何第三方运行时依赖，因此裸虚拟环境即可运行语义 MCP server：

```bash
.venv/bin/python -m evoontology.runtime.mcp_server --store <workspace>
```

这也是下方条目用 `python -m` 直接启动、而不经插件里 `scripts/launch-evoontology` 包装脚本的原因：那个脚本用于定位插件内置的 core 包，WorkBuddy 安装并没有内置副本。

### 2. 注册语义 MCP server

把下面这段合并进 `~/.workbuddy/mcp.json`，两个占位符都替换为绝对路径，且不要删掉已有 server。

```json
{
  "mcpServers": {
    "evo-semantic": {
      "command": "/absolute/path/to/EvoOntology/.venv/bin/python",
      "args": [
        "-m",
        "evoontology.runtime.mcp_server",
        "--store",
        "/absolute/path/to/EvoOntology/benchmarks/ddr_10k/.evoontology"
      ],
      "disabled": false
    }
  }
}
```

`--store` 指定本体工作区。`mcp.json` 是用户级而非项目级配置，且 WorkBuddy 不对其内容做变量展开，因此路径必须写死。要换工作区就改 `--store`；要同时服务多个工作区，就再注册若干条目（例如 `evo-semantic-bird`）各自带 `--store`。

客户端会以 `initialize` / `notifications/initialized` 握手拉起该 server，随后可见 `browse_semantics`、`resolve_semantics` 两个导航工具与 `evo-semantic://session-manifest` 资源。Build / Evolve / Explore 通过同一连接访问其余工具（`publish_ontology_build`、`finalize_evolution_run`、`visualize_ontology` 及 evolution session 系列）。

### 3. 安装三个 skill

```bash
mkdir -p ~/.workbuddy/skills
cp -R plugins/claude-code/skills/build-ontology   ~/.workbuddy/skills/
cp -R plugins/claude-code/skills/evolve-ontology  ~/.workbuddy/skills/
cp -R plugins/claude-code/skills/explore-ontology ~/.workbuddy/skills/
```

skill 目录顶层必须含 `SKILL.md`，拷贝后 `references/` 子目录保持同级。WorkBuddy 在会话启动时加载 skill，拷贝后需重启会话。

### 4. 验证

新开一个会话，确认 MCP server 列表中出现 `evo-semantic`；再让 agent 对一个确定存在的术语调用 `resolve_semantics`。返回工具结果而不是 "no such tool" 错误，即说明注册与 `--store` 路径都正确。

## 运行 benchmark

DDR-10K 的 agent 会拉起一个基于 `mcp` SDK 的 SQLite MCP 工具服务，这与零依赖的核心是两套依赖面：

```bash
.venv/bin/pip install -r benchmarks/ddr_10k/requirements.txt
.venv/bin/pip install "pydantic<2.14"
```

第二行为何不可省略见 [已知问题](#已知问题与修复)。之后按 [`benchmarks/ddr_10k/README.md`](../../benchmarks/ddr_10k/README.zh-CN.md) 的顺序执行即可。

### 数据布局

把 DDR-10K 产物放到 `benchmarks/ddr_10k/data/` 下；仓库自带配置只引用这三个路径。

```text
benchmarks/ddr_10k/data/
├── 10k_financial_data.db   # SQLite 财务事实库
├── entity_ids.json         # 公司标识有序列表
└── qa.json                 # {"metadata": ..., "results": [...]} 题集
```

本体工作区放在相邻的 `benchmarks/ddr_10k/.evoontology/`，使 MCP 的 `--store` 路径与场景 `log_dir` 落在同一处口径。两个目录均已由 `.gitignore` 排除。

## 与 Claude Code 插件的差异

| 能力 | Claude Code | WorkBuddy |
| --- | --- | --- |
| skill 调用 | `/evoontology:build-ontology` | 直接用 skill 名 `build-ontology`，由描述自动匹配 |
| skill 与 MCP 接入 | 插件市场安装 | 手动拷入 `~/.workbuddy/skills/` 与 `~/.workbuddy/mcp.json` |
| MCP 路径解析 | 由插件展开 `${CLAUDE_PLUGIN_ROOT}` | 仅支持字面绝对路径 |
| 工作区选择 | 在插件 `.mcp.json` 的 `args` 追加 `"--store", "<root>"` | 改用户级 `mcp.json` 的 `--store` |
| Session Start 进化提醒 | `hooks/hooks.json` 的 SessionStart hook | 不可移植，改为按需检查（见下） |

提醒 hook 输出的是 Claude Code 专有的 `hookSpecificOutput` 结构，无法照搬。改为按需检查——在工作区根目录用 hook 内部同一个 trigger：

```bash
.venv/bin/python -c "
from evoontology import EvolutionTrigger
t = EvolutionTrigger('.evoontology'); t.initialize(); print(t.check())
"
```

默认累计 30 条新轨迹或满 7 天后 `evolution_due` 为真。为真时让 agent 运行 `evolve-ontology`；两个客户端都不会自动进化。

## 已知问题与修复

| 现象 | 原因 | 修复 |
| --- | --- | --- |
| benchmark 侧任意模块 `import mcp` 时报 `ImportError: cannot import name 'eval_type_backport' from 'pydantic._internal._typing_extra'` | `mcp==1.12.0` 在 `mcp/server/fastmcp/utilities/func_metadata.py` 中导入 `eval_type_backport`，而 pydantic 2.14 移除了该符号。`requirements.txt` 钉了 `mcp` 却没钉 pydantic，全新安装会解析到 2.14.x，导致每次 `import mcp` 失败 | `pip install "pydantic<2.14"`。实测：2.11.10 与 2.13.0 正常，2.14.3 报错 |
| 评估器请求 `/v1/v1/chat/completions` 返回 `404` | `run_evaluation.py` 以 `f"{base_url.rstrip('/')}/v1/chat/completions"` 拼 URL，因此 `base_url` 必须是 API 根 | 把 `vllm_base_url` / `base_url` 配成**不带** `/v1` 的地址，例如 `https://host/openapi` |
| 在沙箱化的 agent shell 里 `pip install` 中断 | 宿主的批量文件删除守卫会拦截 pip 替换 `site-packages` 包时的删除动作 | 在普通终端执行安装，或仅对该条命令绕开守卫（`env -u <GUARD_VAR> .venv/bin/pip install ...`） |
| 拷贝后 skill 不出现 | skill 与 `mcp.json` 只在会话启动时读取一次 | 重启会话 |
| `publish_ontology_build` 报 `An active ontology already exists; evolve it instead` | 一个工作区仅一个 active 版本是刻意设定的不变量，不是缺陷 | 对 active 版本运行 `evolve-ontology`；确实要从零重建时，再删除工作区内的 `versions/<name>/` 与 `active.json` |
| `resolve_semantics` 对某术语返回 `ambiguous` | 两个 Term 抢同一个 alias | 让每个 alias 只归属一个 Term；`ambiguous` 本身是「本体需要修正」的正确信号 |

后两条是本体层行为而非 WorkBuddy 特有，任何客户端都能复现。列出它们是因为首次运行很容易误判为安装失败。

## 验证情况

本文档写成前，已在 WorkBuddy 上针对 DDR-10K 的 5 家公司 smoke 切片完整跑通：

| 环节 | 工具 |
| --- | --- |
| 配置并划定工作区 | `configure_ontology_project`、`prepare_ontology_workload` |
| 构建与发布 | `save_version`、`validate_semantics`、`annotate_ontology_version`、`publish_ontology_build` |
| agent 侧消费 | `resolve_semantics`、`browse_semantics` |
| 进化 | `start_evolution_run`、`finalize_evolution_run` |

全部经由上述 stdio 连接正常返回，未需要任何 WorkBuddy 专属代码路径。
