"""The person's dotfiles: what this version refuses by name, and their overlap with Shaffy OS's home."""

import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy import desktop_key
from shaffy.capture import capture
from shaffy.dotfiles import (
    REFUSED,
    Claim,
    Claims,
    Entry,
    Mode,
    _attributes,
    _claims,
    _conflicts,
    _entries,
    prepare,
    preview,
    refusals,
)
from shaffy.loader import SYSTEM_OVERRIDES, load, parse

FIXTURE = ROOT / 'tests/fixtures/supplements/comprehensive'
XDG_FOLDERS = ('XDG_STATE_HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME')


class AttributeTests(unittest.TestCase):
    def test_prefixes_are_read_in_chezmoi_order_until_one_is_out_of_order_or_literal(self):
        for name, folder, attributes in (
            ('private_dot_local', True, ('private', 'dot')),
            ('exact_private_readonly_dot_x', True, ('exact', 'private', 'readonly', 'dot')),
            ('remove_external_x', True, ('remove', 'external')),
            ('readonly_private_x', True, ('readonly',)),
            ('dot_exact_x', True, ('dot',)),
            ('literal_exact_x', True, ()),
            ('run_x', True, ()),
            ('executable_private_x', False, ('executable',)),
            ('encrypted_private_x', False, ('encrypted', 'private')),
            ('private_encrypted_x', False, ('private',)),
            ('create_private_x', False, ('create', 'private')),
            ('modify_empty_x', False, ('modify',)),
            ('symlink_private_x', False, ('symlink',)),
            ('remove_dot_x', False, ('remove', 'dot')),
            ('run_once_before_x.sh', False, ('run', 'once', 'before')),
            ('run_before_once_x.sh', False, ('run', 'before')),
            ('literal_run_x', False, ()),
            ('dot_runner', False, ('dot',)),
            ('dot_private_x', False, ('dot',)),
            ('exact_x', False, ()),
        ):
            with self.subTest(name=name, folder=folder):
                self.assertEqual(_attributes(name, folder), attributes)


class RefusalTests(unittest.TestCase):
    def test_each_refused_feature_is_named_with_its_reason(self):
        cases = (
            ('run_once_install-packages.sh', 'script'),
            ('dot_config/run_onchange_after_reload.sh.tmpl', 'script'),
            ('.chezmoiscripts/setup.sh', 'script'),
            ('dot_config/.chezmoiscripts/run_x.sh', 'script'),
            ('encrypted_private_dot_netrc.age', 'encrypted'),
            ('dot_config/create_encrypted_token', 'encrypted'),
            ('.chezmoiexternal.toml', 'external'),
            ('dot_config/.chezmoiexternal.yaml.tmpl', 'external'),
            ('.chezmoiexternals/plugins.toml', 'external'),
            ('dot_vim/external_pack/start/x.vim', 'external folder'),
            ('.chezmoiremove', 'removal list'),
            ('dot_config/.chezmoiremove', 'removal list'),
            ('home/.chezmoiroot', 'nested root'),
            ('.chezmoiattributes', 'unknown special'),
            ('.chezmoidata.yaml.tmpl', 'unknown special'),
            ('dot_config/.chezmoikeep/x', 'unknown special'),
        )
        self.assertEqual({feature for _, feature in cases}, set(REFUSED))
        for name, feature in cases:
            with self.subTest(name=name):
                [problem] = refusals([name])
                self.assertIn(name, problem)
                self.assertIn(REFUSED[feature], problem)

    def test_one_message_per_refused_path(self):
        self.assertEqual(len(refusals(['run_a.sh', 'dot_bashrc', '.chezmoiscripts/b.sh', 'encrypted_c'])), 3)

    def test_look_alikes_and_supported_features_pass(self):
        names = [
            'literal_run_me',
            'dot_runner',
            'dot_private_x',
            'run_folder/dot_x',
            'private_encrypted_notes',
            'symlink_encrypted_link',
            'private_external_x/y',
            'remove_dot_old',
            'remove_dot_gone/.keep',
            'dot_config/exact_app/kept.conf',
            'create_dot_x',
            'dot_config/app/modify_state.ini',
            'symlink_dot_link',
            '.chezmoiignore',
            '.chezmoiignore.tmpl',
            'dot_config/.chezmoiignore',
            '.chezmoidata.yaml',
            '.chezmoidata.yml',
            '.chezmoidata.jsonc',
            '.chezmoidata.toml',
            '.chezmoidata/more.json',
            '.chezmoitemplates/run_me',
            '.chezmoiroot',
            '.chezmoiversion',
            '.chezmoi.toml.tmpl',
            '.chezmoi.yaml.tmpl',
            '.github/workflows/run_tests.yml',
            'README.md',
        ]
        self.assertEqual(refusals(names), [])


