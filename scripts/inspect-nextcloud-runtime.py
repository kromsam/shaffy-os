#!/usr/bin/python3
"""Report the Nextcloud client's accounts, folder mappings and pause states; resume one folder on request.

Read-only by default: the saved accounts and mappings come from
~/.config/Nextcloud/nextcloud.cfg and the live folder states from the running
client over D-Bus, and the report goes to
~/.local/state/shaffy/runs/sync-runtime/nextcloud-runtime.json. Accounts and
mappings are the client's own, set up by signing in and choosing folders in its
window; a home apply never changes them.

`--resume PATH` resumes exactly one configured folder that is paused, through the
client's own menu action, after a snapper pre snapshot (config home) of the home
as it was. It is the deliberate step after reviewing a mapping, never part of an
apply.
"""
import argparse
import configparser
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from shaffy.snapshots import SnapshotError, home_pair  # noqa: E402

BUS = 'com.nextcloudgmbh.Nextcloud'
ROOT = '/com/nextcloudgmbh/Nextcloud'
ACCOUNT = 'org.freedesktop.CloudProviders.Account'
ACTIONS = 'org.gtk.Actions'


def saved_folders(path):
    """({account url}, {local folder: {remote, paused}}) from the client's configuration.

    The saved `paused` key is what decides a resume: the running client's pause checkbox
    can show another folder's state (see below).
    """
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.optionxform = str
    config.read(path)
    if 'Accounts' not in config:
        return [], {}
    accounts = config['Accounts']
    urls = sorted({value.rstrip('/') for key, value in accounts.items() if key.endswith('\\url')})
    folders = {}
    for key, value in accounts.items():
        if key.endswith('\\localPath'):
            prefix = key.removesuffix('localPath')
            folders[value.rstrip('/')] = {'remote': accounts.get(prefix + 'targetPath'),
                                          'paused': accounts.get(prefix + 'paused', 'false') == 'true'}
    return urls, folders


def bus(*args):
    result = subprocess.run(['busctl', '--user', '--json=short', *args],
                            check=True, capture_output=True, text=True, timeout=15)
    return json.loads(result.stdout)['data'] if result.stdout.strip() else None


def paused(path):
    action = bus('call', BUS, path, ACTIONS, 'Describe', 's', 'pause')[0]
    if not (action[0] and action[1] == '' and action[2][0]['type'] == 'b'):
        raise RuntimeError(f'Nextcloud describes the pause action of {path} unexpectedly')
    return action[2][0]['data']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--resume', metavar='PATH', help='resume this one configured, paused folder')
    args = parser.parse_args(argv)
    urls, mappings = saved_folders(Path.home() / '.config/Nextcloud/nextcloud.cfg')
    if not mappings:
        print('The Nextcloud client has no folders configured; sign in and choose folders in its window.')
        return 0
    resume = args.resume and str(Path(args.resume).expanduser()).rstrip('/')
    if resume and resume not in mappings:
        print(f'{resume} is not a configured Nextcloud folder; configured: {", ".join(sorted(mappings))}',
              file=sys.stderr)
        return 1
    objects = bus('call', BUS, ROOT, 'org.freedesktop.DBus.ObjectManager', 'GetManagedObjects')[0]
    live = {interfaces[ACCOUNT]['Path']['data'].rstrip('/'): path
            for path, interfaces in objects.items() if ACCOUNT in interfaces}
    report = {'accounts': urls, 'saved_only': sorted(set(mappings) - set(live)),
              'running_only': sorted(set(live) - set(mappings)), 'folders': []}
    if resume and resume not in live:
        print(f'{resume} is configured but the running Nextcloud client does not show it; start the '
              'client and try again', file=sys.stderr)
        return 1
    if resume and not mappings[resume]['paused']:
        print(f'{resume} is not paused in the saved configuration; nothing to resume.', file=sys.stderr)
        resume = None
    for local, path in sorted(live.items()):
        if local == resume:
            try:
                snapshot = home_pair(f'resume the Nextcloud folder {local}')
                snapshot.__enter__()
            except SnapshotError as error:
                print(f'Not resuming {local}: {error}', file=sys.stderr)
                return 1
            try:
                # Nextcloud's CloudProviderWrapper uses a shared actionGroup pointer:
                # changing one folder can change another menu's checkbox. Normalize
                # the menu state before activating its per-folder callback. SetState
                # has no folder callback; Activate then always requests paused=false.
                bus('call', BUS, path, ACTIONS, 'SetState', 'sva{sv}', 'pause', 'b', 'true', '0')
                bus('call', BUS, path, ACTIONS, 'Activate', 'sava{sv}', 'pause', '0', '0')
            finally:
                snapshot.__exit__(None, None, None)
            if paused(path):
                raise RuntimeError(f'Nextcloud did not resume {local}')
        saved = mappings.get(local, {})
        report['folders'].append({'path': local, 'remote': saved.get('remote'),
                                  'paused_in_config': saved.get('paused'), 'pause_menu_checked': paused(path),
                                  'details': bus('get-property', BUS, path, ACCOUNT, 'StatusDetails')})
    os.umask(0o077)
    output = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'shaffy/runs' / 'sync-runtime/nextcloud-runtime.json'
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.write_text(json.dumps(report, indent=2) + '\n')
    output.chmod(0o600)
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    sys.exit(main())
