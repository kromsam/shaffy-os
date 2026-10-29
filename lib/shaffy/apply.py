"""Apply the home through its operation protocol (docs/ARCHITECTURE.md, "The home apply").

lock -> capture -> pending -> preflight (settings digest, the integrations
against the ledger, links, desktop key build, sandbox render of the capture,
chezmoi status) -> snapper pre snapshot and copies of changing targets ->
phase-start -> ledger intent -> chezmoi apply with the operation marker ->
verify -> desktop key install -> Niri, Noctalia and Alacritty validation of the
real home -> ledger transitions -> post snapshot -> source check -> phase-done
-> complete -> reload the running Noctalia session.

`--resume` finishes an interrupted operation from its retained capture, in the
checkout it began in, after the same preflight and with a new pre snapshot and
new copies. `--abandon` clears an operation that cannot be resumed and names
the snapshots and copies to review; it deletes nothing.
"""

import copy
import os
from pathlib import Path
import pwd
import shutil
import signal
import subprocess

from . import desktop_key, ledger
from .capture import CaptureError, capture, settings_digest, verify
from .folders import asset_conflicts
from .identity import signing_note
from .loader import SettingsError, load
from .records import LockedError, RecordError, Records, current_target, disagreements, now, read_root_record
from .sandbox import RenderError, render
from .selection import SelectionError, bound_supplement, state_dir
from .source import manifest as source_manifest
from .validate_home import validate_applied

STATE_LOCK_TIMEOUT = 'timeout obtaining persistent state lock'
UNITS = '.config/systemd/user/'
KEEP_RUNS = 10


class ApplyError(Exception):
    """The apply refused or failed; the message says what to do next."""


