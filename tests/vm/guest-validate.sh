#!/bin/bash
# The guest half of the VM validation (tests/vm/README.md). Run it in a terminal of the
# freshly installed CachyOS guest, as the normal user the installer created, from the
# read-only share, one stage at a time and in order:
#
#   prerequisites  the installation Shaffy OS expects, and the shares
#   bootstrap      clone the checkout from the share's bundle; README's Bootstrap section
#   defaults       the public defaults: attach --none, check, both applies twice, snapper pairs
#   after-reboot   after a reboot and a fresh login: session, Noctalia, units, helpers, plugins
#   supplement     the example and the VM supplement with its integrations, applied twice
#
# Reboot and log in again before each after-reboot stage. Every stage prints PASS, FAIL
# and NOTE lines and keeps each command's output in the evidence share. The stages run
# ./bin/shaffy and sudo themselves, which is right only inside this disposable VM: they
# refuse to run anywhere but in a QEMU or KVM guest.
set -uo pipefail

SHARE=${SHAFFY_VM_SHARE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}
EVIDENCE=${SHAFFY_VM_EVIDENCE:-/mnt/shaffy-evidence}
PROJECTS=$HOME/Projects
CHECKOUT=${SHAFFY_VM_CHECKOUT:-$PROJECTS/shaffy-os}
EXAMPLE=$PROJECTS/example-supplement
SUPPLEMENT=$PROJECTS/vm-supplement
export PAGER=cat GIT_PAGER=cat SYSTEMD_PAGER=cat

STAGE='' STAGE_DIR='' STEP=0 PASSES=0 FAILS=0 NOTES=0 LAST='' NEXT='' OUTPUT=''
OPS=()

usage() {
  cat <<'EOF'
Usage: bash /mnt/shaffy-share/guest-validate.sh STAGE

Stages, in order: prerequisites, bootstrap, defaults, then reboot and after-reboot,
then supplement, then reboot and after-reboot again. See tests/vm/README.md.
  --show-bootstrap   print the commands the bootstrap stage takes from README.md
EOF
}

mount_help() {
  cat <<'EOF'
Mount the two shares first (they do not survive a reboot unless /etc/fstab lists them):

  sudo mkdir -p /mnt/shaffy-share /mnt/shaffy-evidence
  sudo mount -t 9p -o trans=virtio,version=9p2000.L,ro shaffy-share /mnt/shaffy-share
  sudo mount -t 9p -o trans=virtio,version=9p2000.L shaffy-evidence /mnt/shaffy-evidence
  sudo chown "$(id -u):$(id -g)" /mnt/shaffy-evidence
EOF
}

die() {
  printf 'guest-validate: %s\n' "$*" >&2
  exit 2
}

# -- results and evidence ------------------------------------------------------

report() { # report KIND TEXT: one result line, on screen and in the evidence
  local line
  line=$(printf '%-4s %s: %s' "$1" "$STAGE" "$2")
  printf '%s\n' "$line"
  printf '%s\n' "$line" >>"$STAGE_DIR/results.txt"
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$line" >>"$EVIDENCE/results.txt"
}
pass() {
  PASSES=$((PASSES + 1))
  report PASS "$*"
}
fail() {
  FAILS=$((FAILS + 1))
  report FAIL "$*"
}
note() {
  NOTES=$((NOTES + 1))
  report NOTE "$*"
}

check() { # check DESCRIPTION COMMAND...: PASS when the command succeeds
  local description=$1
  shift
  if "$@"; then pass "$description"; else fail "$description"; fi
}

evidence_file() {
  STEP=$((STEP + 1))
  LAST=$(printf '%s/%02d-%s.txt' "$STAGE_DIR" "$STEP" "$1")
  {
    printf '$ %s\n' "${*:2}"
    printf '# %s\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$LAST"
}

capture() { # capture NAME COMMAND...: run it on screen and keep its output; its status
  evidence_file "$@"
  shift
  "$@" 2>&1 | tee -a "$LAST"
  local status=${PIPESTATUS[0]}
  printf '\n# exit status %s\n' "$status" >>"$LAST"
  return "$status"
}

keep() { # keep NAME COMMAND...: only keep its output; its status
  evidence_file "$@"
  shift
  "$@" >>"$LAST" 2>&1
  local status=$?
  printf '\n# exit status %s\n' "$status" >>"$LAST"
  return "$status"
}

keep_output() { # keep_output NAME COMMAND...: keep its output and leave it in OUTPUT; its status
  evidence_file "$@"
  shift
  OUTPUT=$("$@" 2>&1)
  local status=$?
  printf '%s\n\n# exit status %s\n' "$OUTPUT" "$status" >>"$LAST"
  return "$status"
}

shaffy() { # the checkout's wrapper, run from the checkout
  (cd "$CHECKOUT" && PYTHONUNBUFFERED=1 ./bin/shaffy "$@")
}

ask() { # ask QUESTION: 0 for yes, 1 for no, 2 for skip
  local answer
  while read -r -p "$1 [y/n/s(kip)] " answer </dev/tty; do
    case $answer in
      y | Y | yes) return 0 ;;
      n | N | no) return 1 ;;
      s | S | skip) return 2 ;;
    esac
  done
  return 2
}

