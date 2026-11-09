"""Render the managed home for a synthetic user and validate it.

The render runs in a bubblewrap sandbox: the host root is read-only, /home, /run,
/tmp, /var/tmp and /dev/shm are replaced, /dev and /proc are the sandbox's own,
the user is `tester` (uid 4242) on `synthetic-host`, and the environment is
cleared, so templates and scripts cannot reach the real home, session or
credentials, and nothing outside the temporary render directory is written.

SHAFFY_RENDER_DIRECT=1 renders without bubblewrap, and is honoured only inside a
container whose account's home carries no Shaffy OS binding, and never for the
preflight of a real apply.
"""

import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import subprocess
import tempfile

from .selection import resolve
from .source import release

USER, UID, HOST = 'tester', 4242, 'synthetic-host'
INSIDE_HOME = '/home/tester'
PATH = '/usr/bin:/bin'


class RenderError(Exception):
    """The sandbox could not be set up or the render did not produce a result."""


def _prepare(root, supplement_path, user_dirs=None):
    home, work = root / 'home', root / 'work'
    home.mkdir(parents=True)
    work.mkdir()
    if user_dirs is not None:
        (home / '.config').mkdir()
        (home / '.config/user-dirs.dirs').write_text(user_dirs)
    (work / 'passwd').write_text(f'{USER}:x:{UID}:{UID}:Shaffy OS test user:{INSIDE_HOME}:/bin/sh\n')
    (work / 'group').write_text(f'{USER}:x:{UID}:\n')
    (work / 'hostname').write_text(HOST + '\n')
    # The template guard lets the render apply only while this file exists.
    (work / 'sandbox-token').write_text(secrets.token_hex(16) + '\n')
    binding = home / '.local/state/shaffy/binding.json'
    binding.parent.mkdir(parents=True)
    mode = 'supplement' if supplement_path else 'none'
    binding.write_text(
        json.dumps({'record_version': 1, 'binding': {'mode': mode, 'path': supplement_path, 'supplement_id': None}})
        + '\n'
    )
    return home, work


def _bwrap(root, checkout, supplement, argv, found=None):
    command = [
        'bwrap',
        '--ro-bind',
        '/',
        '/',
        '--dev',
        '/dev',
        '--proc',
        '/proc',
        '--unshare-all',
        '--new-session',
        '--die-with-parent',
        '--tmpfs',
        '/home',
        '--bind',
        str(root / 'home'),
        INSIDE_HOME,
        '--tmpfs',
        '/run',
        '--tmpfs',
        '/tmp',
        '--tmpfs',
        '/var/tmp',
        '--tmpfs',
        '/dev/shm',
        '--bind',
        str(root / 'work'),
        '/tmp/work',
        '--ro-bind',
        str(checkout),
        '/tmp/checkout',
        '--ro-bind',
        str(root / 'work/passwd'),
        '/etc/passwd',
        '--ro-bind',
        str(root / 'work/group'),
        '/etc/group',
        '--ro-bind',
        str(root / 'work/hostname'),
        '/etc/hostname',
        '--hostname',
        HOST,
        '--uid',
        str(UID),
        '--gid',
        str(UID),
        '--clearenv',
        '--setenv',
        'HOME',
        INSIDE_HOME,
        '--setenv',
        'USER',
        USER,
        '--setenv',
        'LANG',
        'C.UTF-8',
        '--setenv',
        'PATH',
        PATH,
        '--setenv',
        'XDG_CACHE_HOME',
        '/tmp/work/cache',
        '--setenv',
        'SHAFFY_OP_MODE',
        'sandbox',
        '--setenv',
        'SHAFFY_SANDBOX_TOKEN',
        '/tmp/work/sandbox-token',
        '--chdir',
        INSIDE_HOME,
    ]
    if supplement:
        command += ['--ro-bind', supplement, '/tmp/supplement']
    if found:
        command += ['--setenv', 'SHAFFY_RELEASE', found]
    return [*command, '--', *argv]


def _container():
    return Path('/.dockerenv').exists() or Path('/run/.containerenv').exists()


def _direct(allow_direct):
    """Whether to render without bubblewrap; refuse where that could touch a real home."""
    if os.environ.get('SHAFFY_RENDER_DIRECT') != '1':
        return False
    if not allow_direct:
        raise RenderError('SHAFFY_RENDER_DIRECT=1 is refused for the preflight of a real apply')
    if not _container():
        raise RenderError('SHAFFY_RENDER_DIRECT=1 is honoured only inside a disposable container')
    account_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    if (account_home / '.local/state/shaffy/binding.json').exists():
        raise RenderError(
            f'SHAFFY_RENDER_DIRECT=1 is refused: {account_home} holds a Shaffy OS '
            'binding, so this container shares a real home'
        )
    return True


def render(
    checkout, supplement=None, without=False, home=None, keep=None, timeout=300, allow_direct=True, user_dirs=None
):
    """Render the selected settings into a fresh synthetic home; return the result dict.

    The result has `problems` (a list of strings), `converged`, `identity` and
    `files`. `keep` names an empty directory to keep the rendered home for review.
    `user_dirs` can seed synthetic installer-owned XDG entries for validation.
    """
    checkout = Path(checkout).resolve()
    selection = resolve(supplement, without, home)
    direct = _direct(allow_direct)
    if not direct and shutil.which('bwrap') is None:
        raise RenderError('bubblewrap (bwrap) is needed to render in a sandbox')
    with tempfile.TemporaryDirectory(prefix='shaffy-render-') as temporary:
        root = Path(keep) if keep else Path(temporary)
        if keep and any(root.iterdir()):
            raise RenderError(f'{root} must be empty')
        supplement_path = selection['path']
        inside = supplement_path if direct or not supplement_path else '/tmp/supplement'
        rendered, work = _prepare(root, inside, user_dirs)
        found = release(checkout)  # the Git directory may be out of the sandbox's sight
        if direct:
            base = [str(checkout), str(rendered), str(work)]
            env = {
                'HOME': str(rendered),
                'USER': USER,
                'LANG': 'C.UTF-8',
                'PATH': PATH,
                'XDG_CACHE_HOME': str(work / 'cache'),
                'SHAFFY_OP_MODE': 'sandbox',
                'SHAFFY_SANDBOX_TOKEN': str(work / 'sandbox-token'),
            }
            if found:
                env['SHAFFY_RELEASE'] = found
            command = ['/usr/bin/python3', str(checkout / 'lib/shaffy/render_inside.py'), *base]
        else:
            env = None
            command = _bwrap(
                root,
                checkout,
                supplement_path,
                [
                    '/usr/bin/python3',
                    '/tmp/checkout/lib/shaffy/render_inside.py',
                    '/tmp/checkout',
                    INSIDE_HOME,
                    '/tmp/work',
                ],
                found,
            )
        try:
            result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            raise RenderError(f'the render did not finish within {timeout} seconds') from error
        except OSError as error:
            raise RenderError(f'the render could not start: {error}') from error
        try:
            data = json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError) as error:
            raise RenderError(
                'the render produced no result:\n' + (result.stderr or result.stdout).strip()[-2000:]
            ) from error
        data['selection'] = selection
        return data
