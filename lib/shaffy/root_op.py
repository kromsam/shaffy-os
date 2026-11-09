"""The root steps of a system operation.

The play copies this file into /var/lib/shaffy/ops/<op>/ and runs it from there
as root, with the standard library only:

  begin    copy the user's capture into the root-owned operation directory,
           verify it against the manifest and the digest the wrapper passed,
           take the `pre` snapshots of / and /home (before root `pending`
           exists, so rolling / back to them restores records without it),
           create or check the root record, set root `pending` (the system
           lock, serialized across target accounts) and print the settings
           file the play reads. A second `begin` of the same operation resumes.
  finish   take the `post` snapshots, record the phase and completion and
           clear root `pending`.
  abandon  clear root `pending` of an operation that cannot finish, journal
           its snapshots for review and delete nothing.
  rebind   move the root record's binding with the user's (`supplement rebind`).

`--test-prerequisites fake` replaces snapper and allows `--state-dir` and
`--test-fault`; it is refused outside a container.
"""

import argparse
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

RECORD_VERSION = 1
STATE = '/var/lib/shaffy'
OP_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
HOME_CONFIG = {'SYNC_ACL': 'yes', 'TIMELINE_CREATE': 'no', 'NUMBER_CLEANUP': 'yes', 'NUMBER_LIMIT': '10'}


class RootError(Exception):
    """A refusal; the message says what to do."""