confirm() { # confirm OBSERVATION: a manual check, recorded as such
  ask "Manual check: $1?"
  case $? in
    0) pass "(manual) $1" ;;
    1) fail "(manual) $1" ;;
    *) note "(manual, skipped) $1" ;;
  esac
}

# The Python half: checks that read JSON journals, snapper lists and configuration files.
# Each prints lines starting with PASS, FAIL or NOTE, which py_report records.
read -r -d '' PYTHON <<'PY'
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

HOME = Path.home()
STATE = Path(os.environ.get('XDG_STATE_HOME') or HOME / '.local/state') / 'shaffy'
ROOT_JOURNAL = Path(f'/var/lib/shaffy/targets/{os.getuid()}.journal.jsonl')


def say(kind, text):
    print(f'{kind} {text}', flush=True)


def events(path):
    try:
        lines = Path(path).read_text(errors='replace').splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            found.append(event)
    return found


def snapper(config):
    command = ['snapper', '--jsonout', '-c', config, 'list', '--columns',
               'number,type,pre-number,description,userdata']
    if config == 'root':
        command = ['sudo', *command]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        say('FAIL', f'snapper -c {config} list failed: {result.stderr.strip()}')
        return {}
    return {int(s['number']): s for s in json.loads(result.stdout).get(config, [])}


def important(snapshot):
    userdata = snapshot.get('userdata') or {}
    if isinstance(userdata, str):
        return 'important=yes' in userdata
    return userdata.get('important') == 'yes'


def pairs(ops):
    """Every home and system phase of these operations has its pre/post pair in snapper."""
    user, root = events(STATE / 'journal.jsonl'), events(ROOT_JOURNAL)
    expected = []
    for op in ops:
        found = False
        mine = [e for e in user if e.get('op') == op]
        starts = [e for e in mine if e.get('event') in ('phase-start', 'resume-start') and 'pre_snapshot' in e]
        done = [e for e in mine if e.get('event') == 'phase-done' and e.get('phase') == 'home']
        if starts and done:
            # A resume takes its own pre snapshot, described with its attempt number.
            attempt = sum(1 for e in starts if e['event'] == 'resume-start')
            opening = f'shaffy {op} home' + (f' resume {attempt}' if starts[-1]['event'] == 'resume-start' else '')
            expected.append(('home', starts[-1]['pre_snapshot'], done[-1]['post_snapshot'], opening,
                             f'shaffy {op} home'))
            found = True
        theirs = [e for e in root if e.get('op') == op]
        starts = [e for e in theirs if e.get('event') in ('phase-start', 'resume-start')]
        done = [e for e in theirs if e.get('event') == 'phase-done']
        if starts and done:
            for config, pre in sorted(starts[-1]['pre'].items()):
                description = f'shaffy {op} system'
                expected.append((config, pre, done[-1]['post'][config], description, description))
            found = True
        if not found:
            say('FAIL', f'operation {op} has no completed phase in the journals')
    lists = {config: snapper(config) for config in sorted({e[0] for e in expected})}
    for config, pre, post, pre_description, description in expected:
        snapshots = lists.get(config, {})
        first, second = snapshots.get(int(pre)), snapshots.get(int(post))
        good = (first and second and first.get('type') == 'pre' and second.get('type') == 'post'
                and int(second.get('pre-number') or -1) == int(pre)
                and first.get('description') == pre_description and second.get('description') == description
                and important(first) and important(second))
        say('PASS' if good else 'FAIL', f'snapper {config} pair {pre}..{post} "{description}", important=yes')


