#!/usr/bin/env bash
# User tools use reviewed upstream releases; private state never enters Git.
set -euo pipefail
[[ $(id -u) != 0 && $HOME == "$(getent passwd "$(id -un)" | cut -d: -f6)" ]] || {
  echo 'Run as the desktop user with their own HOME, without sudo.' >&2
  exit 1
}
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
pin() {  # the version home/dot_config/mise/config.toml pins for a tool
  /usr/bin/python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["tools"][sys.argv[2]])' \
    "$repo/home/dot_config/mise/config.toml" "$1"
}
tools=()
for tool in claude codex bun bitwarden cargo:raine/workmux; do
  tools+=("$tool@$(pin "$tool")")
done
mise install -y "${tools[@]}"

uv tool install --python /usr/bin/python3 --torch-backend cpu 'zotero-mcp-server[semantic,pdf]==0.12.4'

gbrain_source="$HOME/.local/share/gbrain/source"
gbrain_revision=d13aa742fd68b71bfd6c98be3dda5813791f1d6c
if [[ ! -d "$gbrain_source/.git" ]]; then
  [[ ! -e "$gbrain_source" ]]
  mkdir -p "$(dirname -- "$gbrain_source")"
  git clone https://github.com/garrytan/gbrain.git "$gbrain_source"
fi
[[ -z $(git -C "$gbrain_source" status --porcelain) ]]
if [[ $(git -C "$gbrain_source" rev-parse HEAD) != "$gbrain_revision" ]]; then
  git -C "$gbrain_source" fetch origin "$gbrain_revision"
  git -C "$gbrain_source" checkout --detach "$gbrain_revision"
fi
cd "$gbrain_source"
# Upstream postinstall migrates an existing database. Run migrations deliberately,
# never as an install side effect.
mise exec "bun@$(pin bun)" -- bun install --frozen-lockfile --ignore-scripts
mise exec "bun@$(pin bun)" -- bun src/cli.ts version
