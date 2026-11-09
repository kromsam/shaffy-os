"""What can be updated on this machine, and the routine that updates it (docs/UPDATING.md).

`report` only reads: pacman's and the AUR's pending upgrades, mise's outdated tools,
the user installs against this checkout's pins, the LazyVim plugins against the managed
lock, the applications that update themselves and the Shaffy OS release, and, with the
pins, the retirements past their removal date. `steps` is the update routine, in order;
`./bin/shaffy update` runs it and stops at the first failure.
Neither changes configuration: a release still reaches the machine through an apply.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shutil
import subprocess

from .source import release, release_key

# Applications whose own updaters keep them current: (name, the file that shows it is installed).
SELF_UPDATING = (
    ('SilverBullet Desktop', '.local/lib/silverbullet-desktop/SilverBullet.AppImage'),
    ('Beeper Desktop', '.local/lib/beeper/Beeper.AppImage'),
    ('Spotify', '.local/share/spotify-launcher'),
    # Zotero's add-on updater, after scripts/install-better-bibtex.py installed it.
    ('Better BibTeX', '.zotero/zotero/shaffy.default/extensions/better-bibtex@iris-advies.com.xpi'),
)


@dataclass
class Section:
    title: str
    summary: str
    lines: list = field(default_factory=list)


def _run(command, cwd=None, timeout=300):
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)


def pacman():
    if not shutil.which('checkupdates'):
        return Section('System packages', 'not checked: checkupdates (pacman-contrib) is missing')
    # checkupdates syncs a copy of the databases, so this never risks a partial upgrade.
    result = _run(['checkupdates'])
    if result.returncode not in (0, 2):  # 2: nothing to upgrade
        return Section('System packages', f'not checked: {result.stderr.strip() or "checkupdates failed"}')
    upgrades = result.stdout.splitlines()
    return Section('System packages', f'{len(upgrades)} upgrade(s)' if upgrades else 'up to date')


def aur():
    if not shutil.which('yay'):
        return Section('AUR packages', 'not checked: yay is missing')
    result = _run(['yay', '-Qua'])
    upgrades = [line for line in result.stdout.splitlines() if '->' in line]
    # Exit status 1 also means "nothing to upgrade"; a failed AUR query says error.
    if not upgrades and (result.returncode not in (0, 1) or 'error' in result.stderr.lower()):
        return Section('AUR packages', f'not checked: {result.stderr.strip() or "yay failed"}')
    return Section('AUR packages', f'{len(upgrades)} upgrade(s)' if upgrades else 'up to date', upgrades)


def firmware():
    """Firmware updates fwupd knows of, from the metadata its daily timer refreshes."""
    if not shutil.which('fwupdmgr'):
        return Section('Firmware', 'not checked: fwupd is not installed')
    result = _run(['fwupdmgr', 'get-updates', '--json', '--no-unreported-check', '--no-metadata-check'])
    if result.returncode == 2:  # nothing to update
        return Section('Firmware', 'up to date')
    if result.returncode:
        return Section('Firmware', f'not checked: {result.stderr.strip() or "fwupdmgr failed"}')
    lines = [
        f'{device["Name"]} {device.get("Version", "?")} → {device["Releases"][0]["Version"]}'
        for device in json.loads(result.stdout or '{}').get('Devices', [])
        if device.get('Releases')
    ]
    summary = f'{len(lines)} update(s); install with fwupdmgr update' if lines else 'up to date'
    return Section('Firmware', summary, lines)


def mise(root):
    # From the checkout, so its Ansible controller counts alongside the home's tools.
    result = _run(['mise', 'outdated', '--json'], cwd=root)
    if result.returncode != 0:
        return Section('mise tools', f'not checked: {result.stderr.strip() or "mise outdated failed"}')
    tools = json.loads(result.stdout or '{}')
    lines = [f'{name} {tool["current"]} → {tool["latest"]}' for name, tool in sorted(tools.items())]
    return Section('mise tools', f'{len(lines)} behind' if lines else 'up to date', lines)


def installs(root, home):
    """The user installs this checkout pins, against what the home holds."""
    lines = []
    yaru = re.search(r"VERSION = '([^']+)'", (root / 'scripts/install-yaru-icons.py').read_text()).group(1)
    marker = home / '.local/share/icons/Yaru/.shaffy-version'
    if marker.exists() and marker.read_text().strip() != yaru:
        lines.append(f'Yaru icons {marker.read_text().strip()} → {yaru}')
    revision = re.search(r'revision=([0-9a-f]{40})', (root / 'scripts/build-audio-bridge.sh').read_text()).group(1)
    receipt = home / '.local/share/yabridge/build-receipt.json'
    if receipt.exists() and json.loads(receipt.read_text()).get('revision') != revision:
        lines.append(
            f'yabridge {json.loads(receipt.read_text())["revision"][:12]} → {revision[:12]} '
            '(rebuild by hand: docs/MAINTENANCE.md)'
        )
    return Section('User installs', f'{len(lines)} differ from the pins' if lines else 'match the pins', lines)


def lazyvim(home):
    """Installed plugins whose commit differs from the managed lock."""
    lock = home / '.config/nvim/lazy-lock.json'
    plugins = home / '.local/share/nvim/lazy'
    if not lock.exists() or not plugins.is_dir():
        return Section('LazyVim plugins', 'not installed yet')
    differ = []
    for name, entry in sorted(json.loads(lock.read_text()).items()):
        if not (plugins / name / '.git').exists():
            differ.append(f'{name} missing')
            continue
        head = _run(['git', '-C', str(plugins / name), 'rev-parse', 'HEAD']).stdout.strip()
        if head != entry['commit']:
            differ.append(f'{name} {head[:7]} → {entry["commit"][:7]}')
    return Section('LazyVim plugins', f'{len(differ)} differ from the lock' if differ else 'match the lock', differ)


def self_updating(home):
    present = [name for name, path in SELF_UPDATING if (home / path).exists()]
    return Section('Updates itself', ', '.join([*present, 'pstack']))


def shaffy_release(root):
    """This checkout's release against the newest one its remote has, and unreleased work."""
    current = release(root)
    listing = _run(['git', '-C', str(root), 'ls-remote', '--tags', '--refs', 'origin', 'v*'], timeout=60)
    tags = [line.rsplit('refs/tags/', 1)[-1] for line in listing.stdout.splitlines()]
    newest = max((tag for tag in tags if release_key(tag)), key=release_key, default=None)
    if listing.returncode != 0 or not newest:
        summary = f'release {current or "none"}; the remote could not be read'
    elif current == newest:
        summary = f'release {current}, the newest'
    else:
        summary = f'release {current or "none"}; {newest} is available'
    lines = []
    branch = _run(['git', '-C', str(root), 'branch', '--show-current']).stdout.strip()
    if branch and branch != 'main':
        ahead = _run(['git', '-C', str(root), 'rev-list', '--count', f'main..{branch}']).stdout.strip()
        if ahead not in ('', '0'):
            lines.append(f'{branch} has {ahead} commit(s) not yet released')
    return Section('Shaffy OS', summary, lines)