def booted():
    """The guest booted after the last completed apply."""
    finished = [e['at'] for e in events(STATE / 'journal.jsonl') if e.get('event') in ('phase-done', 'complete')]
    if not finished:
        say('FAIL', 'no completed apply on record')
        return
    last = max(datetime.datetime.fromisoformat(at) for at in finished)
    uptime = float(Path('/proc/uptime').read_text().split()[0])
    boot = datetime.datetime.fromtimestamp(time.time() - uptime, datetime.timezone.utc)
    if boot > last:
        say('PASS', f'the guest booted at {boot:%Y-%m-%dT%H:%M:%SZ}, after the last apply at {last:%Y-%m-%dT%H:%M:%SZ}')
    else:
        say('FAIL', f'the guest booted at {boot:%Y-%m-%dT%H:%M:%SZ}, before the last apply at '
            f'{last:%Y-%m-%dT%H:%M:%SZ}; reboot, log in again and run this stage again')


def noctalia_config():
    """The effective [plugins] table as Noctalia resolves it, its built-in sources included."""
    import tomllib
    result = subprocess.run(['noctalia', 'config', 'export', 'full'], capture_output=True, text=True, check=False)
    try:
        plugins = tomllib.loads(result.stdout).get('plugins', {})
    except ValueError as error:
        say('FAIL', f'noctalia config export full does not parse: {error}')
        return [], []
    return plugins.get('enabled', []), [s['name'] for s in plugins.get('source', []) if s.get('enabled', True)]


def noctalia(*arguments):
    result = subprocess.run(['noctalia', 'msg', *arguments], capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 else ''


def plugins(wait):
    """Noctalia fetched every enabled plugin from its sources and enabled it."""
    enabled, sources = noctalia_config()
    if not enabled:
        say('FAIL', 'the rendered Noctalia configuration enables no plugins')
        return
    deadline = time.time() + wait
    while True:
        listed = {}
        for line in noctalia('plugins', 'list').splitlines():
            fields = line.split()
            if fields:
                listed[fields[0]] = fields
        missing = [p for p in enabled if not (p in listed and 'enabled' in listed[p]
                                              and any(re.match(r'\d', f) for f in listed[p]))]
        if not missing or time.time() > deadline:
            break
        time.sleep(10)
    print('# noctalia msg plugins list\n' + noctalia('plugins', 'list'), file=sys.stderr)
    known = noctalia('plugins', 'source', 'list')
    print('# noctalia msg plugins source list\n' + known, file=sys.stderr)
    for name in sources:
        say('PASS' if re.search(rf'^{re.escape(name)}\s', known, re.M) else 'FAIL',
            f'Noctalia knows its plugin source {name}')
    say('FAIL' if missing else 'PASS',
        f'Noctalia fetched and enabled all {len(enabled)} configured plugins'
        + (f'; missing: {", ".join(missing)}' if missing else ''))
    # `plugins list` names each plugin's source in brackets, such as [official].
    elsewhere = [p for p in enabled if p in listed and not {f'[{s}]' for s in sources} & set(listed[p])]
    say('FAIL' if elsewhere else 'PASS', 'every configured plugin came from a configured source'
        + (f'; not: {", ".join(elsewhere)}' if elsewhere else ''))


def ledger():
    try:
        return json.loads((STATE / 'ledger.json').read_text()).get('integrations', {})
    except (OSError, ValueError):
        return {}


def systemctl(*arguments):
    return subprocess.run(['systemctl', '--user', *arguments], capture_output=True, text=True,
                          check=False).stdout.strip()


def integrations(expected):
    """The ledger holds these integrations as managed, and their units are enabled and running."""
    owned = ledger()
    for name in expected:
        entry = owned.get(name)
        state = entry and entry.get('state')
        say('PASS' if state == 'managed' and not entry.get('transition') else 'FAIL',
            f'the ledger records integration {name} as managed (found {state or "nothing"})')
    for name, entry in sorted(owned.items()):
        if entry.get('state') != 'managed':
            continue
        for record in entry.get('activation', []):
            if record.get('kind') == 'wants-link':
                unit = record['unit']
                enabled, active = systemctl('is-enabled', unit), systemctl('is-active', unit)
                say('PASS' if enabled == 'enabled' and active == 'active' else 'FAIL',
                    f'{name}: {unit} is {enabled or "unknown"} and {active or "unknown"}')
            elif record.get('kind') == 'autostart':
                path = Path(record['path'].replace('~', str(HOME), 1))
                say('PASS' if path.is_file() else 'FAIL', f'{name}: autostart entry {path.name} exists')


def config_keys(names):
    """Each catalogued config-key of these integrations is set in the file the application wrote."""
    import configparser
    import yaml
    catalogue = yaml.safe_load(Path(sys.argv[2]).read_text())
    for name in names:
        for record in catalogue[name].get('activation', []):
            if record.get('kind') != 'config-key':
                continue
            path = Path(record['file'].replace('~', str(HOME), 1))
            if not path.is_file():
                say('FAIL', f'{name}: {path} does not exist; start the application once, then apply again')
                continue
            text = path.read_text(errors='replace')
            for key in record['keys']:
                if path.suffix == '.json':
                    value = json.loads(text)
                    for part in key.split('.'):
                        value = value.get(part) if isinstance(value, dict) else None
                    found = value is not None
                elif '.' in key:
                    parser = configparser.ConfigParser(interpolation=None, strict=False)
                    parser.optionxform = str
                    parser.read_string(text)
                    section, option = key.split('.', 1)
                    found = parser.has_option(section, option)
                else:
                    found = re.search(rf'^{re.escape(key)}=', text, re.M) is not None
                say('PASS' if found else 'FAIL', f'{name}: {path.name} sets {key}')


command = sys.argv[1]
if command == 'pairs':
    pairs(sys.argv[2:])
elif command == 'booted':
    booted()
elif command == 'plugins':
    plugins(int(sys.argv[2]))
elif command == 'integrations':
    integrations(sys.argv[2:])
elif command == 'config-keys':
    config_keys(sys.argv[3:])
else:
    sys.exit(f'unknown check {command}')
PY

py_report() { # py_report NAME CHECK ARGS...: run a Python check and record its lines
  local name=$1 kind text
  shift
  evidence_file "$name" python3 check "$@"
  # shellcheck disable=SC2094 # both streams append to the step's log on purpose
  while IFS= read -r line; do
    kind=${line%% *}
    text=${line#* }
    case $kind in
      PASS) pass "$text" ;;
      FAIL) fail "$text" ;;
      NOTE) note "$text" ;;
    esac
  done < <(python3 -c "$PYTHON" "$@" 2>>"$LAST" | tee -a "$LAST")
}

