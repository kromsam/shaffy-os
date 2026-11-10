"""./bin/shaffy profile check over an author's profile repository: profile.example/ passes it,
and each thing vendoring, a load or a person's privacy needs is refused with its reason.

Every repository is a copy of profile.example/ in a temporary folder, committed as an author
would; the supplement whose private values the scan adds is synthetic.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy import authoring, lint

GIT_ENV = {
    'GIT_CONFIG_GLOBAL': os.devnull,
    'GIT_CONFIG_NOSYSTEM': '1',
    'GIT_AUTHOR_NAME': 'Example Author',
    'GIT_AUTHOR_EMAIL': 'author@example.com',
    'GIT_COMMITTER_NAME': 'Example Author',
    'GIT_COMMITTER_EMAIL': 'author@example.com',
}
# Split, so the repository's own scan passes this file.
PLANTED = '/home/' + 'someone/x'


def git(directory, *arguments):
    subprocess.run(['git', '-C', str(directory), *arguments], capture_output=True, text=True, check=True)


class RepositoryCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = Path(directory.name) / 'profiles'
        shutil.copytree(ROOT / 'profile.example', self.repository)
        git(self.repository, 'init', '-q')
        self.commit()
        self.supplement = Path(directory.name) / 'supplement'
        self.supplement.mkdir()
        (self.supplement / 'config.yaml').write_text(
            'schema_version: 1\nsupplement_id: check-fixture\nidentity:\n  name: Example Person\n'
        )
        (self.supplement / 'denylist.txt').write_text('# synthetic\n\\bexample-secret-word\\b\n')

    def commit(self):
        git(self.repository, 'add', '-A')
        git(self.repository, 'commit', '-q', '--allow-empty', '-m', 'Change the profiles')

    def check(self):
        return authoring.check_repository(ROOT, self.repository, self.supplement)

    def step(self, report, name):
        return next(step for step in report.steps if step.name == name)

    def conflicting(self):
        """Two more profiles that give one asset target different files."""
        for name, text in (('one', 'one\n'), ('two', 'two\n')):
            folder = self.repository / name
            folder.mkdir()
            (folder / 'profile.yaml').write_text(
                f'---\nschema_version: 1\ndescription: Synthetic {name}.\n'
                'assets:\n  "~/.local/share/example-profile/shared.txt": shared.txt\n'
            )
            (folder / 'shared.txt').write_text(text)
        self.commit()


class ScaffoldTests(RepositoryCase):
    def test_the_author_scaffold_passes_profile_check(self):
        result = subprocess.run(
            [str(ROOT / 'bin/shaffy'), 'profile', 'check', str(self.repository), '--supplement', str(self.supplement)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], f'Profile repository {self.repository}: 1 profile (example).')
        self.assertIn('Licence: LICENSE.', lines)
        self.assertTrue(
            any(line.startswith('Personal values: none found in ') and 'denylist' in line for line in lines), lines
        )
        self.assertTrue(
            any(line.startswith('Render: rendered for a synthetic user (example alone: ') for line in lines)
        )
        self.assertIn('The check passed. To publish the repository:', lines)
        self.assertIn('  2. Add the topic shaffy-os-profiles to it, so people find it.', lines)
        self.assertIn('pull request against dev', result.stdout)
        self.assertEqual(git_status(self.repository), '')  # the check changed nothing

    def test_each_profile_renders_alone_and_two_that_conflict_together_are_a_note(self):
        self.conflicting()
        report = self.check()
        self.assertEqual(report.names, ('example', 'one', 'two'))
        render = self.step(report, 'Render')
        self.assertEqual(report.problems, 0, report.lines())
        for name in report.names:
            self.assertIn(f'{name} alone: ', render.summary)
        self.assertTrue(render.notes, render)
        self.assertTrue(all(note.startswith('all together: ') for note in render.notes), render.notes)
        self.assertIn('shared.txt', render.notes[0])
        self.assertIn('author/one and author/two set it differently', render.notes[0])
        self.assertIn('Write the entry you want in config.yaml', render.notes[0])


class RefusalTests(RepositoryCase):
    def test_a_repository_without_a_licence_is_refused_and_says_why(self):
        (self.repository / 'LICENSE').unlink()
        self.commit()
        report = self.check()
        [problem] = self.step(report, 'Licence').problems
        self.assertIn('Everyone who vendors a profile copies its files', problem)
        self.assertIn('a LICENSE file or a LICENSES/ folder at its root', problem)
        self.assertNotIn('The check passed. To publish the repository:', report.lines())

    def test_a_licences_folder_also_serves(self):
        (self.repository / 'LICENSE').unlink()
        (self.repository / 'LICENSES').mkdir()
        (self.repository / 'LICENSES/MIT.txt').write_text('MIT License\n')
        self.commit()
        self.assertEqual(self.step(self.check(), 'Licence').problems, ())

    def test_a_link_in_a_profile_is_refused(self):
        os.symlink('hello.txt', self.repository / 'example/link.txt')
        self.commit()
        problems = self.step(self.check(), 'Files').problems
        self.assertEqual(problems, ('example/link.txt: a link; a profile holds plain files, which vendoring copies',))

    def test_a_named_file_git_does_not_track_is_refused(self):
        manifest = self.repository / 'example/profile.yaml'
        manifest.write_text(manifest.read_text() + 'overrides:\n  fish: example.fish\n')
        self.commit()
        (self.repository / 'example/example.fish').write_text('set -g example_profile 1\n')
        problems = self.step(self.check(), 'Files').problems
        self.assertEqual(
            problems, ('example/example.fish: not in the last commit; vendoring reads a commit, so add and commit it',)
        )

    def test_what_vendoring_refuses_or_cannot_read_from_the_last_commit_is_named(self):
        (self.repository / 'example/.gitignore').write_text('*.tmp\n')
        (self.repository / 'example/a\u202eb.txt').write_text('reversed\n')
        os.symlink('example', self.repository / 'linked')  # a profile folder that is a link
        self.commit()
        manifest = self.repository / 'example/profile.yaml'
        manifest.write_text(manifest.read_text() + 'overrides:\n  fish: example.fish\n')
        (self.repository / 'example/example.fish').write_text('set -g example_profile 1\n')
        git(self.repository, 'add', '-A')  # staged, but in no commit
        problems = self.step(self.check(), 'Files').problems
        self.assertEqual(
            problems,
            (
                "example/: '.gitignore' is not a name a profile may hold, so vendoring refuses the profile",
                "example/: 'a\\u202eb.txt' holds a control or format character, which no name may, so vendoring "
                'refuses the profile',
                'example/example.fish: not in the last commit; vendoring reads a commit, so add and commit it',
                'linked/: a link; a profile is a folder of plain files, which vendoring copies',
            ),
        )

    def test_personal_values_are_named_by_place_and_rule_never_by_value(self):
        readme = self.repository / 'example/README.md'
        readme.write_text(readme.read_text() + f'\nNotes in {PLANTED}, by Example Person.\n\nexample-secret-word\n')
        self.commit()
        report = self.check()
        self.assertEqual(
            self.step(report, 'Personal values').problems,
            (
                'example/README.md:6: home-path',
                'example/README.md:6: settings field identity.name',
                'example/README.md:8: denylist.txt line 2',
            ),
        )
        text = '\n'.join(report.lines())
        for value in (PLANTED, 'Example Person', 'example-secret-word'):
            self.assertNotIn(value, text)
        # A synthetic value the static rules match passes once literals.allow lists it.
        allow = self.repository / 'literals.allow'
        made_up = PLANTED.removesuffix('/x')  # the text the rule matched: the home folder, without the rest
        allow.write_text(allow.read_text() + f'home-path\texample/README.md\t{made_up}\ta made-up folder\n')
        self.commit()
        self.assertEqual(
            self.step(self.check(), 'Personal values').problems,
            ('example/README.md:6: settings field identity.name', 'example/README.md:8: denylist.txt line 2'),
        )

    def test_literals_allow_is_scanned_for_the_supplements_private_values(self):
        allow = self.repository / 'literals.allow'
        allow.write_text(allow.read_text() + '# Not example-secret-word, which only the denylist names.\n')
        self.commit()
        number = len(allow.read_text().splitlines())
        self.assertEqual(
            self.step(self.check(), 'Personal values').problems, (f'literals.allow:{number}: denylist.txt line 2',)
        )

    def test_an_origin_with_a_token_reaches_no_message(self):
        token = 'synthetic-token-' + 'x' * 12
        git(self.repository, 'remote', 'add', 'origin', f'https://someone:{token}@example.test/alice/profiles')
        self.conflicting()
        text = '\n'.join(self.check().lines())
        self.assertIn('tell its author: https://example.invalid/this-repository', text)
        self.assertNotIn(token, text)

    def test_an_invalid_profile_is_refused_with_the_reason_and_the_way_to_shaffy(self):
        manifest = self.repository / 'example/profile.yaml'
        manifest.write_text(manifest.read_text() + 'desktop:\n  browser: example.desktop\n')
        (self.repository / 'Not_A_Name').mkdir()
        (self.repository / 'Not_A_Name/profile.yaml').write_text('schema_version: 1\ndescription: x\n')
        self.commit()
        report = self.check()
        problems = self.step(report, 'Profiles').problems
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(problems[0].startswith("Not_A_Name/: not a profile's name"), problems[0])
        self.assertIn('example/profile.yaml: desktop.browser: a profile cannot set this', problems[1])
        self.assertIn('./bin/shaffy profile propose --to shaffy --key desktop.browser', problems[1])
        self.assertEqual(self.step(report, 'Render').summary, 'nothing renders until a profile loads.')

    def test_a_folder_that_is_not_a_git_repository_is_refused(self):
        shutil.rmtree(self.repository / '.git')
        report = self.check()
        self.assertEqual([step.name for step in report.steps], ['Repository'])
        self.assertIn('git init', report.steps[0].problems[0])


@unittest.skipUnless(
    all(lint.available(step) for step in lint.STEPS if step.tool in ('ansible-lint', 'markdownlint-cli2')),
    'needs ansible-lint and markdownlint-cli2 from mise.dev.toml',
)
class CodeTests(RepositoryCase):
    def test_the_check_runs_no_code_the_repository_brings(self):
        markers = self.repository.parent / 'markers'
        markers.mkdir()
        # A playbook makes ansible-lint lint the folder as a project, with its configuration's rulesdir.
        (self.repository / 'site.yml').write_text('---\n- name: Synthetic\n  hosts: localhost\n  tasks: []\n')
        (self.repository / '.ansible-lint').write_text('---\nrulesdir:\n  - rules\n')
        (self.repository / 'rules').mkdir()
        (self.repository / 'rules/planted.py').write_text(
            f'from pathlib import Path\n\nPath({str(markers / "ansible-lint")!r}).touch()\n'
        )
        # markdownlint-cli2 runs a configuration module it finds beside a file it lints.
        (self.repository / 'example/.markdownlint-cli2.cjs').write_text(
            f'require("node:fs").writeFileSync({json.dumps(str(markers / "markdownlint-cli2"))}, "");\n'
            'module.exports = {};\n'
        )
        readme = self.repository / 'example/README.md'
        readme.write_text(readme.read_text() + '\n#Not a heading\n')  # a problem only markdownlint-cli2 finds
        self.commit()
        problems = self.step(self.check(), 'Lint').problems
        self.assertEqual(sorted(path.name for path in markers.iterdir()), [])
        self.assertTrue(any('example/README.md' in line and 'MD018' in line for line in problems), problems)
        self.assertEqual(git_status(self.repository), '')


def git_status(directory):
    return subprocess.run(
        ['git', '-C', str(directory), 'status', '--porcelain'], capture_output=True, text=True, check=True
    ).stdout


if __name__ == '__main__':
    unittest.main()