def pin_section(root, upstream=None):
    from . import pins

    found = pins.check(root, upstream)
    behind = [p for p in found if p.behind]
    lines = [
        f'{p.name} {p.current} → {p.latest}' + (f' ({p.note})' if p.note and not p.raise_to else '') for p in behind
    ]
    lines += [f'{p.name}: not checked: {p.error}' for p in found if p.error]
    summary = (
        f'{len(behind)} behind; the maintainers raise them for a release with scripts/refresh-pins.py (MAINTAINING.md)'
        if behind
        else 'up to date'
    )
    return Section('Repository pins', summary, lines)


def removal_section(root, today=None):
    """Retirements and one-off migrations past their removal date (lib/shaffy/sunset.py)."""
    from . import sunset

    due = sunset.due(root, today)
    lines = [f'{path}:{number}: {line}' for path, number, _, line in due]
    summary = (
        f'{len(due)} past their date; the maintainers delete them for a release (MAINTAINING.md)' if due else 'none due'
    )
    return Section('Removals', summary, lines)


def report(root, home, pins=True):
    """Every section, read in parallel; the network reads dominate."""
    checks = [
        pacman,
        aur,
        firmware,
        lambda: mise(root),
        lambda: installs(root, home),
        lambda: lazyvim(home),
        lambda: self_updating(home),
        lambda: shaffy_release(root),
    ]
    if pins:
        checks += [lambda: pin_section(root), lambda: removal_section(root)]
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures = [pool.submit(check) for check in checks]
    sections = []
    for future in futures:
        try:
            sections.append(future.result())
        except Exception as error:  # report the failure and keep the other sections
            sections.append(Section('Check', f'failed: {type(error).__name__}: {error}'))
    return sections