CLAIMS = Claims(
    paths={
        '.config': Claim('folder', 'upstream', 'home/dot_config', Mode(False, False)),
        '.config/fish': Claim('folder', 'upstream', 'home/dot_config/fish', Mode(False, False)),
        '.config/fish/config.fish': Claim('file', 'upstream', 'home/dot_config/fish/config.fish'),
        '.config/niri': Claim('folder', 'upstream', 'home/dot_config/niri'),
        '.config/niri/user.kdl': Claim('file', 'overrides', 'niri'),
        '.config/autostart': Claim('folder', 'upstream', 'home/dot_config/autostart'),
        '.config/autostart/Nextcloud.desktop': Claim('file', 'integration', 'nextcloud'),
        '.local': Claim('folder', 'upstream', 'home/private_dot_local', Mode(True, False)),
        '.local/bin': Claim('folder', 'upstream', 'home/private_dot_local/bin'),
        '.local/bin/example-launcher': Claim('file', 'assets', '.local/bin/example-launcher'),
        '.ssh': Claim('folder', 'upstream', 'home/private_dot_ssh', Mode(True, False)),
        '.docs': Claim('folder', 'upstream', 'home/readonly_dot_docs', Mode(False, True)),
        'Wallpapers': Claim('folder', 'assets', 'Wallpapers/photo.jpg'),
    },
    patterns={'.ssh/**': Claim('folder', 'integration', 'ssh')},
)


def conflicts(target, kind='file', claims=CLAIMS, **modes):
    return _conflicts(claims, [Entry(target, 'mine', kind, **modes)])


