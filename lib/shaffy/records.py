"""The target user's binding record, journal and lock.

The record holds identity, location and progress, never settings values. It is
written atomically (temporary file, fsync, rename, directory fsync), so a crash
leaves either the old or the new record; the journal is append-only JSONL.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import pwd
import tempfile

from .selection import state_dir

RECORD_VERSION = 1


class LockedError(Exception):
    """Another Shaffy OS operation holds this target's lock."""


class RecordError(Exception):
    """The record is unreadable, from a newer version, or for another target."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def current_target():
    entry = pwd.getpwuid(os.getuid())
    return {'uid': entry.pw_uid, 'user': entry.pw_name, 'home': str(Path.home())}


class Records:
    """Binding record, journal and lock under one state directory."""

    def __init__(self, directory=None, fault=None):
        self.dir = Path(directory) if directory else state_dir()
        self.binding = self.dir / 'binding.json'
        self.journal = self.dir / 'journal.jsonl'
        self.lock_path = self.dir / 'lock'
        self.fault = fault or (lambda point: None)
        self.torn = []

    def _ensure_dir(self):
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    def read(self):
        """Return the record, or None when this target has never been bound."""
        try:
            record = json.loads(self.binding.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise RecordError(f'{self.binding} is unreadable: {error}') from error
        if record.get('record_version') != RECORD_VERSION:
            raise RecordError(f'{self.binding} has record_version {record.get("record_version")!r}; '
                              f'this Shaffy OS reads {RECORD_VERSION}')
        return record

    def write(self, record):
        """Replace the record atomically; stale temporary files from a crash are removed."""
        self._ensure_dir()
        for stale in self.dir.glob('.binding-*.tmp'):
            stale.unlink(missing_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.dir, prefix='.binding-', suffix='.tmp')
        try:
            with os.fdopen(handle, 'w') as stream:
                json.dump({**record, 'record_version': RECORD_VERSION}, stream, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            self.fault('before-replace')
            os.replace(temporary, self.binding)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        self.fault('after-replace')
        directory = os.open(self.dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def append(self, op, event, **details):
        """Append one whole journal line and fsync it; a torn last line is closed first."""
        self._ensure_dir()
        data = (json.dumps({'at': now(), 'op': op, 'event': event, **details}) + '\n').encode()
        descriptor = os.open(self.journal, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            size = os.fstat(descriptor).st_size
            if size and os.pread(descriptor, 1, size - 1) != b'\n':
                data = b'\n' + data
            while data:
                data = data[os.write(descriptor, data):]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def events(self, limit=None):
        """Parsed journal events; unreadable lines are skipped and kept in self.torn."""
        try:
            lines = self.journal.read_text(errors='replace').splitlines()
        except FileNotFoundError:
            return []
        events, self.torn = [], []
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                event = None
            if isinstance(event, dict) and 'op' in event and 'event' in event:
                events.append(event)
            else:
                self.torn.append(line)
        return events[-limit:] if limit else events

    @contextmanager
    def lock(self):
        """Hold this target's lock, or fail at once if another operation holds it."""
        self._ensure_dir()
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise LockedError('another Shaffy OS operation is running for '
                                  f'{current_target()["user"]}') from error
            yield
        finally:
            os.close(descriptor)

    def check_target(self, record):
        """The bound target must be the user running this command."""
        target, current = record['target'], current_target()
        if (target['uid'], target['home']) != (current['uid'], current['home']):
            raise RecordError(f"this installation is bound for {target['user']} ({target['home']}), "
                              f"not {current['user']} ({current['home']})")


def new_record(binding, checkout):
    return {'record_version': RECORD_VERSION, 'target': current_target(), 'binding': binding,
            'checkout': {'path': str(checkout)}, 'system': {'bound': False},
            'pending': None, 'last_completed_op': None}


ROOT_STATE = Path('/var/lib/shaffy')


def root_state():
    """The root records' directory. With the test adapter it is never the real one: it is
    SHAFFY_TEST_ROOT_STATE, or a directory beside the test's own state."""
    if os.environ.get('SHAFFY_TEST_PREREQUISITES') == 'fake':
        return Path(os.environ.get('SHAFFY_TEST_ROOT_STATE') or state_dir() / 'fake-root')
    return ROOT_STATE


def read_root_record(uid):
    """The root record of a target (world-readable: it holds no settings values), or None."""
    path = root_state() / 'targets' / f'{uid}.json'
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except PermissionError as error:
        raise RecordError(f'{path} is unreadable because {root_state()} is not readable by this user; it '
                          f'holds no settings values, so run: sudo chmod 755 {root_state()}') from error
    except (OSError, ValueError) as error:
        raise RecordError(f'{path} is unreadable: {error}') from error


def root_events(uid, op=None):
    try:
        lines = (root_state() / 'targets' / f'{uid}.journal.jsonl').read_text(errors='replace').splitlines()
    except FileNotFoundError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and (op is None or event.get('op') == op):
            events.append(event)
    return events


def disagreements(user, root):
    """What the user and root records disagree on; an empty list means they agree."""
    problems = []
    for label, mine, theirs in (
            ('target.uid', user['target']['uid'], root['target']['uid']),
            ('target.home', user['target']['home'], root['target']['home']),
            ('binding.mode', user['binding']['mode'], root['binding']['mode']),
            ('binding.supplement_id', user['binding']['supplement_id'], root['binding']['supplement_id']),
            ('binding.path', user['binding']['path'], root['binding']['path'])):
        if mine != theirs:
            problems.append(f'{label} is {mine!r} in the user record but {theirs!r} in the root record')
    pending, root_pending = user.get('pending') or {}, root.get('pending') or {}
    system = pending.get('scope') == 'system'
    if (system or root_pending) and (pending.get('op') if system else None) != root_pending.get('op'):
        problems.append(f"the unfinished system operation is {pending.get('op') if system else None!r} "
                        f"in the user record but {root_pending.get('op')!r} in the root record")
    return problems
