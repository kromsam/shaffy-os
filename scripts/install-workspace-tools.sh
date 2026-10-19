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
for tool in claude codex bun ruby aqua:protonpass/pass-cli github:raine/workmux npm:agent-browser; do
  tools+=("$tool@$(version "$tool")")
done
mise install -y "${tools[@]}"

uv tool install --python /usr/bin/python3 --torch-backend cpu 'zotero-mcp-server[semantic,pdf]==0.12.4'

mise exec -- agent-browser install
/usr/bin/python3 "$repo/scripts/install-compound-engineering.py"
