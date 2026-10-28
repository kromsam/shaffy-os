#!/usr/bin/env bash
# mise tools follow latest; other sources retain their reviewed revisions.
set -euo pipefail
[[ $(id -u) != 0 && $HOME == "$(getent passwd "$(id -un)" | cut -d: -f6)" ]] || {
  echo 'Run as the desktop user with their own HOME, without sudo.' >&2
  exit 1
}
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
# Every public tool home/dot_config/mise/config.toml names. mise takes each one's version
# and options from the home's configuration, which the home apply renders from that file.
read -ra tools < <(/usr/bin/python3 -c 'import sys, tomllib; print(*tomllib.load(open(sys.argv[1], "rb"))["tools"])' \
  "$repo/home/dot_config/mise/config.toml")

# Zotero MCP was a uv tool until 2026-10; mise installs it now, so remove that copy.
if command -v uv >/dev/null && uv tool list 2>/dev/null | grep -q '^zotero-mcp-server '; then
  uv tool uninstall zotero-mcp-server
fi
(cd "$HOME" && mise install -y "${tools[@]}")

mise exec -- agent-browser install
/usr/bin/python3 "$repo/scripts/install-compound-engineering.py"
