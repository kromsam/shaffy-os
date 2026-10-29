"""Follow platform XDG directories unless the supplement explicitly overrides them.

Only configured directory entries are modified. Compatibility links and assets
must never replace real folders or traverse links.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

XDG_ROLES = {
    'desktop': 'DESKTOP',
    'documents': 'DOCUMENTS',
    'downloads': 'DOWNLOAD',
    'music': 'MUSIC',
    'pictures': 'PICTURES',
    'public': 'PUBLICSHARE',
    'templates': 'TEMPLATES',
    'videos': 'VIDEOS',
}


def folder_path(settings, home, role):
    """An explicit home-relative override, otherwise the native XDG lookup.

    xdg-user-dir parses the platform file without evaluating it as shell code.
    Its fallback for an unset directory is kept; no localized names are guessed.
    """
    home = Path(home)
    override = settings.get('folders', {}).get(role)
    if override is not None:
        return home / override
    if role == 'projects':
        raise ValueError(
            'folders.projects: set a target for a projects compatibility link; projects is not a standard XDG directory'
        )
    env = dict(os.environ, HOME=str(home))
    if home != Path.home():
        env['XDG_CONFIG_HOME'] = str(home / '.config')
    result = subprocess.run(['xdg-user-dir', XDG_ROLES[role]], env=env, capture_output=True, text=True, check=True)
    path = Path(result.stdout.removesuffix('\n'))
    if not path.is_absolute():
        raise ValueError(f'xdg-user-dir {XDG_ROLES[role]} returned a non-absolute path')
    return path


def merge_user_dirs(original, overrides, home=None):
    """Preserve platform entries and comments; own only the explicit standard roles."""
    values = {f'XDG_{XDG_ROLES[role]}_DIR': f'$HOME/{name}' for role, name in overrides.items() if role in XDG_ROLES}
    home = Path(home) if home is not None else Path.home()
    lines, found = [], set()
    for line in original.splitlines(keepends=True):
        match = re.match(r'\s*(XDG_[A-Z]+_DIR)\s*=', line)
        key = match[1] if match else None
        if key in values:
            found.add(key)
            # Keep an already-correct entry byte-for-byte, including its comment.
            alternatives = (values[key], str(home / values[key].removeprefix('$HOME/')))
            same = re.fullmatch(
                r'\s*'
                + key
                + r'\s*=\s*"(?:'
                + '|'.join(re.escape(value) for value in alternatives)
                + r')"[^\S\n]*(?:#[^\n]*)?\n?',
                line,
            )
            if not same:
                line = f'{key}="{values[key]}"\n'
        lines.append(line)
    text = ''.join(lines)
    for key in sorted(values.keys() - found):
        if text and not text.endswith('\n'):
            text += '\n'
        text += f'{key}="{values[key]}"\n'
    return text


def link_conflicts(settings, home):
    """Problems for configured folder_links: a real folder or file in the way, or a missing target."""
    problems = []
    for name, folder in sorted(settings.get('folder_links', {}).items()):
        path = Path(home) / name
        try:
            target = folder_path(settings, home, folder)
        except (ValueError, OSError, subprocess.CalledProcessError) as error:
            problems.append(f'folder_links.{name}: {error}')
            continue
        if path == target:
            problems.append(f'folder_links.{name}: would point to itself; drop the unnecessary link')
            continue
        if target == Path(home):
            # xdg-user-dir answers with the home for an unset or disabled directory.
            problems.append(
                f'folder_links.{name}: the {folder} directory is the home itself; set it in '
                "the installation's XDG settings or in folders, or drop the link"
            )
            continue
        if not path.is_symlink() and path.exists():
            problems.append(
                f'folder_links.{name}: {path} is a real {"folder" if path.is_dir() else "file"}; '
                f'move its contents into {target} and remove it, or drop '
                'the link from the settings'
            )
        elif not target.is_dir():
            problems.append(
                f'folder_links.{name}: its target {target} is not a folder; create or '
                'migrate it first, or drop the link from the settings'
            )
    return problems


def main():
    if sys.argv[1] == 'resolve':
        home, overrides = sys.argv[2], json.loads(sys.argv[3])
        roles = [*XDG_ROLES, *(['projects'] if 'projects' in overrides else [])]
        print(json.dumps({role: str(folder_path({'folders': overrides}, home, role)) for role in roles}))
    elif sys.argv[1] == 'merge':
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from shaffy.fastload import load_data

        settings = load_data(Path(__file__).resolve().parents[2])['shaffy']['settings']
        sys.stdout.write(merge_user_dirs(sys.stdin.read(), settings.get('folders', {})))
    else:
        raise SystemExit('usage: folders.py resolve HOME OVERRIDES_JSON | merge')


def asset_conflicts(assets, home):
    """Problems for supplement assets: a real folder at the target, or a link or file on its way."""
    problems = []
    for asset in assets:
        label, current = f'assets.~/{asset["target"]}', Path(home)
        for part in Path(asset['target']).parts[:-1]:
            current = current / part
            if current.is_symlink():
                problems.append(
                    f'{label}: {current} is a link, which the copy would replace with a folder; '
                    'name the file by its real path instead'
                )
                break
            if current.exists() and not current.is_dir():
                problems.append(f'{label}: {current} is a file, not a folder')
                break
        else:
            target = Path(home) / asset['target']
            if target.is_dir() and not target.is_symlink():
                problems.append(
                    f'{label}: {target} is a real folder, which the copy would replace; name a file inside it'
                )
    return problems


if __name__ == '__main__':
    main()
