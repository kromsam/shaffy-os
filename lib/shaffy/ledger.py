"""The ownership ledger's home part: ~/.local/state/shaffy/ledger.json.

An entry per integration this installation manages records its state and the
activation it created or adopted, so `disabled` reverses exactly that. A
transition first writes its intent (`transition`), then the home operation acts,
then the final state is written; an entry left with a transition belongs to the
unfinished operation.
"""
import json
import os
from pathlib import Path
import tempfile

from .records import RecordError, now

LEDGER_VERSION = 1
REVERSIBLE = ('wants-link', 'autostart')


class Ledger:
    def __init__(self, directory):
        self.path = Path(directory) / 'ledger.json'

    def read(self, target=None):
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {'ledger_version': LEDGER_VERSION, 'target': target, 'integrations': {}}
        except (OSError, ValueError) as error:
            raise RecordError(f'the ownership ledger {self.path} is unreadable: {error}') from error
        if not isinstance(data, dict) or data.get('ledger_version') != LEDGER_VERSION:
            raise RecordError(f'the ownership ledger {self.path} has an unknown ledger_version')
        return data

    def write(self, data):
        """Replace the ledger atomically: temporary file, fsync, rename, directory fsync."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='.ledger-', suffix='.tmp')
        try:
            with os.fdopen(handle, 'w') as stream:
                json.dump(data, stream, indent=2, sort_keys=True)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def expand(path, home):
    return Path(home) / path[2:] if path.startswith('~/') else Path(path)


def _path(record, home):
    return expand(record['link'] if record['kind'] == 'wants-link' else record['path'], home)


def present(record, home):
    """Whether an activation record already exists on this machine."""
    if record['kind'] == 'wants-link':
        return os.path.lexists(expand(record['link'], home))
    if record['kind'] == 'autostart':
        return os.path.lexists(expand(record['path'], home))
    if record['kind'] == 'config-key':
        return expand(record['file'], home).exists()
    return False


def adoptable(integration, home, key='adopt_requires'):
    """Why this integration cannot be adopted (or, with activate_requires, activated) yet, or None.

    `adopt_requires` names a line a file must hold before existing activation is
    owned, for an integration whose state must first take the managed shape;
    `activate_requires` one that must hold before an apply creates the activation.
    """
    required = integration.get(key)
    if not required:
        return None
    path = expand(required['file'], home)
    if 'json' in required:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = None
        wanted = required['json']
        if not isinstance(data, dict) or any(value is None or data.get(field) != value
                                             for field, value in wanted.items()):
            return (f"{required['file']} does not record "
                    + ', '.join(f'{field} {value!r}' for field, value in wanted.items())
                    + f" yet; {required['hint']}")
        return None
    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = []
    if required['line'] not in (line.strip() for line in lines):
        return f"{required['file']} does not hold {required['line']!r} yet; {required['hint']}"
    return None


def plan(integrations, ledger, home, op):
    """Compare the settings with the ledger: (problems, transitions) for the home scope.

    A transition is {name, scope, to, activation}; `to` is managed, disabled or released.
    An entry still carrying a transition of operation `op` is repeated (a resume); one of
    another operation was abandoned and waits for `integration adopt|release`. An
    integration whose `activate_requires` does not hold yet is deferred: this apply
    renders it as not managed and records nothing, so the apply that installs what the
    precondition needs is not refused. Returns (problems, transitions, deferred), with
    deferred as {name: reason}.
    """
    problems, transitions, deferred = [], [], {}
    entries = ledger['integrations']
    for name in sorted(set(integrations) | set(entries)):
        integration, entry = integrations.get(name), entries.get(name)
        if (integration or entry)['scope'] != 'home':
            continue
        if integration is None:
            problems.append(f'integration {name} is in the ownership ledger but no longer in '
                            f'defaults/integrations.yml; run ./bin/shaffy integration {name} release')
            continue
        state = integration['state']
        if entry and entry.get('transition') and entry['transition']['op'] != op:
            problems.append(f"integration {name} was left changing by abandoned operation "
                            f"{entry['transition']['op']}; review it, then run ./bin/shaffy integration "
                            f'{name} adopt or release')
            continue
        if state is None:
            if entry and entry['state'] == 'managed':
                problems.append(f'integration {name} is managed on this installation but the settings no '
                                f'longer name it; set integrations.{name} to disabled or released')
            continue
        current = entry['state'] if entry else None
        change = {'name': name, 'scope': 'home', 'to': state}
        if state == 'managed' and current != 'managed':
            if entry is None and any(present(r, home) for r in integration['activation']):
                blocked = adoptable(integration, home)
                problems.append(f'integration {name} is already active on this machine but not in the '
                                f'ownership ledger; ' + (f'{blocked}, then' if blocked else 'review it, then run')
                                + f' ./bin/shaffy integration {name} adopt')
                continue
            blocked = None if entry and entry.get('transition') else \
                adoptable(integration, home, 'activate_requires')
            if blocked:
                deferred[name] = blocked
                continue
            transitions.append({**change, 'activation': integration['activation']})
        elif state == 'managed' and entry['activation'] != integration['activation']:
            # The catalogue changed what activating it means; record the new activation.
            transitions.append({**change, 'activation': integration['activation']})
        elif state == 'disabled' and current == 'managed':
            transitions.append({**change, 'activation': entry['activation']})
        elif state == 'released' and entry:
            transitions.append({**change, 'activation': []})
    return problems, transitions, deferred


def units_to_start(ledger, settled):
    """The units behind the wants links of integrations that just became managed."""
    return [record['unit'] for name, to in settled if to == 'managed'
            for record in ledger['integrations'][name]['activation'] if record['kind'] == 'wants-link']


def removals(transitions, home):
    """Home-relative paths that disabling removes, for the operation's copies."""
    return [str(_path(r, home).relative_to(home)) for t in transitions if t['to'] == 'disabled'
            for r in t['activation'] if r['kind'] in REVERSIBLE and os.path.lexists(_path(r, home))]