# -- helpers -------------------------------------------------------------------

readme_bootstrap() { # the commands of the sh block in README.md's Bootstrap section
  awk '/^## / { inside = ($0 == "## Bootstrap") }
       inside && /^```sh$/ { block = 1; next }
       block && /^```$/ { exit }
       block && NF && $1 !~ /^#/ { print }' "$1"
}

require_checkout() {
  [[ -x $CHECKOUT/bin/shaffy ]] || die "no checkout at $CHECKOUT; run the bootstrap stage first"
}

require_no_unfinished() { # a failed apply leaves its operation unfinished; every later apply refuses
  local line
  line=$(shaffy status --no-drift 2>&1 | grep '^Unfinished operation: ')
  [[ -z $line || $line == 'Unfinished operation: none' ]] ||
    die "operation ${line#Unfinished operation: } is unfinished. In $CHECKOUT, read ./bin/shaffy status," \
      "then run ./bin/shaffy apply --resume (or --abandon), and run this stage again"
}

bundle_valid() { # bundle_valid BUNDLE: git verifies a bundle only inside a repository
  local scratch status
  scratch=$(mktemp -d) || return 1
  git init -q "$scratch" && git -C "$scratch" bundle verify "$1"
  status=$?
  rm -rf "$scratch"
  return "$status"
}

operation_of() { # the operation an apply's output names, recorded for the pair check
  local op
  op=$(sed -nE 's/.*Applied the (home|system) .*as operation ([^;]+);.*/\2/p' "$LAST" | tail -1)
  [[ -z $op ]] || OPS+=("$op")
}

apply_system() { # apply_system NAME DESCRIPTION [converged]
  if capture "$1" shaffy apply --system; then
    pass "$2"
    operation_of
    local recap
    recap=$(grep -E '^localhost +:' "$LAST" | tail -1 | tr -s ' ')
    if [[ ${3:-} == converged ]]; then
      check "the system apply changes nothing ($recap)" grep -qE 'changed=0 .*failed=0' <<<"$recap"
    else
      note "system apply: $recap"
    fi
  else
    fail "$2"
  fi
}

apply_home() { # apply_home NAME DESCRIPTION [converged]
  if capture "$1" shaffy apply --home; then
    pass "$2"
    operation_of
    if [[ ${3:-} == converged ]]; then
      check "the home apply changes nothing" grep -q 'Applied the home (0 target(s) changed)' "$LAST"
    fi
  else
    fail "$2"
  fi
}

