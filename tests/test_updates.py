"""The update report, the update routine and the pin refresh, against fake upstreams and commands."""

import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shaffy import pins, updates
from support import vendor_profile

NOW = datetime.datetime(2026, 10, 28, tzinfo=datetime.UTC)
OLD, NEW = '2026-10-01T00:00:00Z', '2026-10-27T00:00:00Z'  # NEW is younger than three days
PINNED = (
    'requirements.yml',
    'scripts/install-yaru-icons.py',
    'scripts/install-better-bibtex.py',
    'README.md',
    'docs/DESKTOP-BEHAVIOUR.md',
    'scripts/build-audio-bridge.sh',
    '.github/workflows/selftest.yml',
    '.github/workflows/supplement.yml',
    'tests/tool-versions.json',
    'ruff.toml',
)
DIGEST = 'sha256:' + 'ab' * 32
YARU = b'yaru archive'
BETTER_BIBTEX = b'better bibtex release'
BETTER_BIBTEX_URL = (
    'https://github.com/retorquere/zotero-better-bibtex/releases/download/v9.9.6/zotero-better-bibtex-9.9.6.xpi'
)


class FakeUpstream(pins.Upstream):
    def __init__(self):
        super().__init__(NOW)

    def json(self, url):
        if url.startswith(pins.GALAXY):
            return {
                'data': [
                    {'version': '99.0.0', 'created_at': NEW},
                    {'version': '98.0.0-rc1', 'created_at': OLD},
                    {'version': '97.1.0', 'created_at': OLD},
                ]
            }
        if url.startswith('https://hub.docker.com/'):
            return {'digest': DIGEST}
        if url.startswith('https://api.github.com/repos/retorquere/zotero-better-bibtex/releases'):
            release = {'draft': False, 'prerelease': False, 'published_at': OLD}
            return [
                {**release, 'tag_name': 'v9.9.10', 'published_at': NEW},
                {**release, 'tag_name': 'release'},  # the update channel's moving tag
                {**release, 'tag_name': 'v9.9.8', 'prerelease': True},
                {**release, 'tag_name': 'v9.9.7', 'draft': True, 'published_at': None},
                {**release, 'tag_name': 'v9.9.6'},
            ]
        raise AssertionError(url)

    def python(self):
        return 'py399'

    def read(self, url):
        if url == BETTER_BIBTEX_URL:
            return BETTER_BIBTEX
        assert url == 'https://github.com/ubuntu/yaru/archive/refs/tags/30.04.1ubuntu.tar.gz', url
        return YARU

    def refs(self, url, *patterns):
        if 'yaru' in url:
            return {f'refs/tags/{tag}': '0' * 40 for tag in ('30.04.1ubuntu', '31.04', '29.10.9ubuntu', 'r40')}
        if 'yabridge' in url:
            return {patterns[0]: 'f' * 40}
        if 'checkout' in url:
            return {'refs/tags/v9': '1' * 40, 'refs/tags/v9.1.0': '2' * 40, 'refs/tags/v10.0.0-beta': '3' * 40}
        raise AssertionError(url)


class PinTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for path in PINNED:
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, self.root / path)
        shutil.copytree(ROOT / 'home/dot_config/nvim', self.root / 'home/dot_config/nvim')

    def found(self):
        return {pin.name: pin for pin in pins.check(self.root, FakeUpstream())}

    def test_each_pin_takes_the_newest_stable_release_old_enough(self):
        found = self.found()
        self.assertEqual(found['Ansible collection community.general'].latest, '97.1.0')
        self.assertEqual(found['Yaru icons'].latest, '30.04.1ubuntu')  # the pinned …ubuntu line
        self.assertEqual(found['Better BibTeX for Zotero'].latest, '9.9.6')
        self.assertEqual(found['GitHub action actions/checkout'].latest, 'v9.1.0')
        self.assertEqual(found['CI container cachyos/cachyos:latest'].latest, 'ab' * 6)
        self.assertEqual(found['ruff target, the system Python'].latest, 'py399')
        self.assertTrue(all(not pin.error for pin in found.values()), found)

    def test_raising_every_pin_rewrites_each_place_it_appears(self):
        old = {path: (self.root / path).read_text() for path in PINNED}
        for pin in self.found().values():
            if pin.behind and pin.raise_to and pin.name != 'LazyVim plugin lock':
                pin.raise_to()
        text = {path: (self.root / path).read_text() for path in PINNED}
        self.assertIn('version: 97.1.0', text['requirements.yml'])
        yaru = hashlib.sha256(YARU).hexdigest()
        for path in ('scripts/install-yaru-icons.py', 'docs/DESKTOP-BEHAVIOUR.md'):
            self.assertIn('30.04.1ubuntu', text[path])
            self.assertIn(yaru, text[path])
        self.assertIn('refs/tags/30.04.1ubuntu.tar.gz', text['README.md'])
        # The installer fetches the release the pin was taken from and accepts it.
        installer = runpy.run_path(str(self.root / 'scripts/install-better-bibtex.py'))
        self.assertEqual(installer['VERSION'], '9.9.6')
        served = {BETTER_BIBTEX_URL: BETTER_BIBTEX}
        with mock.patch.dict(installer['download'].__globals__, fetch=lambda url: io.BytesIO(served[url])):
            installer['download'](installer['URL'], self.root / 'better-bibtex.xpi')
        self.assertEqual((self.root / 'better-bibtex.xpi').read_bytes(), BETTER_BIBTEX)
        for path in ('.github/workflows/selftest.yml', '.github/workflows/supplement.yml'):
            self.assertEqual(text[path].count(f'actions/checkout@{"2" * 40} # v9.1.0'), 2, path)
        for path in ('.github/workflows/selftest.yml', '.github/workflows/supplement.yml', 'tests/tool-versions.json'):
            self.assertIn(f'cachyos/cachyos@{DIGEST}', text[path])
        self.assertIn('target-version = "py399"', text['ruff.toml'])
        # yabridge is a reviewed source: reported, never rewritten.
        self.assertEqual(text['scripts/build-audio-bridge.sh'], old['scripts/build-audio-bridge.sh'])
        self.assertTrue(self.found()['yabridge (new-wine10-embedding)'].behind)
        self.assertIsNone(self.found()['yabridge (new-wine10-embedding)'].raise_to)
        self.assertFalse(any(p.behind for p in self.found().values() if p.raise_to and p.name != 'LazyVim plugin lock'))

    def test_an_unreachable_upstream_is_reported_and_hides_nothing_else(self):
        upstream = FakeUpstream()
        upstream.refs = mock.Mock(side_effect=subprocess.CalledProcessError(128, 'git'))
        found = pins.check(self.root, upstream)
        self.assertEqual(sorted(p.name for p in found if p.error), ['actions checkout', 'yabridge', 'yaru'])
        self.assertIn('Ansible collection community.general', [p.name for p in found])

    def test_the_lock_is_refreshed_in_a_sandbox_that_lets_plugins_move(self):
        lock = self.root / 'home/dot_config/nvim/lazy-lock.json'
        locked = json.loads(lock.read_text())
        moved = dict(locked, **{'lazy.nvim': {'branch': 'main', 'commit': 'f' * 40}})
        # What rendering a home template needs: the settings loader and the public defaults.
        for part in ('lib', 'defaults', 'schema', 'home/.chezmoitemplates'):
            shutil.copytree(ROOT / part, self.root / part, ignore=shutil.ignore_patterns('__pycache__'))

        def nvim(command, env, **kwargs):
            config = Path(env['XDG_CONFIG_HOME']) / 'nvim'
            self.assertEqual(env['SHAFFY_LAZY_REFRESH'], '1')
            self.assertTrue((config / '.markdownlint-cli2.yaml').exists())  # dot_ names become dotfiles
            # Templates render, so the refresh sees the public extras and locks their plugins.
            extras = json.loads((config / 'lazyvim.json').read_text())['extras']
            self.assertIn('lazyvim.plugins.extras.lang.markdown', extras)
            self.assertFalse(list(config.rglob('*.tmpl')))
            self.assertNotIn(str(Path.home()), env['XDG_DATA_HOME'])
            (config / 'lazy-lock.json').write_text(json.dumps(moved, indent=2) + '\n')
            return subprocess.CompletedProcess(command, 0, '', '')

        changed = pins.refresh_lazyvim(self.root, nvim)
        self.assertEqual(changed, ['home/dot_config/nvim/lazy-lock.json', '1 plugin(s) moved'])
        self.assertEqual(json.loads(lock.read_text())['lazy.nvim']['commit'], 'f' * 40)
        self.assertIn(
            'if not vim.env.SHAFFY_LAZY_REFRESH then', (ROOT / 'home/dot_config/nvim/lua/config/lazy.lua').read_text()
        )


