"""What the system scope needs before it runs.

One documented step provides it: the pacman packages below, then, in the
checkout, `mise trust`, `mise install ansible-core` and the Galaxy collections
of requirements.yml. Nothing here installs anything; it reports what is
missing, and finds Python modules without importing them.
"""

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

PACKAGES = (
    'mise',
    'uv',
    'chezmoi',
    'python-yaml',
    'python-jsonschema',
    'snapper',
    'git',
    'base-devel',
    'bubblewrap',
    'xdg-user-dirs',
)
COMMANDS = {
    'mise': 'mise',
    'uv': 'uv',
    'chezmoi': 'chezmoi',
    'snapper': 'snapper',
    'git': 'git',
    'makepkg': 'base-devel',
    'cc': 'base-devel',
    'bwrap': 'bubblewrap',
    'xdg-user-dir': 'xdg-user-dirs',
}
MODULES = {'yaml': 'python-yaml', 'jsonschema': 'python-jsonschema'}
UNTRUSTED = re.compile(r'Config files in (\S+) are not trusted')


def pacman_step():
    return 'sudo pacman -S --needed ' + ' '.join(PACKAGES)


def galaxy_step():
    return 'mise exec ansible-core -- ansible-galaxy collection install -r requirements.yml -p collections'


def _packages():
    missing = sorted(
        {package for command, package in COMMANDS.items() if shutil.which(command) is None}
        | {package for module, package in MODULES.items() if importlib.util.find_spec(module) is None}
    )
    if not missing:
        return []
    return [f'missing packages: {", ".join(missing)}; run {pacman_step()}']


def _controller(checkout):
    """The mise-managed ansible-core, or what to run to install it."""
    try:
        result = subprocess.run(
            ['mise', 'where', 'ansible-core'], cwd=checkout, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return [f'mise could not run: {error}']
    if result.returncode == 0:
        return []
    untrusted = UNTRUSTED.search(result.stderr)
    if untrusted:
        return [f'mise does not trust {untrusted.group(1)}; review it, then run mise trust {untrusted.group(1)}']
    if 'not installed' in result.stderr:
        return [f'ansible-core is not installed; run mise install ansible-core in {checkout}']
    return ['mise where ansible-core failed: ' + result.stderr.strip()[-300:]]


def pinned_collections(checkout):
    """{name: version} from requirements.yml; read with PyYAML only when it is installed."""
    if importlib.util.find_spec('yaml') is None:
        return {}
    import yaml

    data = yaml.safe_load((Path(checkout) / 'requirements.yml').read_text())
    return {entry['name']: str(entry['version']) for entry in data['collections']}


def _collections(checkout):
    stale = []
    for name, version in pinned_collections(checkout).items():
        manifest = Path(checkout, 'collections/ansible_collections', *name.split('.'), 'MANIFEST.json')
        try:
            found = json.loads(manifest.read_text())['collection_info']['version']
        except OSError, ValueError, KeyError:
            found = None
        if found != version:
            stale.append(f'{name} {version}' + (f' (found {found})' if found else ''))
    if not stale:
        return []
    return [f'Galaxy collections not installed as pinned: {", ".join(stale)}; run {galaxy_step()} in {checkout}']


def _snapshots():
    problems = []
    for mount in ('/', '/home'):
        kind = subprocess.run(
            ['findmnt', '-no', 'FSTYPE', mount], capture_output=True, text=True, check=False
        ).stdout.strip()
        if kind != 'btrfs':
            problems.append(
                f'{mount} is not a Btrfs subvolume (found {kind or "nothing"}); Shaffy OS needs Btrfs and snapper'
            )
    if not os.path.exists('/etc/snapper/configs/root'):
        problems.append(
            'snapper has no root configuration; create it as the distribution documents before the first system apply'
        )
    return problems


def problems(checkout, snapshots=True):
    """Every missing system prerequisite, each with the command that provides it. A container
    test replaces snapper, so it skips the snapshot prerequisites."""
    found = _packages()
    if shutil.which('mise'):
        found += _controller(checkout)
    return found + _collections(checkout) + (_snapshots() if snapshots else [])


def ansible_command(checkout, *arguments):
    """The mise-managed controller's command and environment, run from the checkout. Inherited ANSIBLE_*
    settings are dropped: ANSIBLE_SKIP_TAGS=always would skip the operation's root steps, and
    ANSIBLE_COLLECTIONS_PATH would replace the pinned collections."""
    env = {key: value for key, value in os.environ.items() if not key.startswith('ANSIBLE_')}
    env['ANSIBLE_CONFIG'] = str(Path(checkout) / 'ansible.cfg')
    return ['mise', 'exec', 'ansible-core', '--', *arguments], env