status_is() { # status_is BINDING-LINE: status names the binding, nothing unfinished, no drift
  capture status shaffy status
  check "status reports: $1" grep -qxF "$1" "$LAST"
  check "status reports no unfinished operation" grep -qx 'Unfinished operation: none' "$LAST"
  check "status reports no home drift" grep -qx 'Home drift: none' "$LAST"
}

snapshot_evidence() {
  keep snapper-root sudo snapper -c root list
  keep snapper-home snapper -c home list
  if ((${#OPS[@]})); then
    py_report snapper-pairs pairs "${OPS[@]}"
  else
    fail "no apply of this stage named its operation, so no snapper pair can be checked"
  fi
}

copy_supplement() { # copy_supplement FROM TO: as README's A first supplement section starts one
  mkdir -p "$2" && cp -rT "$1" "$2" && { [[ -d $2/.git ]] || git -C "$2" init -q; }
}

# -- stages --------------------------------------------------------------------

stage_prerequisites() {
  keep os-release cat /etc/os-release
  keep kernel uname -a
  keep mounts findmnt --real
  keep memory-and-disk bash -c 'free -m; echo; df -h / /home'
  keep packages-synchronised bash -c 'ls -l --time-style=full-iso /var/lib/pacman/sync/'
  keep share-info cat "$SHARE/SHARE-INFO"

  check "the guest runs CachyOS" grep -qx 'ID=cachyos' /etc/os-release
  check "the guest booted through UEFI" test -d /sys/firmware/efi
  local fs root
  for mount in / /home; do
    fs=$(findmnt -no FSTYPE "$mount")
    root=$(findmnt -no FSROOT "$mount")
    local want=/@
    [[ $mount == /home ]] && want=/@home
    check "$mount is the Btrfs subvolume ${want#/} (found $fs ${root:-nothing})" \
      test "$fs" = btrfs -a "$root" = "$want"
  done
  if check "the account can use sudo" sudo -v; then
    keep btrfs-subvolumes sudo btrfs subvolume list /
  fi
  if [[ -f /etc/snapper/configs/root ]]; then
    pass "snapper has its root configuration"
  else
    fail "snapper has no root configuration; install with Btrfs and the Limine boot loader, or create" \
      "it with sudo snapper -c root create-config /"
  fi
  keep snapper-configs snapper list-configs
  [[ ! -f /etc/snapper/configs/home ]] || note "a snapper home configuration exists already; normally the first system apply creates it"
  check "Niri, Noctalia and SDDM are installed" keep desktop-packages pacman -Q niri noctalia sddm
  keep cachyos-niri-noctalia pacman -Q cachyos-niri-noctalia || note "cachyos-niri-noctalia is not installed"
  check "SDDM is the enabled display manager" keep sddm systemctl is-enabled sddm.service
  check "this terminal runs in a Niri session" test -n "${NIRI_SOCKET:-}"
  check "Noctalia is running" keep noctalia-process pgrep -a -u "$UID" -x noctalia
  check "the guest has a render node (virtio-vga-gl)" keep render-nodes compgen -G '/dev/dri/renderD*'
  check "the account is a normal user (uid $UID)" test "$UID" -ge 1000
  check "the network reaches GitHub, where Noctalia's plugin sources live" \
    keep network curl -fsSI --max-time 30 https://github.com
  check "the share holds a valid Git bundle" keep bundle bundle_valid "$SHARE/shaffy-os.bundle"
  check "the share holds the synthetic supplements" test -f "$SHARE/supplements/example/config.yaml" -a \
    -f "$SHARE/supplements/vm/config.yaml"
  NEXT="bash $SHARE/guest-validate.sh bootstrap"
}

stage_bootstrap() {
  local commit
  commit=$(sed -n 's/^commit=//p' "$SHARE/SHARE-INFO")
  mkdir -p "$PROJECTS"
  if [[ ! -d $CHECKOUT/.git ]]; then
    check "cloned the checkout from the share's bundle" capture clone git clone "$SHARE/shaffy-os.bundle" "$CHECKOUT"
  elif [[ -n $(git -C "$CHECKOUT" status --porcelain) ]]; then
    fail "the checkout at $CHECKOUT has local changes; it is left as it is"
  else
    # The bundle's HEAD, not a branch: the share may since have been built from another branch.
    # shellcheck disable=SC2016 # $1 and $2 belong to the inner shell
    check "fast-forwarded the checkout to the share's commit" capture update \
      bash -c 'git -C "$1" fetch origin HEAD && git -C "$1" merge --ff-only "$2"' bash "$CHECKOUT" "$commit"
  fi
  require_checkout
  keep checkout git -C "$CHECKOUT" log -1 --format='%H %cI %s'
  check "the checkout is at the share's commit $commit" test "$(git -C "$CHECKOUT" rev-parse HEAD)" = "$commit"

  capture check-before shaffy check --no-render
  note "before the bootstrap, check exits with $? (it should name what is missing)"

  local lines line number=0
  mapfile -t lines < <(readme_bootstrap "$CHECKOUT/README.md")
  check "README's Bootstrap section holds commands (${#lines[@]})" test "${#lines[@]}" -ge 3
  for line in "${lines[@]}"; do
    number=$((number + 1))
    if capture "readme-$number" bash -c "cd \"\$1\" && $line" bash "$CHECKOUT"; then
      pass "README: $line"
    elif grep -q 'not trusted' "$LAST"; then
      note "mise asked to trust mise.toml, as README says it may; trusting it"
      keep mise-toml cat "$CHECKOUT/mise.toml"
      # shellcheck disable=SC2016 # $1 and $2 belong to the inner shell
      capture mise-trust bash -c 'cd "$1" && mise trust mise.toml' bash "$CHECKOUT"
      check "README: $line (after mise trust)" \
        capture "readme-$number-again" bash -c "cd \"\$1\" && $line" bash "$CHECKOUT"
    else
      fail "README: $line"
    fi
  done
  # shellcheck disable=SC2016 # $1 and $2 belong to the inner shell
  keep versions bash -c 'pacman -Q mise uv chezmoi python-yaml python-jsonschema snapper git bubblewrap niri noctalia
    cd "$1" && mise exec ansible-core -- ansible --version' bash "$CHECKOUT"
  if capture check-after shaffy check; then
    check "check reports the system scope ready" grep -qx 'System scope: prerequisites are in place.' "$LAST"
    check "check renders the home" grep -q '^Rendered [0-9]* files for a synthetic user' "$LAST"
  else
    fail "check passes after the bootstrap"
  fi
  NEXT="bash $SHARE/guest-validate.sh defaults"
}

stage_defaults() {
  require_checkout
  require_no_unfinished
  local binding
  binding=$(shaffy status --no-drift 2>&1 | sed -n 's/^Binding: //p')
  case $binding in
    'not bound'*) check "bound the installation to the public defaults" capture attach shaffy supplement attach --none ;;
    'public defaults only') note "the installation is already bound to the public defaults" ;;
    *)
      fail "the installation is bound to something else: $binding"
      return
      ;;
  esac
  check "check validates the public defaults and renders the home" capture check shaffy check
  keep diff shaffy diff
  apply_system apply-system "the first system apply completes"
  check "the first system apply created snapper's home configuration" test -f /etc/snapper/configs/home
  keep snapper-home-config snapper -c home get-config
  apply_home apply-home "the first home apply completes"
  status_is 'Binding: public defaults only'
  apply_system apply-system-again "a second system apply completes" converged
  apply_home apply-home-again "a second home apply completes" converged
  snapshot_evidence
  NEXT="reboot the VM, log in through SDDM, mount the shares, then: bash $SHARE/guest-validate.sh after-reboot"
}

