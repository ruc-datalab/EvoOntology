<p align="center">
  English | <a href="USAGE.zh-CN.md">简体中文</a>
</p>

# EvoOntology Product Guide

This guide explains how to use EvoOntology in practice. The product extracts the capabilities shared by the three benchmarks into one deterministic core package and two self-contained plugins:

- `evoontology/` — benchmark-independent runtime for the ontology store, MCP runtime, trajectories, triggers, evaluation, evolution lifecycle, and validation gate.
- `plugins/` — Claude Code and Codex plugins that both provide `build-ontology`, `evolve-ontology`, and `explore-ontology` skills plus MCP; Claude Code also includes a Session Start reminder. Both bundle the same core.

The product consists of one core package, including the validation gate, and three skills. It has no CLI. Intelligent analysis stays in the skills; Python implements only the runtime, minimal deterministic validation, and the evolution lifecycle state machine. The default is zero configuration: users do not need to provide a workspace path, evaluation mode, judge model, or trigger parameters.

---

## 1. Installation

Choose your client. Claude Code and Codex install through their Marketplace and need no repository clone, virtual environment, or separate `pip install`. WorkBuddy has no Marketplace and is wired from a clone instead.

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

Adding the Marketplace does not install the plugin. Confirm that the final `plugin list` reports it as installed and enabled. After installing or updating, start a new session and invoke the client's `build-ontology` skill.

### WorkBuddy

```bash
git clone https://github.com/ruc-datalab/EvoOntology.git
cd EvoOntology
python3 -m venv .venv && .venv/bin/pip install -e .
plugins/workbuddy/install.sh
```

The installer copies the three skills to `~/.workbuddy/skills/` and registers the `evo-semantic` MCP server in `~/.workbuddy/mcp.json`. Both paths are absolute because WorkBuddy expands no variables in that file. Restart the session so both take effect; see the [WorkBuddy integration](plugins/workbuddy/README.md) for manual steps, workspace selection, and platform caveats.

---

## 2. Workspace layout

By default, the workspace is `.evoontology/` at the project root. It is created automatically the first time `build-ontology` runs:

```text
.evoontology/
├── project.json         # mode / data source / workload / evaluator / boundary
├── active.json          # {"active_version": "ontology_v0"}
├── versions/            # all versions: published ontology_vN and candidate vN-cK; five JSON files per version
├── trajectories/        # one JSON trajectory per task
├── evolution/           # one run_N/ directory per evolution run
│   └── run_N/
│       ├── run.json                 # status / Parent / current Candidate / round / frozen budget
│       ├── trajectory-sources.json  # user-confirmed trajectory source records
│       ├── rounds.jsonl             # one summary per round
│       └── evaluations/             # formal Parent/Candidate evaluation summaries
└── state.json           # trigger checkpoint and thresholds
```

Each version contains five record files for Term, Mapping, Relation, Constraint, and Evidence objects. The Data Agent runtime on the benchmark-adapter side appends a trajectory to `trajectories/` after each task.

Workspace initialization is staged. After Step 0 is confirmed, EvoOntology writes `project.json`. Only after the initial version is saved and passes the semantic MCP's `validate_semantics` check with `version` set to `ontology_v0` does it write `active.json` and `state.json`. The core resolves `<project-root>/.evoontology/` by default; a benchmark may pass another path explicitly.

### Choosing a mode

- `fixed_split`: for benchmarks with a fixed question set, ground truth, and evaluation boundary. The Construction Pool is used for Build and diagnosis; the Validation Reserve is used only for the final gate. Reuse the official split instead of creating random folds.
- `rolling_trajectory`: for production use or cold starts. Initialize from a seed workload, then collect new task trajectories after each checkpoint. Without ground truth, compare Parent and Candidate using independently sampled tasks and an LLM Judge; do not force a Fold A/B split.

The mode is written to `project.json` after Step 0 confirmation and reused by Build and Evolve.

---

## 3. Skill entry points

| Workflow | Claude Code | Codex | Purpose |
| --- | --- | --- | --- |
| Build | `/evoontology:build-ontology` | `$build-ontology` | Build and publish `ontology_v0` |
| Evolve | `/evoontology:evolve-ontology` | `$evolve-ontology` | Diagnose → attribute → patch → Parent/Candidate gate → publish |
| Explore | `/evoontology:explore-ontology` | `$explore-ontology` | Read-only exploration of questions, evidence, results, and version differences |

The agent follows the relevant skill for all three entry points; they are not deterministic Python operations. See [versioning](plugins/claude-code/docs/versioning.md) for the naming and switching rules: published versions use `ontology_vN`, candidates use `vN-cK`, and acceptance maps `vN-cK` to `ontology_vN+1`.

---

## 4. Evolution loop: EvolutionSession

Each `evolve-ontology` invocation corresponds to one run managed by the core `EvolutionSession` state machine. The skill decides what to change and why; the session ensures that a run cannot end incorrectly:

```text
running ──Reject──▶ running (design the next Candidate in the same run)
running ──Accept──▶ accepted (publish a new version and advance the checkpoint)
running ──budget exhausted / user interruption / missing data / unreliable evaluation──▶ incomplete
```

