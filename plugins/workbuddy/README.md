<p align="center">
  English | <a href="README.zh-CN.md">简体中文</a>
</p>

# EvoOntology — WorkBuddy Integration

WorkBuddy discovers **user-level skills** in `~/.workbuddy/skills/` and **MCP servers** in `~/.workbuddy/mcp.json`. It has no plugin marketplace and no `${PLUGIN_ROOT}` substitution, so this integration wires the same three skills and the same semantic MCP runtime through those two paths instead of through a plugin manifest.

The skills are byte-identical to [`plugins/claude-code/skills/`](../claude-code/skills/), so this directory reuses them instead of shipping a second copy. Only the wiring differs.

## Components

| Component | Location | Purpose |
| --- | --- | --- |
| Build / Evolve / Explore skills | `~/.workbuddy/skills/{build,evolve,explore}-ontology/` | Copied from `plugins/claude-code/skills/` |
| Semantic MCP server | `~/.workbuddy/mcp.json` → `evo-semantic` | Absolute-path stdio entry, see [`mcp.example.json`](mcp.example.json) |
| Deterministic core | `pip install -e .` | Semantic MCP runtime, store, trajectories, trigger, evaluation gate |

## Prerequisites

| Requirement | Notes |
| --- | --- |
| Python 3.10+ | Verified on CPython 3.12.12, macOS |
| Repository clone | The clone's absolute path is baked into `mcp.json`, so keep it stable |
| OpenAI-compatible endpoint | Only needed to run a benchmark agent |
| DDR-10K data | Only needed for `benchmarks/ddr_10k` runs; see [Data layout](#data-layout) |

## Installation

Run [`install.sh`](install.sh) for the whole flow, or follow the steps below manually.

### 1. Create the environment

```bash
git clone https://github.com/ruc-datalab/EvoOntology.git
cd EvoOntology
python3 -m venv .venv
.venv/bin/pip install -e .
```

The core package declares no third-party runtime dependencies, so the semantic MCP server runs on a bare virtual environment:

```bash
.venv/bin/python -m evoontology.runtime.mcp_server --store <workspace>
```

This is why the entry below launches `python -m` directly instead of going through the bundled `scripts/launch-evoontology` wrapper: the wrapper exists to locate the plugin's vendored core, which a WorkBuddy install does not have.

### 2. Register the semantic MCP server

Merge the following into `~/.workbuddy/mcp.json`, replacing both placeholders with absolute paths. Do not remove servers that are already present.

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

`--store` selects the ontology workspace. `mcp.json` is user-global rather than project-scoped, and WorkBuddy expands no variables inside it, so the path must be literal. To serve a different workspace, change `--store`; to serve several at once, register additional entries such as `evo-semantic-bird` with their own `--store`.

The client starts the server with the `initialize` / `notifications/initialized` handshake and then sees `browse_semantics`, `resolve_semantics`, and the `evo-semantic://session-manifest` resource. Build, Evolve, and Explore reach the remaining tools (`publish_ontology_build`, `finalize_evolution_run`, `visualize_ontology`, and the evolution-session tools) through the same connection.

### 3. Install the three skills

```bash
mkdir -p ~/.workbuddy/skills
cp -R plugins/claude-code/skills/build-ontology   ~/.workbuddy/skills/
cp -R plugins/claude-code/skills/evolve-ontology  ~/.workbuddy/skills/
cp -R plugins/claude-code/skills/explore-ontology ~/.workbuddy/skills/
```

A skill directory must contain `SKILL.md` at its top level; the copies keep the `references/` subdirectory alongside it. WorkBuddy loads skills at session start, so restart the session after copying.

### 4. Verify

Start a new session and confirm that `evo-semantic` appears in the MCP server list. Then ask the agent to call `resolve_semantics` on a term you know exists. A tool result rather than a "no such tool" error confirms both the registration and the `--store` path.

## Benchmark runs

The DDR-10K agent spawns a SQLite MCP tool server built on the `mcp` SDK, which is a separate dependency surface from the dependency-free core:

```bash
.venv/bin/pip install -r benchmarks/ddr_10k/requirements.txt
.venv/bin/pip install "pydantic<2.14"
```

See [Known issues](#known-issues-and-fixes) for why the second line is not optional. Runs then follow the sequence in [`benchmarks/ddr_10k/README.md`](../../benchmarks/ddr_10k/README.md).

### Data layout

Place the DDR-10K artifacts under `benchmarks/ddr_10k/data/`; the shipped configurations reference these three paths and nothing else.

```text
benchmarks/ddr_10k/data/
├── 10k_financial_data.db   # SQLite financial-fact store
├── entity_ids.json         # ordered list of company identifiers
└── qa.json                 # {"metadata": ..., "results": [...]} question set
```

The ontology workspace stays next to it in `benchmarks/ddr_10k/.evoontology/`, which keeps the MCP `--store` path and the scenario `log_dir` in one place per benchmark. Both directories are ignored by `.gitignore`.

## Differences from the Claude Code plugin

| Capability | Claude Code | WorkBuddy |
| --- | --- | --- |
| Skill invocation | `/evoontology:build-ontology` | Skill name `build-ontology`; the agent selects it from its description |
| Skill and MCP wiring | Plugin marketplace install | Manual copies into `~/.workbuddy/skills/` and `~/.workbuddy/mcp.json` |
| MCP path resolution | `${CLAUDE_PLUGIN_ROOT}` expanded from the plugin | Literal absolute paths only |
| Workspace selection | Append `"--store", "<root>"` to the plugin `.mcp.json` | Edit `--store` in the user-global `mcp.json` |
| Session-start evolution reminder | `hooks/hooks.json` SessionStart hook | Not portable; run the check on demand (below) |

The reminder hook emits a Claude-Code-specific `hookSpecificOutput` payload, so it does not carry over. Check on demand instead — from the workspace root, using the same trigger the hook uses:

```bash
.venv/bin/python -c "
from evoontology import EvolutionTrigger
t = EvolutionTrigger('.evoontology'); t.initialize(); print(t.check())
"
```

`evolution_due` turns true after the default 30 new trajectories or 7 days since the checkpoint. Ask the agent to run `evolve-ontology` when it does; evolution never starts automatically in either client.

## Known issues and fixes

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ImportError: cannot import name 'eval_type_backport' from 'pydantic._internal._typing_extra'` when any benchmark module imports `mcp` | `mcp==1.12.0` imports `eval_type_backport` in `mcp/server/fastmcp/utilities/func_metadata.py`, and pydantic 2.14 removed that symbol. `requirements.txt` pins `mcp` but not pydantic, so a fresh install resolves to 2.14.x and every `import mcp` fails | `pip install "pydantic<2.14"`. Verified: 2.11.10 and 2.13.0 import cleanly; 2.14.3 fails |
| `404` from the evaluator on `/v1/v1/chat/completions` | `run_evaluation.py` builds its URL as `f"{base_url.rstrip('/')}/v1/chat/completions"`, so `base_url` must be the API root | Set `vllm_base_url` / `base_url` **without** a trailing `/v1`, e.g. `https://host/openapi` |
| `pip install` aborts inside a sandboxed agent shell | Host file-deletion guards intercept the removals pip performs while replacing packages in `site-packages` | Run the install in a normal terminal, or scope the guard away for that one command (`env -u <GUARD_VAR> .venv/bin/pip install ...`) |
| A skill does not appear after copying it | Skills and `mcp.json` are read once at session start | Restart the session |
| `publish_ontology_build` returns `An active ontology already exists; evolve it instead` | One active version per workspace is a deliberate invariant, not a bug | Run `evolve-ontology` against the active version, or remove `versions/<name>/` plus `active.json` from the workspace if you really mean to rebuild from scratch |
| `resolve_semantics` returns `ambiguous` for a term | Two Terms claim the same alias | Keep each alias on exactly one Term; `ambiguous` is the correct signal that the ontology itself needs fixing |

The last two are ontology-layer behaviour rather than WorkBuddy quirks, and they reproduce in every client. They are listed because they are easy to misread as installation failures during a first run.

## Verification

The following was exercised end to end on WorkBuddy against DDR-10K before this document was written, on the 5-company smoke split:

| Step | Tools |
| --- | --- |
| Configure and scope the project | `configure_ontology_project`, `prepare_ontology_workload` |
| Build and publish | `save_version`, `validate_semantics`, `annotate_ontology_version`, `publish_ontology_build` |
| Consume from the agent | `resolve_semantics`, `browse_semantics` |
| Evolve | `start_evolution_run`, `finalize_evolution_run` |

All of them returned normal results over the stdio connection described above; no WorkBuddy-specific code path was required.
