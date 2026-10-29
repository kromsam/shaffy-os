#!/bin/bash
# Run as the desktop user, without sudo. Defaults to read-only.
set -euo pipefail
mode=${1:---check}
[[ $# -le 1 && ( $mode == --check || $mode == --apply ) ]] || {
  echo 'Usage: bash scripts/configure-office-apps.sh [--check|--apply]' >&2
  exit 2
}
[[ $(id -u) != 0 && $HOME == "$(getent passwd "$(id -un)" | cut -d: -f6)" ]] || {
  echo 'Run as the desktop user with their own HOME, without sudo.' >&2
  exit 1
}
[[ -f /etc/arch-release ]]
for app in libreoffice-writer libreoffice-calc libreoffice-impress; do
  [[ -f /usr/share/applications/$app.desktop ]]
done
command -v xdg-mime >/dev/null

# The ChatGPT (Codex) desktop entry registers itself for Office documents, CSV
# and TSV, and wins whenever no explicit user default exists. Pin every document
# type it claims, plus their macro and template variants, to LibreOffice. Its
# codex callback handler and all unrelated associations stay untouched.
declare -A office_defaults=(
  [application/msword]=libreoffice-writer.desktop
  [application/vnd.ms-word.document.macroEnabled.12]=libreoffice-writer.desktop
  [application/vnd.ms-word.template.macroEnabled.12]=libreoffice-writer.desktop
  [application/vnd.openxmlformats-officedocument.wordprocessingml.document]=libreoffice-writer.desktop
  [application/vnd.openxmlformats-officedocument.wordprocessingml.template]=libreoffice-writer.desktop
  [text/csv]=libreoffice-calc.desktop
  [text/tab-separated-values]=libreoffice-calc.desktop
  [application/vnd.ms-excel]=libreoffice-calc.desktop
  [application/vnd.ms-excel.sheet.macroEnabled.12]=libreoffice-calc.desktop
  [application/vnd.ms-excel.sheet.binary.macroEnabled.12]=libreoffice-calc.desktop
  [application/vnd.ms-excel.template.macroEnabled.12]=libreoffice-calc.desktop
  [application/vnd.openxmlformats-officedocument.spreadsheetml.sheet]=libreoffice-calc.desktop
  [application/vnd.openxmlformats-officedocument.spreadsheetml.template]=libreoffice-calc.desktop
  [application/vnd.ms-powerpoint]=libreoffice-impress.desktop
  [application/vnd.ms-powerpoint.presentation.macroEnabled.12]=libreoffice-impress.desktop
  [application/vnd.ms-powerpoint.slideshow.macroEnabled.12]=libreoffice-impress.desktop
  [application/vnd.ms-powerpoint.template.macroEnabled.12]=libreoffice-impress.desktop
  [application/vnd.openxmlformats-officedocument.presentationml.presentation]=libreoffice-impress.desktop
  [application/vnd.openxmlformats-officedocument.presentationml.slideshow]=libreoffice-impress.desktop
  [application/vnd.openxmlformats-officedocument.presentationml.slide]=libreoffice-impress.desktop
  [application/vnd.openxmlformats-officedocument.presentationml.template]=libreoffice-impress.desktop
)
changed=0
mismatch=0
for mime in $(printf '%s\n' "${!office_defaults[@]}" | sort); do
  wanted=${office_defaults[$mime]}
  before=$(xdg-mime query default "$mime")
  if [[ $before != "$wanted" && $mode == --apply ]]; then
    xdg-mime default "$wanted" "$mime"
    changed=1
  fi
  after=$(xdg-mime query default "$mime")
  printf '%s: %s -> %s\n' "$mime" "${before:-unset}" "${after:-unset}"
  [[ $after == "$wanted" ]] || mismatch=1
done
printf 'configuration_changed: %s\n' "$changed"
[[ $mismatch == 0 ]] || {
  echo 'Office defaults need attention; use --apply to set the LibreOffice defaults.' >&2
  exit 1
}
echo 'LibreOffice document associations verified.'
