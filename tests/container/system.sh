#!/bin/bash
# The system integration test, run as root inside a
# disposable container of the recorded CachyOS image with the checkout mounted
# read-only at /src. It follows the documented bootstrap, creates a target
# user with a test-only sudo rule, applies the system twice through the
# wrapper with the fake snapper, and crashes the operation between record
# writes to exercise each recovery row. Every result line starts with RESULT.
set -euo pipefail

# The container is disposable, so a full upgrade stands in for the refreshed databases.
pacman -Syu --needed --noconfirm mise uv chezmoi python-yaml python-jsonschema snapper git base-devel \
  bubblewrap xdg-user-dirs sudo >/dev/null
# Desktop prerequisites a Niri installation brings; check asks for the desktop key's build tools.
pacman -S --needed --noconfirm wayland libxkbcommon >/dev/null
useradd --create-home tester
echo 'tester ALL=(ALL) NOPASSWD: ALL' >/etc/sudoers.d/shaffy-test
cp -a /src /home/tester/checkout
rm -rf /home/tester/checkout/collections /home/tester/checkout/artifacts
cp -a /src/tests/fixtures/supplements/comprehensive /home/tester/supplement
# An extra package, so the first apply changes something and the second must change nothing.
printf 'packages:\n  extra: [htop]\n' >>/home/tester/supplement/config.yaml
# A settings value shaped like Jinja: the actual play route must carry it as text.
python3 - <<'EOF2'
from pathlib import Path
path = Path('/home/tester/supplement/config.yaml')
probe = ('    jinja-probe:\n      command: "/usr/bin/true"\n      env:\n'
         '        PROBE: "{{ lookup(\'pipe\', \'touch /tmp/shaffy-jinja-ran\') }}"\n')
path.write_text(path.read_text().replace('  mcp_servers:\n', '  mcp_servers:\n' + probe, 1))
EOF2
cp /home/tester/supplement/config.yaml /home/tester/config.yaml.good
mkdir /home/tester/Bilder /home/tester/Projects # the fixture's folder_links targets
chown -R tester: /home/tester

run() { # run a command as the target user in the checkout; never fails the script
  runuser -u tester -- env -i HOME=/home/tester USER=tester PATH=/usr/bin:/bin LANG=C.UTF-8 \
    SHAFFY_TEST_PREREQUISITES=fake SHAFFY_TEST_ROOT_STATE=/var/lib/shaffy ${FAULT:+SHAFFY_TEST_FAULT=$FAULT} \
    bash -c 'cd /home/tester/checkout && exec "$@"' bash "$@"
}
state() { # print the records as one JSON line
  python3 - <<'EOF'
import json
from pathlib import Path
user = json.loads(Path('/home/tester/.local/state/shaffy/binding.json').read_text())
import pwd
uid = pwd.getpwnam('tester').pw_uid
root_path = Path(f'/var/lib/shaffy/targets/{uid}.json')
journal = Path(f'/var/lib/shaffy/targets/{uid}.journal.jsonl')
root = json.loads(root_path.read_text()) if root_path.exists() else None
events = [json.loads(line)['event'] for line in journal.read_text().splitlines()] if journal.exists() else []
print(json.dumps({'user_pending': (user['pending'] or {}).get('op'), 'bound': user['system']['bound'],
                  'user_last': user['last_completed_op'], 'root_pending': (root or {}).get('pending') and root['pending']['op'],
                  'root_last': (root or {}).get('last_completed_op'), 'root_events': events}))
EOF
}

run mise trust mise.toml >/dev/null
run mise install ansible-core >/dev/null
run mise exec ansible-core -- ansible-galaxy collection install -r requirements.yml -p collections >/dev/null
echo "RESULT check $(run ./bin/shaffy check --no-render --supplement /home/tester/supplement | tail -1)"
run ./bin/shaffy supplement attach --supplement /home/tester/supplement >/dev/null

for attempt in first second; do
  set +e
  output=$(run ./bin/shaffy apply --system --tags packages,home_backup,desktop 2>&1)
  code=$?
  set -e
  recap=$(grep -E '^localhost +:' <<<"$output" | tr -s ' ')
  echo "RESULT apply-$attempt code=$code recap=$recap"
  echo "RESULT state-$attempt $(state)"
  echo "RESULT jinja-$attempt $(test -e /tmp/shaffy-jinja-ran && echo evaluated || echo literal)"
  [[ $code == 0 ]] || {
    echo "$output" | tail -40
    exit 1
  }
done