class ConflictTests(unittest.TestCase):
    def test_a_claimed_file_is_refused_naming_its_owner(self):
        for target, owner in (
            ('.config/fish/config.fish', 'upstream: home/dot_config/fish/config.fish'),
            ('.config/niri/user.kdl', 'overrides.niri'),
            ('.local/bin/example-launcher', 'assets'),
            ('.config/autostart/Nextcloud.desktop', 'integration nextcloud'),
        ):
            for kind in ('file', 'symlink', 'folder'):
                with self.subTest(target=target, kind=kind):
                    [problem] = conflicts(target, kind)
                    self.assertIn(f'~/{target} ', problem)
                    self.assertIn(owner, problem)

    def test_a_claimed_folder_takes_only_a_folder_with_its_attributes(self):
        self.assertEqual(conflicts('.config/fish', 'folder'), [])
        self.assertEqual(conflicts('.local', 'folder', private=True), [])
        self.assertEqual(conflicts('.docs', 'folder', readonly=True), [])
        for target, kind, modes, named in (
            ('.local', 'folder', {}, 'private'),
            ('.config', 'folder', {'private': True}, 'private'),
            ('.docs', 'folder', {}, 'readonly_'),
            ('.config/fish', 'folder', {'exact': True}, 'exact_'),
            ('.config/fish', 'file', {}, 'mine'),
            ('.config/fish', 'symlink', {}, 'mine'),
        ):
            with self.subTest(target=target, kind=kind, modes=modes):
                [problem] = conflicts(target, kind, **modes)
                self.assertIn(f'~/{target} ', problem)
                self.assertIn('upstream: home/', problem)
                self.assertIn(named, problem)

    def test_a_folder_shaffy_os_sets_no_mode_on_takes_any_mode_but_stays_a_folder(self):
        self.assertEqual(conflicts('Wallpapers', 'folder', private=True), [])
        [problem] = conflicts('Wallpapers', 'folder', readonly=True)
        self.assertIn('readonly_', problem)
        for kind, modes in (('file', {}), ('symlink', {}), ('folder', {'exact': True})):
            with self.subTest(kind=kind, modes=modes):
                [problem] = conflicts('Wallpapers', kind, **modes)
                self.assertIn('assets: ~/Wallpapers/photo.jpg', problem)

    def test_anything_below_a_claimed_pattern_is_refused(self):
        for target, kind in (('.ssh/config', 'file'), ('.ssh/keys/id', 'file'), ('.ssh/keys', 'folder')):
            with self.subTest(target=target):
                [problem] = conflicts(target, kind, private=True)
                self.assertIn(f'~/{target} ', problem)
                self.assertIn('.ssh/**', problem)
                self.assertIn('integration ssh', problem)

    def test_a_patterns_root_is_a_folder_and_its_neighbours_are_free(self):
        self.assertEqual(conflicts('.ssh', 'folder', private=True), [])
        self.assertEqual(len(conflicts('.ssh', 'folder')), 1)
        self.assertEqual(conflicts('.sshx/config'), [])

    def test_a_removal_at_or_above_a_claim_is_refused(self):
        for target, owner in (
            ('.config/fish/config.fish', 'upstream: home/dot_config/fish/config.fish'),
            ('.config', 'upstream: home/dot_config'),
            ('.ssh/known_hosts', 'integration ssh'),
        ):
            with self.subTest(target=target):
                [problem] = conflicts(target, 'remove')
                self.assertIn(f'~/{target} ', problem)
                self.assertIn(owner, problem)
        self.assertEqual(conflicts('.bash_history', 'remove'), [])

    def test_unclaimed_targets_pass_and_each_conflict_is_one_message(self):
        free = [Entry(target, 'mine', 'file') for target in ('.config/fish/conf.d/50-mine.fish', '.gitconfig')]
        refused = [Entry('.config/fish/config.fish', 'mine', 'file'), Entry('.ssh/config', 'mine', 'file')]
        self.assertEqual(_conflicts(CLAIMS, free), [])
        self.assertEqual(len(_conflicts(CLAIMS, free + refused)), 2)


class PrepareTests(unittest.TestCase):
    def test_the_dotfiles_pass_reads_only_a_capture(self):
        shaffy = load(ROOT, supplement=FIXTURE)['shaffy']
        with self.assertRaisesRegex(ValueError, 'only from a capture'):
            prepare(ROOT, shaffy)

    def test_without_the_setting_there_is_no_pass(self):
        self.assertEqual(prepare(ROOT, load(ROOT, without=True)['shaffy']), (None, []))


