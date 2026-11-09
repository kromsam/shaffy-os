#!/bin/bash
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
# So that configuration must be this checkout's: a tool it lacks would install without its options.
/usr/bin/python3 - "$repo/home/dot_config/mise/config.toml" "${XDG_CONFIG_HOME:-$HOME/.config}/mise/config.toml" <<'PY'
import sys, tomllib
try:
    tools = [tomllib.load(open(path, 'rb')).get('tools') for path in sys.argv[1:]]
except OSError:
    tools = [None, False]
if tools[0] != tools[1]:
    sys.exit("The home's mise configuration differs from this checkout's; run ./bin/shaffy apply --home first.")
PY

# And every tool of the supplement's mise fragment, with the options the home apply rendered.
read -ra personal < <(/usr/bin/python3 -c 'import sys, tomllib
try:
    print(*tomllib.load(open(sys.argv[1], "rb")).get("tools", {}))
except FileNotFoundError:
    print()' "${XDG_CONFIG_HOME:-$HOME/.config}/mise/conf.d/90-supplement.toml")
(cd "$HOME" && mise install -y "${tools[@]}" "${personal[@]}")

/usr/bin/python3 "$repo/scripts/install-pstack.py"
# Better BibTeX for the managed Zotero profile; Zotero updates it once it is installed.
/usr/bin/python3 "$repo/scripts/install-better-bibtex.py"