def begin(ledger, transitions, op):
    """Record each transition's intent before the home operation writes.

    chezmoi creates a managed integration's links and entries, so only disabling has
    `remaining` records for the wrapper to reverse.
    """
    for change in transitions:
        entry = ledger['integrations'].setdefault(change['name'], {
            'class': 'integration', 'scope': change['scope'], 'state': None, 'adopted': False,
            'activation': change['activation'], 'created_by': op})
        if change['to'] == 'managed':
            entry['activation'] = change['activation']
        entry['transition'] = {'op': op, 'to': change['to'],
                               'remaining': [r for r in change['activation'] if r['kind'] in REVERSIBLE]
                               if change['to'] == 'disabled' else []}
        entry.update(changed_by=op, changed_at=now())
    return ledger


def finish(ledger, op, home, write):
    """Complete this operation's transitions after chezmoi verified the home; each step idempotent.

    chezmoi created a managed integration's activation; disabling removes the links and
    autostart entries it made (chezmoi now ignores them, so it would not); configuration
    keys in application files are the application's and stay.
    """
    done = []
    for name, entry in list(ledger['integrations'].items()):
        transition = entry.get('transition')
        if not transition or transition['op'] != op:
            continue
        while transition['remaining']:
            path = _path(transition['remaining'][0], home)
            if os.path.lexists(path):
                path.unlink()
            transition['remaining'].pop(0)
            write(ledger)
        if transition['to'] == 'released':
            del ledger['integrations'][name]
        else:
            entry.update(state=transition['to'], changed_by=op, changed_at=now(), transition=None)
        done.append((name, transition['to']))
    return done


def adopt(ledger, name, integration, op):
    ledger['integrations'][name] = {
        'class': 'integration', 'scope': integration['scope'], 'state': 'managed', 'adopted': True,
        'activation': integration['activation'], 'created_by': op, 'changed_by': op,
        'changed_at': now(), 'transition': None}
    return ledger
