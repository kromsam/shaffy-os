"""AUR packages come from the AUR's current recipe, built as the target user in the cache;
the repository keeps no recipes."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def aur_packages():
    """The default AUR names, declared alongside the repository packages."""
    return sorted(
        entry['name']
        for path in (ROOT / 'defaults/packages').glob('*.yml')
        for entry in yaml.safe_load(path.read_text()).get('aur', [])
    )


class RoleTests(unittest.TestCase):
    def test_roles_install_only_the_resolved_aur_selection(self):
        for role in ('desktop_apps',):
            tasks = yaml.safe_load((ROOT / f'roles/{role}/tasks/main.yml').read_text())
            installs = [
                task for task in tasks if task.get('ansible.builtin.include_role', {}).get('name') == 'aur_package'
            ]
            self.assertEqual(len(installs), 1)
            self.assertEqual(installs[0]['loop'], '{{ shaffy.packages.aur.' + role + ' }}')
            self.assertEqual(installs[0]['loop_control']['loop_var'], 'aur_package')
            self.assertNotIn('aur_package', installs[0].get('vars', {}))

    def test_the_roles_take_these_packages_from_the_aur_and_the_repository_keeps_no_recipes(self):
        self.assertEqual(aur_packages(), ['ttf-ms-fonts'])
        self.assertFalse((ROOT / 'packages').exists())
        files = subprocess.run(
            ['git', '-C', str(ROOT), 'ls-files', '-z', '-co', '--exclude-standard'],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split('\0')
        self.assertEqual(
            sorted({path for path in files if Path(path).name == 'PKGBUILD'}),
            ['tests/fixtures/packages/shaffy-probe/PKGBUILD'],
        )

    def test_yay_is_declared_for_their_updates(self):
        group = yaml.safe_load((ROOT / 'defaults/packages/desktop_apps.yml').read_text())
        self.assertIn('yay', group['packages'])


@unittest.skipIf(
    shutil.which('makepkg') is None or shutil.which('git') is None or os.getuid() == 0,
    'needs makepkg, git and an unprivileged user',
)
class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        tmp = Path(self.temporary.name)
        # A local stand-in for aur.archlinux.org, serving shaffy-probe.git.
        self.aur = tmp / 'aur'
        repository = self.aur / 'shaffy-probe.git'
        shutil.copytree(ROOT / 'tests/fixtures/packages/shaffy-probe', repository)
        git = [
            'git',
            '-C',
            str(repository),
            '-c',
            'user.name=t',
            '-c',
            'user.email=t@example.invalid',
            '-c',
            'commit.gpgsign=false',
        ]
        subprocess.run(git[:3] + ['init', '-q'], check=True)
        subprocess.run(git + ['add', 'PKGBUILD'], check=True)
        subprocess.run(git + ['commit', '-q', '-m', 'probe'], check=True)
        self.commit = subprocess.run(
            git[:3] + ['rev-parse', 'HEAD'], capture_output=True, text=True, check=True
        ).stdout.strip()
        self.checkout = tmp / 'checkout'
        (self.checkout / 'scripts').mkdir(parents=True)
        shutil.copy(ROOT / 'scripts/build-aur-package.sh', self.checkout / 'scripts')
        self.cache = tmp / 'cache'
        self.env = {
            'HOME': str(tmp / 'home'),
            'XDG_CACHE_HOME': str(self.cache),
            'PATH': '/usr/bin:/bin',
            'LANG': 'C.UTF-8',
            'SHAFFY_AUR_URL': f'file://{self.aur}',
            'GIT_CONFIG_GLOBAL': '/dev/null',
        }

    def run_script(self, *arguments):
        return subprocess.run(
            ['/usr/bin/bash', str(self.checkout / 'scripts/build-aur-package.sh'), *arguments],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=300,
            check=False,
        )

    def test_fetch_reports_the_commit_and_the_needs_and_build_reports_the_digest(self):
        before = sorted(str(p.relative_to(self.checkout)) for p in self.checkout.rglob('*'))
        fetched = self.run_script('fetch', 'shaffy-probe')
        self.assertEqual(fetched.returncode, 0, fetched.stderr[-1500:])
        recipe = json.loads(fetched.stdout.strip().splitlines()[-1])
        self.assertEqual(recipe['commit'], self.commit)
        self.assertEqual(sorted(recipe['depends']), ['bash', 'coreutils'])  # without the version constraint
        built = self.run_script('build', 'shaffy-probe', recipe['commit'])
        self.assertEqual(built.returncode, 0, built.stderr[-1500:])
        result = json.loads(built.stdout.strip().splitlines()[-1])
        package = Path(result['path'])
        self.assertEqual(package.parent, self.cache / 'shaffy/build/shaffy-probe/out')
        self.assertTrue(package.name.startswith('shaffy-probe-1.0-1-any.pkg.tar'))
        self.assertEqual(result['sha256'], hashlib.sha256(package.read_bytes()).hexdigest())
        # Nothing lands in the checkout.
        self.assertEqual(sorted(str(p.relative_to(self.checkout)) for p in self.checkout.rglob('*')), before)

    def test_a_build_needs_the_fetched_commit(self):
        self.assertEqual(self.run_script('fetch', 'shaffy-probe').returncode, 0)
        other = self.run_script('build', 'shaffy-probe', '0' * 40)
        self.assertNotEqual(other.returncode, 0)
        self.assertIn('is not at', other.stderr)

    def test_unknown_names_and_bad_arguments_are_refused(self):
        for arguments, message in (
            (('fetch', 'no-such-recipe'), 'cannot fetch'),
            (('fetch', '../escape'), 'usage'),
            (('build', 'shaffy-probe', 'HEAD'), 'usage'),
            (('build', 'shaffy-probe', '0' * 40), 'fetch shaffy-probe first'),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_script(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)


if __name__ == '__main__':
    unittest.main()