### At the start of a new run

1. **Resume first**: resume an unfinished run instead of starting another one.
2. **Freeze data**: `fixed_split` reuses persisted training and validation subsets; `rolling_trajectory` collects eligible trajectories after the checkpoint, freezes the batch, and splits it into an Evolution Pool and Validation Reserve.
3. **Confirm the budget**: explain the planned number of rounds, eight by default, and freeze it in `run.json` after confirmation. A resumed run retains its confirmed budget. Extending an exhausted budget requires confirmation again.
4. **Confirm trajectory sources**: if a source or scope is unresolved, explain each source's path, content scope, time range, and purpose, then write the confirmed selection to `run_N/trajectory-sources.json`. A new run reuses the latest source record by default and verifies that paths remain valid; reconfirm only when sources are added, become invalid, or change scope. If no trajectory is available, run the Parent baseline first and diagnose from its evaluation results, errors, and counterexamples.

### During the loop

- Diagnose → attribute → patch: choose one primary mechanism across **Content / Tool / Schema**. Each Candidate tests one primary hypothesis; every change must trace to the target dimension and be reversible to the Parent.
- Evaluate: evaluate the Candidate from its own stored version with `--semantic-version` and do not modify `active.json` during comparison. Use absolute scoring with ground truth, or anonymous LLM Judge A/B comparison without it.
- **Reject is not terminal**: append a summary to `rounds.jsonl`, update attribution and the problem map, then design the next Candidate. Do not advance the checkpoint or end the run.
- **Accept ends the search** and begins finalization.

### Finalization

After Accept: deterministic validation → publish as `ontology_vN+1` without overwriting any published version → update `active.json` → advance the checkpoint once → mark the run `accepted`.

Incomplete runs neither publish nor advance the checkpoint; the same batch is retried in the next run. Judgment-based stop reasons such as `missing_data`, `unreliable_evaluation`, and `external_block` require at least `min_rejects_before_incomplete` formal candidate rejections in the same run, two by default. Only `user_interrupted` and `missing_permissions` may stop immediately. The final report must be based on the session terminal state and persisted records, not conversation memory.

---

## 5. Configuration: zero configuration by default

There is no default `config.yaml`. To change behavior, tell Claude or Codex directly—for example, “Remind me after every 60 tasks.” The agent updates internal state in `state.json` rather than a configuration file.

- Evolution triggers by default after at least 30 new tasks since the checkpoint or at least seven days. The first checkpoint is the publication time of `ontology_v0`. **Only a formal gate Accept advances the checkpoint**; Reject continues within the same run, and Incomplete does not advance it.
- The evaluation protocol is selected automatically: use ground truth when the benchmark provides an evaluator, otherwise use an LLM Judge. See [evaluation protocol](plugins/claude-code/docs/evaluation-protocol.md).

---

## 6. MCP integration

The plugin uses `.mcp.json` to spawn the service as a module. The client starts it automatically, so no manual server process is required. The default workspace is `.evoontology/` in the current project. WorkBuddy instead registers the same server in the user-level `~/.workbuddy/mcp.json` with an absolute `--store` path; see the [WorkBuddy integration](plugins/workbuddy/README.md).

The Data Agent receives:

- `browse_semantics(query, kind, limit)` — discover relevant concepts;
- `resolve_semantics(mentions, context)` — resolve concepts to grounded mappings and related relations, constraints, and evidence;
- `evo-semantic://session-manifest` — a concise resource read at session start.

The same `evo-semantic` service exposes deterministic operations to Build, Evolve, and Visualize, including `validate_semantics`, `visualize_ontology`, `evolution_status`, version helpers, and evolution-session tools. Plugin-only installation therefore does not require `python -m evoontology...` in the user's project.

The two navigation tools return metadata and guidance. Database queries and Python execution remain the responsibility of the benchmark's native tools.

---

## 7. Validation gate

Before `build-ontology` or `evolve-ontology` publishes a version, the agent automatically invokes the semantic MCP's `validate_semantics` tool. It checks valid JSON, complete references, and loadability; users do not run it manually.

Validation is structural only. Database-semantic checks—whether tables and columns exist, mappings execute, and evidence can be reproduced—belong to the Builder's exploration phase.

---

## 8. Minimal end-to-end flow

```bash
# 1. Install through the Claude Code or Codex Marketplace, or wire WorkBuddy from a clone (Section 1)

# 2. Build ontology_v0 (Claude Code / Codex)
/evoontology:build-ontology
$build-ontology

# 3. Connect the Data Agent through MCP (.mcp.json; the client spawns it automatically)

# 4. Trigger evolution (Claude Code / Codex), or wait for the trajectory-threshold reminder
/evoontology:evolve-ontology
$evolve-ontology   # the agent loops over Candidates using semantic MCP evolution tools;
                   # Accept validates, publishes, updates active.json, and advances the checkpoint
```

The agent automatically calls `validate_semantics` before publication.

---

## 9. Out of scope for the first release

Web UI, SaaS, multitenancy, message queues, resident workers, parallel Candidates, automatic loops, and frequent schema changes are outside this release. Fully unattended evolution requires a resident background worker; the first release provides detection and reminders, with execution initiated by a person.