echo "RESULT desktop $(pacman -Q grim tesseract localsend restic htop 2>&1 | cut -d' ' -f1 | tr '\n' ' ')"

# Crash between record writes, then recover.
for fault in after-pending after-root-pre-snapshots after-root-pending after-root-phase-start \
  after-root-phase-done after-play; do
  set +e
  FAULT=$fault run ./bin/shaffy apply --system --tags packages,home_backup,desktop >/dev/null 2>&1
  crashed=$?
  echo "RESULT crash-$fault code=$crashed $(state)"
  resumed=$(run ./bin/shaffy apply --resume 2>&1)
  code=$?
  set -e
  echo "RESULT resume-$fault code=$code $(state)"
  [[ $code == 0 ]] || {
    echo "$resumed" | tail -40
    exit 1
  }
done

# Settings that make the play fail every time: a group name in `remove` (pacman would expand it).
printf '  remove: [cachyos]\n' >>/home/tester/supplement/config.yaml
set +e
run ./bin/shaffy apply --system --tags packages,home_backup,desktop >/dev/null 2>&1
failed=$?
run ./bin/shaffy apply --resume >/dev/null 2>&1
resumed=$?
run ./bin/shaffy apply --abandon >/dev/null 2>&1
abandoned=$?
set -e
echo "RESULT failing apply=$failed resume=$resumed abandon=$abandoned $(state)"
grep -q 'pacman groups' <<<"$(run ./bin/shaffy apply --system --tags packages,home_backup,desktop 2>&1 || true)" &&
  echo "RESULT group-refusal named" || echo "RESULT group-refusal missing"
run ./bin/shaffy apply --abandon >/dev/null 2>&1 || true
cp /home/tester/config.yaml.good /home/tester/supplement/config.yaml
set +e
run ./bin/shaffy apply --system --tags packages,home_backup,desktop >/dev/null 2>&1
code=$?
set -e
echo "RESULT after-abandon code=$code $(state)"

# / rolled back to a moment root held an operation: root pending the user record does not know.
uid=$(id -u tester)
python3 - "$uid" <<'EOF2'
import json, sys
path = f'/var/lib/shaffy/targets/{sys.argv[1]}.json'
record = json.load(open(path))
record['pending'] = {'op': '20000101T000000Z-rolled', 'scope': 'system', 'capture': '/nonexistent',
                     'phase': 'system', 'started_at': 'x'}
json.dump(record, open(path, 'w'))
EOF2
set +e
run ./bin/shaffy apply --system --tags packages,home_backup,desktop >/dev/null 2>&1
refused=$?
run ./bin/shaffy apply --system --abandon >/dev/null 2>&1
cleared=$?
run ./bin/shaffy apply --system --tags packages,home_backup,desktop >/dev/null 2>&1
code=$?
set -e
echo "RESULT rollback refused=$refused abandon=$cleared apply=$code $(state)"
echo "RESULT status $(run ./bin/shaffy status --no-drift | grep -E '^System:')"

# An AUR package: the probe recipe served from a local git repository in place of the AUR,
# built as the target user in the cache, installed as root from a verified copy.
aur=/home/tester/aur
mkdir -p "$aur" && cp -a /src/tests/fixtures/packages/shaffy-probe "$aur/shaffy-probe.git"
chown -R tester: "$aur"
# As the target user, who owns the repository and clones it; git refuses another owner's.
runuser -u tester -- git -C "$aur/shaffy-probe.git" init -q
runuser -u tester -- git -C "$aur/shaffy-probe.git" add PKGBUILD
runuser -u tester -- git -C "$aur/shaffy-probe.git" -c user.name=t -c user.email=t@example.invalid \
  -c commit.gpgsign=false commit -q -m probe
# shellcheck disable=SC2174 # only the operation directory needs 0700
mkdir -p -m 0700 /var/lib/shaffy/ops/test-aur-package
for attempt in first second; do
  recap=$(run env ANSIBLE_ROLES_PATH=/home/tester/checkout/roles mise exec ansible-core -- ansible-playbook \
    -i localhost, tests/container/aur-package.yml 2>&1 | grep -E '^localhost +:' | tr -s ' ')
  echo "RESULT aur-$attempt $recap"
done
# shellcheck disable=SC2012 # a listing for the log
echo "RESULT aur-installed $(pacman -Q shaffy-probe 2>&1) copy=$(ls /var/lib/shaffy/ops/test-aur-package/packages/ | tr '\n' ' ')\
build=$(ls /home/tester/.cache/shaffy/build/shaffy-probe/out/ | tr '\n' ' ')"