class Result:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class ReportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)

    def test_package_checks_read_the_exit_status_their_tools_use(self):
        with mock.patch.object(updates.shutil, 'which', return_value='/usr/bin/x'):
            for result, summary in (
                (Result(2), 'up to date'),
                (Result(0, 'a 1 -> 2\nb 1 -> 2\n'), '2 upgrade(s)'),
                (Result(1, stderr='no network'), 'not checked: no network'),
            ):
                with self.subTest(summary=summary), mock.patch.object(updates, '_run', return_value=result):
                    self.assertEqual(updates.pacman().summary, summary)
            for result, summary in (
                (Result(1), 'up to date'),
                (Result(0, 'beeper 1 -> 2\n'), '1 upgrade(s)'),
                (Result(1, stderr=' -> error: AUR unreachable'), 'not checked'),
            ):
                with self.subTest(summary=summary), mock.patch.object(updates, '_run', return_value=result):
                    self.assertTrue(updates.aur().summary.startswith(summary))
        with mock.patch.object(updates.shutil, 'which', return_value=None):
            self.assertIn('pacman-contrib', updates.pacman().summary)

    def test_firmware_updates_come_from_fwupds_metadata(self):
        device = {'Name': 'System Firmware', 'Version': '309', 'Releases': [{'Version': '312'}]}
        with mock.patch.object(updates.shutil, 'which', return_value='/usr/bin/fwupdmgr'):
            for result, summary, lines in (
                (Result(2), 'up to date', []),
                (
                    Result(0, json.dumps({'Devices': [device, {'Name': 'Mouse'}]})),
                    '1 update(s)',
                    ['System Firmware 309 → 312'],
                ),
                (Result(1, stderr='daemon not running'), 'not checked: daemon not running', []),
            ):
                with self.subTest(summary=summary), mock.patch.object(updates, '_run', return_value=result):
                    section = updates.firmware()
                    self.assertTrue(section.summary.startswith(summary), section.summary)
                    self.assertEqual(section.lines, lines)
        with mock.patch.object(updates.shutil, 'which', return_value=None):
            self.assertEqual(updates.firmware().summary, 'not checked: fwupd is not installed')

    def test_user_installs_are_compared_with_this_checkouts_pins(self):
        (self.home / '.local/share/icons/Yaru').mkdir(parents=True)
        (self.home / '.local/share/icons/Yaru/.shaffy-version').write_text('1.0ubuntu\n')
        (self.home / '.local/share/yabridge').mkdir(parents=True)
        (self.home / '.local/share/yabridge/build-receipt.json').write_text(json.dumps({'revision': 'e' * 40}))
        section = updates.installs(ROOT, self.home)
        self.assertEqual(section.summary, '2 differ from the pins')
        self.assertTrue(section.lines[0].startswith('Yaru icons 1.0ubuntu → '))
        self.assertTrue(section.lines[1].endswith('(rebuild by hand: docs/MAINTENANCE.md)'))

    def test_plugins_are_compared_with_the_managed_lock(self):
        (self.home / '.config/nvim').mkdir(parents=True)
        (self.home / '.config/nvim/lazy-lock.json').write_text(
            json.dumps({'a.nvim': {'commit': 'a' * 40}, 'b.nvim': {'commit': 'b' * 40}, 'c.nvim': {'commit': 'c' * 40}})
        )
        for name in ('a.nvim', 'b.nvim'):
            (self.home / '.local/share/nvim/lazy' / name / '.git').mkdir(parents=True)
        heads = {'a.nvim': 'a' * 40, 'b.nvim': '9' * 40}
        with mock.patch.object(updates, '_run', side_effect=lambda command: Result(0, heads[Path(command[2]).name])):
            section = updates.lazyvim(self.home)
        self.assertEqual(section.lines, ['b.nvim 9999999 → bbbbbbb', 'c.nvim missing'])


