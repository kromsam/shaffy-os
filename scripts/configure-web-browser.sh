#!/bin/bash
# Run as the desktop user, without sudo. Defaults to read-only.
set -euo pipefail
mode=${1:---check}
browser_desktop=${2:-zen.desktop}
[[ $# -le 2 && (${mode} == --check || ${mode} == --apply) && ${browser_desktop} =~ ^[A-Za-z0-9._-]+\.desktop$ ]] || {
  echo 'Usage: bash scripts/configure-web-browser.sh [--check|--apply] [BROWSER.desktop]' >&2
  exit 2
}
[[ $(id -u) != 0 && ${HOME} == "$(getent passwd "$(id -un)" | cut -d: -f6)" ]] || {
  echo 'Run as the desktop user with their own HOME, without sudo.' >&2
  exit 1
}
[[ -f /etc/arch-release && -f /usr/share/applications/${browser_desktop} ]] || {
  echo "The browser's desktop entry /usr/share/applications/${browser_desktop} is not installed." >&2
  exit 1
}
command -v xdg-mime >/dev/null
command -v xdg-settings >/dev/null

# Explicit user defaults avoid falling back to ChatGPT's HTTP/HTTPS handlers.
# Leave its codex callback handler and all unrelated associations untouched.
changed=0
mismatch=0
for mime in x-scheme-handler/http x-scheme-handler/https text/html application/xhtml+xml; do
  before=$(xdg-mime query default "${mime}")
  if [[ ${before} != "${browser_desktop}" && ${mode} == --apply ]]; then
    xdg-mime default "${browser_desktop}" "${mime}"
    changed=1
  fi
  after=$(xdg-mime query default "${mime}")
  printf '%s: %s -> %s\n' "${mime}" "${before:-unset}" "${after:-unset}"
  [[ ${after} == "${browser_desktop}" ]] || mismatch=1
done
browser=$(xdg-settings get default-web-browser)
printf 'Default web browser: %s\nconfiguration_changed: %s\n' "${browser}" "${changed}"
[[ ${browser} == "${browser_desktop}" ]] || mismatch=1
[[ ${mismatch} == 0 ]] || {
  echo 'Browser defaults need attention; use --apply to set the declared browser defaults.' >&2
  exit 1
}
echo "Declared browser associations verified: ${browser_desktop}"
