#!/bin/bash
# The repository's GitHub settings as code (MAINTAINING.md): OpenTofu on infra/github.
# The token comes from the environment and lives only in this process.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# A rule for agents, not a wall: the settings are the maintainer's to change.
if [[ -n "${CLAUDECODE:-}" ]] || compgen -v CODEX_ >/dev/null; then
  echo "github-settings: plan and apply are the maintainer's; an agent does not run them." >&2
  exit 1
fi

if [[ $# -eq 0 ]]; then
  echo "Usage: scripts/github-settings.sh init|plan|apply [tofu arguments]" >&2
  exit 64
fi

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "github-settings: set GITHUB_TOKEN to a fine-grained token for kromsam/shaffy-os alone with Administration read and write." >&2
  exit 1
fi

# Outside every checkout, and apart from ~/.local/state/shaffy, which Shaffy OS owns.
if [[ "$1" == init ]]; then
  state_dir="${XDG_STATE_HOME:-${HOME}/.local/state}/shaffy-os-infra"
  mkdir -p "${state_dir}"
  shift
  set -- init "-backend-config=path=${state_dir}/github.tfstate" "$@"
fi

# OpenTofu is a development tool of the checkout (mise.dev.toml).
MISE_ENV=dev exec mise exec -- tofu -chdir=infra/github "$@"
