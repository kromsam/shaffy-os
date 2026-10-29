"""Runs inside the render sandbox: apply twice, verify, then validate the home.

Arguments: CHECKOUT HOME WORK. Prints one JSON line with problems, convergence,
the identity chezmoi saw and the number of rendered files.
"""

import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    first = chezmoi('apply', '--force')
    if first.returncode:
        result['problems'].append('chezmoi apply failed: ' + first.stderr.strip()[-1500:])
        return result
    second = chezmoi('apply', '--force', '--verbose')
    result['converged'] = second.returncode == 0 and not second.stdout.strip()
    if not result['converged']:
        result['problems'].append(
            'a second apply still changed files:\n' + (second.stdout + second.stderr).strip()[-1500:]
        )
    verify = chezmoi('verify')
    if verify.returncode:
        result['problems'].append('chezmoi verify failed: ' + verify.stderr.strip()[-500:])
    identity = chezmoi(
        'execute-template', '{{ .chezmoi.homeDir }}|{{ .chezmoi.username }}|{{ .chezmoi.uid }}|{{ .chezmoi.hostname }}'
    )
    result['identity'] = identity.stdout.strip()
    # An asset whose target the ignore rules skip (say, under an integration that is not
    # managed) would never be installed, and nothing else would say so.
    assets = chezmoi(
        'execute-template',
        '{{ range (includeTemplate "shaffy.json" . | fromJson).shaffy.assets }}{{ .target }}\n{{ end }}',
    )
    for target in assets.stdout.splitlines():
        if target and not (home / target).exists():
            result['problems'].append(
                f'assets.~/{target}: its target is ignored here (for example under an '
                'integration that is not managed), so it would never be installed'
            )
    result['files'] = sum(1 for path in home.rglob('*') if path.is_file() and not path.is_symlink())
    result['problems'] += validate_home(home, checkout)
    return result


if __name__ == '__main__':
    print(json.dumps(main(*sys.argv[1:4])))
