#!/usr/bin/env bash
# mise tools follow latest; other sources retain their reviewed revisions.
set -euo pipefail
[[ $(id -u) != 0 && $HOME == "$(getent passwd "$(id -un)" | cut -d: -f6)" ]] || {
  echo 'Run as the desktop user with their own HOME, without sudo.' >&2
  exit 1
}
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
version() {  # the selector home/dot_config/mise/config.toml declares for a tool
  /usr/bin/python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["tools"][sys.argv[2]])' \
    "$repo/home/dot_config/mise/config.toml" "$1"
}
tools=()
for tool in claude codex bun ruby bitwarden github:raine/workmux; do
  tools+=("$tool@$(version "$tool")")
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
mise exec "bun@$(version bun)" -- bun install --frozen-lockfile --ignore-scripts
mise exec "bun@$(version bun)" -- bun src/cli.ts version