published_on_loopback_only() { # published_on_loopback_only: a port published without a host address
  local id bindings
  id=$(docker run -d -p 8080 busybox:stable sleep 60) || return 1
  bindings=$(docker port "$id" 8080)
  docker rm -f "$id" >/dev/null
  [[ -n $bindings ]] && ! grep -qv '^127\.0\.0\.1:' <<<"$bindings"
}

stage_after_reboot() {
  require_checkout
  py_report booted booted
  local session
  session=$(loginctl show-user "$USER" -p Display --value 2>/dev/null)
  keep session loginctl show-session "$session"
  check "the graphical session is Niri on Wayland" grep -qx 'Type=wayland' "$LAST"
  check "the session's desktop is Niri" grep -qx 'Desktop=niri' "$LAST"
  check "SDDM started the session" grep -qE '^Service=sddm' "$LAST"
  check "Niri answers on its socket" keep niri-version niri msg version
  keep niri-outputs niri msg outputs
  check "Noctalia is running" keep noctalia-process pgrep -a -u "$UID" -x noctalia
  check "Noctalia answers" keep noctalia-status noctalia msg status
  keep_output failed-user-units systemctl --user list-units --state=failed --no-legend --plain
  check "no user unit failed${OUTPUT:+: $(awk '{printf "%s ", $1}' <<<"$OUTPUT")}" test -z "$OUTPUT"
  keep_output failed-system-units systemctl list-units --state=failed --no-legend --plain
  check "no system unit failed${OUTPUT:+: $(awk '{printf "%s ", $1}' <<<"$OUTPUT")}" test -z "$OUTPUT"
  check "the rootless Docker engine, the user service the home enables, is active" systemctl --user is-active --quiet docker.service
  check "the system Docker engine is off" bash -c '! systemctl is-active --quiet docker.service'
  check "a rootless container publishes a port on a boot that never ran the system engine" \
    keep rootless-container docker run --rm -p 127.0.0.1::8080 busybox:stable true
  check "a port published without an address listens on loopback only, as ~/.config/docker/daemon.json says" \
    published_on_loopback_only
  # Inode 256 is the root of a Btrfs subvolume; the system apply made it before the engine started.
  check "Docker's data is the user's own subvolume, outside the home's snapshots" \
    test "$(stat -c '%i %U' "$HOME/.local/share/docker")" = "256 $(id -un)"

  local missing=() target
  while IFS= read -r target; do
    [[ -x $HOME/$target ]] || missing+=("$target")
  done < <(chezmoi --config "$CHECKOUT/chezmoi.toml" --source "$CHECKOUT/home" managed --include files \
    --path-style relative 2>/dev/null | grep '^\.local/bin/')
  [[ -x $HOME/.local/bin/shaffy-desktop-key ]] || missing+=(.local/bin/shaffy-desktop-key)
  keep helpers ls -l "$HOME/.local/bin"
  check "every managed helper in ~/.local/bin and shaffy-desktop-key is installed and executable${missing:+; missing: ${missing[*]}}" \
    test "${#missing[@]}" -eq 0

  py_report plugins plugins 300
  py_report integrations integrations
  local binding
  binding=$(shaffy status --no-drift 2>&1 | grep '^Binding: ')
  status_is "$binding"
  keep noctalia-log tail -n 300 "${XDG_CACHE_HOME:-$HOME/.cache}/noctalia/noctalia.log"
  keep journal-errors journalctl -b -p err --no-pager
  keep user-journal-errors journalctl --user -b -p err --no-pager

  confirm "SDDM showed its login screen and accepted the password"
  confirm "the Noctalia bar and the wallpaper are shown"
  confirm "Super+Space opens the Noctalia launcher and typing immediately searches applications"
  if [[ $binding == 'Binding: public defaults only' ]]; then
    NEXT="bash $SHARE/guest-validate.sh supplement"
  else
    NEXT="the validation is complete; keep the evidence folder as tests/vm/README.md describes"
  fi
}

