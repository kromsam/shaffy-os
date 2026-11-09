#!/usr/bin/python3
"""Install Shaffy OS's boot splash and select the configured Plymouth theme; default to a check.

desktop.boot_splash names the theme. Shaffy OS's own, shaffy-os, is CachyOS's `cachyos` theme with a
Shaffy OS wordmark in place of CachyOS's logo: its theme file and wordmark come from this repository,
its other images from the installed cachyos-plymouth-theme. They are copies, not links: mkinitcpio puts
a theme's links into the initramfs without their targets, and the disk password prompt runs from there.
The selection is Theme= in /etc/plymouth/plymouthd.conf. The script replaces only a selection that
CachyOS's installer made or that it made itself, and refuses another. After a change it rebuilds the
boot images once with limine-mkinitcpio, as a kernel update does, so CachyOS's boot hooks renew Limine's
hashes and, where Secure Boot is set up, enrol its configuration and sign the loader again. It never
touches Secure Boot keys and never reboots.
"""

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

OWN = 'shaffy-os'
# The theme shaffy-os takes its spinner and password prompt from (cachyos-plymouth-theme).
BASE = 'cachyos'
THEMES = '/usr/share/plymouth/themes'
CONF = '/etc/plymouth/plymouthd.conf'
# A fresh CachyOS selects one of these: its installers choose cachyos-bootanimation, or cachyos for an
# AMD GPU, and without a Theme= Plymouth falls back to its own default.
INSTALLED = (None, 'cachyos-bootanimation', BASE)
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')


def run(*command):
    """What a command prints, its errors included; a failure raises with them."""
    done = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        env={**os.environ, 'LC_ALL': 'C'},
    )
    if done.returncode:
        raise RuntimeError(f'{command[0]} failed with status {done.returncode}:\n{done.stdout.strip()[-3000:]}')
    return done.stdout


def selection(text):
    """The theme plymouthd.conf selects: the first Theme= in a [Daemon] section, as
    plymouth-set-default-theme reads it for mkinitcpio's plymouth hook; None without one."""
    section = None
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if line.startswith('['):
            section = line
        elif section == '[Daemon]' and line.split('=', 1)[0].strip() == 'Theme' and '=' in line:
            return line.split('=', 1)[1].strip() or None
    return None


def selecting(text, theme):
    """plymouthd.conf with `theme` selected: that first Theme= replaced, else one added under the first
    [Daemon], else a [Daemon] section added at the top, as plymouth-set-default-theme does."""
    lines, section, daemon = text.splitlines(keepends=True), None, None
    for index, line in enumerate(lines):
        bare = line.split('#', 1)[0].strip()
        if bare.startswith('['):
            section = bare
            daemon = index if daemon is None and bare == '[Daemon]' else daemon
        elif section == '[Daemon]' and bare.split('=', 1)[0].strip() == 'Theme' and '=' in bare:
            lines[index] = f'Theme={theme}\n'
            return ''.join(lines)
    if daemon is None:
        return f'[Daemon]\nTheme={theme}\n' + text
    lines.insert(daemon + 1, f'Theme={theme}\n')
    return ''.join(lines)


def module(theme_file):
    """The Plymouth plugin a theme file names."""
    found = re.search(r'(?m)^\s*ModuleName\s*=\s*(\S+)', theme_file.read_text())
    if not found:
        raise RuntimeError(f'{theme_file} names no ModuleName')
    return found[1]