class Snapper:
    """The real prerequisite adapter: Btrfs subvolumes and the snapper `home` config."""

    name = 'snapper'

    def problems(self):
        found = []
        for mount in ('/', '/home'):
            try:
                kind = subprocess.run(
                    ['findmnt', '-no', 'FSTYPE', mount], capture_output=True, text=True, check=False
                ).stdout.strip()
            except FileNotFoundError:
                return ['findmnt is not installed, so the Btrfs subvolumes cannot be checked']
            if kind != 'btrfs':
                found.append(f'{mount} is not a Btrfs subvolume (found {kind or "nothing"})')
        if shutil.which('snapper') is None:
            found.append('snapper is not installed')
        elif subprocess.run(['snapper', '-c', 'home', 'list'], capture_output=True, check=False).returncode:
            found.append(
                'the snapper configuration `home` does not exist or is not readable '
                'by this user; ./bin/shaffy apply --system creates it'
            )
        return found

    def snapshot(self, kind, description, pre=None):
        command = [
            'snapper',
            '-c',
            'home',
            'create',
            '--type',
            kind,
            '--print-number',
            '--cleanup-algorithm',
            'number',
            '--userdata',
            'important=yes',
            '--description',
            description,
        ]
        if pre is not None:
            command += ['--pre-number', str(pre)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise ApplyError(f'snapper could not create the {kind} snapshot: {result.stderr.strip()}')
        return int(result.stdout.strip())


class FakeSnapper:
    """Test stand-in; refused for the account's real home so it can never skip real snapshots."""

    name = 'fake'

    def __init__(self, state):
        self.log = Path(state) / 'fake-snapshots.jsonl'

    def problems(self):
        if Path.home().resolve() == Path(pwd.getpwuid(os.getuid()).pw_dir).resolve():
            return ['the test prerequisite adapter is refused for the real home directory']
        return []

    def snapshot(self, kind, description, pre=None):
        import json

        entries = self.log.read_text().splitlines() if self.log.exists() else []
        number = len(entries) + 1
        self.log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.log.open('a') as stream:
            stream.write(json.dumps({'number': number, 'type': kind, 'pre': pre, 'description': description}) + '\n')
        return number


def adapter(state):
    return FakeSnapper(state) if os.environ.get('SHAFFY_TEST_PREREQUISITES') == 'fake' else Snapper()


def _fault(point, prerequisites):
    """Crash on purpose at a named point; only ever honoured with the test adapter."""
    if prerequisites.name == 'fake' and os.environ.get('SHAFFY_TEST_FAULT') == point:
        os.kill(os.getpid(), signal.SIGKILL)


class HomeApply:
    def __init__(self, checkout, chezmoi_command, out=print):
        self.checkout = Path(checkout).resolve()
        self.chezmoi_command = chezmoi_command
        self.out = out
        self.state = state_dir()
        self.records = Records()
        self.ledger = ledger.Ledger(self.state)
        self.prerequisites = adapter(self.state)
        self.key = None  # the built desktop key, set by the preflight
        self.managed_dirs = set()  # targets that stay directories, set by the preflight
        self.note = None  # a warning to repeat after the apply, set by the preflight
        self.deferred = {}  # integrations waiting for their activate_requires, set by the preflight
        self.units_changed = False  # whether the write changes user unit files, set before it

    # -- helpers -----------------------------------------------------------
    def _chezmoi(self, *arguments, marker=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith('SHAFFY_OP') and k != 'SHAFFY_SANDBOX_TOKEN'}
        if marker:
            env.update(marker)
        return subprocess.run(self.chezmoi_command(*arguments), capture_output=True, text=True, env=env, check=False)

    def _marker(self, pending):
        # Deferred integrations render as not managed in this operation (ledger.plan).
        return {
            'SHAFFY_OP': pending['op'],
            'SHAFFY_OP_MODE': 'apply',
            'SHAFFY_OP_CAPTURE': pending['capture'],
            'SHAFFY_OP_DEFERRED': ','.join(pending.get('deferred', [])),
        }

    def _events(self, op):
        return [e for e in self.records.events() if e.get('op') == op]

    def _captured(self, pending):
        supplement = Path(pending['capture']) / 'supplement'
        return supplement if supplement.exists() else None

    def _preflight(self, pending):
        """Everything that must hold before a phase writes; the same for start and resume."""
        problems = list(self.prerequisites.problems())
        captured = self._captured(pending)
        try:
            shaffy = load(self.checkout, supplement=captured, without=captured is None)['shaffy']
        except (SettingsError, SelectionError) as error:
            return problems + [f'the captured settings no longer load: {error}']
        settings = shaffy['settings']
        if settings_digest(settings) != pending['settings_sha256']:
            problems.append(
                'the effective settings differ from those captured when the operation began, '
                "because the checkout's defaults, schema or loader changed"
            )
        try:
            owned = self.ledger.read(self._target())
        except RecordError as error:
            return problems + [str(error)]
        refused, transitions, deferred = ledger.plan(shaffy['integrations'], owned, Path.home(), pending['op'])
        if refused:
            return problems + refused
        pending['deferred'] = sorted(deferred)
        self.deferred = deferred
        problems += asset_conflicts(shaffy['assets'], Path.home())
        self.note = signing_note(settings, Path.home())
        missing = desktop_key.problems()
        problems += missing
        if not missing:
            try:
                self.key = desktop_key.build(self.checkout)
            except desktop_key.BuildError as error:
                problems.append(str(error))
        try:
            problems += render(
                self.checkout,
                supplement=captured,
                without=captured is None,
                allow_direct=self.prerequisites.name == 'fake',
            )['problems']
        except RenderError as error:
            problems.append(str(error))
        if problems:
            return problems
        status = self._chezmoi('status', marker=self._marker(pending))
        if status.returncode:
            return [
                'another chezmoi command is running'
                if STATE_LOCK_TIMEOUT in status.stderr
                else 'chezmoi status failed: ' + status.stderr.strip()[-800:]
            ]
        managed = self._chezmoi(
            'managed', '--include', 'dirs', '--path-style', 'relative', marker=self._marker(pending)
        )
        if managed.returncode:
            return ['chezmoi managed failed: ' + managed.stderr.strip()[-800:]]
        self.managed_dirs = set(managed.stdout.splitlines())
        if source_manifest(self.checkout)['sha256'] != pending['source']['sha256']:
            return ['the checkout changed during the preflight; run the command again once it is settled']
        # chezmoi status: two status letters, a space, the target; the second letter is the change.
        pending['_changing'] = [(line[1], line[3:]) for line in status.stdout.splitlines() if len(line) > 3]
        if desktop_key.differs(self.key, Path.home()):
            pending['_changing'].append(('M', desktop_key.TARGET))
        # Disabling removes activation chezmoi no longer sees, so it is copied like any change.
        pending['_changing'] += [('D', relative) for relative in ledger.removals(transitions, Path.home())]
        pending['_transitions'] = transitions
        return []

    def _copy_before(self, op, changing, name):
        """Copy every existing file chezmoi will change, and every directory it will delete.

        A directory is deleted when chezmoi removes it or puts a file or symlink in its
        place; one that stays a directory (a permission change) keeps its contents and is
        not copied. Shaffy OS's own state directory is never copied.
        """
        copies = Path(self.state) / 'runs' / op / name
        copies.mkdir(parents=True, exist_ok=True)
        state = Path(self.state).resolve()

        def skip_state(directory, names):
            return [n for n in names if (Path(directory) / n).resolve() == state]

        for change, relative in changing:
            source, target = Path.home() / relative, copies / relative
            if source.is_symlink() or source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target, follow_symlinks=False)
            elif source.is_dir() and not target.exists() and (change == 'D' or relative not in self.managed_dirs):
                shutil.copytree(source, target, symlinks=True, ignore=skip_state)
        return copies

    def _target(self):
        target = current_target()
        return {'uid': target['uid'], 'home': target['home']}

    def _prune(self, keep_op):
        """Keep the runs of the newest completed operations; never an abandoned or unfinished one."""
        runs = Path(self.state) / 'runs'
        if not runs.is_dir():
            return
        completed = {e['op'] for e in self.records.events() if e['event'] == 'phase-done'}
        old = sorted(
            (p for p in runs.iterdir() if p.is_dir() and p.name != keep_op and p.name in completed), reverse=True
        )
        for stale in old[KEEP_RUNS - 1 :]:
            shutil.rmtree(stale, ignore_errors=True)

    def _require_record(self, record):
        if not record:
            raise ApplyError(
                'this installation is not bound; run ./bin/shaffy supplement attach --supplement PATH (or --none) first'
            )
        self.records.check_target(record)
        root = read_root_record(record['target']['uid'])
        if root and not record['system']['bound']:
            raise ApplyError(
                'a root record exists for this user but the user record does not know it; '
                'run ./bin/shaffy supplement attach --adopt-system-record'
            )
        if record['system']['bound'] and not root:
            raise ApplyError('the user record says the system is bound, but there is no root record')
        problems = disagreements(record, root) if root else []
        if problems:
            raise ApplyError('the user and root records disagree:\n  ' + '\n  '.join(problems))

    # -- the protocol ------------------------------------------------------
    def start(self, op, supplement=None, checkout=None):
        with self.records.lock():
            record = self.records.read()
            if record and record['pending']:
                # First: an unfinished system operation writes the root record before it marks
                # the user record, so the records disagree until it is resumed.
                self.records.check_target(record)
                raise ApplyError(
                    f'operation {record["pending"]["op"]} is unfinished; run '
                    './bin/shaffy apply --resume, or --abandon if it cannot be resumed'
                )
            self._require_record(record)
            bound = Path(record['checkout']['path'])
            override = self.checkout != bound
            if override and (checkout is None or Path(checkout).expanduser().resolve() != self.checkout):
                raise ApplyError(
                    f'this installation applies from {bound}; run the command there, add '
                    f'--checkout {self.checkout} to use this checkout for one apply, or run '
                    './bin/shaffy supplement rebind --checkout to move the binding'
                )
            binding = record['binding']
            if supplement and (binding['mode'] != 'supplement' or Path(supplement).resolve() != Path(binding['path'])):
                raise ApplyError(
                    f'{supplement} is not the bound supplement; preview it with '
                    './bin/shaffy diff --supplement, or run ./bin/shaffy supplement '
                    'rebind --path to move the binding'
                )
            selection = bound_supplement(binding)
            manifest = capture(self.checkout, selection, self.state, op, load)
            if manifest['supplement_id'] != binding['supplement_id']:
                shutil.rmtree(Path(self.state) / 'runs' / op, ignore_errors=True)
                raise ApplyError(
                    f'the bound supplement now says supplement_id {manifest["supplement_id"]!r}, '
                    f'not {binding["supplement_id"]!r}; run ./bin/shaffy supplement rebind '
                    f'--path {binding["path"]} --new-id to accept it'
                )
            source = source_manifest(self.checkout)
            record['pending'] = {
                'op': op,
                'scope': 'home',
                'capture': manifest['directory'],
                'phase': 'home',
                'started_at': now(),
                'checkout': str(self.checkout),
                'settings_sha256': manifest['settings_sha256'],
                'source': source,
            }
            if override:
                self.records.append(op, 'checkout-override', checkout=str(self.checkout), bound=str(bound))
            self.records.append(
                op,
                'begin',
                scope='home',
                capture=manifest['directory'],
                checkout=str(self.checkout),
                settings_sha256=manifest['settings_sha256'],
                source=source,
            )
            self.records.write(record)
            _fault('after-pending', self.prerequisites)
            problems = self._preflight(record['pending'])
            if problems:
                self.records.append(op, 'phase-not-started', problems=problems)
                record['pending'] = None
                self.records.write(record)
                shutil.rmtree(Path(self.state) / 'runs' / op, ignore_errors=True)
                raise ApplyError('preflight failed; nothing was changed:\n  ' + '\n  '.join(problems))
            changing = record['pending'].pop('_changing')
            transitions = record['pending'].pop('_transitions')
            self.units_changed = any(relative.startswith(UNITS) for _, relative in changing)
            pre = self.prerequisites.snapshot('pre', f'shaffy {op} home')
            copies = self._copy_before(op, changing, 'before')
            self.records.append(
                op, 'phase-start', phase='home', pre_snapshot=pre, copies=str(copies), changing=len(changing)
            )
            _fault('after-phase-start', self.prerequisites)
            return self._write(record, pre, copies, len(changing), transitions, resumed=False)

    def resume(self):
        with self.records.lock():
            record = self.records.read()
            self._require_record(record)
            pending = record['pending']
            if not pending:
                raise ApplyError('there is no unfinished operation to resume')
            op = pending['op']
            began_in = Path(pending['checkout'])
            if began_in != self.checkout:
                raise ApplyError(
                    f'operation {op} began in {began_in}; resume it there, or run ./bin/shaffy apply --abandon'
                )
            events = [e['event'] for e in self._events(op)]
            if any(op in line for line in self.records.torn):
                raise ApplyError(
                    f'the journal has unreadable lines for operation {op}, so it cannot be '
                    'resumed safely; run ./bin/shaffy apply --abandon and review the home'
                )
            if 'begin' not in events:
                raise ApplyError(
                    f'the journal has no record of how operation {op} began, so it cannot '
                    'be resumed safely; run ./bin/shaffy apply --abandon and review the home'
                )
            if 'phase-done' in events:
                record['pending'] = None
                record['last_completed_op'] = op
                self.records.write(record)
                self.out(f'Operation {op} had finished; its completion is now recorded.')
                return 0
            if 'phase-start' not in events:
                self.records.append(op, 'abandoned', reason='nothing had been written')
                record['pending'] = None
                self.records.write(record)
                shutil.rmtree(Path(self.state) / 'runs' / op, ignore_errors=True)
                self.out(f'Operation {op} had written nothing and is cleared. Run ./bin/shaffy apply --home again.')
                return 0
            try:
                verify(pending['capture'])
            except CaptureError as error:
                raise ApplyError(f'{error}; run ./bin/shaffy apply --abandon and review the home') from error
            current = source_manifest(self.checkout)
            if current['sha256'] != pending['source']['sha256']:
                self.records.append(op, 'source-changed', during='resume', source=current)
                pending['source'] = current
                self.records.write(record)
            problems = self._preflight(pending)
            if problems:
                self.records.append(op, 'resume-refused', problems=problems)
                raise ApplyError(
                    f'cannot resume operation {op} yet; this attempt changed nothing:\n  '
                    + '\n  '.join(problems)
                    + '\n  Fix the cause and resume, or run ./bin/shaffy apply --abandon.'
                )
            changing = pending.pop('_changing')
            transitions = pending.pop('_transitions')
            self.units_changed = any(relative.startswith(UNITS) for _, relative in changing)
            attempt = sum(1 for e in events if e == 'resume-start') + 1
            pre = self.prerequisites.snapshot('pre', f'shaffy {op} home resume {attempt}')
            copies = self._copy_before(op, changing, f'before-resume-{attempt}')
            self.records.append(op, 'resume-start', pre_snapshot=pre, copies=str(copies), changing=len(changing))
            return self._write(record, pre, copies, len(changing), transitions, resumed=True)

    def abandon(self):
        with self.records.lock():
            record = self.records.read()
            self._require_record(record)
            pending = record['pending']
            if not pending:
                raise ApplyError('there is no unfinished operation to abandon')
            op = pending['op']
            starts = [e for e in self._events(op) if e['event'] in ('phase-start', 'resume-start')]
            self.records.append(
                op,
                'abandoned',
                reason='abandoned by the user',
                pre_snapshots=[e['pre_snapshot'] for e in starts],
                copies=[e['copies'] for e in starts],
            )
            record['pending'] = None
            self.records.write(record)
            unsettled = sorted(
                name
                for name, entry in self.ledger.read()['integrations'].items()
                if (entry.get('transition') or {}).get('op') == op
            )
        if starts:
            self.out(
                f'Operation {op} is abandoned. It may have written part of the home. Nothing was '
                'deleted: compare the home with snapper pre snapshot(s) '
                + ', '.join(str(e['pre_snapshot']) for e in starts)
                + ' and the copies in '
                + ', '.join(e['copies'] for e in starts)
                + '.'
            )
        else:
            self.out(f'Operation {op} is abandoned; it had written nothing.')
        for name in unsettled:
            self.out(
                f'Integration {name} was changing; review it, then run ./bin/shaffy integration '
                f'{name} adopt (it is active and yours) or release (stop tracking it).'
            )
        return 0

    def _write(self, record, pre, copies, changing, transitions, resumed):
        pending = record['pending']
        op = pending['op']
        before = None
        if transitions:
            owned = self.ledger.read(self._target())
            before = copy.deepcopy(owned)
            self.ledger.write(ledger.begin(owned, transitions, op))
            self.records.append(
                op, 'ledger-intent', transitions=[{'integration': t['name'], 'to': t['to']} for t in transitions]
            )
        applied = self._chezmoi('apply', '--force', marker=self._marker(pending))
        if applied.returncode and STATE_LOCK_TIMEOUT in applied.stderr:
            # chezmoi takes its state lock before it writes anything, so this attempt wrote nothing.
            if resumed:
                self.records.append(
                    op, 'resume-refused', problems=['another chezmoi command is running'], pre_snapshot=pre
                )
                raise ApplyError(
                    f'another chezmoi command is running; this resume of operation {op} '
                    'changed nothing. Run ./bin/shaffy apply --resume when it has finished.'
                )
            self.records.append(
                op, 'phase-not-started', problems=['another chezmoi command is running'], pre_snapshot=pre
            )
            if before is not None:
                # Nothing was written, so the transitions this operation announced never began.
                self.ledger.write(before)
                self.records.append(op, 'ledger-intent-withdrawn')
            record['pending'] = None
            self.records.write(record)
            shutil.rmtree(Path(self.state) / 'runs' / op, ignore_errors=True)
            raise ApplyError(
                'another chezmoi command is running; nothing was changed. '
                'Run ./bin/shaffy apply again when it has finished.'
            )
        if applied.returncode:
            self.records.append(op, 'phase-failed', stderr=applied.stderr.strip()[-2000:])
            raise ApplyError(
                f'chezmoi apply failed; operation {op} stays unfinished.\n'
                f'  Copies: {copies}; snapper pre snapshot {pre}.\n'
                f'  {applied.stderr.strip()[-1500:]}\n'
                '  Fix the cause, then run ./bin/shaffy apply --resume (or --abandon).'
            )
        _fault('after-chezmoi', self.prerequisites)
        checked = self._chezmoi('verify', marker=self._marker(pending))
        if checked.returncode:
            self.records.append(op, 'phase-failed', stderr='chezmoi verify: ' + checked.stderr[-1000:])
            raise ApplyError(
                f'the home does not match the settings after applying; operation {op} '
                'stays unfinished; run ./bin/shaffy apply --resume'
            )
        if desktop_key.differs(self.key, Path.home()):
            desktop_key.install(self.key, Path.home())
        problems = validate_applied(Path.home())
        if problems:
            self.records.append(op, 'phase-failed', problems=problems)
            raise ApplyError(
                f'the applied home does not validate; operation {op} stays unfinished.\n  '
                + '\n  '.join(p.replace('\n', '\n    ') for p in problems)
                + f'\n  Copies: {copies}; snapper pre snapshot {pre}. Fix the cause, then '
                'run ./bin/shaffy apply --resume (or --abandon).'
            )
        owned = self.ledger.read(self._target())
        settled = ledger.finish(owned, op, Path.home(), self.ledger.write)
        if settled:
            self.ledger.write(owned)
            self.records.append(
                op, 'ledger-done', transitions=[{'integration': name, 'to': to} for name, to in settled]
            )
        post = self.prerequisites.snapshot('post', f'shaffy {op} home', pre=pre)
        current = source_manifest(self.checkout)
        if current['sha256'] != pending['source']['sha256']:
            self.records.append(op, 'source-changed', during='apply', source=current, post_snapshot=post)
            self.records.write(record)
            raise ApplyError(
                f'the checkout changed while operation {op} was applying, so it is not '
                'recorded as complete; run ./bin/shaffy apply --resume to finish it with '
                'the current checkout'
            )
        self.records.append(op, 'phase-done', phase='home', post_snapshot=post)
        record['pending'] = None
        record['last_completed_op'] = op
        self.records.write(record)
        self._prune(op)
        self.out(
            f'Applied the home ({changing} target(s) changed) as operation {op}; '
            f'snapshots {pre} and {post}; copies in {copies}.'
        )
        if self.note:
            self.out(f'Note: {self.note}')
        for name, to in settled:
            self.out(
                f'Integration {name} is now {to}.'
                + (' A service it enabled keeps running until you stop it or log out.' if to == 'disabled' else '')
            )
        for name, reason in sorted(self.deferred.items()):
            self.out(f'Integration {name} waits: {reason}.')
        self._start_units(ledger.units_to_start(owned, settled))
        self._reload_session()
        return 0

    def _start_units(self, units):
        """Reload the user manager after unit files changed, and start what a newly managed
        integration enabled; never fails the apply.

        A running user manager keeps the unit files it loaded, so a timer would otherwise run
        the old unit. The wants links take effect at the next login by themselves; with a
        running user manager the units start now. The test adapter never touches a real one.
        """
        if not units and not self.units_changed:
            return
        runtime = os.environ.get('XDG_RUNTIME_DIR')
        if self.prerequisites.name == 'fake' or not runtime or not os.path.exists(os.path.join(runtime, 'bus')):
            if units:
                self.out(
                    'Note: ' + ', '.join(units) + ' start at the next login; to start them now, run '
                    'systemctl --user daemon-reload && systemctl --user start ' + ' '.join(units)
                )
            else:
                self.out(
                    'Note: user unit files changed; run systemctl --user daemon-reload, or log in '
                    'again, so the running user manager uses them.'
                )
            return
        steps = [['systemctl', '--user', 'daemon-reload']] + (
            [['systemctl', '--user', 'start', *units]] if units else []
        )
        for step in steps:
            try:
                result = subprocess.run(step, capture_output=True, text=True, timeout=60, check=False)
                failure = (result.stderr or result.stdout).strip()[-300:] if result.returncode else None
            except (OSError, subprocess.TimeoutExpired) as error:
                failure = str(error)
            if failure is not None:
                self.out(f'Note: {" ".join(step)} failed; the home is applied. {failure}')
                return
        if units:
            self.out('Started ' + ', '.join(units) + '.')

    def _reload_session(self):
        """Have a running Noctalia reread its configuration and templates; never fails the apply."""
        if not os.environ.get('NIRI_SOCKET') or not shutil.which('noctalia'):
            return
        for step in (['noctalia', 'msg', 'config-reload'], ['noctalia', 'msg', 'templates-apply']):
            try:
                result = subprocess.run(step, capture_output=True, text=True, timeout=30, check=False)
            except (OSError, subprocess.TimeoutExpired) as error:
                self.out(f'Note: {" ".join(step)} did not run ({error}); the home is applied.')
                return
            if result.returncode:
                self.out(
                    f'Note: {" ".join(step)} failed; the home is applied. '
                    + (result.stderr or result.stdout).strip()[-300:]
                )
                return
        # A reload never fetches plugin sources: Noctalia clones one only when it starts, and not
        # again in that run if the network was not up yet.
        try:
            listing = subprocess.run(
                ['noctalia', 'msg', 'plugins', 'source', 'list'],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except OSError, subprocess.TimeoutExpired:
            listing = None
        missing = (
            unfetched_plugin_sources(listing.stdout, state_dir().parent) if listing and not listing.returncode else []
        )
        if missing:
            self.out(
                f'Note: Noctalia has not fetched its plugin source(s) {", ".join(missing)}, so their plugins are '
                'missing; it clones a source only when it starts. With the network up, restart it: '
                'pkill -x noctalia; niri msg action spawn -- noctalia'
            )
        # templates-apply writes the Niri colour include, so the whole Niri configuration is checked again.
        niri = subprocess.run(['niri', 'validate'], capture_output=True, text=True, check=False)
        if niri.returncode:
            self.out(
                'Warning: niri validate fails after Noctalia applied its templates:\n'
                + (niri.stderr or niri.stdout).strip()[-800:]
            )


def unfetched_plugin_sources(listing, state_home):
    """The git sources in LISTING (`noctalia msg plugins source list`) that Noctalia has not cloned."""
    missing = []
    for line in listing.splitlines():
        fields = line.split()
        if (
            len(fields) >= 2
            and fields[1] == 'git'
            and not (Path(state_home) / 'noctalia/plugins/sources' / fields[0] / 'repo').is_dir()
        ):
            missing.append(fields[0])
    return missing


def run(checkout, chezmoi_command, op, supplement=None, resume=False, abandon=False, checkout_override=None, out=print):
    """Entry point for the CLI; returns an exit code and prints refusals clearly."""
    import sys

    try:
        home_apply = HomeApply(checkout, chezmoi_command, out)
        if abandon:
            return home_apply.abandon()
        if resume:
            return home_apply.resume()
        return home_apply.start(op, supplement, checkout_override)
    except (ApplyError, LockedError, RecordError, SelectionError, CaptureError) as error:
        print(f'shaffy: {error}', file=sys.stderr)
        return 1
    except SettingsError as error:
        print('shaffy: invalid settings:\n  ' + '\n  '.join(error.problems), file=sys.stderr)
        return 1
