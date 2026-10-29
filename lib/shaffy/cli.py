"""Subcommands of `bin/shaffy`: selftest, lint, check, data, diff, apply, status, outdated,
update, supplement, integration and hook."""

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def selftest(args):
    """Run the unittest suite in parallel (tests/runner.py), or in order with --jobs 1;
    --skip-host reproduces what CI runs."""
    # Test scaffolding is the one thing written under the checkout.
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    env = dict(os.environ)
    if args.skip_host:
        env['SHAFFY_SKIP_HOST_TESTS'] = '1'
    if args.jobs == 1:
        command = [
            sys.executable,
            '-m',
            'unittest',
            'discover',
            '-s',
            str(ROOT / 'tests'),
            '-t',
            str(ROOT / 'tests'),
            '-p',
            'test_*.py',
        ]
    else:
        command = [sys.executable, str(ROOT / 'tests/runner.py'), *(['--jobs', str(args.jobs)] if args.jobs else [])]
    if args.verbose:
        command.append('-v')
    return subprocess.call(command, cwd=ROOT, env=env)


def lint(args):
    """Format and lint the checkout (lib/shaffy/lint.py)."""
    from . import lint as linter

    return linter.main(args)


def _selection_arguments(parser):
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--supplement', metavar='PATH', help='use this supplement')
    choice.add_argument('--without-supplement', action='store_true', help='use the public defaults only')