@unittest.skipUnless(shutil.which('chezmoi'), 'chezmoi is not installed')
class CapturedPrepareTests(unittest.TestCase):
    """prepare() on a capture of the comprehensive fixture, in a home of its own."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.supplement = self.root / 'supplement'
        shutil.copytree(FIXTURE, self.supplement)
        (self.root / 'home').mkdir()
        kept = {name: value for name, value in os.environ.items() if name not in XDG_FOLDERS}
        environ = {**kept, 'HOME': str(self.root / 'home'), 'SHAFFY_OP': 'op-outside'}
        patcher = mock.patch.dict(os.environ, environ, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def prepared(self):
        selection = {'mode': 'supplement', 'path': str(self.supplement), 'source': 'explicit'}
        made = capture(ROOT, selection, self.root / 'state', 'op-1', load, dotfiles=True)
        return prepare(ROOT, json.loads((Path(made['directory']) / 'settings.json').read_text())['shaffy'])

    def test_the_pass_has_its_own_configuration_state_and_no_shaffy_variable(self):
        chezmoi, problems = self.prepared()
        self.assertEqual(problems, [])
        self.assertEqual(chezmoi.name, 'dotfiles')
        self.assertFalse([name for name in chezmoi.env if name.startswith('SHAFFY_')])
        config = json.loads((self.root / 'state/runs/op-1/dotfiles/chezmoi.json').read_text())
        self.assertEqual(config['persistentState'], str(self.root / 'home/.local/state/shaffy/dotfiles.boltdb'))
        self.assertEqual(config['cacheDir'], str(self.root / 'home/.cache/shaffy/dotfiles'))
        self.assertEqual(config['mode'], 'file')
        self.assertEqual(config['data']['shaffy']['settings']['identity']['email'], 'test@example.invalid')
        self.assertIs(config['warnings']['configFileTemplateHasChanged'], False)
        self.assertEqual((self.root / 'state/runs/op-1/dotfiles/chezmoi.json').stat().st_mode & 0o777, 0o600)

    def test_a_refused_name_stops_the_pass_before_chezmoi_reads_the_tree(self):
        (self.supplement / 'dotfiles/run_once_example.sh').write_text('#!/bin/sh\n')
        chezmoi, problems = self.prepared()
        self.assertIsNone(chezmoi)
        self.assertEqual(len(problems), 1)
        self.assertIn('run_once_example.sh is a script', problems[0])
        self.assertFalse((self.root / 'state/runs/op-1/dotfiles').exists())

    def test_the_dotfiles_folder_is_the_source_whatever_its_chezmoiroot_names(self):
        tree = self.supplement / 'dotfiles'
        (self.root / 'moved').mkdir()
        for path in list(tree.iterdir()):
            path.rename(self.root / 'moved' / path.name)
        (self.root / 'moved').rename(tree / 'home')
        (tree / '.chezmoiroot').write_text('home\n')
        chezmoi, problems = self.prepared()
        self.assertEqual(problems, [])
        source = chezmoi.command[chezmoi.command.index('--source') + 1]
        self.assertEqual(source, str(self.root / 'state/runs/op-1/capture/supplement/dotfiles'))

    def test_dotfiles_that_cannot_be_read_or_checked_are_refused_not_raised(self):
        (self.supplement / 'dotfiles/.chezmoiroot').write_bytes(b'\xff\n')
        chezmoi, problems = self.prepared()
        self.assertIsNone(chezmoi)
        self.assertIn('dotfiles: they cannot be checked', problems[0])
        (self.supplement / 'dotfiles/.chezmoiroot').unlink()
        failing = subprocess.CalledProcessError(1, ['chezmoi', 'target-path'], stderr='chezmoi: broken\n')
        with mock.patch('shaffy.dotfiles._target_paths', side_effect=failing):
            shutil.rmtree(self.root / 'state')
            chezmoi, problems = self.prepared()
        self.assertIsNone(chezmoi)
        self.assertIn('chezmoi: broken', problems[0])

    def test_a_preview_is_private_under_the_state_and_reads_a_copy_of_the_state(self):
        state = self.root / 'home/.local/state/shaffy'
        state.mkdir(parents=True)
        (state / 'dotfiles.boltdb').write_bytes(b'')
        with preview(ROOT, supplement=self.supplement) as (reference, chezmoi, problems):
            self.assertEqual((reference, problems), ('dotfiles', []))
            config = Path(chezmoi.command[chezmoi.command.index('--config') + 1])
            [folder] = [path for path in state.iterdir() if path.name.startswith('preview-')]
            self.assertTrue(config.is_relative_to(folder))
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            copy = Path(json.loads(config.read_text())['persistentState'])
            self.assertTrue(copy.is_relative_to(folder))
            self.assertTrue(copy.is_file())
        self.assertEqual([path.name for path in state.iterdir()], ['dotfiles.boltdb'])

    def test_overlap_comes_back_with_the_pass(self):
        (self.supplement / 'dotfiles/dot_config/fish/config.fish').write_text('# mine\n')
        chezmoi, problems = self.prepared()
        self.assertEqual(chezmoi.name, 'dotfiles')
        self.assertEqual(len(problems), 1)
        self.assertIn('~/.config/fish/config.fish ', problems[0])


class EntryTests(unittest.TestCase):
    def test_kinds_and_modes_come_from_the_source_on_disk_and_its_name(self):
        with tempfile.TemporaryDirectory() as directory:
            source, home = Path(directory) / 'source', Path(directory) / 'home'
            sources = {
                '.local': 'private_dot_local',
                '.config': 'dot_config',
                '.config/app': 'dot_config/exact_readonly_app',
                '.config/app/kept.conf': 'dot_config/exact_readonly_app/private_kept.conf',
                '.link': 'symlink_dot_link',
                '.old': 'remove_dot_old',
                '.gone': 'remove_dot_gone',
            }
            for relative in ('private_dot_local', 'dot_config/exact_readonly_app', 'remove_dot_gone'):
                (source / relative).mkdir(parents=True)
            for relative in ('dot_config/exact_readonly_app/private_kept.conf', 'symlink_dot_link', 'remove_dot_old'):
                (source / relative).write_text('x\n')
            listing = {
                target: {
                    'absolute': str(home / target),
                    'sourceAbsolute': str(source / relative),
                    'sourceRelative': relative,
                }
                for target, relative in sources.items()
            }
            listing['.config/app/stale'] = {'absolute': str(home / '.config/app/stale')}  # pruned by the exact folder
            entries = _entries(json.dumps(listing))
        self.assertEqual(
            {entry.target: (entry.kind, entry.private, entry.readonly, entry.exact) for entry in entries},
            {
                '.local': ('folder', True, False, False),
                '.config': ('folder', False, False, False),
                '.config/app': ('folder', False, True, True),
                '.config/app/kept.conf': ('file', True, False, False),
                '.link': ('symlink', False, False, False),
                '.old': ('remove', False, False, False),
                '.gone': ('remove', False, False, False),
            },
        )


def claims_for(supplement, **environ):
    """Shaffy OS's claims for SUPPLEMENT in a home of its own; ENVIRON may name folders under {home}."""
    shaffy = load(ROOT, supplement=supplement)['shaffy']
    with tempfile.TemporaryDirectory() as home:
        kept = {name: value for name, value in os.environ.items() if name not in XDG_FOLDERS}
        chosen = {name: value.format(home=home) for name, value in environ.items()}
        with mock.patch.dict(os.environ, {**kept, 'HOME': home, **chosen}, clear=True):
            return _claims(ROOT, shaffy), shaffy