class RoutineTests(unittest.TestCase):
    def test_packages_come_first_and_the_routine_stops_at_a_failure(self):
        with tempfile.TemporaryDirectory() as home:
            steps = updates.steps(ROOT, Path(home))
        self.assertEqual([s.command[:3] for s in steps if s.packages], [['sudo', 'pacman', '-Syu'], ['yay', '-Sua']])
        self.assertTrue(steps[0].packages and steps[1].packages and not any(s.packages for s in steps[2:]))
        self.assertEqual(steps[-1].command, ['nvim', '--headless', '+Lazy! restore', '+qa'])
        ran, out = [], []
        routine = [
            updates.Step('one', ['one']),
            updates.Step('two', ['two'], skip=lambda: 'not needed'),
            updates.Step('three', ['three']),
            updates.Step('four', ['four']),
        ]
        code = updates.run(
            routine,
            out=out.append,
            call=lambda command, cwd=None: ran.append(command[0]) or (3 if command == ['three'] else 0),
        )
        self.assertEqual((code, ran), (3, ['one', 'three']))
        self.assertIn('[2/4] two: skipped, not needed', out)
        self.assertTrue(out[-1].startswith('Stopped: "three" exited with 3.'))

    def test_update_needs_a_terminal_unless_the_packages_are_left_out(self):
        from shaffy import cli

        ran = []
        with (
            mock.patch('shaffy.records.Records.read', return_value=None),
            mock.patch.object(cli.os, 'geteuid', return_value=1000),
            mock.patch.object(cli.sys.stdin, 'isatty', return_value=False),
            mock.patch.object(updates, 'run', side_effect=lambda steps: ran.append(steps) or 0),
            mock.patch('sys.stdout', new_callable=io.StringIO),
            mock.patch('sys.stderr', new_callable=io.StringIO),
        ):
            self.assertEqual(cli.main(['update']), 1)
            self.assertEqual(cli.main(['update', '--skip-packages']), 0)
        self.assertEqual(len(ran), 1)
        self.assertFalse(any(step.packages for step in ran[0]))

    def test_update_waits_for_an_unfinished_operation(self):
        from shaffy import cli

        with (
            mock.patch('shaffy.records.Records.read', return_value={'pending': {'op': 'op-1'}}),
            mock.patch.object(cli.os, 'geteuid', return_value=1000),
            mock.patch('sys.stderr', new_callable=io.StringIO) as err,
        ):
            self.assertEqual(cli.main(['update', '--skip-packages']), 1)
        self.assertIn('operation op-1 is unfinished', err.getvalue())


