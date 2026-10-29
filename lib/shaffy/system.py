"""Apply the system through its operation protocol (docs/ARCHITECTURE.md, "The system apply").

The unprivileged wrapper takes the user lock, captures the settings (the
loaded settings included), writes the user `pending` entry with scope
`system` and runs mise's ansible-playbook as the target user, so sudo
asks for the password in the terminal. The play's first root tasks copy
and verify the capture and set root `pending` (lib/shaffy/root_op.py); its
last clears it. When the play succeeds the wrapper sets `system.bound` and
completes the operation.

Recovery trusts root `pending`: an operation root never took is cleared, one
root holds is resumed or abandoned (records.yml clears root `pending` and
deletes nothing), and one root completed is completed for the user.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

from . import bootstrap
from .apply import ApplyError
from .capture import CaptureError, capture, verify
from .loader import SettingsError, load
from .records import (
    LockedError,
    RecordError,
    Records,
    current_target,
    disagreements,
    now,
    read_root_record,
    root_events,
    root_state,
)
from .selection import SelectionError, bound_supplement, state_dir
from .source import manifest as source_manifest


def _in_container():
    return Path('/.dockerenv').exists() or Path('/run/.containerenv').exists()


class SystemApply:
    def __init__(self, checkout, out=print, tags=None):
        self.checkout = Path(checkout).resolve()
        self.out = out
        self.tags = tags
        self.state = state_dir()
        self.records = Records()
        self.fake = os.environ.get('SHAFFY_TEST_PREREQUISITES') == 'fake'
        if self.fake and not _in_container():
            raise ApplyError('the test prerequisite adapter is refused for system applies outside a container')

    def _fault(self, point):
        if self.fake and os.environ.get('SHAFFY_TEST_FAULT') == point:
            os.kill(os.getpid(), signal.SIGKILL)

    def _ready(self):
        problems = bootstrap.problems(self.checkout, snapshots=not self.fake)
        if problems:
            raise ApplyError('the system prerequisites are not in place:\n  ' + '\n  '.join(problems))
        if not self.fake and not sys.stdin.isatty():
            raise ApplyError('a system change asks for the sudo password in a terminal; run it in one')

    def _checks(self, record, checkout):
        if not record:
            raise ApplyError(
                'this installation is not bound; run ./bin/shaffy supplement attach --supplement PATH (or --none) first'
            )
        self.records.check_target(record)
        bound = Path(record['checkout']['path'])
        if self.checkout != bound and (checkout is None or Path(checkout).expanduser().resolve() != self.checkout):
            raise ApplyError(
                f'this installation applies from {bound}; run the command there, or add '
                f'--checkout {self.checkout} to use this checkout for one apply'
            )
        root = read_root_record(record['target']['uid'])
        if root and not record['system']['bound']:
            raise ApplyError(
                'a root record exists for this user but the user record does not know it; '
                'run ./bin/shaffy supplement attach --adopt-system-record'
            )
        if root:
            problems = disagreements(record, root)
            if problems:
                raise ApplyError('the user and root records disagree:\n  ' + '\n  '.join(problems))
        self._ready()

    def _environment(self, operation):
        """The operation for the play, as one JSON environment variable (never Jinja-evaluated)."""
        if self.fake:
            operation['test'] = {'prerequisites': 'fake', 'state_dir': str(root_state())}
            if os.environ.get('SHAFFY_TEST_FAULT', '').startswith('after-root-'):
                operation['test']['fault'] = os.environ['SHAFFY_TEST_FAULT']
        return {'SHAFFY_SYSTEM': json.dumps(operation)}

    def _run_play(self, playbook, operation, tags=None):
        arguments = ['ansible-playbook', '-i', 'localhost,', str(self.checkout / playbook)]
        if not self.fake:
            arguments.append('--ask-become-pass')
        if tags:
            arguments += ['--tags', tags]
        command, env = bootstrap.ansible_command(self.checkout, *arguments)
        env.update(self._environment(operation))
        return subprocess.run(command, cwd=self.checkout, env=env, check=False).returncode

    def _target(self, record):
        return {'uid': record['target']['uid'], 'user': record['target']['user'], 'home': record['target']['home']}

    # -- apply ---------------------------------------------------------------
    def start(self, op, checkout=None):
        with self.records.lock():
            record = self.records.read()
            if record and record['pending']:
                raise ApplyError(
                    f'operation {record["pending"]["op"]} is unfinished; run ./bin/shaffy apply --resume (or --abandon)'
                )
            self._checks(record, checkout)
            manifest = capture(self.checkout, bound_supplement(record['binding']), self.state, op, load)
            if manifest['supplement_id'] != record['binding']['supplement_id']:
                shutil.rmtree(Path(manifest['directory']).parent, ignore_errors=True)
                raise ApplyError(
                    f'the bound supplement now says supplement_id {manifest["supplement_id"]!r}, '
                    f'not {record["binding"]["supplement_id"]!r}; run ./bin/shaffy supplement rebind '
                    f'--path {record["binding"]["path"]} --new-id to accept it'
                )
            if self.checkout != Path(record['checkout']['path']):
                self.records.append(
                    op, 'checkout-override', checkout=str(self.checkout), bound=record['checkout']['path']
                )
            source = source_manifest(self.checkout)
            record['pending'] = {
                'op': op,
                'scope': 'system',
                'capture': manifest['directory'],
                'phase': 'system',
                'started_at': now(),
                'checkout': str(self.checkout),
                'settings_sha256': manifest['settings_sha256'],
                'source': source,
                'tags': self.tags,
            }
            self.records.append(
                op,
                'begin',
                scope='system',
                capture=manifest['directory'],
                checkout=str(self.checkout),
                settings_sha256=manifest['settings_sha256'],
                source=source,
                tags=self.tags,
            )
            self.records.write(record)
            self._fault('after-pending')
            return self._play(record)

    def _play(self, record):
        pending = record['pending']
        op = pending['op']
        capture_dir = os.path.realpath(pending['capture'])
        operation = {
            'op': op,
            'capture': capture_dir,
            'target': self._target(record),
            'manifest_sha256': hashlib.sha256((Path(capture_dir) / 'manifest.json').read_bytes()).hexdigest(),
            'checkout': str(self.checkout),
        }
        self.out(f'Running the system play for operation {op}; sudo asks for your password.')
        if self._run_play('site.yml', operation, pending.get('tags')):
            self.records.append(op, 'system-failed')
            raise ApplyError(
                f'the system play failed; operation {op} stays unfinished. See ./bin/shaffy status; '
                'fix the cause and run ./bin/shaffy apply --resume, or, when the captured '
                'settings themselves are wrong, ./bin/shaffy apply --abandon.'
            )
        self._fault('after-play')
        return self._complete(record)

    def _complete(self, record):
        """Step 3 of the system protocol: the root side is complete, so is the user side."""
        pending = record['pending']
        op = pending['op']
        root = read_root_record(record['target']['uid'])
        if not root or root['pending'] or root['last_completed_op'] != op:
            raise ApplyError(
                f'the play ended but the root record does not show operation {op} complete; see ./bin/shaffy status'
            )
        changed = source_manifest(self.checkout)['sha256'] != pending['source']['sha256']
        if changed:
            self.records.append(op, 'source-changed', during='system')
        record['system']['bound'] = True
        record['last_completed_op'] = op
        record['pending'] = None
        self.records.append(op, 'complete', scope='system')
        self.records.write(record)
        self.out(
            f'Applied the system as operation {op}; snapper pairs are in the root journal '
            f'({root_state()}/targets/{record["target"]["uid"]}.journal.jsonl).'
        )
        if changed:
            # Root has finished; a new operation, not a resume, applies the checkout as it is now.
            self.out(
                'Warning: the checkout changed while the play ran; run ./bin/shaffy apply --system '
                'again so the system matches it.'
            )
        return 0

    # -- recovery ------------------------------------------------------------
    def _root_holds(self, record, op):
        root = read_root_record(record['target']['uid'])
        return root, bool(root and root['pending'] and root['pending']['op'] == op)

    def resume(self):
        with self.records.lock():
            record = self.records.read()
            self.records.check_target(record)
            pending = record['pending']
            op = pending['op']
            self.tags = pending.get('tags')  # a resume runs the roles the operation began with
            if Path(pending['checkout']) != self.checkout:
                raise ApplyError(f'operation {op} began in {pending["checkout"]}; resume it there')
            root, holds = self._root_holds(record, op)
            if root and root['last_completed_op'] == op and not root['pending']:
                return self._complete(record)
            if not holds:
                if any(e.get('event') == 'phase-start' for e in root_events(record['target']['uid'], op)):
                    raise ApplyError(
                        f'the root journal shows operation {op} started, but the root record does '
                        'not hold it (was / rolled back?); see ./bin/shaffy status'
                    )
                self.records.append(op, 'abandoned', reason='root never took the operation')
                record['pending'] = None
                self.records.write(record)
                self.out(
                    f'System operation {op} had not reached root and is cleared. Run ./bin/shaffy apply --system again.'
                )
                return 0
            try:
                verify(pending['capture'])
            except CaptureError as error:
                raise ApplyError(f'{error}; run ./bin/shaffy apply --abandon') from error
            self._ready()
            current = source_manifest(self.checkout)
            if current['sha256'] != pending['source']['sha256']:
                self.records.append(op, 'source-changed', during='resume', source=current)
                pending['source'] = current
                self.records.write(record)
            self.records.append(op, 'resume-start', scope='system')
            return self._play(record)

    def abandon(self):
        """Give up a system operation root holds (or never took); root keeps its copy and snapshots.
        It works from the root record alone when the user record is gone (a restored /home)."""
        with self.records.lock():
            record = self.records.read()
            if record:
                self.records.check_target(record)
            target = record['target'] if record else current_target()
            pending = (
                record['pending'] if record and record['pending'] and record['pending']['scope'] == 'system' else None
            )
            root = read_root_record(target['uid'])
            op = pending['op'] if pending else (root['pending']['op'] if root and root['pending'] else None)
            if not op:
                raise ApplyError('there is no unfinished system operation to abandon')
            if pending and root and root['last_completed_op'] == op and not root['pending']:
                # Root finished it: this is step 3 of the protocol, not an abandonment.
                return self._complete(record)
            holds = bool(root and root['pending'] and root['pending']['op'] == op)
            record = record or {'target': target}
            if holds:
                self._ready()
                self.out(f'Abandoning system operation {op} as root; sudo asks for your password.')
                if self._run_play(
                    'records.yml',
                    {'op': op, 'action': 'abandon', 'target': self._target(record), 'checkout': str(self.checkout)},
                ):
                    raise ApplyError(f'root could not abandon operation {op}; see the output above')
            self.records.append(op, 'abandoned', reason='abandoned by the user', root=holds)
            if pending:
                record['pending'] = None
                self.records.write(record)
            target = record['target']
        self.out(
            f'System operation {op} is abandoned; nothing was deleted. The system may be partly changed: '
            f'compare it with the snapper pre snapshots in {root_state()}/targets/'
            f'{target["uid"]}.journal.jsonl, then run ./bin/shaffy apply --system again.'
        )
        return 0

    def rebind(self, record, binding, op):
        """Move the root binding with the user's; the caller holds the user lock and writes the user side."""
        self._ready()
        self.out("Moving the root record's binding; sudo asks for your password.")
        if self._run_play(
            'records.yml',
            {
                'op': op,
                'action': 'rebind',
                'target': self._target(record),
                'binding': binding,
                'checkout': str(self.checkout),
            },
        ):
            raise ApplyError('root could not move its binding; the user record is unchanged')


def run(checkout, op, resume=False, abandon=False, checkout_override=None, tags=None, out=print):
    try:
        system = SystemApply(checkout, out, tags)
        if abandon:
            return system.abandon()
        return system.resume() if resume else system.start(op, checkout_override)
    except (ApplyError, LockedError, RecordError, SelectionError, CaptureError) as error:
        print(f'shaffy: {error}', file=sys.stderr)
        return 1
    except SettingsError as error:
        print('shaffy: invalid settings:\n  ' + '\n  '.join(error.problems), file=sys.stderr)
        return 1