@unittest.skipUnless(shutil.which('chezmoi'), 'chezmoi is not installed')
class ClaimTests(unittest.TestCase):
    """Shaffy OS's claims, derived from this checkout and the comprehensive fixture."""

    @classmethod
    def setUpClass(cls):
        cls.claims, cls.shaffy = claims_for(FIXTURE)

    def refused(self, target, kind='file', **modes):
        return conflicts(target, kind, self.claims, **modes)

    def assertOwner(self, target, owner, kind='file', **modes):
        problems = self.refused(target, kind, **modes)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(f'~/{target} ', problems[0])
        self.assertIn(owner, problems[0])

    def test_files_name_their_upstream_source_or_fragment(self):
        self.assertOwner('.config/fish/config.fish', 'upstream: home/dot_config/fish/config.fish')
        self.assertOwner('.config/niri/user.kdl', 'overrides.niri')
        self.assertOwner('.config/fish/supplement.fish', 'overrides.fish')

    def test_integration_targets_are_claimed_in_every_state_but_released(self):
        # In the fixture ssh is managed, zettlr disabled and backup not requested at all.
        self.assertOwner('.ssh/config', 'integration ssh')
        self.assertOwner('.ssh/known_hosts', 'integration ssh')
        self.assertOwner('.config/autostart/shaffy-proton-pass.desktop', 'integration ssh')
        self.assertOwner('.config/Zettlr/config.json', 'integration zettlr')
        self.assertOwner('.config/systemd/user/timers.target.wants/restic-home-backup.timer', 'integration backup')

    def test_a_released_integration_yields_its_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            supplement = Path(directory) / 'supplement'
            shutil.copytree(FIXTURE, supplement)
            config = (supplement / 'config.yaml').read_text()
            self.assertEqual(config.count('  ssh: managed\n'), 1)
            (supplement / 'config.yaml').write_text(config.replace('  ssh: managed\n', '  ssh: released\n'))
            claims, shaffy = claims_for(supplement)
        self.assertEqual(shaffy['integrations']['ssh']['state'], 'released')
        for target in ('.ssh/config', '.ssh/known_hosts', '.config/autostart/shaffy-proton-pass.desktop'):
            with self.subTest(target=target):
                self.assertEqual(conflicts(target, claims=claims), [])
        # ~/.ssh itself stays Shaffy OS's private folder; the other integrations keep theirs.
        self.assertEqual(conflicts('.ssh', 'folder', claims, private=True), [])
        self.assertEqual(len(conflicts('.ssh', 'folder', claims)), 1)
        self.assertEqual(len(conflicts('.config/Zettlr/config.json', claims=claims)), 1)

    def test_shared_folders_carry_their_attributes(self):
        self.assertEqual(self.refused('.local', 'folder', private=True), [])
        self.assertOwner('.local', 'upstream: home/private_dot_local', 'folder')
        self.assertEqual(self.refused('.config/fish', 'folder'), [])
        self.assertOwner('.config/fish', 'upstream: home/dot_config/fish', 'folder', exact=True)

    def test_retired_targets_the_desktop_key_and_the_assets_are_claimed(self):
        lines = (ROOT / 'home/.chezmoiremove').read_text().splitlines()
        retired = [line for line in lines if line and not line.startswith('#')]
        self.assertTrue(retired)
        for target in retired:
            with self.subTest(target=target):
                self.assertOwner(target, 'upstream: home/.chezmoiremove')
        self.assertOwner(desktop_key.TARGET, 'upstream')
        assets = parse(FIXTURE / 'config.yaml')['assets']
        for target in assets:
            with self.subTest(target=target):
                self.assertOwner(target.removeprefix('~/'), 'assets')
        self.assertOwner('Wallpapers', 'assets')
        self.assertEqual(self.refused('Wallpapers', 'folder', private=True), [])
        self.assertEqual(self.refused('.cache', 'folder', private=True), [])

    def test_state_cache_and_plain_chezmoi_folders_are_claimed_whole(self):
        for target in (
            '.config/chezmoi/chezmoi.toml',
            '.local/share/chezmoi/dot_bashrc',
            '.local/state/shaffy/dotfiles.boltdb',
            '.cache/shaffy/dotfiles/x',
        ):
            with self.subTest(target=target):
                self.assertEqual(len(self.refused(target)), 1)
        self.assertEqual(self.refused('.config/chezmoi', 'folder'), [])

    def test_the_state_folder_is_where_xdg_state_home_puts_it(self):
        claims, _ = claims_for(FIXTURE, XDG_STATE_HOME='{home}/state')
        self.assertEqual(len(conflicts('state/shaffy/dotfiles.boltdb', claims=claims)), 1)
        # chezmoi.toml keeps Shaffy OS's own chezmoi state under ~/.local/state/shaffy all the same.
        self.assertEqual(len(conflicts('.local/state/shaffy/chezmoistate.boltdb', claims=claims)), 1)

    def test_an_exact_folder_owns_everything_it_holds(self):
        # home/ has no exact_ folder today, so a small checkout holds one.
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            (checkout / 'home/dot_config/exact_private_app').mkdir(parents=True)
            (checkout / 'home/dot_config/exact_private_app/kept.conf').write_text('kept\n')
            (checkout / 'home/.chezmoiremove').write_text('')
            shutil.copy(ROOT / 'chezmoi.toml', checkout)
            with mock.patch.dict(os.environ, {'HOME': directory}):
                claims = _claims(checkout, {'integrations': {}, 'assets': []})
        for target in ('.config/app/mine.conf', '.config/app/kept.conf', '.config/app/sub/x'):
            with self.subTest(target=target):
                [problem] = conflicts(target, claims=claims)
                self.assertIn('upstream: home/dot_config/exact_private_app', problem)
        self.assertEqual(conflicts('.config/app', 'folder', claims, private=True), [])
        self.assertEqual(conflicts('.config/other.conf', claims=claims), [])

    def test_drop_ins_and_new_files_are_the_dotfiles_own(self):
        for target in (
            '.config/fish/conf.d/50-mine.fish',
            '.config/mise/conf.d/50-mine.toml',
            '.config/jj/config.toml',
        ):
            with self.subTest(target=target):
                self.assertEqual(self.refused(target), [])

    def test_fragment_owners_are_the_schemas_home_overrides(self):
        schema = json.loads((ROOT / 'schema/supplement.schema.json').read_text())
        home_overrides = set(schema['properties']['overrides']['properties']) - set(SYSTEM_OVERRIDES)
        owners = {claim.via for claim in self.claims.paths.values() if claim.owner == 'overrides'}
        self.assertEqual(owners, home_overrides)

    def test_every_catalogue_target_is_a_literal_or_a_glob_the_claims_hold(self):
        for name, integration in self.shaffy['integrations'].items():
            for target in integration['targets']:
                with self.subTest(target=target):
                    self.assertNotRegex(target, r'[{}]')  # PurePosixPath.full_match has no braces
                    claim = self.claims.patterns.get(target) or self.claims.paths.get(target)
                    self.assertEqual((claim.owner, claim.via), ('integration', name))


