#!/usr/bin/env bash
# Install only the native CE hosts and browser declared by the home source.
set -euo pipefail
[[ $(id -u) != 0 ]] || { echo 'Run as the desktop user, without sudo.' >&2; exit 1; }
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
for tool in claude codex npm:agent-browser; do
  selector=$(/usr/bin/python3 -c 'import sys,tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["tools"][sys.argv[2]])' "$repo/home/dot_config/mise/config.toml" "$tool")
  /usr/bin/mise install -y "$tool@$selector"
done
/usr/bin/mise exec npm:agent-browser -- agent-browser install
/usr/bin/python3 "$repo/scripts/install-compound-engineering.py"