class RefreshScriptTests(unittest.TestCase):
    def setUp(self):
        self.script = runpy.run_path(str(ROOT / 'scripts/refresh-pins.py'))

    def branch(self, name):
        return mock.patch.object(
            self.script['subprocess'], 'run', return_value=subprocess.CompletedProcess([], 0, name + '\n', '')
        )

    def test_main_is_only_reported_on(self):
        raised = []
        pin = pins.Pin('probe', '1', '2', raise_to=lambda: raised.append(1) or ['probe.txt'])
        with (
            mock.patch.object(pins, 'check', return_value=[pin]),
            mock.patch('sys.stdout', new_callable=io.StringIO) as out,
        ):
            with self.branch('main'), self.assertRaisesRegex(SystemExit, 'refresh the pins on dev'):
                self.script['main']([])
            with self.branch('main'):
                self.assertEqual(self.script['main'](['--check']), 0)
            self.assertEqual(raised, [])
            with self.branch('dev'):
                self.assertEqual(self.script['main']([]), 0)
        self.assertEqual(raised, [1])
        self.assertIn('probe: 1 → 2 (probe.txt)', out.getvalue())

    def test_a_pin_that_fails_to_rise_is_reported_and_fails_the_run(self):
        def fail():
            raise ValueError('no single entry')

        found = [
            pins.Pin('broken', '1', '2', raise_to=fail),
            pins.Pin('fine', '1', '1'),
            pins.Pin('offline', '?', error='URLError: down'),
        ]
        with (
            mock.patch.object(pins, 'check', return_value=found),
            self.branch('dev'),
            mock.patch('sys.stdout', new_callable=io.StringIO) as out,
        ):
            self.assertEqual(self.script['main']([]), 1)
        self.assertIn('broken: not raised: ValueError: no single entry', out.getvalue())
        self.assertIn('offline: not checked: URLError: down', out.getvalue())
        self.assertIn('fine: 1, up to date', out.getvalue())


class ProfileRefs(pins.Upstream):
    """Where the authors' refs point now; one of the sources cannot be read."""

    def refs(self, url, *patterns):
        if url == 'https://example.test/alice/profiles':
            return {'HEAD': '3' * 40}
        raise subprocess.CalledProcessError(128, ['git', 'ls-remote'], stderr='fatal: repository not found\n')


class ProfileSectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name) / 'home'
        supplement = Path(directory.name) / 'supplement'
        supplement.mkdir()
        (supplement / 'config.yaml').write_text('schema_version: 1\nsupplement_id: synthetic\n')
        manifest = {'profile.yaml': 'schema_version: 1\ndescription: x\n'}
        for profile_id, source, commit, ref in (
            ('alice/notes', 'https://example.test/alice/profiles', '1' * 40, 'HEAD'),
            ('alice/keys', 'https://example.test/alice/profiles', '3' * 40, 'HEAD'),
            ('bob/keys', 'https://example.test/bob/profiles', '2' * 40, '2' * 40),
            ('carol/gone', 'https://example.test/carol/missing', '4' * 40, 'HEAD'),
        ):
            vendor_profile(supplement, profile_id, manifest, source=source, commit=commit, ref=ref)
        self.bind({'mode': 'supplement', 'path': str(supplement)})
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in ('XDG_STATE_HOME', 'SHAFFY_OP_MODE'):
            os.environ.pop(name, None)

    def bind(self, binding):
        state = self.home / '.local/state/shaffy'
        state.mkdir(parents=True, exist_ok=True)
        (state / 'binding.json').write_text(json.dumps({'binding': binding}))

    def test_profiles_behind_their_ref_are_reported(self):
        section = updates.profile_section(self.home, ProfileRefs())
        self.assertEqual(section.title, 'Profiles')
        self.assertEqual(
            section.summary,
            "1 behind; take each with ./bin/shaffy profile update ID, which shows the author's commits and what "
            'runs code',
        )
        self.assertIn('alice/notes 1111111 → 3333333 (HEAD of example.test/alice/profiles)', section.lines)
        self.assertFalse([line for line in section.lines if line.startswith('alice/keys')])  # up to date

    def test_a_pinned_profile_is_reported_as_pinned(self):
        self.assertIn('bob/keys pinned at 2222222', updates.profile_section(self.home, ProfileRefs()).lines)

    def test_an_unreachable_profile_source_hides_nothing_else(self):
        lines = updates.profile_section(self.home, ProfileRefs()).lines
        self.assertIn(
            'carol/gone: not checked: https://example.test/carol/missing cannot be read: fatal: repository not found',
            lines,
        )
        self.assertEqual(len(lines), 3)  # alice/notes behind, bob/keys pinned, carol/gone not checked

    def test_without_a_supplement_no_profile_is_in_use(self):
        self.bind({'mode': 'none'})
        self.assertEqual(updates.profile_section(self.home, ProfileRefs()).summary, 'none in use')


if __name__ == '__main__':
    unittest.main()