# Names whose prefixes chezmoi reads one way or another; each folder holds `executable_c`.
FOLDERS = (
    'dot_a',
    'private_dot_b',
    'exact_c',
    'readonly_d',
    'remove_dot_e',
    'external_f',
    'external_private_g',
    'private_external_h',
    'exact_private_readonly_dot_i',
    'readonly_private_j',
    'dot_exact_k',
    'literal_exact_l',
    'private_literal_dot_m',
    'run_n',
    'encrypted_o',
    'symlink_p',
)
FILES = (
    'dot_a1',
    'private_dot_b1',
    'executable_private_c1',
    'private_executable_d1',
    'readonly_e1',
    'empty_f1',
    'encrypted_private_g1',
    'create_private_h1',
    'create_encrypted_i1',
    'modify_private_j1',
    'modify_empty_k1',
    'remove_dot_l1',
    'remove_private_m1',
    'run_n1',
    'run_once_before_o1',
    'run_onchange_after_p1',
    'run_before_once_q1',
    'run_dot_q2',
    'symlink_dot_r1',
    'symlink_private_s1',
    'symlink_encrypted_s2',
    'literal_run_t1',
    'dot_runner',
    'dot_private_v1',
    'private_literal_executable_w1',
    'private_encrypted_y1',
    'executable_readonly_z1',
    'readonly_executable_z2',
    'empty_private_z3',
    'exact_z4',
    'external_z5',
    'create_literal_private_z9',
    'encrypted_private_readonly_empty_executable_dot_y5',
)
MODIFY = '{{- /* chezmoi:modify-template */ -}}\nmodified\n'


