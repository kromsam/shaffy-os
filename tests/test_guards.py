"""Direct chezmoi changes are refused outside ./bin/shaffy."""

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy.guard import refusal


class DecisionTests(unittest.TestCase):
    def test_mutating_commands_are_refused(self):
        for command, arguments in (
            ('apply', []),
            ('apply', ['--dry-run']),
            ('update', []),
            ('destroy', ['x']),
            ('purge', ['--force']),
            # init applies, purges the checkout or rewrites the config.
            ('init', []),
            ('init', ['--apply']),
            ('init', ['--apply=false']),
            ('init', ['--one-shot=true']),
            ('init', ['-p']),
            ('edit', ['--apply', 'x']),
            ('edit', ['-a', 'x']),
            ('edit', ['--watch', 'x']),
            # Boolean flags with values, as chezmoi's flag parser reads them.
            ('edit', ['--apply=1', 'x']),
            ('edit', ['--apply=True', 'x']),
            ('edit', ['-a=true', 'x']),
            ('edit', ['-va=1', 'x']),
            ('edit', ['-av=false', 'x']),
            ('edit', ['--watch=true', 'x']),
            # chezmoi takes `--` as the value of a flag that expects one.
            ('edit', ['-o', '--', '--apply', 'x']),
            ('edit', ['--', '--apply']),
        ):
            with self.subTest(command=command, arguments=arguments):
                self.assertIn('Shaffy OS', refusal(command, arguments))

    def test_reading_and_plain_editing_are_allowed(self):
        for command, arguments in (
            ('diff', []),
            ('status', []),
            ('cat', ['x']),
            ('verify', []),
            ('managed', []),
            ('edit', ['x']),
            ('data', []),
            ('edit', ['--apply=false', 'x']),
            ('edit', ['-a=0', 'x']),
            ('edit', ['-va=F', 'x']),
            ('edit', ['--watch=FALSE', 'x']),
            ('edit', ['-v', 'x']),
        ):
            with self.subTest(command=command, arguments=arguments):
                self.assertIsNone(refusal(command, arguments))

    def test_only_the_wrapper_passes_the_hook(self):
        self.assertIsNone(refusal('apply', [], mode='apply'))
        # The render sandbox passes the template guard only, with its token file.
        self.assertIsNotNone(refusal('apply', [], mode='sandbox'))
        self.assertIsNotNone(refusal('apply', [], mode='preview'))


def _tree_digest(root):
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob('*')):
        if {'__pycache__', '.git'} & set(path.relative_to(root).parts):
            continue  # Python bytecode and git metadata are not configuration
        if path.name == 'chezmoistate.boltdb' or path.relative_to(root).parts == ('.config', 'chezmoi'):
            continue  # chezmoi's own state, created by any run without the tracked config
        digest.update(str(path.relative_to(root)).encode())
        if path.is_file() and not path.is_symlink():
            digest.update(path.read_bytes())
    return digest.hexdigest()