def write(path, data):
    """Replace a file whole, mode 0644: an interrupted write leaves the old one."""
    temporary = path.with_name(f'.{path.name}.new')
    with open(temporary, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


class Splash:
    def __init__(self, root, state, apply, run, source):
        self.root, self.state, self.apply, self.run, self.source = root, state, apply, run, source
        self.notes, self.copies = [], None
        self.record_path = state / 'boot-splash.json'
        self.record = json.loads(self.record_path.read_text()) if self.record_path.exists() else {}

    def path(self, name):
        return self.root / name.lstrip('/')

    def keep(self, **changes):
        """Record what this script selected and whether a rebuild is due, before the change it describes."""
        self.record = {**self.record, **changes}
        self.state.mkdir(parents=True, exist_ok=True)
        write(self.record_path, (json.dumps(self.record, indent=2, sort_keys=True) + '\n').encode())

    def copy(self, path):
        """Keep a copy of a file from before this run changed it."""
        if not path.is_file():
            return
        if self.copies is None:
            self.copies = self.state / 'boot-splash-copies' / datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
            self.copies.mkdir(parents=True, mode=0o700)
        target = self.copies / path.relative_to(self.root)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    def theme_files(self):
        """{name: content} of shaffy-os: the theme file and wordmark from the repository, and every other
        image of CachyOS's theme as the installed package has it."""
        base = self.path(THEMES) / BASE
        if not (base / f'{BASE}.plymouth').is_file():
            raise RuntimeError(
                f'{base} is missing: the shaffy-os theme takes its spinner and password prompt from '
                'cachyos-plymouth-theme, which the boot_splash role installs; install it, or name another '
                'theme in desktop.boot_splash'
            )
        files = {p.name: p.read_bytes() for p in sorted(base.glob('*.png')) if p.name != 'watermark.png'}
        return files | {p.name: p.read_bytes() for p in sorted(self.source.iterdir()) if p.is_file()}

    def theme_changes(self, files):
        """The files of shaffy-os to write and the ones to remove."""
        folder = self.path(THEMES) / OWN
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
            raise RuntimeError(f'{folder} is not a folder')
        present = {p.name: p for p in folder.iterdir() if not p.is_dir() or p.is_symlink()} if folder.exists() else {}
        writes = [
            name
            for name, data in files.items()
            if name not in present
            or present[name].is_symlink()
            or not present[name].is_file()
            or present[name].read_bytes() != data
        ]
        return writes, sorted(set(present) - set(files))

    def require(self, theme, theme_file):
        """Refuse, before anything is written, where a selection would not reach the boot screen."""
        for tool, package in (
            ('plymouth-set-default-theme', 'plymouth'),
            ('limine-mkinitcpio', 'limine-mkinitcpio-hook'),
        ):
            if not self.path(f'/usr/bin/{tool}').is_file():
                raise RuntimeError(
                    f"/usr/bin/{tool} is missing ({package}): the boot splash needs Plymouth and CachyOS's "
                    'Limine tooling, which its installer sets up; set desktop.boot_splash: false to leave it alone'
                )
        if 'splash' not in self.path('/proc/cmdline').read_text().split():
            raise RuntimeError(
                'the kernel command line (/proc/cmdline) has no splash, so Plymouth shows no theme: add splash to '
                'KERNEL_CMDLINE[default] in /etc/default/limine and reboot, or set desktop.boot_splash: false'
            )
        # With an enrolled configuration, Limine refuses entries whose checksum no hook re-enrolled.
        default = self.path('/etc/default/limine')
        enrolled = [
            match[1]
            for line in (default.read_text().splitlines() if default.is_file() else [])
            if (match := re.fullmatch(r'\s*ENABLE_ENROLL_LIMINE_CONFIG\s*=\s*"?(\w*)"?\s*', line))
        ]
        hooks = self.path('/etc/boot/hooks/post.d')
        enrols = hooks.is_dir() and any(
            not hook.name.endswith('.disabled')
            and os.access(hook, os.X_OK)
            and 'limine-enroll-config' in (os.readlink(hook) if hook.is_symlink() else hook.name)
            for hook in hooks.iterdir()
        )
        if enrolled[-1:] == ['yes'] and not enrols:
            raise RuntimeError(
                "Limine's configuration is enrolled (ENABLE_ENROLL_LIMINE_CONFIG=yes), but no hook in "
                '/etc/boot/hooks/post.d/ runs limine-enroll-config: Limine would refuse the rebuilt entries'
            )
        plugin = self.path(f'/usr/lib/plymouth/{module(theme_file)}.so')
        if not plugin.is_file():
            raise RuntimeError(f'{theme} needs the Plymouth plugin {plugin.name}, which is not installed')

    def converge(self, theme):
        """Install shaffy-os when it is `theme`, select `theme` and rebuild after a change; return whether
        anything changed, or with apply=False would change."""
        theme_file = (self.source if theme == OWN else self.path(THEMES) / theme) / f'{theme}.plymouth'
        if not theme_file.is_file():
            raise RuntimeError(
                f'desktop.boot_splash names {theme}, but {THEMES}/{theme}/{theme}.plymouth does not exist: '
                'install that theme first, or name one that is installed'
            )
        files = self.theme_files() if theme == OWN else {}
        writes, removes = self.theme_changes(files) if theme == OWN else ([], [])
        conf = self.path(CONF)
        text = conf.read_text() if conf.exists() else ''
        current = selection(text)
        if current not in (*INSTALLED, OWN, theme, self.record.get('selected')):
            raise RuntimeError(
                f'Plymouth shows the theme {current}, which Shaffy OS did not select, so it is left in place. To '
                f'keep it, set desktop.boot_splash: {current} in the supplement; Shaffy OS then takes over the '
                'selection, and a later value of the setting replaces it. To leave Plymouth alone, set false'
            )
        reselect = current != theme
        if not (writes or removes or reselect or self.record.get('rebuild')):
            if self.apply and self.record.get('selected') != theme:
                self.keep(selected=theme)  # a selection that is already right becomes Shaffy OS's
            return False
        self.require(theme, theme_file)
        if not self.apply:
            self.notes += [f'would install {name} in {THEMES}/{OWN}' for name in writes]
            self.notes += [f'would remove {name} from {THEMES}/{OWN}' for name in removes]
            self.notes += [f'would select {theme} in {CONF}'] if reselect else []
            self.notes.append('would rebuild the boot images with limine-mkinitcpio')
            return True
        # Recorded first: an interrupted apply finds it and rebuilds even when the files are right.
        self.keep(selected=theme, rebuild=True)
        folder = self.path(THEMES) / OWN
        for name in removes:
            self.copy(folder / name)
            (folder / name).unlink()
        if writes:
            folder.mkdir(exist_ok=True)
            os.chmod(folder, 0o755)  # whatever the umask, as the packaged themes are
            for name in writes:
                self.copy(folder / name)
                write(folder / name, files[name])  # its rename replaces a link rather than following it
        if reselect:
            self.copy(conf)
            conf.parent.mkdir(parents=True, exist_ok=True)
            write(conf, selecting(text, theme).encode())
        self.rebuild(theme)
        return True

    def rebuild(self, theme):
        """Rebuild every kernel's boot image with the selection, which must read back first, as mkinitcpio's
        plymouth hook reads it."""
        lines = self.run('plymouth-set-default-theme').strip().splitlines()
        if not lines or lines[-1].strip() != theme:
            found = lines[-1].strip() if lines else 'nothing'
            raise RuntimeError(f'plymouth-set-default-theme reads {found}, not {theme}, from {CONF}')
        output = self.run('limine-mkinitcpio')
        # It skips a kernel whose image fails to build and still succeeds, so its own errors are read.
        failed = re.findall(r'mkinitcpio failed for kernel \S+', output)
        if failed:
            raise RuntimeError(
                f'limine-mkinitcpio kept the old images: {"; ".join(failed)}. The next apply rebuilds them again'
            )
        self.keep(rebuild=False)


def theme_name(value):
    if not NAME.fullmatch(value):
        raise argparse.ArgumentTypeError(f'{value!r} is not a Plymouth theme name')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='report what would change; the default')
    mode.add_argument('--apply', action='store_true', help='install, select and rebuild')
    parser.add_argument('--theme', type=theme_name, required=True, help='the Plymouth theme to select')
    parser.add_argument('--state-dir', type=Path, default=Path('/var/lib/shaffy'))
    # Root runs a copy of this script, so the theme's own files are named rather than found beside it.
    parser.add_argument(
        '--source', type=Path, required=True, help="shaffy-os's own files: roles/boot_splash/files/shaffy-os"
    )
    args = parser.parse_args(argv)
    splash = Splash(Path('/'), args.state_dir, args.apply, run, args.source)
    changed = splash.converge(args.theme)
    for note in splash.notes:
        print(f'note: {note}')
    print(f'changed: {int(changed)}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as error:
        sys.exit(f'configure-boot-splash: {error}')