stage_supplement() {
  require_checkout
  require_no_unfinished
  local id integrations
  read -r id integrations < <(python3 -c 'import sys, yaml; s = yaml.safe_load(open(sys.argv[1]))
print(s["supplement_id"], *sorted(s["integrations"]))' "$SHARE/supplements/vm/config.yaml")
  [[ -n $integrations ]] || die "the VM supplement in the share requests no integrations"

  check "copied supplement.example as README describes" copy_supplement "$SHARE/supplements/example" "$EXAMPLE"
  check "check validates and renders the example supplement" capture check-example shaffy check --supplement "$EXAMPLE"
  check "copied the VM supplement" copy_supplement "$SHARE/supplements/vm" "$SUPPLEMENT"
  check "check validates and renders the VM supplement" capture check-vm shaffy check --supplement "$SUPPLEMENT"
  keep diff-preview shaffy diff --supplement "$SUPPLEMENT"

  local binding
  binding=$(shaffy status --no-drift 2>&1 | sed -n 's/^Binding: //p')
  if [[ $binding == "supplement $id at $SUPPLEMENT" ]]; then
    note "the installation is already bound to the VM supplement"
  else
    check "rebound the installation from the public defaults to the VM supplement" \
      capture rebind shaffy supplement rebind --path "$SUPPLEMENT" --new-id
  fi
  capture status-before shaffy status --no-drift
  # An earlier run of this stage may have made some managed already; the ledger check covers those.
  local managed_before
  managed_before=" $(python3 -c 'import json, sys
try:
    owned = json.load(open(sys.argv[1]))["integrations"]
except (OSError, ValueError, KeyError, TypeError):
    owned = {}
print(*sorted(name for name, entry in owned.items() if entry.get("state") == "managed"))' \
    "${XDG_STATE_HOME:-$HOME/.local/state}/shaffy/ledger.json") "
  apply_system apply-system "the system apply with the VM supplement completes"
  apply_home apply-home "the home apply with the VM supplement completes"
  local name
  for name in $integrations; do
    if [[ $managed_before == *" $name "* ]]; then
      note "integration $name was already managed before this run"
    else
      check "the home apply made integration $name managed" grep -qF "Integration $name is now managed." "$LAST"
    fi
  done
  # shellcheck disable=SC2086  # one argument per integration
  py_report integrations integrations $integrations
  check "the SSH client configuration is private" test "$(stat -c %a "$HOME/.ssh/config" 2>/dev/null)" = 600
  check "Zotero's managed profile is the default one" grep -qx 'Path=shaffy.default' "$HOME/.zotero/zotero/profiles.ini"

  # REAPER, Zettlr and Zotero write their own configuration on first start; the next apply
  # then sets the catalogued keys in it.
  ask "Start REAPER, Zettlr and Zotero once each, let them finish starting, close REAPER and Zettlr and leave Zotero open. Done"
  case $? in
    0)
      apply_home apply-home-first-run "the home apply after the applications' first start completes"
      # shellcheck disable=SC2086
      py_report config-keys config-keys "$CHECKOUT/defaults/integrations.yml" $integrations
      check "Zotero keeps its library in ~/.local/share/zotero-data" test -f "$HOME/.local/share/zotero-data/zotero.sqlite"
      if curl -fsS --max-time 5 http://127.0.0.1:23119/connector/ping >/dev/null 2>&1; then
        check "Zotero's local API answers" keep zotero-api curl -fsS --max-time 5 -H 'Zotero-API-Version: 3' \
          'http://127.0.0.1:23119/api/users/0/items?limit=1'
      else
        note "Zotero is not running, so its local API was not checked"
      fi
      ;;
    *) note "the applications' first start was skipped, so their configuration keys were not checked" ;;
  esac

  apply_system apply-system-again "a second system apply completes" converged
  apply_home apply-home-again "a second home apply completes" converged
  status_is "Binding: supplement $id at $SUPPLEMENT"
  snapshot_evidence
  NEXT="reboot the VM, log in through SDDM, mount the shares, then: bash $SHARE/guest-validate.sh after-reboot"
}