def derived_target(source, folder):
    """The target of SOURCE (a FOLDER or not) as _attributes reads it: prefixes off, dot_ to a dot, nothing
    read below external_."""
    names, literal = [], False
    parts = PurePosixPath(source).parts
    for depth, part in enumerate(parts):
        attributes = () if literal else _attributes(part, folder or depth < len(parts) - 1)
        name = part
        for attribute in attributes:
            name = name.removeprefix(attribute + '_')
        names.append('.' + name if 'dot' in attributes else name if literal else name.removeprefix('literal_'))
        literal = literal or 'external' in attributes
    return '/'.join(names)


@unittest.skipUnless(shutil.which('chezmoi'), 'chezmoi is not installed')
class ChezmoiAgreementTests(unittest.TestCase):
    """The name parser against the real chezmoi on a corpus of names, in a throwaway home."""

    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source, home = root / 'source', root / 'home'
        for folder in FOLDERS:
            (source / folder).mkdir(parents=True)
            (source / folder / 'executable_c').write_text('c\n')
        for name in FILES:
            (source / name).write_text(MODIFY if name.startswith('modify_') else 'x\n')
        (source / '.chezmoiscripts').mkdir()
        (source / '.chezmoiscripts/run_b.sh').write_text('#!/bin/sh\n')
        home.mkdir()
        config = root / 'chezmoi.json'
        config.write_text(json.dumps({'persistentState': str(root / 'state.boltdb')}))

        def chezmoi(*arguments):
            return subprocess.run(
                [
                    'chezmoi',
                    '--config',
                    str(config),
                    '--config-format',
                    'json',
                    '--source',
                    str(source),
                    '--destination',
                    str(home),
                    *arguments,
                ],
                capture_output=True,
                text=True,
                env={'HOME': str(home), 'PATH': os.environ['PATH']},
                check=True,
            ).stdout

        def included(kind):
            return set(chezmoi('managed', '--include', kind, '--path-style', 'relative').splitlines())

        listing = json.loads(chezmoi('managed', '--include', 'all', '--format', 'json', '--path-style', 'all'))
        cls.targets = {found['sourceRelative']: target for target, found in listing.items()}
        cls.files = sorted(str(path.relative_to(source)) for path in source.rglob('*') if path.is_file())
        cls.dirs, cls.symlinks = included('dirs'), included('symlinks')
        cls.scripts, cls.encrypted = included('scripts'), included('encrypted')
        # chezmoi lists what an exact_ folder would prune: a planted file in each folder shows which.
        for folder in cls.dirs:
            (home / folder).mkdir(parents=True, exist_ok=True)
            (home / folder / 'planted').write_text('p\n')
        cls.removed = included('remove')
        cls.exact = {str(PurePosixPath(target).parent) for target in cls.removed if target.endswith('/planted')}
        cls.modes = {
            target: found['perm']
            for target, found in json.loads(
                chezmoi('dump', '--exclude', 'encrypted,scripts', '--format', 'json')
            ).items()
            if 'perm' in found
        }
        cls.entries = _entries(
            chezmoi('managed', '--include', 'dirs,files,symlinks,remove', '--format', 'json', '--path-style', 'all')
        )
        # A folder whose child kept its prefix is one chezmoi reads without prefixes, an external_ folder.
        cls.literal = {
            str(PurePosixPath(target).parent) for target in cls.targets.values() if target.endswith('/executable_c')
        }

    def test_attributes_end_where_chezmoi_ends_them(self):
        self.assertLessEqual({*FOLDERS, *FILES}, set(self.targets))
        self.assertEqual(
            {source: derived_target(source, target in self.dirs) for source, target in self.targets.items()},
            self.targets,
        )

    def test_refusals_are_what_chezmoi_would_run_decrypt_or_read_without_prefixes(self):
        expected = {
            source
            for source, target in self.targets.items()
            if target in self.scripts | self.encrypted or str(PurePosixPath(target).parent) in self.literal
        }
        self.assertLessEqual(
            {'run_n1', '.chezmoiscripts/run_b.sh', 'create_encrypted_i1', 'external_f/executable_c'}, expected
        )
        self.assertEqual({name for name in self.files if refusals([name])}, expected)

    def test_entries_read_kinds_and_modes_as_chezmoi_applies_them(self):
        def expected(entry):
            if entry.target in self.removed:
                kind = 'remove'
            elif entry.target in self.symlinks:
                kind = 'symlink'
            elif entry.target in self.dirs:
                # chezmoi 2.74 lists and applies a remove_ folder as a folder; a removal is the stricter reading.
                kind = 'remove' if PurePosixPath(entry.source).name.startswith('remove_') else 'folder'
            else:
                kind = 'file'
            mode = self.modes.get(entry.target, 0o777)
            return kind, not mode & 0o077, not mode & 0o222, entry.target in self.exact

        checked = [
            entry
            for entry in self.entries
            if entry.target not in self.encrypted
            and not ({entry.target, str(PurePosixPath(entry.target).parent)} & self.literal)
        ]
        self.assertGreater(len(checked), len(FOLDERS) + len(FILES))
        self.assertEqual(self.exact, {'c', '.i'})
        self.assertEqual(
            {entry.target: (entry.kind, entry.private, entry.readonly, entry.exact) for entry in checked},
            {entry.target: expected(entry) for entry in checked},
        )


if __name__ == '__main__':
    unittest.main()
