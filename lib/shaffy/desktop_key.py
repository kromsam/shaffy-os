"""Build and install the desktop key helper.

`shaffy-desktop-key`, the small Wayland virtual-keyboard sender that
~/.local/bin/shaffy-desktop calls, is compiled from files/desktop-key into
$XDG_CACHE_HOME/shaffy/build/desktop-key, never under the checkout. The home
phase installs it into ~/.local/bin when the build differs from what is there.
"""

import filecmp
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

TARGET = '.local/bin/shaffy-desktop-key'
TOOLS = {'cc': 'gcc', 'wayland-scanner': 'wayland', 'pkg-config': 'pkgconf'}
LIBRARIES = ('wayland-client', 'xkbcommon')


class BuildError(Exception):
    """The helper could not be compiled."""


def build_dir():
    cache = os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache'
    return Path(cache) / 'shaffy/build/desktop-key'


def problems():
    """What is missing to build the helper, without building it."""
    missing = [f'{tool} (package {package})' for tool, package in TOOLS.items() if not shutil.which(tool)]
    if missing:
        return ['the desktop key needs ' + ', '.join(missing)]
    if subprocess.run(['pkg-config', '--exists', *LIBRARIES], check=False).returncode:
        return [
            'the desktop key needs the wayland-client and xkbcommon development files '
            '(packages wayland and libxkbcommon)'
        ]
    return []


def build(checkout):
    """Compile into the build directory and return the binary's path."""
    source, directory = Path(checkout) / 'files/desktop-key', build_dir()
    directory.mkdir(parents=True, exist_ok=True)
    protocol = source / 'virtual-keyboard-unstable-v1.xml'
    binary = directory / 'shaffy-desktop-key'
    try:
        flags = shlex.split(
            subprocess.run(
                ['pkg-config', '--cflags', '--libs', *LIBRARIES], capture_output=True, text=True, check=True
            ).stdout
        )
        for kind, output in (
            ('client-header', 'virtual-keyboard-client.h'),
            ('private-code', 'virtual-keyboard-protocol.c'),
        ):
            subprocess.run(
                ['wayland-scanner', kind, str(protocol), str(directory / output)],
                capture_output=True,
                text=True,
                check=True,
            )
        subprocess.run(
            [
                'cc',
                '-std=c11',
                '-O2',
                '-Wall',
                '-Wextra',
                '-Werror',
                '-I',
                str(directory),
                str(source / 'desktop-key.c'),
                str(directory / 'virtual-keyboard-protocol.c'),
                '-o',
                str(binary) + '.new',
                *flags,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, 'stderr', None) or str(error)
        raise BuildError('the desktop key did not build: ' + detail.strip()[-1500:]) from error
    os.replace(str(binary) + '.new', binary)
    return binary


def differs(binary, home):
    target = Path(home) / TARGET
    return not target.is_file() or target.is_symlink() or not filecmp.cmp(binary, target, shallow=False)


def install(binary, home):
    """Replace ~/.local/bin/shaffy-desktop-key atomically with the built binary."""
    target = Path(home) / TARGET
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix='.shaffy-desktop-key-')
    try:
        with os.fdopen(handle, 'wb') as stream, open(binary, 'rb') as built:
            shutil.copyfileobj(built, stream)
        os.chmod(temporary, 0o755)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
