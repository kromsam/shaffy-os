#!/usr/bin/env bash
# Scope the local GBrain server to read-only keyword search for the AI tools (the gbrain
# integration), then install its launcher, proxy, unit and AI configuration.
set -euo pipefail
[[ $(id -u) != 0 ]] || { echo 'Run as the desktop user, without sudo.' >&2; exit 1; }
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source=$("$repo/bin/shaffy" data | /usr/bin/python3 -c \
  'import json, sys; print(json.load(sys.stdin)["shaffy"]["settings"].get("ai", {}).get("gbrain", {}).get("source", ""))')
[[ -n $source ]] || { echo 'Set ai.gbrain.source in the supplement first.' >&2; exit 1; }
# One owner at a time; this is our managed server, not an unrelated CLI process.
systemctl --user stop gbrain-mcp.service
# Bring the server back however this ends, including a refused apply.
trap 'systemctl --user start gbrain-mcp.service' EXIT
"$HOME/.local/bin/gbrain" config set search.mcp_keyword_only true
"$HOME/.local/bin/gbrain" config set memory.auto_writeback off
python3 "$repo/scripts/prepare-gbrain-mcp.py" "$source"
# The launcher, its proxy, the unit and both AI configurations come from the home apply.
"$repo/bin/shaffy" apply --home
systemctl --user daemon-reload
# Started on demand by the proxy; no login autostart or background worker.
systemctl --user start gbrain-mcp.service
