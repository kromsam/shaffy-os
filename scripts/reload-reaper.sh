#!/bin/bash
# Close normally: REAPER may ask the user to save if they edited the new project.
# The home apply refuses a user or home other than the bound one.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if pgrep -x reaper >/dev/null; then
  reaper -nonewinst -close:exit
  for _ in {1..20}; do
    if ! pgrep -x reaper >/dev/null; then break; fi
    sleep 0.5
  done
fi
if pgrep -x reaper >/dev/null; then
  echo 'REAPER is still open; complete its save/close dialog before applying configuration.' >&2
  exit 1
fi
# Reopen REAPER whether or not the apply went through; its refusal says why.
status=0
"$repo/bin/shaffy" apply --home || status=$?
niri msg action spawn -- /usr/bin/reaper -new
exit "$status"