# -- main ----------------------------------------------------------------------

begin() {
  STAGE=$1
  ((EUID != 0)) || die "run this as the installed user, not as root"
  case $(systemd-detect-virt --vm 2>/dev/null) in
    kvm | qemu) ;;
    *) die "this is not a QEMU or KVM guest, and the stages change the system they run on" ;;
  esac
  if [[ ! -d $EVIDENCE || ! -w $EVIDENCE ]]; then
    mount_help >&2
    die "the evidence share is not mounted and writable at $EVIDENCE"
  fi
  [[ -f $SHARE/SHARE-INFO ]] || {
    mount_help >&2
    die "no share at $SHARE (run this script from the mounted share)"
  }
  STAGE_DIR=$EVIDENCE/$STAGE-$(date -u +%Y%m%dT%H%M%SZ)
  mkdir -p "$STAGE_DIR" || die "cannot write $STAGE_DIR"
  printf 'Stage %s; evidence in %s\n\n' "$STAGE" "$STAGE_DIR"
  # shellcheck disable=SC2016 # $1 and $2 belong to the inner shell
  keep context bash -c 'date -u; id; hostnamectl 2>/dev/null; echo; cat "$1"
    git -C "$2" log -1 --format="checkout %H %s" 2>/dev/null' bash "$SHARE/SHARE-INFO" "$CHECKOUT"
}

finish() {
  printf '\nStage %s: %d passed, %d failed, %d notes. Evidence: %s\n' "$STAGE" "$PASSES" "$FAILS" "$NOTES" "$STAGE_DIR"
  printf '%s %s: %d passed, %d failed, %d notes\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$STAGE" "$PASSES" "$FAILS" \
    "$NOTES" >>"$EVIDENCE/results.txt"
  [[ -z $NEXT ]] || printf 'Next: %s\n' "$NEXT"
  ((FAILS == 0))
}

case ${1:-} in
  prerequisites | bootstrap | defaults | after-reboot | supplement)
    begin "$1"
    "stage_${1//-/_}"
    finish
    ;;
  --show-bootstrap) readme_bootstrap "$CHECKOUT/README.md" ;;
  -h | --help) usage ;;
  *)
    usage >&2
    exit 2
    ;;
esac