def now():
    return datetime.now(UTC).isoformat(timespec='seconds')


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, data, mode):
    """Replace a file atomically: temporary file, fsync, rename, directory fsync."""
    path = Path(path)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}-')
    try:
        with os.fdopen(handle, 'w') as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _config_values(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        match = re.match(r'^([A-Z_]+)="(.*)"$', line.strip())
        if match:
            values[match.group(1)] = match.group(2)
    return values


class Snapper:
    name = 'snapper'

    def __init__(self, targets):
        self.targets = targets

    def problems(self):
        found = []
        for mount in ('/', '/home'):
            kind = subprocess.run(
                ['findmnt', '-no', 'FSTYPE', mount], capture_output=True, text=True, check=False
            ).stdout.strip()
            if kind != 'btrfs':
                found.append(f'{mount} is not a Btrfs subvolume (found {kind or "nothing"})')
        if not os.path.exists('/etc/snapper/configs/root'):
            found.append('snapper has no root configuration')
        return found

    def ensure_home(self, user):
        """Create the `home` configuration, and repair its settings or add this user when
        they drifted, so every target user can take and read home pairs. Returns what it did."""
        path = '/etc/snapper/configs/home'
        created = not os.path.exists(path)
        if created:
            self._run('-c', 'home', 'create-config', '/home')
        values = _config_values(path)
        users = values.get('ALLOW_USERS', '').split()
        wanted = dict(HOME_CONFIG, ALLOW_USERS=' '.join(users + ([user] if user not in users else [])))
        changes = [f'{key}={value}' for key, value in wanted.items() if values.get(key) != value]
        if changes:
            self._run('-c', 'home', 'set-config', *changes)
        return 'created' if created else ('configured' if changes else None)

    def snapshot(self, config, kind, description, pre=None):
        command = [
            '-c',
            config,
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
        return int(self._run(*command).strip())

    def _run(self, *arguments):
        result = subprocess.run(['snapper', *arguments], capture_output=True, text=True, check=False)
        if result.returncode:
            raise RootError(f'snapper {" ".join(arguments[:3])} failed: {result.stderr.strip()}')
        return result.stdout


class FakeSnapper:
    """Test stand-in, honoured only inside a container."""

    name = 'fake'

    def __init__(self, targets):
        self.log = Path(targets) / 'fake-snapshots.jsonl'
        self.users = Path(targets) / 'fake-home-users'

    def problems(self):
        return []

    def ensure_home(self, user):
        users = self.users.read_text().split() if self.users.exists() else []
        if user in users:
            return None
        self.users.write_text(' '.join([*users, user]) + '\n')
        return 'configured' if users else 'created'

    def snapshot(self, config, kind, description, pre=None):
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        with self.log.open('a') as stream:
            stream.write(json.dumps({'config': config, 'type': kind, 'pre': pre}) + '\n')
        return len(lines) + 1


def _in_container():
    return Path('/.dockerenv').exists() or Path('/run/.containerenv').exists()


class Operation:
    def __init__(self, args):
        self.args = args
        if not OP_ID.match(args.op):
            raise RootError(f'{args.op!r} is not an operation ID')
        fake = args.test_prerequisites == 'fake'
        if fake and not _in_container():
            raise RootError('the test prerequisite adapter is refused outside a container')
        if (args.state_dir or args.test_fault) and not fake:
            raise RootError('--state-dir and --test-fault are for tests only')
        self.state = Path(args.state_dir or STATE)
        self.targets = self.state / 'targets'
        self.record_path = self.targets / f'{args.uid}.json'
        self.journal = self.targets / f'{args.uid}.journal.jsonl'
        self.op_dir = self.state / 'ops' / args.op
        self.snapper = (FakeSnapper if fake else Snapper)(self.targets)
        # sudo resets the environment, so a test passes its fault point as an argument.
        self.fault = args.test_fault

    def _fault(self, point):
        if self.fault == point:
            os.kill(os.getpid(), signal.SIGKILL)

    def _dirs(self):
        for directory, mode in ((self.state, 0o755), (self.targets, 0o755), (self.state / 'ops', 0o700)):
            directory.mkdir(mode=mode, exist_ok=True)
            os.chmod(directory, mode)

    def append(self, event, **details):
        data = (json.dumps({'at': now(), 'op': self.args.op, 'event': event, **details}) + '\n').encode()
        descriptor = os.open(self.journal, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.fchmod(descriptor, 0o644)  # the user side reads it for recovery; it holds no settings
            size = os.fstat(descriptor).st_size
            if size and os.pread(descriptor, 1, size - 1) != b'\n':
                data = b'\n' + data
            while data:
                data = data[os.write(descriptor, data) :]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def events(self):
        try:
            lines = self.journal.read_text(errors='replace').splitlines()
        except FileNotFoundError:
            return []
        found = []
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get('op') == self.args.op:
                found.append(event)
        return found

    def locked(self, action):
        """Run an action with the umask and the lock every root step holds."""
        umask = os.umask(0o077)
        try:
            self._dirs()
            descriptor = os.open(self.state / 'lock', os.O_RDWR | os.O_CREAT, 0o600)
            try:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise RootError('another Shaffy OS system step is running; try again') from error
                return action()
            finally:
                os.close(descriptor)
        finally:
            os.umask(umask)

    def read_record(self):
        try:
            record = json.loads(self.record_path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise RootError(f'{self.record_path} is unreadable: {error}') from error
        if record.get('record_version') != RECORD_VERSION:
            raise RootError(f'{self.record_path} has an unknown record_version')
        return record

    # -- begin ---------------------------------------------------------------
    def _copy_capture(self):
        """Copy the user's capture: regular files and directories only, never following links,
        and checking each file's type only after opening it, so nothing can be swapped in."""
        source, target = Path(os.path.realpath(self.args.capture)), self.op_dir / 'capture'
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(mode=0o700)
        for directory, subdirectories, files in os.walk(source, followlinks=False):
            relative = Path(directory).relative_to(source)
            for name in subdirectories:
                if (Path(directory) / name).is_symlink():
                    raise RootError(f'the capture contains a symbolic link: {relative / name}')
                (target / relative / name).mkdir(mode=0o700)
            for name in files:
                try:
                    descriptor = os.open(Path(directory) / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                except OSError as error:  # ELOOP: a symbolic link
                    raise RootError(f'the capture contains something other than a file: {relative / name}') from error
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    os.close(descriptor)
                    raise RootError(f'the capture contains something other than a file: {relative / name}')
                with (
                    os.fdopen(descriptor, 'rb') as stream,
                    open(target / relative / name, 'xb', opener=lambda p, f: os.open(p, f, 0o600)) as copy,
                ):
                    shutil.copyfileobj(stream, copy)
        return target

    def _verify(self, copy):
        manifest_path = copy / 'manifest.json'
        if not manifest_path.is_file() or digest(manifest_path) != self.args.manifest_sha256:
            raise RootError('the capture manifest does not match the digest the wrapper passed')
        manifest = json.loads(manifest_path.read_text())
        present = sorted(
            str(p.relative_to(copy))
            for p in copy.rglob('*')
            if p.is_file() and p.relative_to(copy) != Path('manifest.json')
        )
        if present != sorted(manifest['files']):
            raise RootError('the capture holds other files than its manifest lists')
        for name, expected in manifest['files'].items():
            if digest(copy / name) != expected:
                raise RootError(f'the captured {name} does not match its manifest')
        return manifest

    def _play_settings(self, copy):
        """The settings the play reads: the captured ones, with file paths inside the root copy."""
        data = json.loads((copy / 'settings.json').read_text())
        shaffy = data['shaffy']
        capture = os.path.realpath(self.args.capture)
        inside = os.path.realpath(copy) + '/'

        def rebase(path):
            relative = os.path.relpath(path, capture) if os.path.isabs(path) else '..'
            if relative.startswith('..') or os.path.normpath(path) != path:
                raise RootError(f'a captured reference lies outside the capture: {path}')
            rebased = os.path.join(copy, relative)
            if not os.path.realpath(rebased).startswith(inside) or not Path(rebased).is_file():
                raise RootError(f'the captured reference {path} is missing from the root copy')
            return rebased

        shaffy['overrides'] = {name: rebase(path) for name, path in shaffy.get('overrides', {}).items()}
        shaffy['references'] = [rebase(path) for path in shaffy.get('references', [])]
        play = self.op_dir / 'settings.play.json'
        write_json(play, data, 0o600)
        return play

    def _binding(self, copy, manifest):
        selection = json.loads((copy / 'selection.json').read_text())
        return {
            'mode': selection['mode'],
            'path': selection.get('path'),
            'supplement_id': manifest.get('supplement_id'),
        }

    def begin(self):
        return self.locked(self._begin)

    def _begin(self):
        args = self.args
        for other in sorted(self.targets.glob('*.json')):
            if other == self.record_path:
                continue
            pending = json.loads(other.read_text()).get('pending')
            if pending:
                raise RootError(
                    f'system operation {pending["op"]} of another user is unfinished; '
                    'it has to be resumed or abandoned first'
                )
        record = self.read_record()
        target = {'uid': args.uid, 'user': args.user, 'home': args.home}
        resumed = bool(record and record['pending'] and record['pending']['op'] == args.op)
        if record and record['pending'] and not resumed:
            raise RootError(f'system operation {record["pending"]["op"]} is unfinished; resume or abandon it')
        if record and (record['target']['uid'], record['target']['home']) != (args.uid, args.home):
            raise RootError(f'the root record names {record["target"]}, not {target}')
        problems = self.snapper.problems()
        if problems:
            raise RootError('; '.join(problems))
        self.op_dir.mkdir(mode=0o700, exist_ok=True)
        copy = self.op_dir / 'capture' if resumed else self._copy_capture()
        manifest = self._verify(copy)
        binding = self._binding(copy, manifest)
        if record and record['binding'] != binding:
            raise RootError(
                f"the root record's binding {record['binding']} differs from the operation's "
                f'{binding}; run ./bin/shaffy supplement rebind'
            )
        play = self._play_settings(copy)
        home = self.snapper.ensure_home(args.user)
        if home:
            self.append(f'snapper-home-{home}', allow_user=args.user)
        # The pre snapshots come before root `pending` exists, so / rolled back to them
        # carries the records as they were before this operation.
        pre = {config: self.snapper.snapshot(config, 'pre', f'shaffy {args.op} system') for config in ('root', 'home')}
        self._fault('after-root-pre-snapshots')
        if not resumed:
            record = record or {
                'record_version': RECORD_VERSION,
                'target': target,
                'binding': binding,
                'pending': None,
                'last_completed_op': None,
                'phases': {},
            }
            record['pending'] = {
                'op': args.op,
                'scope': 'system',
                'capture': str(copy),
                'phase': 'system',
                'started_at': now(),
            }
            # The record first: recovery trusts root `pending`, the journal only explains it.
            write_json(self.record_path, record, 0o644)
            self._fault('after-root-pending')
            self.append('begin', capture=str(copy), manifest_sha256=args.manifest_sha256)
        self.append('resume-start' if resumed else 'phase-start', phase='system', pre=pre)
        self._fault('after-root-phase-start')
        return {'settings_file': str(play), 'resumed': resumed, 'pre': pre}

    # -- finish, abandon, rebind ---------------------------------------------
    def _prune(self, keep=10):
        """Keep the root copies of the newest ten completed operations. An unfinished or abandoned
        operation's copy is never pruned (abandon promises to delete nothing); a directory without
        a capture holds only the root steps of a record change and goes."""
        kept = set()
        for path in self.targets.glob('*.json'):
            pending = json.loads(path.read_text()).get('pending')
            if pending:
                kept.add(pending['op'])
        for journal in self.targets.glob('*.journal.jsonl'):
            for line in journal.read_text(errors='replace').splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get('event') == 'abandoned':
                    kept.add(event.get('op'))
        candidates = [
            p for p in (self.state / 'ops').iterdir() if p.is_dir() and p.name not in kept and p.name != self.args.op
        ]
        for helper in (p for p in candidates if not (p / 'capture').is_dir()):
            shutil.rmtree(helper, ignore_errors=True)
        done = sorted((p for p in candidates if (p / 'capture').is_dir()), reverse=True)
        for stale in done[keep - 1 :]:
            shutil.rmtree(stale, ignore_errors=True)

    def _pending_record(self):
        record = self.read_record()
        if not record or not record['pending'] or record['pending']['op'] != self.args.op:
            raise RootError(f'operation {self.args.op} is not the unfinished system operation on record')
        return record

    def finish(self):
        return self.locked(self._finish)

    def _finish(self):
        record = self._pending_record()
        starts = [e for e in self.events() if e['event'] in ('phase-start', 'resume-start')]
        if not starts:
            raise RootError(f'operation {self.args.op} has no phase-start on record')
        pre = starts[-1]['pre']
        post = {
            config: self.snapper.snapshot(config, 'post', f'shaffy {self.args.op} system', pre=number)
            for config, number in pre.items()
        }
        self.append('phase-done', phase='system', post=post)
        self._fault('after-root-phase-done')
        record['phases']['system'] = {'op': self.args.op, 'completed_at': now()}
        record['last_completed_op'] = self.args.op
        record['pending'] = None
        write_json(self.record_path, record, 0o644)
        self.append('complete')
        self._prune()
        return {'post': post}

    def abandon(self):
        return self.locked(self._abandon)

    def _abandon(self):
        record = self._pending_record()
        pre = [e['pre'] for e in self.events() if e['event'] in ('phase-start', 'resume-start')]
        record['pending'] = None
        write_json(self.record_path, record, 0o644)
        self.append('abandoned', pre_snapshots=pre, copy=str(self.op_dir))
        return {'pre_snapshots': pre, 'copy': str(self.op_dir)}

    def rebind(self):
        return self.locked(self._rebind)

    def _rebind(self):
        record = self.read_record()
        if not record:
            raise RootError(f'there is no root record for uid {self.args.uid}')
        if record['pending']:
            raise RootError(f'system operation {record["pending"]["op"]} is unfinished; resume or abandon it')
        new = {'mode': self.args.mode, 'path': self.args.path, 'supplement_id': self.args.supplement_id}
        old = record['binding']
        record['binding'] = new
        write_json(self.record_path, record, 0o644)
        self.append('rebind', **{'from': old, 'to': new})
        return {'binding': new}


def main(argv):
    parser = argparse.ArgumentParser(prog='root_op.py')
    parser.add_argument('action', choices=('begin', 'finish', 'abandon', 'rebind'))
    parser.add_argument('--op', required=True)
    parser.add_argument('--uid', required=True, type=int)
    parser.add_argument('--user')
    parser.add_argument('--home')
    parser.add_argument('--capture')
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--mode', choices=('supplement', 'none'))
    parser.add_argument('--path')
    parser.add_argument('--supplement-id')
    parser.add_argument('--test-prerequisites', choices=('fake',))
    parser.add_argument('--state-dir')
    parser.add_argument('--test-fault')
    args = parser.parse_args(argv)
    if args.action == 'begin' and not all((args.user, args.home, args.capture, args.manifest_sha256)):
        parser.error('begin needs --user, --home, --capture and --manifest-sha256')
    if args.action == 'rebind' and not args.mode:
        parser.error('rebind needs --mode, and --path and --supplement-id for a supplement')
    try:
        operation = Operation(args)
        result = getattr(operation, args.action)()
    except RootError as error:
        print(f'shaffy: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
