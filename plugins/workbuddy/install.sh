#!/usr/bin/env bash
#
# Wire EvoOntology into WorkBuddy.
#
# Copies the three Claude Code skills into the user-level skill directory and
# registers the semantic MCP server in the user-level mcp.json, using absolute
# paths because WorkBuddy expands no variables in that file.
#
# The script is idempotent: existing skill directories are moved aside rather
# than deleted, and mcp.json is backed up before it is rewritten.

set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)

skills_source="$repo_root/plugins/claude-code/skills"
skills_dir="${WORKBUDDY_SKILLS_DIR:-$HOME/.workbuddy/skills}"
mcp_json="${WORKBUDDY_MCP_JSON:-$HOME/.workbuddy/mcp.json}"
server_name="evo-semantic"
store="$repo_root/benchmarks/ddr_10k/.evoontology"
python_bin="$repo_root/.venv/bin/python"
dry_run=0

usage() {
  cat <<'EOF'
Usage: install.sh [options]

  --store PATH        Ontology workspace to serve (default: <repo>/benchmarks/ddr_10k/.evoontology)
  --name NAME         MCP server entry name (default: evo-semantic)
  --python PATH       Interpreter for the MCP entry (default: <repo>/.venv/bin/python)
  --skills-dir PATH   Skill directory (default: $HOME/.workbuddy/skills)
  --mcp-json PATH     MCP configuration file (default: $HOME/.workbuddy/mcp.json)
  --dry-run           Print the planned actions without touching anything
  -h, --help          Show this message
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --store) store=${2:?--store needs a value}; shift 2 ;;
    --name) server_name=${2:?--name needs a value}; shift 2 ;;
    --python) python_bin=${2:?--python needs a value}; shift 2 ;;
    --skills-dir) skills_dir=${2:?--skills-dir needs a value}; shift 2 ;;
    --mcp-json) mcp_json=${2:?--mcp-json needs a value}; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "install.sh: unknown argument '$1'" >&2; usage >&2; exit 64 ;;
  esac
done

if [ ! -d "$skills_source" ]; then
  echo "install.sh: skill source not found at $skills_source" >&2
  echo "           run this script from a full repository clone" >&2
  exit 66
fi

if [ ! -x "$python_bin" ]; then
  echo "install.sh: no interpreter at $python_bin" >&2
  echo "           create it first:  python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 66
fi

if ! "$python_bin" -c "import evoontology" >/dev/null 2>&1; then
  echo "install.sh: 'evoontology' is not importable by $python_bin" >&2
  echo "           run:  $repo_root/.venv/bin/pip install -e $repo_root" >&2
  exit 66
fi

case "$store" in
  /*) ;;
  *) echo "install.sh: --store must be an absolute path (got '$store')" >&2; exit 64 ;;
esac

timestamp=$(date +%Y%m%d-%H%M%S)

# --- skills ---------------------------------------------------------------
for skill in build-ontology evolve-ontology explore-ontology; do
  target="$skills_dir/$skill"
  if [ -e "$target" ]; then
    if diff -rq "$skills_source/$skill" "$target" >/dev/null 2>&1; then
      echo "skill   $skill: up to date"
      continue
    fi
    backup="$target.bak.$timestamp"
    echo "skill   $skill: moving existing copy to $(basename "$backup")"
    [ "$dry_run" -eq 1 ] || mv "$target" "$backup"
  else
    echo "skill   $skill: installing"
  fi
  [ "$dry_run" -eq 1 ] || mkdir -p "$skills_dir"
  [ "$dry_run" -eq 1 ] || cp -R "$skills_source/$skill" "$target"
done

# --- MCP registration -----------------------------------------------------
[ -d "$(dirname "$mcp_json")" ] || { [ "$dry_run" -eq 1 ] || mkdir -p "$(dirname "$mcp_json")"; }

if [ "$dry_run" -eq 1 ]; then
  echo "mcp     would register '$server_name' -> $python_bin (store: $store)"
else
  "$python_bin" - "$mcp_json" "$server_name" "$python_bin" "$store" "$timestamp" <<'PY'
import json
import pathlib
import shutil
import sys

path = pathlib.Path(sys.argv[1])
name, command, store, stamp = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]

data = {}
if path.is_file():
    text = path.read_text(encoding="utf-8").strip()
    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            sys.exit(f"{path} is not valid JSON ({exc}); fix or remove it and re-run")

servers = data.setdefault("mcpServers", {})
entry = {
    "command": command,
    "args": ["-m", "evoontology.runtime.mcp_server", "--store", store],
    "disabled": False,
}

if servers.get(name) == entry:
    print(f"mcp     '{name}' already up to date")
else:
    action = "updated" if name in servers else "registered"
    if path.is_file():
        backup = path.with_name(f"{path.name}.bak.{stamp}")
        shutil.copy2(path, backup)
        print(f"mcp     backed up {path.name} to {backup.name}")
    servers[name] = entry
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"mcp     '{name}' {action}")
PY
fi

cat <<EOF

Done. Restart the WorkBuddy session so the new skills and MCP server are picked up.

Verify with:
  1. '$server_name' appears in the MCP server list
  2. the agent can call resolve_semantics on a term you know exists

Shared settings, if you serve a different workspace or use another client:
  --store '$store'
  --mcp-json '$mcp_json'
EOF