@dataclass
class Step:
    title: str
    command: list
    cwd: Path = None
    # Skipped when this returns a reason.
    skip: object = None
    # pacman and the AUR, which ask for sudo.
    packages: bool = False


def steps(root, home):
    """The update routine, in order: packages first, then what builds on them."""
    yaru = re.search(r"VERSION = '([^']+)'", (root / 'scripts/install-yaru-icons.py').read_text()).group(1)
    archive = home / f'.cache/shaffy/downloads/yaru-{yaru}.tar.gz'
    marker = home / '.local/share/icons/Yaru/.shaffy-version'
    return [
        Step(
            'System packages (snap-pac, where installed, snapshots the system around the upgrade)',
            ['sudo', 'pacman', '-Syu'],
            packages=True,
        ),
        Step(
            'AUR packages (read the recipe changes yay shows)',
            ['yay', '-Sua'],
            packages=True,
            skip=lambda: None if shutil.which('yay') else 'yay is not installed',
        ),
        Step('Ansible controller', ['mise', 'upgrade', 'ansible-core'], cwd=root),
        Step(
            'Ansible collections',
            [
                'mise',
                'exec',
                'ansible-core',
                '--',
                'ansible-galaxy',
                'collection',
                'install',
                '-r',
                'requirements.yml',
                '-p',
                'collections',
            ],
            cwd=root,
        ),
        Step(
            'mise tools (Zotero MCP among them), the agent plugins and Better BibTeX',
            ['bash', str(root / 'scripts/install-workspace-tools.sh')],
        ),
        Step(
            'Yaru icons',
            [
                'sh',
                '-c',
                'mkdir -p "$(dirname "$1")" && { [ -f "$1" ] || curl --fail --location '
                '--output "$1" "$2"; } && python3 "$3" "$1"',
                'yaru',
                str(archive),
                f'https://github.com/ubuntu/yaru/archive/refs/tags/{yaru}.tar.gz',
                str(root / 'scripts/install-yaru-icons.py'),
            ],
            skip=lambda: (
                None
                if marker.exists() and marker.read_text().strip() != yaru
                else 'not installed, or already the pinned release'
            ),
        ),
        Step(
            'LazyVim plugins, back to the managed lock',
            ['nvim', '--headless', '+Lazy! restore', '+qa'],
            skip=lambda: None if lazyvim(home).lines else 'they match the lock',
        ),
    ]


def run(steps_to_run, out=print, call=subprocess.call):
    """Run the steps in order; returns the failing step's exit code, or 0."""
    for number, step in enumerate(steps_to_run, 1):
        reason = step.skip() if step.skip else None
        if reason:
            out(f'[{number}/{len(steps_to_run)}] {step.title}: skipped, {reason}')
            continue
        out(f'[{number}/{len(steps_to_run)}] {step.title}: {" ".join(step.command)}')
        code = call(step.command, cwd=step.cwd)
        if code:
            out(f'Stopped: "{step.title}" exited with {code}. Fix it, then run ./bin/shaffy update again.')
            return code
    return 0
