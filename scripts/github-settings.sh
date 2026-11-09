#!/bin/bash
# The repository's GitHub settings as code (MAINTAINING.md): OpenTofu on infra/github.
# The token and the state passphrase live only in this process.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# A rule for agents, not a wall: it keeps them out of the maintainer's Proton Pass session.
if [[ -n "${CLAUDECODE:-}" ]] || compgen -v CODEX_ >/dev/null; then
  echo "github-settings: plan and apply are the maintainer's; an agent does not run them." >&2
  exit 1
fi

if [[ $# -eq 0 ]]; then
  echo "Usage: scripts/github-settings.sh init|plan|apply [tofu arguments]" >&2
  exit 64
fi

GITHUB_TOKEN="$(gh auth token --user kromsam)"
export GITHUB_TOKEN
TF_VAR_state_encryption_passphrase="$(
  pass-cli item view --vault-name 'Shaffy OS' --item-title 'OpenTofu state' --field password
)"
export TF_VAR_state_encryption_passphrase

# Outside every checkout, and apart from ~/.local/state/shaffy, which Shaffy OS owns.
if [[ "$1" == init ]]; then
  state_dir="${XDG_STATE_HOME:-${HOME}/.local/state}/shaffy-os-infra"
  mkdir -p "${state_dir}"
  shift
  set -- init "-backend-config=path=${state_dir}/github.tfstate" "$@"
fi

# OpenTofu is a development tool of the checkout (mise.dev.toml).
MISE_ENV=dev exec mise exec -- tofu -chdir=infra/github "$@"