def _tracked_config_problem():
    """The tracked chezmoi.toml must keep the guards and the recorded state path."""
    import tomllib

    config = tomllib.loads((ROOT / 'chezmoi.toml').read_text())
    if config.get('edit', {}).get('apply') or config.get('edit', {}).get('watch'):
        return 'chezmoi.toml must not set [edit] apply or watch'
    missing = [
        name
        for name in ('apply', 'edit', 'update', 'init', 'destroy', 'purge')
        if 'bin/shaffy' not in ' '.join(config.get('hooks', {}).get(name, {}).get('pre', {}).get('args', []))
    ]
    if missing:
        return 'chezmoi.toml lacks the Shaffy OS hooks for: ' + ', '.join(missing)
    try:
        result = subprocess.run(
            [
                'chezmoi',
                '--config',
                str(ROOT / 'chezmoi.toml'),
                '--source',
                str(ROOT / 'home'),
                'dump-config',
                '--format',
                'json',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        from shaffy.bootstrap import pacman_step

        return f'missing packages: chezmoi; run {pacman_step()}'
    actual = json.loads(result.stdout).get('persistentState') if result.returncode == 0 else None
    if actual != str(chezmoi_state_path()):
        return f'chezmoi state resolves to {actual}, expected {chezmoi_state_path()}'
    return None


def chezmoi_state_path():
    """chezmoi's state file, which the tracked chezmoi.toml names."""
    return Path.home() / '.local/state/shaffy/chezmoistate.boltdb'


def _system_report():
    """System prerequisites; missing ones do not fail a check of the home."""
    from shaffy.bootstrap import problems

    found = problems(ROOT, snapshots=os.environ.get('SHAFFY_TEST_PREREQUISITES') != 'fake')
    if not found:
        print('System scope: prerequisites are in place.')
        return
    print('System scope is not ready (the home scope does not need it):')
    for problem in found:
        print(f'  - {problem}')


def check(args):
    """Validate the selection, defaults and supplement without using the cache."""
    import importlib.util

    from shaffy.bootstrap import MODULES, pacman_step

    missing = [package for module, package in MODULES.items() if importlib.util.find_spec(module) is None]
    if missing:
        return _fail(f'missing packages: {", ".join(missing)}; run {pacman_step()}')
    from shaffy.loader import SettingsError, load
    from shaffy.records import RecordError, Records
    from shaffy.selection import SelectionError

    try:
        record = Records().read()
        if record:
            Records().check_target(record)
    except RecordError as error:
        print(f'shaffy: {error}', file=sys.stderr)
        return 1
    problem = _tracked_config_problem()
    if problem:
        return _fail(problem)
    if record and record['pending']:
        print(f'Note: operation {record["pending"]["op"]} is unfinished; run ./bin/shaffy status for recovery.')
    try:
        data = load(ROOT, args.supplement, args.without_supplement)
    except SelectionError as error:
        print(f'shaffy: {error}', file=sys.stderr)
        return 1
    except SettingsError as error:
        print('shaffy: invalid settings:', file=sys.stderr)
        for problem in error.problems:
            print(f'  {problem}', file=sys.stderr)
        return 1
    # --no-home: the settings and their render alone, for any user, as a supplement's CI checks them.
    if not args.no_home:
        from shaffy.desktop_key import problems as desktop_key_problems
        from shaffy.folders import asset_conflicts

        conflicts = asset_conflicts(data['shaffy']['assets'], Path.home()) + desktop_key_problems()
        if conflicts:
            return _fail('the home cannot be applied as it is:\n  ' + '\n  '.join(conflicts))
        from shaffy.identity import signing_note

        note = signing_note(data['shaffy']['settings'], Path.home())
        if note:
            print(f'Note: {note}')
    selection = data['shaffy']['selection']
    if selection['mode'] == 'supplement':
        print(
            f'Settings are valid: supplement {selection["supplement_id"]} at {selection["path"]} '
            f'({selection["source"]}).'
        )
    else:
        print(f'Settings are valid: public defaults only ({selection["source"]}).')
    if args.no_render:
        if not args.no_home:
            _system_report()
        return 0
    import tempfile

    from shaffy.capture import CaptureError, capture
    from shaffy.noctalia import overlaps
    from shaffy.sandbox import RenderError, render

    # Render a capture, as an apply does: links inside the supplement become the files they
    # name, so the sandbox sees exactly what an apply would.
    with (
        tempfile.TemporaryDirectory(prefix='shaffy-check-') as keep,
        tempfile.TemporaryDirectory(prefix='shaffy-check-capture-') as state,
    ):
        try:
            if selection['mode'] == 'supplement':
                manifest = capture(ROOT, selection, state, 'check', load)
                result = render(ROOT, supplement=Path(manifest['directory']) / 'supplement', keep=keep)
            else:
                result = render(ROOT, without=True, keep=keep)
        except (RenderError, CaptureError) as error:
            print(f'shaffy: {error}', file=sys.stderr)
            return 1
        found = [] if args.no_home else overlaps(Path(keep) / 'home/.config/noctalia', Path.home())
    if found:
        print('Noctalia keys also set by its runtime files, which load later and win:')
        for key, managed, source, runtime in found:
            state = 'same value' if managed == runtime else f'runtime has {runtime!r}'
            print(f'  {key} in {source} ({state}); promote it into a fragment or clear it deliberately')
    if result['problems']:
        print('shaffy: the rendered home has problems:', file=sys.stderr)
        for problem in result['problems']:
            print('  ' + problem.replace('\n', '\n    '), file=sys.stderr)
        return 1
    print(
        f'Rendered {result["files"]} files for a synthetic user; Niri, Noctalia, Fish, tmux '
        'and every TOML, YAML and JSON file validate, and a second apply changes nothing.'
    )
    if not args.no_home:
        _system_report()
    return 0


def data(args):
    """Print the normalized settings as JSON, from the cache when inputs are unchanged."""
    from shaffy.fastload import main as fast_main

    argv = ['--checkout', str(ROOT)]
    if args.supplement:
        argv += ['--supplement', args.supplement]
    if args.without_supplement:
        argv.append('--without-supplement')
    return fast_main(argv)


def operation_id():
    from shaffy.records import now

    return now().replace(':', '').replace('-', '').replace('+0000', 'Z') + '-' + secrets.token_hex(3)


def _fail(message):
    print(f'shaffy: {message}', file=sys.stderr)
    return 1


def attach(args):
    """Bind this installation to a supplement, or record that it uses none."""
    from shaffy.loader import SettingsError, load
    from shaffy.records import LockedError, RecordError, Records, current_target, new_record, read_root_record
    from shaffy.selection import SelectionError

    records = Records()
    try:
        with records.lock():
            existing = records.read()
            if args.none:
                binding = {'mode': 'none', 'path': None, 'supplement_id': None}
            else:
                selection = load(ROOT, supplement=args.supplement)['shaffy']['selection']
                binding = {'mode': 'supplement', 'path': selection['path'], 'supplement_id': selection['supplement_id']}
            root = read_root_record(current_target()['uid'])
            if args.adopt_system_record:
                # Recognise the root record a system apply left: it must name this user,
                # home and supplement_id; a supplement that moved since has its root binding moved too.
                if not root:
                    return _fail('there is no root record for this user to adopt')
                if root['pending']:
                    return _fail(
                        f'root holds system operation {root["pending"]["op"]}; review it, then run '
                        './bin/shaffy apply --system --abandon before adopting'
                    )
                record = existing or new_record(binding, ROOT)
                if existing and existing['system']['bound']:
                    return _fail('this installation already knows its root record')
                if existing and existing['binding'] != binding:
                    return _fail('the selected supplement is not the one this installation is bound to')
                mismatches = [
                    label
                    for label, mine, theirs in (
                        ('target.uid', record['target']['uid'], root['target']['uid']),
                        ('target.home', record['target']['home'], root['target']['home']),
                        ('binding.mode', binding['mode'], root['binding']['mode']),
                        ('binding.supplement_id', binding['supplement_id'], root['binding']['supplement_id']),
                    )
                    if mine != theirs
                ]
                if mismatches:
                    return _fail('the root record does not match this installation: ' + ', '.join(mismatches))
                op = operation_id()
                if root['binding']['path'] != binding['path']:
                    from shaffy.apply import ApplyError
                    from shaffy.system import SystemApply

                    try:
                        SystemApply(ROOT).rebind(record, binding, op)
                    except ApplyError as error:
                        return _fail(error)
                record['system']['bound'] = True
                records.append(
                    op,
                    'attach',
                    binding=binding,
                    checkout=str(ROOT),
                    adopt_system_record=True,
                    moved_from=root['binding']['path'] if root['binding']['path'] != binding['path'] else None,
                )
                records.write(record)
                print('Adopted the root record for this installation. Nothing was applied.')
                return 0
            if existing:
                bound = existing['binding']
                return _fail(
                    f'this installation is already bound ({bound["mode"]}'
                    + (f' at {bound["path"]}' if bound['path'] else '')
                    + '); use ./bin/shaffy supplement rebind'
                )
            if root:
                return _fail(
                    'a root record exists for this user; run this command again with '
                    '--adopt-system-record to recognise it'
                )
            op = operation_id()
            records.append(op, 'attach', binding=binding, checkout=str(ROOT))
            records.write(new_record(binding, ROOT))
    except (LockedError, RecordError, SelectionError) as error:
        return _fail(error)
    except SettingsError as error:
        return _fail('invalid settings:\n  ' + '\n  '.join(error.problems))
    where = f'supplement {binding["supplement_id"]} at {binding["path"]}' if binding['path'] else 'public defaults only'
    print(f'Bound this installation to {where}; checkout {ROOT}. Nothing was applied.')
    _print_affected_integrations()
    return 0


def _print_affected_integrations():
    """What the new binding means for the integrations: attach and rebind show them."""
    lines = _integration_report()
    if lines:
        print('Integrations this binding affects:')
        for line in lines:
            print(f'  {line}')


def rebind(args):
    """Move the binding to a relocated supplement or another checkout."""
    from shaffy.loader import SettingsError, load
    from shaffy.records import LockedError, RecordError, Records
    from shaffy.selection import SelectionError

    records = Records()
    try:
        with records.lock():
            record = records.read()
            if not record:
                return _fail('this installation is not bound; use ./bin/shaffy supplement attach')
            records.check_target(record)
            if record['pending']:
                return _fail(f'operation {record["pending"]["op"]} is unfinished; resolve it before rebinding')
            op = operation_id()
            if args.path:
                selection = load(ROOT, supplement=args.path)['shaffy']['selection']
                old = record['binding']
                if old['supplement_id'] != selection['supplement_id'] and not args.new_id:
                    return _fail(
                        f'{args.path} holds supplement {selection["supplement_id"]}, '
                        f'not {old["supplement_id"]}; add --new-id to change it deliberately'
                    )
                new = {'mode': 'supplement', 'path': selection['path'], 'supplement_id': selection['supplement_id']}
                if record['system']['bound']:
                    # The root record moves first, in the same operation.
                    from shaffy.apply import ApplyError
                    from shaffy.system import SystemApply

                    try:
                        SystemApply(ROOT).rebind(record, new, op)
                    except ApplyError as error:
                        return _fail(error)
                records.append(op, 'rebind', **{'from': old, 'to': new})
                record['binding'] = new
            else:
                checkout = Path(args.checkout).expanduser().resolve()
                if not (checkout / 'bin/shaffy').is_file():
                    return _fail(f'{checkout} is not a Shaffy OS checkout')
                records.append(op, 'rebind-checkout', **{'from': record['checkout']['path'], 'to': str(checkout)})
                record['checkout'] = {'path': str(checkout)}
            records.write(record)
    except (LockedError, RecordError, SelectionError) as error:
        return _fail(error)
    except SettingsError as error:
        return _fail('invalid settings:\n  ' + '\n  '.join(error.problems))
    print('Binding updated. Nothing was applied.')
    _print_affected_integrations()
    return 0


def _integration_report(supplement=None, without=False):
    """What the next apply would refuse, change or defer for the integrations, compared with the ledger.

    Empty while an operation is unfinished: its own transitions belong to its resume.
    """
    from shaffy.ledger import Ledger, plan
    from shaffy.loader import SettingsError, load
    from shaffy.records import RecordError, Records
    from shaffy.selection import SelectionError

    try:
        record = Records().read()
        if record and record['pending']:
            return []
        integrations = load(ROOT, supplement=supplement, without=without)['shaffy']['integrations']
        owned = Ledger(Records().dir).read()
    except SettingsError, SelectionError, RecordError:
        return []  # check and apply report settings problems themselves
    problems, transitions, deferred = plan(integrations, owned, Path.home(), None)
    return (
        [f'Integration awaiting a decision: {problem}' for problem in problems]
        + [f'Integration {t["name"]}: the next apply makes it {t["to"]}' for t in transitions]
        + [
            f'Integration {name}: waits, so the next apply leaves it out: {reason}'
            for name, reason in sorted(deferred.items())
        ]
    )


def integration(args):
    """Record activation already present as owned (adopt), or stop tracking it (release)."""
    from shaffy import ledger
    from shaffy.integrations import load_catalogue
    from shaffy.loader import SettingsError, load, parse
    from shaffy.records import LockedError, RecordError, Records
    from shaffy.selection import SelectionError

    records = Records()
    try:
        with records.lock():
            record = records.read()
            if not record:
                return _fail('this installation is not bound; use ./bin/shaffy supplement attach')
            records.check_target(record)
            if record['pending']:
                return _fail(f'operation {record["pending"]["op"]} is unfinished; resolve it first')
            book = ledger.Ledger(records.dir)
            target = {'uid': record['target']['uid'], 'home': record['target']['home']}
            owned = book.read(target)
            entry = owned['integrations'].get(args.name)
            op = operation_id()
            if args.action == 'release':
                if not entry:
                    return _fail(f'integration {args.name} is not in the ownership ledger')
                del owned['integrations'][args.name]
                book.write(owned)
                records.append(op, 'ledger-release', integration=args.name, state=entry['state'])
                print(
                    f'Integration {args.name} is no longer tracked; its files and activation are '
                    'unchanged. Remove it from the settings, or set it to released.'
                )
                return 0
            catalogue, problems = load_catalogue(ROOT, parse)
            if problems:
                return _fail('\n  '.join(['defaults/integrations.yml is invalid:', *problems]))
            if args.name not in catalogue:
                return _fail(f'there is no integration {args.name} in defaults/integrations.yml')
            if catalogue[args.name]['scope'] != 'home':
                return _fail(f'integration {args.name} belongs to the system; it is adopted by a system apply')
            if entry and entry['state'] == 'managed' and not entry.get('transition'):
                return _fail(f'integration {args.name} is already managed')
            activation = catalogue[args.name].get('activation', [])
            found = [r for r in activation if ledger.present(r, Path.home())]
            if not found and entry and entry.get('transition'):
                return _fail(
                    f'integration {args.name} was left changing by an abandoned operation and '
                    f'nothing of it is active here; run ./bin/shaffy integration {args.name} release, '
                    'then apply to create it'
                )
            if not found:
                return _fail(
                    f'nothing of integration {args.name} is active here, so there is nothing to '
                    f'adopt; set integrations.{args.name}: managed and apply to create it'
                )
            integration_settings = load(ROOT)['shaffy']['integrations'][args.name]
            blocked = ledger.adoptable(integration_settings, Path.home())
            if not blocked and len(found) < len(activation):
                # Adopting part of it lets the next apply create the rest, so that must be allowed too.
                blocked = ledger.adoptable(integration_settings, Path.home(), 'activate_requires')
            if blocked:
                return _fail(f'integration {args.name} cannot be adopted yet: {blocked}')
            ledger.adopt(owned, args.name, catalogue[args.name], op)
            book.write(owned)
            records.append(
                op,
                'ledger-adopt',
                integration=args.name,
                present=[r['kind'] for r in found],
                replaced=entry and entry['state'],
            )
    except (LockedError, RecordError, SelectionError) as error:
        return _fail(error)
    except SettingsError as error:
        return _fail('invalid settings:\n  ' + '\n  '.join(error.problems))
    print(
        f'Integration {args.name} is managed and adopted: its activation already here '
        f'({len(found)} of {len(activation)} record(s)) now belongs to this installation. '
        f'Keep integrations.{args.name}: managed in the settings.'
    )
    return 0


def status(args):
    """Report the binding, checkout, unfinished work, recent events and drift."""
    from shaffy.records import RecordError, Records, disagreements, read_root_record, root_events
    from shaffy.source import release

    records = Records()
    try:
        record = records.read()
        root = read_root_record(record['target']['uid']) if record else None
    except RecordError as error:
        return _fail(error)
    if not record:
        print('Binding: not bound (run ./bin/shaffy supplement attach).')
    else:
        binding = record['binding']
        print(
            'Binding: '
            + (
                f'supplement {binding["supplement_id"]} at {binding["path"]}'
                if binding['mode'] == 'supplement'
                else 'public defaults only'
            )
        )
        where = 'this checkout' if Path(record['checkout']['path']) == ROOT else 'another checkout'
        found = release(record['checkout']['path'])
        print(
            f'Checkout: {record["checkout"]["path"]} ({where}), ' + (f'release {found}' if found else 'no release tag')
        )
        target = record['target']
        print(f'Target: {target["user"]} (uid {target["uid"]}), {target["home"]}')
        pending = record['pending']
        print(
            'Unfinished operation: '
            + (
                f'{pending["op"]} ({pending["scope"]}, {pending["phase"]}, started {pending["started_at"]})'
                if pending
                else 'none'
            )
        )
        if pending and pending['scope'] == 'system':
            print(f'  Began in checkout {pending["checkout"]}.')
            starts = [
                e
                for e in root_events(target['uid'], pending['op'])
                if e.get('event') in ('phase-start', 'resume-start')
            ]
            if starts:
                print(
                    f'  The system may be partly changed. Snapper pre snapshots {starts[-1]["pre"]}; run '
                    './bin/shaffy apply --resume to finish from the captured settings.'
                )
            elif root_events(target['uid'], pending['op']):
                print('  Root took the operation but changed nothing yet. Run ./bin/shaffy apply --resume.')
            else:
                print('  Root has not taken it. Run ./bin/shaffy apply --resume to clear it.')
        elif pending:
            print(f'  Began in checkout {pending.get("checkout", record["checkout"]["path"])}.')
            starts = [
                e
                for e in records.events()
                if e['op'] == pending['op'] and e['event'] in ('phase-start', 'resume-start')
            ]
            if starts:
                print(
                    f'  Home files may be partly written. Snapper pre snapshot '
                    f'{starts[-1]["pre_snapshot"]}; copies of the changing files in '
                    f'{starts[-1]["copies"]}. Run ./bin/shaffy apply --resume to finish from the '
                    'captured settings, or ./bin/shaffy apply --abandon if it cannot be resumed.'
                )
            else:
                print('  Nothing was written yet. Run ./bin/shaffy apply --resume to clear it.')
        print(f'Last completed operation: {record["last_completed_op"] or "none"}')
        if root:
            print(
                f'System: bound {"yes" if record["system"]["bound"] else "no"}; last completed '
                f'{root["last_completed_op"] or "none"}; unfinished '
                f'{root["pending"]["op"] if root["pending"] else "none"}'
            )
            for problem in (
                disagreements(record, root)
                if record['system']['bound']
                else [
                    'a root record exists but the user record does not know it; run '
                    './bin/shaffy supplement attach --adopt-system-record'
                ]
            ):
                print(f'  Records disagree: {problem}')
            if root['pending'] and not (pending and pending['op'] == root['pending']['op']):
                print(
                    '  Root holds an operation this user record does not (was / rolled back?); '
                    'review it, then run ./bin/shaffy apply --system --abandon'
                )
        else:
            print(
                f'System: {"bound, but the root record is missing" if record["system"]["bound"] else "not applied yet"}'
            )
    if record:
        from shaffy.ledger import Ledger

        try:
            owned = Ledger(records.dir).read()['integrations']
        except RecordError as error:
            print(f'Integrations: {error}')
            owned = {}
        for name, entry in sorted(owned.items()):
            transition = entry.get('transition')
            print(
                f'Integration {name}: {entry["state"] or "not yet managed"}'
                + (' (adopted)' if entry.get('adopted') else '')
                + (f'; changing to {transition["to"]} in operation {transition["op"]}' if transition else '')
            )
            if transition and not (pending and pending['op'] == transition['op']):
                print(
                    f'  Left by an abandoned operation; review it, then run ./bin/shaffy integration '
                    f'{name} adopt or release.'
                )
        if not pending:
            for line in _integration_report():
                print(line)
    state = chezmoi_state_path()
    print(f'chezmoi state: {state}' if state.exists() else f'chezmoi state: none yet; the first apply creates {state}')
    events = records.events(limit=5)
    if records.torn:
        print(f'Journal: {len(records.torn)} unreadable line(s) in {records.journal}')
    if events:
        print('Recent events:')
        for event in events:
            print(f'  {event["at"]}  {event["event"]}  {event["op"]}')
    if args.no_drift:
        return 0
    drift = subprocess.run(chezmoi_command('status'), capture_output=True, text=True, check=False)
    if drift.returncode:
        print('Home drift: unknown; chezmoi status failed:')
        for line in drift.stderr.strip().splitlines()[-5:]:
            print(f'  {line}')
        return 1
    lines = drift.stdout.splitlines()
    print(f'Home drift: {len(lines)} target(s) differ from the source' if lines else 'Home drift: none')
    for line in lines[:10]:
        print(f'  {line}')
    return 0


def outdated(args):
    """Report what can be updated, from packages to this repository's pins; changes nothing."""
    from shaffy.updates import report

    for section in report(ROOT, Path.home(), pins=not args.no_pins):
        print(f'{section.title}: {section.summary}')
        for line in section.lines:
            print(f'  {line}')
    print(
        'The machine: ./bin/shaffy update. The repository pins: python3 scripts/refresh-pins.py on dev. '
        'See docs/UPDATING.md.'
    )
    return 0


def update(args):
    """Update the packages and the user installs in order, stopping at the first failure."""
    from shaffy import updates
    from shaffy.records import RecordError, Records

    if os.geteuid() == 0:
        return _fail('run as the desktop user; sudo asks for the password where it is needed')
    try:
        record = Records().read()
    except RecordError as error:
        return _fail(error)
    if record and record['pending']:
        return _fail(
            f'operation {record["pending"]["op"]} is unfinished; resume or abandon it first (./bin/shaffy status)'
        )
    steps = [step for step in updates.steps(ROOT, Path.home()) if not (args.skip_packages and step.packages)]
    if not args.skip_packages and not sys.stdin.isatty():
        return _fail(
            'the package upgrades ask for confirmation and the sudo password in a terminal; '
            'run it in one, or pass --skip-packages'
        )
    if record and Path(record['checkout']['path']) != ROOT:
        print(f'Updating with the pins of {ROOT}, not the bound checkout {record["checkout"]["path"]}.')
    code = updates.run(steps)
    if code == 0:
        print(
            'Done. ./bin/shaffy outdated shows what is left; a new Shaffy OS release reaches the '
            'machine through an apply (docs/UPDATING.md).'
        )
    return code


def _chezmoi_arguments():
    """chezmoi's own argument vector; CHEZMOI_ARGS joins it with spaces, which loses quoting.

    The hook runs as `sh -c 'exec bin/shaffy hook'`, so its parent is chezmoi; its
    command line is used when it matches CHEZMOI_ARGS.
    """
    joined = os.environ.get('CHEZMOI_ARGS', '')
    try:
        with open(f'/proc/{os.getppid()}/cmdline', 'rb') as stream:
            parent = [a.decode(errors='surrogateescape') for a in stream.read().split(b'\0')[:-1]]
    except OSError:
        parent = []
    return parent if ' '.join(parent) == joined else joined.split()


def hook(args):
    """Called by chezmoi's configuration hooks; refuse direct changes."""
    from shaffy.guard import refusal

    message = refusal(
        os.environ.get('CHEZMOI_COMMAND', ''), _chezmoi_arguments()[1:], mode=os.environ.get('SHAFFY_OP_MODE')
    )
    if message:
        print(message, file=sys.stderr)
        return 1
    return 0


def chezmoi_command(*arguments):
    return ['chezmoi', '--config', str(ROOT / 'chezmoi.toml'), '--source', str(ROOT / 'home'), *arguments]


def diff(args):
    """Show home changes. An explicit selection is previewed from a frozen capture."""
    from shaffy.capture import CaptureError, capture
    from shaffy.loader import SettingsError, load
    from shaffy.records import Records
    from shaffy.selection import SelectionError, resolve, state_dir

    env = dict(os.environ)
    if args.supplement or args.without_supplement:
        try:
            selection = resolve(args.supplement, args.without_supplement)
            op = operation_id()
            Records().append(op, 'preview', selection=selection)
            manifest = capture(ROOT, selection, state_dir(), op, load)
        except (SelectionError, CaptureError) as error:
            return _fail(error)
        except SettingsError as error:
            return _fail('invalid settings:\n  ' + '\n  '.join(error.problems))
        env.update(SHAFFY_OP=op, SHAFFY_OP_MODE='preview', SHAFFY_OP_CAPTURE=manifest['directory'])
        for line in _integration_report(supplement=args.supplement, without=args.without_supplement):
            print(line, file=sys.stderr)
        print(
            f'Previewing {selection["mode"] == "supplement" and selection["path"] or "the public defaults"}'
            f' from capture {op}.',
            file=sys.stderr,
        )
        try:
            return subprocess.run(chezmoi_command('diff', *args.paths), env=env, check=False).returncode
        finally:
            import shutil

            shutil.rmtree(Path(manifest['directory']).parent, ignore_errors=True)
    for line in _integration_report():
        print(line, file=sys.stderr)
    return subprocess.run(chezmoi_command('diff', *args.paths), env=env, check=False).returncode


def apply(args):
    """Apply the home or the system through its operation protocol."""
    from shaffy.records import RecordError, Records

    try:
        record = Records().read()
    except RecordError as error:
        return _fail(error)
    pending = record and record['pending']
    system_pending = bool(pending and pending.get('scope') == 'system')
    if system_pending and args.resume:
        from shaffy.system import run as run_system

        return run_system(ROOT, pending['op'], resume=True)
    # --system --abandon also clears a root `pending` the user record does not know (after a rollback).
    if args.abandon and (system_pending or (args.system and not pending)):
        from shaffy.system import run as run_system

        return run_system(ROOT, None, abandon=True)
    if args.system and not (args.resume or args.abandon):
        if args.supplement:
            return _fail('--supplement is for home applies; a system apply uses the binding')
        from shaffy.system import run as run_system

        return run_system(ROOT, operation_id(), checkout_override=args.checkout, tags=args.tags)
    if args.tags:
        return _fail('--tags selects system roles; use it with --system')
    from shaffy.apply import run

    return run(
        ROOT,
        chezmoi_command,
        operation_id(),
        supplement=args.supplement,
        resume=args.resume,
        abandon=args.abandon,
        checkout_override=args.checkout,
    )


def main(argv):
    parser = argparse.ArgumentParser(prog='shaffy', description='Shaffy OS setup commands.')
    commands = parser.add_subparsers(dest='command', required=True, metavar='command')
    test = commands.add_parser('selftest', help='run the test suite')
    test.add_argument('--skip-host', action='store_true', help='skip tests bound to this machine, as CI does')
    test.add_argument(
        '-j',
        '--jobs',
        type=int,
        metavar='N',
        help='worker processes (default: one per CPU); 1 runs plain unittest in order',
    )
    test.add_argument('-v', '--verbose', action='store_true', help='list every test')
    test.set_defaults(run=selftest)
    tidy = commands.add_parser('lint', help='format and lint the checkout, one tool per language (STYLE.md)')
    tidy.add_argument('paths', nargs='*', metavar='PATH', help='files or directories; default: the checkout')
    tidy.add_argument('--fix', action='store_true', help='let the formatters and safe fixes rewrite the files')
    tidy.add_argument('--changed', action='store_true', help='only the files that differ from HEAD')
    tidy.add_argument('--hook', action='store_true', help='read a Claude Code or Codex hook event on stdin')
    tidy.add_argument(
        '--supplement',
        nargs='?',
        const='',
        metavar='PATH',
        help='lint the bound supplement, or the one named, by the same rules',
    )
    tidy.set_defaults(run=lint)
    validate = commands.add_parser('check', help='validate the settings and a sandboxed render; changes nothing')
    _selection_arguments(validate)
    validate.add_argument(
        '--no-render', action='store_true', help='validate the settings only, without rendering the home'
    )
    validate.add_argument(
        '--no-home',
        action='store_true',
        help="leave out this machine's home and system: the settings and their render alone, as CI checks them",
    )
    validate.set_defaults(run=check)
    show = commands.add_parser('data', help='print the effective settings as JSON')
    _selection_arguments(show)
    show.set_defaults(run=data)
    preview = commands.add_parser('diff', help='show what an apply would change in the home')
    _selection_arguments(preview)
    preview.add_argument('paths', nargs='*', help='limit the diff to these targets')
    preview.set_defaults(run=diff)
    change = commands.add_parser('apply', help='apply the bound settings to the home')
    scope = change.add_mutually_exclusive_group()
    scope.add_argument('--home', action='store_true', help='apply the home (the default)')
    scope.add_argument(
        '--system', action='store_true', help='apply the system through Ansible; sudo asks for the password'
    )
    recovery = change.add_mutually_exclusive_group()
    recovery.add_argument('--resume', action='store_true', help='finish an interrupted operation')
    recovery.add_argument(
        '--abandon', action='store_true', help='give up an operation that cannot be resumed; deletes nothing'
    )
    change.add_argument(
        '--tags', metavar='TAGS', help='with --system, apply only these roles (comma-separated Ansible tags)'
    )
    change.add_argument('--checkout', metavar='PATH', help='apply once from this checkout instead of the bound one')
    change.add_argument(
        '--supplement', metavar='PATH', help='must be the bound supplement; use diff to preview another'
    )
    change.set_defaults(run=apply)
    guard = commands.add_parser('hook', help=argparse.SUPPRESS)
    guard.add_argument('name')
    guard.set_defaults(run=hook)
    report = commands.add_parser('status', help='report the binding, unfinished work and drift')
    report.add_argument('--no-drift', action='store_true', help='skip the chezmoi drift check')
    report.set_defaults(run=status)
    behind = commands.add_parser('outdated', help='report what can be updated; changes nothing')
    behind.add_argument('--no-pins', action='store_true', help="leave out this repository's own pins")
    behind.set_defaults(run=outdated)
    upgrade = commands.add_parser('update', help='update the packages and the user installs, in order')
    upgrade.add_argument('--skip-packages', action='store_true', help='leave out pacman and the AUR, which need sudo')
    upgrade.set_defaults(run=update)
    supplement = commands.add_parser('supplement', help='bind this installation to settings')
    actions = supplement.add_subparsers(dest='action', required=True, metavar='action')
    bind = actions.add_parser('attach', help='bind an unbound installation; applies nothing')
    choice = bind.add_mutually_exclusive_group(required=True)
    choice.add_argument('--supplement', metavar='PATH', help='the settings repository to use')
    choice.add_argument('--none', action='store_true', help='record that no supplement is used')
    bind.add_argument(
        '--adopt-system-record',
        action='store_true',
        help='recognise the root record an earlier system apply left; changes nothing as root',
    )
    bind.set_defaults(run=attach)
    move = actions.add_parser('rebind', help='follow a relocated supplement or checkout')
    target = move.add_mutually_exclusive_group(required=True)
    target.add_argument('--path', metavar='PATH', help="the supplement's new location")
    target.add_argument('--checkout', metavar='PATH', help='the checkout to apply from')
    move.add_argument('--new-id', action='store_true', help='accept a supplement with a different supplement_id')
    move.set_defaults(run=rebind)
    own = commands.add_parser('integration', help='record or release ownership of an integration')
    own.add_argument('name', help='an integration from defaults/integrations.yml')
    own.add_argument(
        'action',
        choices=('adopt', 'release'),
        help='adopt: record its activation already here as managed; release: stop tracking it and change nothing',
    )
    own.set_defaults(run=integration)
    args = parser.parse_args(argv)
    return args.run(args)