@unittest.skipUnless(shutil.which('chezmoi') and shutil.which('git'), 'needs chezmoi and git')
class CommandMatrixTests(unittest.TestCase):
    """Every way to change the home through chezmoi, run against a copy of the checkout."""

    def setUp(self):
        # A fresh copy per test, so a guard that fails cannot spoil the other cases.
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.checkout = root / 'checkout'
        for part in ('home', '.agents', 'bin', 'lib', 'schema', 'defaults'):
            shutil.copytree(ROOT / part, self.checkout / part, ignore=shutil.ignore_patterns('__pycache__'))
        (self.checkout / 'scripts').mkdir()
        shutil.copy(ROOT / 'scripts/merge-ai-config.py', self.checkout / 'scripts')
        shutil.copy(ROOT / 'chezmoi.toml', self.checkout)
        # No background maintenance: a detached repack after the commit races the clone and cleanup.
        # The user's own Git configuration (signing, hooks) must not reach the scratch checkout.
        git = [
            'git',
            '-c',
            'user.name=t',
            '-c',
            'user.email=t@example.invalid',
            '-c',
            'commit.gpgsign=false',
            '-c',
            'maintenance.auto=false',
            '-c',
            'gc.auto=0',
        ]
        subprocess.run([*git, 'init', '-q', '-b', 'main', str(self.checkout)], check=True)
        subprocess.run([*git, '-C', str(self.checkout), 'add', '-A'], check=True)
        subprocess.run([*git, '-C', str(self.checkout), 'commit', '-q', '-m', 'copy'], check=True)
        subprocess.run(['git', 'clone', '-q', '--bare', str(self.checkout), str(root / 'origin.git')], check=True)
        subprocess.run(
            ['git', '-C', str(self.checkout), 'remote', 'add', 'origin', str(root / 'origin.git')], check=True
        )
        self.home = root / 'home'
        self.target = self.home / '.config/tmux/tmux.conf'
        self.target.parent.mkdir(parents=True)
        self.target.write_text("# the user's own file\n")
        self.env = {
            'HOME': str(self.home),
            'PATH': '/usr/bin:/bin',
            'LANG': 'C.UTF-8',
            'EDITOR': 'true',
            'VISUAL': 'true',
        }

    def chezmoi(self, *arguments, config=True, env=None):
        command = ['chezmoi']
        if config:
            command += ['--config', str(self.checkout / 'chezmoi.toml')]
        command += [
            '--source',
            str(self.checkout / 'home'),
            '--destination',
            str(self.home),
            '--no-tty',
            '--no-pager',
            *arguments,
        ]
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            env={**self.env, **(env or {})},
            cwd=self.home,
            check=False,
        )

    def assertRefusedAndUnchanged(self, *arguments, config=True, env=None):
        home, source = _tree_digest(self.home), _tree_digest(self.checkout)
        result = self.chezmoi(*arguments, config=config, env=env)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertRegex(result.stderr, r'Shaffy OS: .*\./bin/shaffy')
        # With the tracked config the hook refuses before any template runs; without it, a template.
        self.assertIn(': pre: sh: exit status' if config else 'error calling fail', result.stderr)
        self.assertTrue((self.checkout / '.git').is_dir(), 'the checkout was deleted')
        self.assertEqual(_tree_digest(self.home), home, 'the home changed')
        self.assertEqual(_tree_digest(self.checkout), source, 'the source changed')

    def without_config(self, *arguments, env=None):
        self.assertRefusedAndUnchanged(
            *arguments, config=False, env={'XDG_STATE_HOME': str(self.home / '.state'), **(env or {})}
        )

    def both_layers(self, *arguments):
        """Refused by the hook with the tracked config, and by the source without it."""
        self.assertRefusedAndUnchanged(*arguments)
        self.without_config(*arguments)

    def test_apply_in_every_form_is_refused(self):
        target = str(self.target)
        for arguments in (
            ('apply', '--force'),
            ('apply', '--dry-run'),
            ('apply', '--force', target),
            ('apply', '--force', '--source-path', str(self.checkout / 'home/dot_config/tmux/tmux.conf')),
        ):
            with self.subTest(arguments=arguments):
                self.both_layers(*arguments)

    def test_edit_and_apply_is_refused(self):
        for arguments in (
            ('edit', '--apply', str(self.target)),
            ('edit', '-a', str(self.target)),
            ('edit', '--watch', str(self.target)),
            ('edit', '--apply=1', str(self.target)),
            ('edit', '-a=true', str(self.target)),
            ('edit', '-o', '--', '--apply', str(self.target)),
        ):
            with self.subTest(arguments=arguments):
                self.both_layers(*arguments)

    def test_init_in_every_form_is_refused_and_keeps_the_checkout(self):
        for arguments in (
            ('init',),
            ('init', '--apply'),
            ('init', '--one-shot'),
            ('init', '--one-shot=true'),
            ('init', '--apply=1'),
            ('init', '-a=true'),
            ('init', '--purge', '--force'),
            ('init', '-p', '--force'),
            ('init', '--purge-binary', '--force'),
        ):
            with self.subTest(arguments=arguments):
                self.both_layers(*arguments)

    def test_destroy_is_refused(self):
        self.both_layers('destroy', '--force', str(self.target))

    def test_update_and_purge_are_refused_by_the_hooks(self):
        # Neither reads the source first (update pulls, purge deletes the checkout), so only
        # the tracked chezmoi.toml's hooks stop them; see lib/shaffy/guard.py.
        for arguments in (('update', '--force'), ('purge', '--force')):
            with self.subTest(arguments=arguments):
                self.assertRefusedAndUnchanged(*arguments)

    def test_an_argument_with_spaces_reaches_the_hook_intact(self):
        spaced = str(self.home / 'a file --apply')
        result = self.chezmoi('edit', spaced)
        self.assertNotIn('Shaffy OS', result.stderr)

    def test_edit_apply_from_the_users_own_config_is_refused(self):
        config = self.home / '.config/chezmoi/chezmoi.toml'
        config.parent.mkdir(parents=True)
        config.write_text('[edit]\napply = true\n')
        self.without_config('edit', str(self.target))

    def test_reading_still_works(self):
        for arguments in (('diff',), ('status',), ('cat', str(self.target)), ('managed',)):
            with self.subTest(arguments=arguments):
                result = self.chezmoi(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('warning', result.stderr)

    def test_the_sandbox_mode_alone_is_refused(self):
        self.both_layers('apply', '--force')
        self.without_config(
            'apply',
            '--force',
            env={'SHAFFY_OP_MODE': 'sandbox', 'SHAFFY_SANDBOX_TOKEN': str(Path(self.temporary.name) / 'missing-token')},
        )

    def test_the_render_sandbox_applies_with_its_token_and_own_config(self):
        scratch = Path(self.temporary.name) / 'scratch-home'
        scratch.mkdir()
        work = Path(self.temporary.name) / 'scratch-work'
        work.mkdir()
        (work / 'chezmoi.toml').write_text(f'persistentState = "{work / "state.boltdb"}"\n')
        (work / 'sandbox-token').write_text('t\n')
        env = {
            **self.env,
            'HOME': str(scratch),
            'SHAFFY_OP_MODE': 'sandbox',
            'SHAFFY_SANDBOX_TOKEN': str(work / 'sandbox-token'),
        }

        def apply(config):
            return subprocess.run(
                [
                    'chezmoi',
                    '--config',
                    str(config),
                    '--source',
                    str(self.checkout / 'home'),
                    '--destination',
                    str(scratch),
                    '--no-tty',
                    'apply',
                    '--force',
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                env=env,
            )

        self.assertNotEqual(
            apply(self.checkout / 'chezmoi.toml').returncode, 0, 'the hook must not honour the sandbox mode'
        )
        result = apply(work / 'chezmoi.toml')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((scratch / '.config/tmux/tmux.conf').exists())


if __name__ == '__main__':
    unittest.main()
