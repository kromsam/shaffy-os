"""Runs inside the render sandbox: apply both passes twice, verify, then validate the home.

Arguments: CHECKOUT HOME WORK. Prints one JSON line with problems, convergence,
the identity chezmoi saw and the number of rendered files.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shaffy.dotfiles import sandbox_pass
from shaffy.validate_home import validate_home


def main(checkout, home, work):
    checkout, home, work = Path(checkout), Path(home), Path(work)
    (work / 'chezmoi.toml').write_text(
        f'persistentState = "{work / "state.boltdb"}"\n[warnings]\nconfigFileTemplateHasChanged = false\n'
    )

    def chezmoi(*arguments):
        return subprocess.run(
            [
                'chezmoi',
                '--config',
                str(work / 'chezmoi.toml'),
                '--source',
                str(checkout / 'home'),
                '--destination',
                str(home),
                '--no-tty',
                '--no-pager',
                *arguments,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    result = {'problems': [], 'converged': False, 'identity': None, 'files': 0}
    loaded = chezmoi('execute-template', '{{ includeTemplate "shaffy.json" . }}')
    if loaded.returncode:
        result['problems'].append('the settings do not load: ' + loaded.stderr.strip()[-1500:])
        return result
    shaffy = json.loads(loaded.stdout)['shaffy']
    dotfiles, refused = sandbox_pass(shaffy, work)
    result['problems'] += refused
    # Shaffy OS's pass first, then the dotfiles', in one home: a second round in which either
    # still changes something is a fight between them, or a source that never settles.
    passes = {'': chezmoi} | ({'dotfiles: ': dotfiles.run} if dotfiles else {})
    for label, run in passes.items():
        first = run('apply', '--force')
        if first.returncode:
            result['problems'].append(f'{label}chezmoi apply failed: ' + first.stderr.strip()[-1500:])
            return result
    seconds = {label: run('apply', '--force', '--verbose') for label, run in passes.items()}
    result['converged'] = all(second.returncode == 0 and not second.stdout.strip() for second in seconds.values())
    for label, second in seconds.items():
        if second.returncode or second.stdout.strip():
            result['problems'].append(
                f'{label}a second apply still changed files:\n' + (second.stdout + second.stderr).strip()[-1500:]
            )
    for label, run in passes.items():
        verify = run('verify')
        if verify.returncode:
            result['problems'].append(f'{label}chezmoi verify failed: ' + verify.stderr.strip()[-500:])
    identity = chezmoi(
        'execute-template', '{{ .chezmoi.homeDir }}|{{ .chezmoi.username }}|{{ .chezmoi.uid }}|{{ .chezmoi.hostname }}'
    )
    result['identity'] = identity.stdout.strip()
    # An asset whose target the ignore rules skip (say, under an integration that is not
    # managed) would never be installed, and nothing else would say so.
    for target in (asset['target'] for asset in shaffy['assets']):
        if not (home / target).exists():
            result['problems'].append(
                f'assets.~/{target}: its target is ignored here (for example under an '
                'integration that is not managed), so it would never be installed'
            )
    source = shaffy['selection']['path']
    for link in (path for path in home.rglob('*') if source and path.is_symlink()):
        if Path(os.path.normpath(link.parent / os.readlink(link))).is_relative_to(source):
            result['problems'].append(
                f'dotfiles: ~/{link.relative_to(home)} links into the captured source, which a later apply '
                'removes; copy the file instead'
            )
    result['files'] = sum(1 for path in home.rglob('*') if path.is_file() and not path.is_symlink())
    foreign = set()
    if dotfiles:
        foreign = set(
            dotfiles.run('managed', '--include', 'files,symlinks', '--path-style', 'relative').stdout.split('\n')
        )
        for target in sorted(name for name in foreign if name):
            path = home / target
            if path.is_file() and not path.is_symlink() and source.encode() in path.read_bytes():
                result['problems'].append(
                    f'dotfiles: ~/{target} holds the path of the captured source (.chezmoi.sourceDir), which '
                    'changes with every apply and points into a run that is pruned; leave it out'
                )
    result['problems'] += validate_home(home, checkout, frozenset(foreign))
    return result


if __name__ == '__main__':
    print(json.dumps(main(*sys.argv[1:4])))
