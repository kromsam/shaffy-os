"""Settings loader contract."""

import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shaffy.fastload import load_data
from shaffy.loader import SettingsError, check_reference, load
from shaffy.selection import SelectionError
from support import host_test, serial, vendor_profile

FIXTURES = ROOT / 'tests/fixtures/supplements'


class LoaderCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.tmp = Path(self.temporary.name)
        self.home = self.tmp / 'home'
        self.home.mkdir()
        self.addCleanup(self.temporary.cleanup)

    def supplement(self, text, name=None):
        self.count = getattr(self, 'count', 0) + 1
        path = self.tmp / (name or f'supplement-{self.count}')
        path.mkdir()
        (path / 'config.yaml').write_text(text)
        return path

    def load(self, **kwargs):
        return load(ROOT, home=self.home, **kwargs)

    def problems(self, text):
        with self.assertRaises(SettingsError) as caught:
            self.load(supplement=self.supplement(text))
        return '\n'.join(caught.exception.problems)


class ValidInputTests(LoaderCase):
    def test_defaults_alone_are_valid(self):
        data = self.load(without=True)['shaffy']
        self.assertEqual(data['selection']['mode'], 'none')
        self.assertIn('zotero', data['settings']['ai']['mcp_servers'])
        self.assertNotIn('folders', data['settings'])

    def test_minimal_example_is_valid_and_changes_nothing(self):
        data = self.load(supplement=ROOT / 'supplement.example')['shaffy']
        self.assertEqual(data['selection']['supplement_id'], 'my-desktop')
        self.assertEqual(data['settings'], self.load(without=True)['shaffy']['settings'])

    def test_comprehensive_fixture_overrides_and_extends(self):
        data = self.load(supplement=FIXTURES / 'comprehensive')['shaffy']
        servers = data['settings']['ai']['mcp_servers']
        self.assertEqual(servers['zotero']['env'], {'ZOTERO_LOCAL': 'true', 'ZOTERO_LOCAL_WRITE': 'true'})
        self.assertEqual(servers['zotero']['command'], '/usr/bin/mise')
        self.assertEqual(servers['notes']['args'], ['--read-only'])

    def test_the_beeper_server_is_off_until_a_supplement_switches_it_on(self):
        servers = self.load(without=True)['shaffy']['settings']['ai']['mcp_servers']
        self.assertIs(servers['beeper']['enabled'], False)
        path = self.supplement(
            'schema_version: 1\nsupplement_id: t\nai:\n  mcp_servers:\n    beeper:\n      enabled: true\n'
        )
        beeper = self.load(supplement=path)['shaffy']['settings']['ai']['mcp_servers']['beeper']
        self.assertEqual(beeper, {'enabled': True, 'url': 'http://127.0.0.1:23373/v0/mcp'})

    def test_explicit_empty_list_is_kept(self):
        path = self.supplement(
            'schema_version: 1\nsupplement_id: t\nai:\n  mcp_servers:\n    zotero:\n      args: []\n'
        )
        servers = self.load(supplement=path)['shaffy']['settings']['ai']['mcp_servers']
        self.assertEqual(servers['zotero']['args'], [])

    def test_servers_can_be_explicitly_disabled_without_a_command(self):
        path = self.supplement(
            'schema_version: 1\nsupplement_id: t\n'
            'ai:\n  mcp_servers:\n    zotero:\n      enabled: false\n'
            '    other:\n      enabled: false\n'
        )
        servers = self.load(supplement=path)['shaffy']['settings']['ai']['mcp_servers']
        self.assertFalse(servers['zotero']['enabled'])
        self.assertEqual(servers['other'], {'enabled': False})

    def test_a_server_can_be_an_address_instead_of_a_command(self):
        path = self.supplement(
            'schema_version: 1\nsupplement_id: t\n'
            'ai:\n  mcp_servers:\n    chat:\n      url: "http://127.0.0.1:9000/mcp"\n'
        )
        servers = self.load(supplement=path)['shaffy']['settings']['ai']['mcp_servers']
        self.assertEqual(servers['chat'], {'url': 'http://127.0.0.1:9000/mcp'})


class FoldTests(LoaderCase):
    """The fold of the defaults and a supplement is today's merge; profiles add layers between."""

    def layers(self, supplement):
        from shaffy.loader import IDENTITY_KEYS, Layer, parse

        defaults = parse(ROOT / 'defaults/config.yaml')
        overrides = parse(supplement / 'config.yaml')

        def strip(data):
            return {k: v for k, v in data.items() if k not in IDENTITY_KEYS}

        return [
            Layer('defaults', 'defaults', 'defaults/config.yaml', None, '', strip(defaults)),
            Layer('supplement', 'supplement', 'config.yaml', supplement, '', strip(overrides)),
        ]

    def test_fold_of_defaults_and_supplement_equals_merge(self):
        from shaffy.loader import fold, merge, parse

        defaults = parse(ROOT / 'defaults/config.yaml')
        # A union list in the defaults would gather where merge replaces; the equivalence ends there.
        self.assertNotIn('extra', defaults.get('packages', {}))
        self.assertNotIn('lazyvim', defaults)
        for supplement in (FIXTURES / 'comprehensive', FIXTURES / 'vm', ROOT / 'supplement.example'):
            with self.subTest(supplement=supplement.name):
                layers = self.layers(supplement)
                settings, origin, problems = fold(layers)
                self.assertEqual(problems, [])
                expected = merge(
                    *({k: v for k, v in layer.data.items() if k not in ('overrides', 'assets')} for layer in layers)
                )
                self.assertEqual(settings, expected)
                self.assertEqual({layer.name for layer in origin.values()} <= {'defaults', 'supplement'}, True)

    def test_origin_names_the_layer_that_set_each_unit(self):
        from shaffy.loader import fold

        _, origin, _ = fold(self.layers(FIXTURES / 'comprehensive'))
        self.assertEqual(origin[('ai', 'mcp_servers', 'zotero')].name, 'supplement')
        self.assertEqual(origin[('ai', 'mcp_servers', 'beeper')].name, 'defaults')
        self.assertEqual(origin[('desktop', 'browser')].name, 'defaults')
        self.assertEqual(origin[('lazyvim', 'extras', 'lazyvim.plugins.extras.lang.ruby')].name, 'supplement')
        self.assertEqual(origin[('ai', 'claude_plugins', 'example-lsp@example-plugins')].name, 'supplement')


class InvalidInputTests(LoaderCase):
    def test_duplicate_key_names_line(self):
        message = self.problems('schema_version: 1\nsupplement_id: a\nsupplement_id: b\n')
        self.assertIn("duplicate key 'supplement_id'", message)
        self.assertIn('config.yaml:3:', message)

    def test_unknown_key(self):
        self.assertIn("unknown key 'colour'", self.problems('schema_version: 1\nsupplement_id: t\ncolour: blue\n'))

    def test_wrong_type(self):
        message = self.problems(
            'schema_version: 1\nsupplement_id: t\nai:\n  mcp_servers:\n    zotero:\n      args: serve\n'
        )
        self.assertIn('ai.mcp_servers.zotero.args: must be of type array', message)

    def test_claude_plugins_name_a_marketplace_repository_and_leave_pstack_to_its_switch(self):
        base = 'schema_version: 1\nsupplement_id: t\nai:\n  claude_plugins:\n'
        path = self.supplement(base + '    example-lsp@example-plugins: example/plugins\n')
        plugins = self.load(supplement=path)['shaffy']['settings']['ai']['claude_plugins']
        self.assertEqual(plugins, {'example-lsp@example-plugins': 'example/plugins'})
        self.assertIn('example-lsp', self.problems(base + '    example-lsp: example/plugins\n'))
        for name in ('pstack@pstack-claude', 'fork@pstack-claude'):
            self.assertIn(name, self.problems(base + f'    {name}: a/b\n'))

    def test_yaml_1_2_typing_keeps_yes_a_string_and_rejects_bare_true(self):
        # "yes" is a string in YAML 1.2, as editors read it; bare true is a boolean.
        text = (
            'schema_version: 1\nsupplement_id: t\nai:\n  mcp_servers:\n    zotero:\n'
            '      env:\n        ZOTERO_LOCAL: yes\n        ZOTERO_LOCAL_WRITE: {}\n'
        )
        message = self.problems(text.replace('{}', 'true'))
        self.assertIn('ZOTERO_LOCAL_WRITE: must be of type string, found bool True', message)
        self.assertNotIn('ZOTERO_LOCAL:', message)

    def test_yaml_1_1_octal_and_sexagesimal_are_not_reinterpreted(self):
        from shaffy.loader import _Loader
        import yaml

        values = yaml.load('mode: 0600\ntime: 1:20\nday: 2026-09-23\nflag: on\n', Loader=_Loader)
        self.assertEqual(values, {'mode': 600, 'time': '1:20', 'day': '2026-09-23', 'flag': 'on'})

    def test_unsupported_version(self):
        self.assertIn('schema_version: 2 is not supported', self.problems('schema_version: 2\nsupplement_id: t\n'))

    def test_missing_identity(self):
        self.assertIn("'supplement_id' is a required property", self.problems('schema_version: 1\n'))

    def test_null_and_empty_mappings_are_errors(self):
        self.assertIn(
            'ai: must be of type object, found null', self.problems('schema_version: 1\nsupplement_id: t\nai:\n')
        )
        self.assertIn('ai: must not be empty', self.problems('schema_version: 1\nsupplement_id: t\nai: {}\n'))

    def test_display_and_keyboard_values_cannot_inject_kdl(self):
        message = self.problems(
            'schema_version: 1\nsupplement_id: t\ndesktop:\n  displays:\n'
            "    - name: 'eDP-1\" { }'\n  keyboard:\n    layouts: 'us\" }'\n"
        )
        self.assertIn('desktop.displays.0.name', message)
        self.assertIn('desktop.keyboard.layouts', message)

    def test_override_references_stay_inside_the_supplement(self):
        message = self.problems(
            'schema_version: 1\nsupplement_id: t\noverrides:\n  niri: ../outside.kdl\n  fish: /etc/passwd\n'
        )
        self.assertIn('overrides.niri', message)
        self.assertIn('overrides.fish', message)
        missing = self.problems('schema_version: 1\nsupplement_id: t\noverrides:\n  niri: absent.kdl\n')
        self.assertIn("overrides.niri: 'absent.kdl' does not exist in the supplement", missing)

    def test_override_paths_are_resolved_and_listed_as_references(self):
        data = self.load(supplement=FIXTURES / 'comprehensive')['shaffy']
        self.assertEqual(data['overrides']['fish'], str(FIXTURES / 'comprehensive/overrides/fish.fish'))
        self.assertEqual(len(data['references']), 8)  # five overrides and three asset files
        self.assertNotIn('overrides', data['settings'])

    def test_identity_and_folders_are_validated(self):
        message = self.problems(
            'schema_version: 1\nsupplement_id: t\n'
            'identity:\n  name: \'A "quoted" name\'\n  email: not-an-email\n'
            "folders:\n  documents: 'Docs\"'\n  projects: Code\n"
        )
        for key in ('identity.name', 'identity.email', 'folders.documents', "unknown key 'projects'"):
            self.assertIn(key, message)

    def test_new_server_needs_a_command(self):
        message = self.problems(
            'schema_version: 1\nsupplement_id: t\nai:\n  mcp_servers:\n    other:\n      args: []\n'
        )
        self.assertIn('ai.mcp_servers.other: needs a command or a url', message)

    def test_a_server_is_a_command_or_an_address(self):
        start = 'schema_version: 1\nsupplement_id: t\nai:\n  mcp_servers:\n'
        for server, expected in (
            (
                '    zotero:\n      url: "http://127.0.0.1:9000/mcp"\n',
                'ai.mcp_servers.zotero: has both a command and a url',
            ),
            (
                '    chat:\n      url: "http://127.0.0.1:9000/mcp"\n      args: []\n',
                'ai.mcp_servers.chat: args and env need a command',
            ),
            ('    chat:\n      url: "file:///tmp/mcp"\n', 'ai.mcp_servers.chat.url'),
        ):
            with self.subTest(server=server):
                self.assertIn(expected, self.problems(start + server))

    def test_server_enabled_must_be_a_boolean(self):
        message = self.problems(
            'schema_version: 1\nsupplement_id: t\nai:\n  mcp_servers:\n    zotero:\n      enabled: "false"\n'
        )
        self.assertIn('ai.mcp_servers.zotero.enabled: must be of type boolean', message)


class MinimumReleaseTests(LoaderCase):
    REQUIRES = 'schema_version: 1\nsupplement_id: t\nminimum_release: {}\n'

    def at(self, found):
        return mock.patch('shaffy.loader.release', return_value=found)

    def test_older_checkout_is_refused_without_unknown_key_noise(self):
        with self.at('v2026.10.19'):
            message = self.problems(self.REQUIRES.format('v2026.10.20') + 'later_field: 1\n')
        self.assertIn('needs Shaffy OS v2026.10.20 or later; this checkout is at v2026.10.19', message)
        self.assertNotIn('unknown key', message)

    def test_same_day_releases_count_up(self):
        with self.at('v2026.10.19'):
            self.assertIn('is at v2026.10.19', self.problems(self.REQUIRES.format('v2026.10.19.1')))
        with self.at('v2026.10.19.1'):
            self.load(supplement=self.supplement(self.REQUIRES.format('v2026.10.19.1')))

    def test_met_minimum_is_not_a_setting(self):
        with self.at('v2026.11.02'):
            data = self.load(supplement=self.supplement(self.REQUIRES.format('v2026.10.20')))['shaffy']
        self.assertNotIn('minimum_release', data['settings'])

    def test_checkout_without_release_tags_is_refused(self):
        with self.at(None):
            self.assertIn('no release tag is reachable', self.problems(self.REQUIRES.format('v2026.10.20')))

    def test_malformed_minimum_is_a_schema_error(self):
        with self.at('v2026.10.19') as release:
            self.assertIn('minimum_release', self.problems(self.REQUIRES.format('2026.10.20')))
        release.assert_not_called()

    def test_release_is_the_newest_tag_head_contains(self):
        from shaffy.source import release

        repository = self.tmp / 'checkout'
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
            '-c',
            'tag.gpgsign=false',
        ]
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(repository)], check=True)
        for step in (
            ['commit', '-q', '--allow-empty', '-m', 'one'],
            ['tag', 'v2025.12.31'],
            ['commit', '-q', '--allow-empty', '-m', 'two'],
            ['tag', 'v2026.01.02'],
            ['tag', 'v2026.01.02.1'],
            ['tag', 'v2026.01.03-try'],
            ['tag', 'later'],
            ['switch', '-q', '-c', 'side'],
            ['commit', '-q', '--allow-empty', '-m', 'three'],
            ['tag', 'v2026.02.01'],
            ['switch', '-q', 'main'],
        ):
            subprocess.run([*git, *step], check=True)
        with mock.patch.dict(os.environ):
            os.environ.pop('SHAFFY_OP_MODE', None)
            self.assertEqual(release(repository), 'v2026.01.02.1')
            self.assertIsNone(release(self.home))

    def test_sandbox_takes_the_release_it_is_given(self):
        from shaffy.source import release

        with mock.patch.dict(os.environ, {'SHAFFY_OP_MODE': 'sandbox', 'SHAFFY_RELEASE': 'v2026.01.02'}):
            self.assertEqual(release(ROOT), 'v2026.01.02')
            del os.environ['SHAFFY_RELEASE']
            self.assertIsNone(release(ROOT))


class ReferenceTests(LoaderCase):
    def test_reference_inside_symlinked_supplement_is_accepted(self):
        real = self.supplement('schema_version: 1\nsupplement_id: t\n', 'real')
        (real / 'overrides').mkdir()
        (real / 'overrides/niri.kdl').write_text('')
        link = self.tmp / 'linked'
        link.symlink_to(real)
        self.assertEqual(check_reference(link, 'overrides/niri.kdl', 'test'), str(real / 'overrides/niri.kdl'))

    def test_escaping_references_fail_clearly(self):
        root = self.supplement('schema_version: 1\nsupplement_id: t\n')
        (self.tmp / 'outside.txt').write_text('secret')
        (root / 'escape').symlink_to(self.tmp / 'outside.txt')
        for reference, reason in (
            ('../outside.txt', 'resolves outside'),
            ('escape', 'resolves outside'),
            (str(self.tmp / 'outside.txt'), 'must be relative'),
            ('missing.kdl', 'does not exist'),
        ):
            with self.subTest(reference=reference), self.assertRaises(SettingsError) as caught:
                check_reference(root, reference, 'overrides.niri')
            self.assertIn(reason, str(caught.exception))


class DotfilesTests(LoaderCase):
    def dotfiles(self, reference):
        return self.supplement(f'schema_version: 1\nsupplement_id: t\ndotfiles: {json.dumps(reference)}\n')

    def test_a_folder_in_the_supplement_is_named_apart_from_the_settings(self):
        path = self.dotfiles('dots/')
        (path / 'dots').mkdir()
        data = self.load(supplement=path)['shaffy']
        self.assertEqual(data['dotfiles'], 'dots')
        self.assertNotIn('dotfiles', data['settings'])
        self.assertEqual(data['references'], [])  # references are files, which a root copy reads
        self.assertIsNone(self.load(supplement=ROOT / 'supplement.example')['shaffy']['dotfiles'])

    def test_a_folder_through_a_link_inside_the_supplement_is_accepted(self):
        path = self.dotfiles('dots')
        (path / 'repos/dots').mkdir(parents=True)
        (path / 'dots').symlink_to('repos/dots')
        self.assertEqual(self.load(supplement=path)['shaffy']['dotfiles'], 'dots')

    def test_anything_but_a_folder_inside_the_supplement_is_refused(self):
        (self.tmp / 'outside').mkdir()
        for reference, prepare, reason in (
            (
                'dots',
                lambda path: (path / 'dots').write_text('x'),
                "'dots' does not exist in the supplement as a folder",
            ),
            ('dots', lambda path: None, "'dots' does not exist in the supplement as a folder"),
            ('.', lambda path: None, "'.' is the supplement itself"),
            ('self', lambda path: (path / 'self').symlink_to('.'), "'self' is the supplement itself"),
            ('escape', lambda path: (path / 'escape').symlink_to(self.tmp / 'outside'), 'resolves outside'),
            (str(self.tmp / 'outside'), lambda path: None, 'dotfiles'),
            ('../outside', lambda path: None, 'dotfiles'),
        ):
            with self.subTest(reference=reference, reason=reason):
                path = self.dotfiles(reference)
                prepare(path)
                with self.assertRaises(SettingsError) as caught:
                    self.load(supplement=path)
                self.assertIn('dotfiles', str(caught.exception))
                self.assertIn(reason, str(caught.exception))

    def test_public_defaults_cannot_name_dotfiles(self):
        checkout = self.tmp / 'checkout'
        for folder in ('defaults', 'schema'):
            shutil.copytree(ROOT / folder, checkout / folder)
        with open(checkout / 'defaults/config.yaml', 'a') as stream:
            stream.write('dotfiles: dots\n')
        with self.assertRaises(SettingsError) as caught:
            load(checkout, without=True, home=self.home)
        self.assertIn("unknown key 'dotfiles'", str(caught.exception))

    def test_the_defaults_may_not_list_profiles(self):
        checkout = self.tmp / 'checkout'
        for folder in ('defaults', 'schema'):
            shutil.copytree(ROOT / folder, checkout / folder)
        with open(checkout / 'defaults/config.yaml', 'a') as stream:
            stream.write('profiles: [example/notes]\n')
        with self.assertRaises(SettingsError) as caught:
            load(checkout, without=True, home=self.home)
        self.assertIn("unknown key 'profiles'", str(caught.exception))

    def test_a_removed_or_escaping_folder_misses_the_cache(self):
        path = self.dotfiles('dots')
        (path / 'real').mkdir()
        (path / 'dots').symlink_to('real')
        (self.tmp / 'outside').mkdir()
        cache = self.tmp / 'cache'
        self.assertEqual(
            load_data(ROOT, supplement=path, home=self.home, directory=cache)['shaffy']['dotfiles'], 'dots'
        )
        (path / 'dots').unlink()
        (path / 'dots').symlink_to(self.tmp / 'outside')
        with self.assertRaisesRegex(SettingsError, 'dotfiles'):
            load_data(ROOT, supplement=path, home=self.home, directory=cache)
        (path / 'dots').unlink()
        with self.assertRaisesRegex(SettingsError, 'dotfiles'):
            load_data(ROOT, supplement=path, home=self.home, directory=cache)


class SelectionTests(LoaderCase):
    def test_both_choices_conflict(self):
        with self.assertRaises(SelectionError):
            self.load(supplement=ROOT / 'supplement.example', without=True)

    def test_missing_explicit_supplement_never_falls_back(self):
        with self.assertRaisesRegex(SelectionError, 'does not exist'):
            self.load(supplement=self.tmp / 'absent')

    def test_missing_bound_supplement_fails(self):
        state = self.home / '.local/state/shaffy'
        state.mkdir(parents=True)
        (state / 'binding.json').write_text(
            json.dumps({'binding': {'mode': 'supplement', 'path': str(self.tmp / 'moved'), 'supplement_id': 't'}})
        )
        with self.assertRaisesRegex(SelectionError, 'the bound supplement .* does not exist'):
            self.load()


class PackageTests(LoaderCase):
    def packages(self, text):
        return self.load(supplement=self.supplement('schema_version: 1\nsupplement_id: t\n' + text))['shaffy'][
            'packages'
        ]

    def test_the_public_groups_are_consistent(self):
        from shaffy.loader import parse
        from shaffy.packages import load_groups

        groups, retired, problems = load_groups(ROOT, parse)
        self.assertEqual(problems, [])
        self.assertEqual(
            set(groups),
            {'boot_splash', 'desktop', 'desktop_apps', 'home_backup', 'kde_connect', 'sync', 'workflows', 'writing'},
        )
        import yaml

        site = {
            entry['role']: entry.get('tags', [])
            for entry in yaml.safe_load((ROOT / 'site.yml').read_text())[0]['roles']
        }
        for name in groups:  # each group is installed by the role of the same name, which site.yml runs
            self.assertIn(
                f"shaffy['packages']['groups']['{name}']", (ROOT / f'roles/{name}/tasks/main.yml').read_text()
            )
            self.assertEqual(site.get(name), [name], f'site.yml does not run the {name} role under its tag')
        self.assertEqual(site.get('packages'), ['packages'])
        for name in retired:  # a retired package is installed by no group
            self.assertFalse(any(name in group['packages'] for group in groups.values()), name)

    def test_defaults_install_every_group_and_remove_only_the_retired(self):
        packages = self.load()['shaffy']['packages']
        self.assertIn('docker', packages['groups']['workflows'])
        self.assertEqual(packages['extra'], [])
        self.assertEqual(packages['remove'], [])
        self.assertIn('zotero', packages['groups']['writing'])
        aur = {'desktop_apps': [{'name': 'ttf-ms-fonts'}], 'workflows': [{'name': 'docker-rootless-extras'}]}
        self.assertEqual(packages['aur'], {group: aur.get(group, []) for group in packages['groups']})

    def test_choices_add_omit_and_remove(self):
        packages = self.packages('packages:\n  extra: [htop, zellij]\n  omit: [reaper]\n  remove: [reaper]\n')
        self.assertNotIn('reaper', packages['groups']['workflows'])
        self.assertEqual(packages['extra'], ['htop', 'zellij'])
        # Only listed packages are removed: the settings' and the public retirements.
        self.assertEqual(packages['remove'], ['reaper'])

    def test_public_retirements_are_removed_with_the_settings_removals(self):
        import shutil

        checkout = self.tmp / 'checkout'
        for part in ('defaults', 'schema'):
            shutil.copytree(ROOT / part, checkout / part)
        (checkout / 'defaults/packages/retired.yml').write_text('retired:\n  - example-retired\n')
        path = self.supplement('schema_version: 1\nsupplement_id: t\npackages:\n  remove: [example-old]\n')
        packages = load(checkout, home=self.home, supplement=path)['shaffy']['packages']
        self.assertEqual(packages['remove'], ['example-old', 'example-retired'])

    def test_aur_omissions_and_removals_follow_the_same_rules(self):
        packages = self.packages('packages:\n  omit: [ttf-ms-fonts]\n  remove: [ttf-ms-fonts]\n')
        self.assertEqual(packages['aur']['desktop_apps'], [])
        self.assertEqual(packages['remove'], ['ttf-ms-fonts'])

    def test_contradictory_choices_fail(self):
        for text, message in (
            ('packages:\n  omit: [docker]\n', 'packages.omit: docker is required by the workflows group'),
            (
                'packages:\n  omit: [docker-rootless-extras]\n',
                'packages.omit: docker-rootless-extras is required by the workflows group',
            ),
            ('packages:\n  omit: [slirp4netns]\n', 'packages.omit: slirp4netns is required by the workflows group'),
            ('packages:\n  omit: [htop]\n', 'packages.omit: htop is in no package group'),
            ('packages:\n  extra: [docker]\n', 'packages.extra: docker is already in the workflows group'),
            (
                'packages:\n  extra: [ttf-ms-fonts]\n',
                'packages.extra: ttf-ms-fonts is already in the desktop_apps group',
            ),
            (
                'packages:\n  remove: [ttf-ms-fonts]\n',
                'packages.remove: ttf-ms-fonts is also installed by the desktop_apps group; omit it too',
            ),
            ('packages:\n  remove: [reaper]\n', 'packages.remove: reaper is also installed by the workflows'),
            (
                'packages:\n  extra: [htop]\n  remove: [htop]\n',
                'packages.remove: htop is also installed by packages.extra',
            ),
            ('packages:\n  omit: [mise]\n  remove: [mise]\n', 'packages.remove: mise is needed by Shaffy OS itself'),
            ('packages:\n  remove: [sudo]\n', 'packages.remove: sudo is needed by Shaffy OS itself'),
            ('packages:\n  remove: [linux-cachyos]\n', 'packages.remove: linux-cachyos is needed by Shaffy OS itself'),
            ('packages:\n  extra: [Htop]\n', 'packages.extra'),
            ('packages:\n  extra: [htop, htop]\n', 'packages.extra'),
            ('packages: {}\n', 'packages'),
        ):
            with self.subTest(text=text):
                self.assertIn(message, self.problems('schema_version: 1\nsupplement_id: t\n' + text))

    def test_a_package_in_two_groups_fails(self):
        from shaffy.loader import parse
        from shaffy.packages import load_groups

        directory = self.tmp / 'checkout/defaults/packages'
        directory.mkdir(parents=True)
        (directory / 'one.yml').write_text('packages: [htop]\nrequired: [htop]\n')
        (directory / 'two.yml').write_text('packages: [htop, nano]\nrequired: [vim]\n')
        (directory / 'retired.yml').write_text('retired: [nano]\n')
        problems = '\n'.join(load_groups(self.tmp / 'checkout', parse)[2])
        self.assertIn('htop already belongs to the one group', problems)
        self.assertIn('required vim is not in its packages', problems)
        self.assertIn('nano is retired but belongs to the two group', problems)

    def test_aur_names_have_the_same_ownership_required_and_retirement_checks(self):
        from shaffy.loader import parse
        from shaffy.packages import load_groups, resolve

        directory = self.tmp / 'checkout/defaults/packages'
        directory.mkdir(parents=True)
        (directory / 'one.yml').write_text('packages: []\naur: [{name: probe-bin}]\nrequired: [probe-bin]\n')
        (directory / 'two.yml').write_text('packages: [probe-bin]\nrequired: []\n')
        (directory / 'retired.yml').write_text('retired: [probe-bin]\n')
        groups, retired, problems = load_groups(self.tmp / 'checkout', parse)
        self.assertIn('probe-bin already belongs to the one group', '\n'.join(problems))
        self.assertIn('probe-bin is retired but belongs to the two group', '\n'.join(problems))
        self.assertIn(
            'packages.omit: probe-bin is required by the one group',
            resolve(groups, retired, {'packages': {'omit': ['probe-bin']}})[1],
        )

    def test_malformed_aur_catalogue_is_reported_without_a_traceback(self):
        from shaffy.loader import parse
        from shaffy.packages import load_groups

        directory = self.tmp / 'checkout/defaults/packages'
        directory.mkdir(parents=True)
        for aur in (
            'null',
            '{}',
            '[probe-bin]',
            '[{}]',
            '[{name: null}]',
            '[{name: probe-bin, extra: true}]',
            '[{name: probe-bin, replaces: probe}]',
            '[{name: probe-bin}, {name: probe-bin}]',
        ):
            with self.subTest(aur=aur):
                (directory / 'one.yml').write_text(f'packages: []\nrequired: []\naur: {aur}\n')
                self.assertTrue(load_groups(self.tmp / 'checkout', parse)[2])


class AssetTests(LoaderCase):
    def test_assets_resolve_inside_the_supplement_with_their_digest(self):
        import hashlib

        supplement = self.supplement(
            'schema_version: 1\nsupplement_id: t\nassets:\n  "~/.local/share/fonts/A B.otf": fonts/a.otf\n'
        )
        (supplement / 'fonts').mkdir()
        (supplement / 'fonts/a.otf').write_bytes(b'font')
        data = self.load(supplement=supplement)['shaffy']
        path = str((supplement / 'fonts/a.otf').resolve())
        self.assertEqual(
            data['assets'],
            [
                {
                    'target': '.local/share/fonts/A B.otf',
                    'path': path,
                    'url': 'file://' + path.replace(' ', '%20'),
                    'sha256': hashlib.sha256(b'font').hexdigest(),
                    'executable': False,
                    'private': False,
                    'layer': 'supplement',
                }
            ],
        )
        self.assertIn(path, data['references'])
        self.assertIn('fonts/a.otf', data['reference_paths'])
        self.assertNotIn('assets', data['settings'])

    def test_system_fragments_travel_inside_the_settings(self):
        supplement = self.supplement(
            'schema_version: 1\nsupplement_id: t\noverrides:\n  keyboard_hwdb: keyboard.hwdb\n'
        )
        (supplement / 'keyboard.hwdb').write_text('evdev:input:b0003v0001p0002*\n KEYBOARD_KEY_70039=leftmeta\n')
        data = self.load(supplement=supplement)['shaffy']
        self.assertEqual(data['system_overrides'], {'keyboard_hwdb': (supplement / 'keyboard.hwdb').read_text()})
        self.assertEqual(self.load()['shaffy']['system_overrides'], {})

    def test_asset_attributes(self):
        supplement = self.supplement(
            'schema_version: 1\nsupplement_id: t\nassets:\n'
            '  "~/.local/bin/tool": {file: tool, executable: true}\n'
            '  "~/.config/x.conf": {file: tool, private: true}\n'
        )
        (supplement / 'tool').write_text('#!/bin/sh\n')
        assets = {a['target']: a for a in self.load(supplement=supplement)['shaffy']['assets']}
        self.assertEqual((assets['.local/bin/tool']['executable'], assets['.local/bin/tool']['private']), (True, False))
        self.assertEqual((assets['.config/x.conf']['executable'], assets['.config/x.conf']['private']), (False, True))
        self.assertIn(
            'assets', self.problems('schema_version: 1\nsupplement_id: t\nassets:\n  "~/x": {file: tool, mode: 0755}\n')
        )

    def test_bad_assets_fail(self):
        self.assertIn(
            'does not exist in the supplement',
            self.problems('schema_version: 1\nsupplement_id: t\nassets:\n  "~/x": missing.otf\n'),
        )
        self.assertIn('assets', self.problems('schema_version: 1\nsupplement_id: t\nassets:\n  "~/x": ../outside\n'))
        for target in ('/etc/x', '~/../x', '~/./x', '~/.config/../x', '~//etc/x', '~/a\\u0007b', '~/a\\"b'):
            with self.subTest(target=target):
                supplement = self.supplement(f'schema_version: 1\nsupplement_id: t\nassets:\n  "{target}": a\n')
                (supplement / 'a').write_text('present, so only the target can be wrong')
                with self.assertRaises(SettingsError) as caught:
                    self.load(supplement=supplement)
                self.assertIn('does not match', '\n'.join(caught.exception.problems))

    def test_an_unreadable_asset_is_a_settings_problem(self):
        supplement = self.supplement('schema_version: 1\nsupplement_id: t\nassets:\n  "~/x": a\n')
        (supplement / 'a').write_text('secret')
        (supplement / 'a').chmod(0)
        self.addCleanup((supplement / 'a').chmod, 0o600)
        if os.access(supplement / 'a', os.R_OK):
            self.skipTest('running as a user who reads any file')
        with self.assertRaises(SettingsError) as caught:
            self.load(supplement=supplement)
        self.assertIn("assets.~/x: 'a' cannot be read", '\n'.join(caught.exception.problems))


class IntegrationTests(LoaderCase):
    def integrations(self, text):
        return self.load(supplement=self.supplement('schema_version: 1\nsupplement_id: t\n' + text))['shaffy'][
            'integrations'
        ]

    def test_the_public_catalogue_is_consistent(self):
        from shaffy.integrations import load_catalogue
        from shaffy.loader import parse

        catalogue, problems = load_catalogue(ROOT, parse)
        self.assertEqual(problems, [])
        self.assertEqual(set(catalogue), {'backup', 'nextcloud', 'reaper', 'ssh', 'syncthing', 'zettlr', 'zotero'})
        for name, entry in catalogue.items():
            for record in entry['activation']:  # every link and entry it records is also a target
                path = record.get('link') or record.get('path')
                if path:
                    self.assertIn(path.removeprefix('~/'), entry['targets'], name)

    def test_an_integration_not_requested_stays_unmanaged(self):
        integrations = self.integrations('integrations:\n  syncthing: managed\n  reaper: released\n')
        self.assertEqual(integrations['syncthing']['state'], 'managed')
        self.assertEqual(integrations['reaper']['state'], 'released')
        self.assertIsNone(integrations['ssh']['state'])
        self.assertIsNone(self.load()['shaffy']['integrations']['syncthing']['state'])

    def test_unknown_integrations_and_states_fail(self):
        for text, message in (
            ('integrations:\n  dropbox: managed\n', 'integrations.dropbox: there is no such integration'),
            ('integrations:\n  syncthing: on\n', 'integrations.syncthing'),
            ('integrations:\n  Syncthing: managed\n', 'integrations'),
            ('integrations: {}\n', 'integrations'),
            ('integrations:\n  backup: managed\n', 'integrations.backup: managing it needs the backup settings'),
            (
                'backup: {host: x, tags: [a],'
                ' repository: {sftp: {host: h, user: u, path: p, host_key: "ssh-rsa AAA"}}}\n',
                'backup.repository.sftp.host_key',
            ),
            (
                'backup: {host: x, tags: [a],'
                ' repository: {sftp: {host: h, user: u, path: p, host_key: "ssh-ed25519 AAA"}},'
                ' excludes: [/etc]}\n',
                'backup.excludes',
            ),
            (
                'backup: {host: x, tags: [a],'
                ' repository: {sftp: {host: h, user: u, path: p, host_key: "ssh-ed25519 AAA"}},'
                ' excludes: ["~/.local/state/"]}\n',
                "where each backup's verification marker is written",
            ),
            (
                'backup: {host: x, tags: [a],'
                ' repository: {sftp: {host: h, user: u, path: p, host_key: "ssh-ed25519 AAA"}},'
                ' excludes: ["~/.local"]}\n',
                'verification marker',
            ),
        ):
            with self.subTest(text=text):
                self.assertIn(message, self.problems('schema_version: 1\nsupplement_id: t\n' + text))

    def test_a_malformed_catalogue_fails(self):
        from shaffy.integrations import load_catalogue
        from shaffy.loader import parse

        directory = self.tmp / 'checkout/defaults'
        directory.mkdir(parents=True)
        (directory / 'integrations.yml').write_text(
            'Bad: {scope: home, targets: []}\n'
            'noscope: {targets: []}\n'
            'badlink: {scope: home, targets: [a], activation: [{kind: wants-link, unit: x}]}\n'
            'outside: {scope: home, targets: [a], activation: [{kind: autostart, path: /etc/x}]}\n'
        )
        problems = '\n'.join(load_catalogue(self.tmp / 'checkout', parse)[1])
        self.assertIn('Bad: an integration name', problems)
        self.assertIn('noscope: needs a scope', problems)
        self.assertIn("badlink: an activation record needs kind and exactly ['link', 'unit']", problems)
        self.assertIn('outside: a home activation path starts with ~/', problems)


class ProfileTests(LoaderCase):
    """Vendored profiles are layers between the defaults and the supplement, which wins."""

    HEAD = 'schema_version: 1\nsupplement_id: t\n'
    MANIFEST = 'schema_version: 1\ndescription: Example settings.\n'

    def listing(self, *ids, extra=''):
        return self.supplement(self.HEAD + 'profiles:\n' + ''.join(f'  - {i}\n' for i in ids) + extra)

    def vendor(self, supplement, profile_id, manifest='', files=None, **kw):
        return vendor_profile(supplement, profile_id, {'profile.yaml': self.MANIFEST + manifest, **(files or {})}, **kw)

    def refused(self, supplement):
        with self.assertRaises(SettingsError) as caught:
            self.load(supplement=supplement)
        return '\n'.join(caught.exception.problems)

    def test_a_profile_sits_between_the_defaults_and_the_supplement(self):
        supplement = self.listing('alice/notes', extra='ai:\n  mcp_servers:\n    zotero:\n      args: [serve]\n')
        self.vendor(
            supplement,
            'alice/notes',
            'ai:\n  mcp_servers:\n    zotero:\n      env:\n        ZOTERO_LOCAL_WRITE: "true"\n'
            '    notes:\n      command: example-notes\n',
        )
        data = self.load(supplement=supplement)['shaffy']
        zotero = data['settings']['ai']['mcp_servers']['zotero']
        self.assertEqual(zotero['command'], '/usr/bin/mise')  # the defaults'
        self.assertEqual(
            zotero['env'], {'ZOTERO_LOCAL': 'true', 'ZOTERO_LOCAL_WRITE': 'true'}
        )  # the profile's over them
        self.assertEqual(zotero['args'], ['serve'])  # config.yaml's over both
        self.assertEqual(data['provenance'], {'ai.mcp_servers.notes': 'alice/notes'})
        self.assertEqual([profile['id'] for profile in data['profiles']], ['alice/notes'])
        self.assertEqual(data['profiles'][0]['description'], 'Example settings.')
        self.assertEqual(data['profiles'][0]['source'], 'https://github.com/example/profiles')

    def test_two_profiles_that_disagree_are_named_until_the_supplement_writes_the_entry(self):
        supplement = self.listing('alice/notes', 'bob/notes')
        self.vendor(
            supplement,
            'alice/notes',
            'ai:\n  mcp_servers:\n    notes:\n      command: one\n',
            source='https://github.com/alice/profiles',
        )
        self.vendor(
            supplement,
            'bob/notes',
            'ai:\n  mcp_servers:\n    notes:\n      command: two\n      args: [x]\n',
            source='https://codeberg.org/bob/profiles',
        )
        message = self.refused(supplement)
        self.assertIn('ai.mcp_servers.notes: alice/notes and bob/notes set it differently', message)
        self.assertIn(
            'tell its author: https://github.com/alice/profiles/issues, https://codeberg.org/bob/profiles', message
        )
        with open(supplement / 'config.yaml', 'a') as stream:
            stream.write('ai:\n  mcp_servers:\n    notes:\n      command: three\n')
        notes = self.load(supplement=supplement)['shaffy']['settings']['ai']['mcp_servers']['notes']
        self.assertEqual(notes, {'command': 'three'})  # no field of either profile

    def test_equal_entries_from_two_profiles_are_no_conflict(self):
        supplement = self.listing('alice/notes', 'bob/notes')
        for profile_id in ('alice/notes', 'bob/notes'):
            self.vendor(supplement, profile_id, 'ai:\n  mcp_servers:\n    notes:\n      command: one\n')
        data = self.load(supplement=supplement)['shaffy']
        self.assertEqual(data['settings']['ai']['mcp_servers']['notes'], {'command': 'one'})
        self.assertIn('ai.mcp_servers.notes', data['provenance'])

    def test_extra_packages_and_lazyvim_extras_gather_every_layer(self):
        supplement = self.listing('alice/notes', 'bob/notes', extra='packages:\n  extra: [example-c]\n')
        self.vendor(
            supplement,
            'alice/notes',
            'packages:\n  extra: [example-a]\nlazyvim:\n  extras: [lazyvim.plugins.extras.lang.ruby]\n',
        )
        self.vendor(supplement, 'bob/notes', 'packages:\n  extra: [example-b, example-a]\n')
        data = self.load(supplement=supplement)['shaffy']
        self.assertEqual(data['packages']['extra'], ['example-a', 'example-b', 'example-c'])
        self.assertEqual(data['settings']['lazyvim']['extras'], ['lazyvim.plugins.extras.lang.ruby'])
        self.assertEqual(data['provenance']['packages.extra.example-a'], 'alice/notes')
        self.assertEqual(data['provenance']['packages.extra.example-b'], 'bob/notes')
        self.assertNotIn('packages.extra.example-c', data['provenance'])

    def test_profile_fragments_become_assets_at_their_place_in_order(self):
        supplement = self.listing('alice/notes', 'bob/notes', extra='overrides:\n  fish: own.fish\n')
        (supplement / 'own.fish').write_text('# the supplement\n')
        self.vendor(
            supplement,
            'alice/notes',
            'overrides:\n  fish: a.fish\n  mise: tools.toml\n',
            {'a.fish': '# alice\n', 'tools.toml': '[tools]\n'},
        )
        self.vendor(supplement, 'bob/notes', 'overrides:\n  fish: b.fish\n', {'b.fish': '# bob\n'})
        data = self.load(supplement=supplement)['shaffy']
        by_target = {asset['target']: asset for asset in data['assets']}
        self.assertEqual(
            sorted(by_target),
            [
                '.config/fish/profiles/01-alice-notes.fish',
                '.config/fish/profiles/02-bob-notes.fish',
                '.config/mise/conf.d/80-profile-01-alice-notes.toml',
            ],
        )
        alice = by_target['.config/fish/profiles/01-alice-notes.fish']
        self.assertEqual((alice['layer'], alice['fragment'], alice['executable']), ('alice/notes', 'fish', False))
        self.assertEqual(Path(alice['path']).read_text(), '# alice\n')
        self.assertEqual(data['overrides'], {'fish': str(supplement / 'own.fish')})  # the supplement's stay as today
        self.assertEqual(data['fragment_places']['fish'], '.config/fish/profiles/*.fish')
        self.assertIn('profiles/lock.json', data['reference_paths'])
        self.assertIn('profiles/alice/notes/tools.toml', data['reference_paths'])
        self.assertIn('profiles/bob/notes/profile.yaml', data['reference_paths'])
        resolved = sorted({str((supplement / path).resolve()) for path in data['reference_paths']})
        self.assertEqual(resolved, data['references'])  # what the cache stamps is what a capture copies

    def test_keyboard_maps_concatenate_in_layer_order_with_headers(self):
        alice = 'evdev:input:b0003v0001p0002*\n KEYBOARD_KEY_70039=leftmeta\n'
        own = 'evdev:input:b0003v0003p0004*\n KEYBOARD_KEY_70039=esc'
        supplement = self.listing('alice/keys', extra='overrides:\n  keyboard_hwdb: own.hwdb\n')
        (supplement / 'own.hwdb').write_text(own)
        self.vendor(supplement, 'alice/keys', 'overrides:\n  keyboard_hwdb: keys.hwdb\n', {'keys.hwdb': alice})
        data = self.load(supplement=supplement)['shaffy']
        self.assertEqual(
            data['system_overrides']['keyboard_hwdb'],
            f'# Shaffy OS: from profile alice/keys\n{alice}\n# Shaffy OS: from the supplement\n{own}\n',
        )
        self.assertEqual(list(data['overrides']), ['keyboard_hwdb'])
        self.assertEqual(data['assets'], [])  # a system fragment is no home asset

    def test_a_keyboard_map_that_names_one_unit_is_refused(self):
        supplement = self.listing('alice/keys')
        text = (
            'evdev:input:b0003v0001p0002*\n KEYBOARD_KEY_70039=leftmeta\n'
            'evdev:name:X:phys:usb-1/input0:*\n KEYBOARD_KEY_70039=esc\n'
        )
        self.vendor(supplement, 'alice/keys', 'overrides:\n  keyboard_hwdb: keys.hwdb\n', {'keys.hwdb': text})
        message = self.refused(supplement)
        self.assertIn(f'{supplement}/profiles/alice/keys/keys.hwdb:3: this match names one unit', message)
        self.assertIn('Tell the author: https://github.com/example/profiles/issues', message)

    def test_two_profiles_one_asset_target_conflict_and_the_supplement_settles_it(self):
        supplement = self.listing('alice/notes', 'bob/notes')
        for profile_id, content in (('alice/notes', 'a\n'), ('bob/notes', 'b\n')):
            self.vendor(supplement, profile_id, 'assets:\n  "~/.local/share/example/x": x.txt\n', {'x.txt': content})
        message = self.refused(supplement)
        self.assertIn('assets.~/.local/share/example/x: alice/notes and bob/notes set it differently', message)
        (supplement / 'mine.txt').write_text('mine\n')
        with open(supplement / 'config.yaml', 'a') as stream:
            stream.write('assets:\n  "~/.local/share/example/x": mine.txt\n')
        data = self.load(supplement=supplement)['shaffy']
        self.assertEqual([(a['layer'], Path(a['path']).name) for a in data['assets']], [('supplement', 'mine.txt')])
        self.assertEqual(
            sorted(data['notes']),
            [
                "assets.~/.local/share/example/x: config.yaml replaces alice/notes's file",
                "assets.~/.local/share/example/x: config.yaml replaces bob/notes's file",
            ],
        )
        # Equal entries are kept once.
        self.vendor(supplement, 'bob/notes', 'assets:\n  "~/.local/share/example/x": x.txt\n', {'x.txt': 'a\n'})
        (supplement / 'config.yaml').write_text(self.HEAD + 'profiles: [alice/notes, bob/notes]\n')
        data = self.load(supplement=supplement)['shaffy']
        self.assertEqual([a['layer'] for a in data['assets']], ['alice/notes'])

    def test_asset_flags_never_attach_to_another_layers_file(self):
        supplement = self.listing('alice/notes', extra='assets:\n  "~/.local/bin/example": mine\n')
        (supplement / 'mine').write_text('#!/bin/sh\n')
        self.vendor(
            supplement,
            'alice/notes',
            'assets:\n  "~/.local/bin/example": {file: tool, executable: true}\n',
            {'tool': '#!/bin/sh\nexit 1\n'},
        )
        assets = self.load(supplement=supplement)['shaffy']['assets']
        self.assertEqual([(a['layer'], a['executable']) for a in assets], [('supplement', False)])

    def test_a_hand_edited_vendored_file_is_refused_with_the_way_upstream(self):
        supplement = self.listing('alice/notes')
        self.vendor(supplement, 'alice/notes', 'overrides:\n  fish: a.fish\n', {'a.fish': '# alice\n'})
        with open(supplement / 'profiles/alice/notes/a.fish', 'a') as stream:
            stream.write('# mine\n')
        message = self.refused(supplement)
        self.assertIn(
            f'{supplement}/profiles/alice/notes/a.fish: differs from alice/notes as vendored at 0000000', message
        )
        self.assertIn("alice/notes: a vendored profile stays its author's", message)
        self.assertIn('./bin/shaffy profile propose alice/notes (https://github.com/example/profiles/issues)', message)
        self.assertIn('git restore profiles/alice/notes', message)

    def test_a_listed_profile_that_is_not_vendored_names_profile_add(self):
        supplement = self.listing('alice/notes')
        self.assertIn('profiles: alice/notes is not vendored; ./bin/shaffy profile add', self.refused(supplement))
        self.vendor(supplement, 'bob/notes')
        self.assertIn('alice/notes is not vendored', self.refused(supplement))

    def test_a_required_integration_must_be_managed_by_the_supplement(self):
        supplement = self.listing('alice/notes')
        self.vendor(supplement, 'alice/notes', 'requires: [zotero]\n')
        self.assertIn(
            'integrations.zotero: alice/notes requires it managed; set integrations.zotero: managed '
            '(docs/SUPPLEMENT.md#integrations)',
            self.refused(supplement),
        )
        with open(supplement / 'config.yaml', 'a') as stream:
            stream.write('integrations:\n  zotero: managed\n')
        data = self.load(supplement=supplement)['shaffy']
        self.assertEqual(data['integrations']['zotero']['state'], 'managed')
        self.assertEqual(data['profiles'][0]['requires'], ['zotero'])
        self.vendor(supplement, 'alice/notes', 'requires: [dropbox]\n')
        self.assertIn('integrations.dropbox: there is no such integration', self.refused(supplement))

    def test_too_new_a_profile_reports_only_that(self):
        supplement = self.listing('alice/notes')
        self.vendor(supplement, 'alice/notes', 'minimum_release: v2026.10.20\ncolour: blue\n')
        with mock.patch('shaffy.loader.release', return_value='v2026.10.19'):
            message = self.refused(supplement)
        self.assertEqual(
            message,
            f'{supplement}/profiles/alice/notes/profile.yaml: minimum_release: profile alice/notes needs Shaffy OS '
            'v2026.10.20 or later; this checkout is at v2026.10.19; update it to a newer release',
        )

    def test_a_profile_may_not_set_what_config_yaml_owns(self):
        supplement = self.listing('alice/notes')
        self.vendor(supplement, 'alice/notes', 'desktop:\n  browser: example.desktop\n')
        message = self.refused(supplement)
        self.assertIn('profile.yaml: desktop.browser: a profile cannot set this', message)
        self.assertIn('./bin/shaffy profile propose alice/notes --to shaffy --key desktop.browser', message)
        self.assertNotIn('unknown key', message)

    def test_profile_references_stay_inside_the_profile(self):
        supplement = self.listing('alice/notes')
        (supplement / 'secret.fish').write_text('# the supplement\n')
        self.vendor(supplement, 'alice/notes', 'overrides:\n  fish: shared.fish\n')
        (supplement / 'profiles/alice/notes/shared.fish').symlink_to('../../../secret.fish')
        message = self.refused(supplement)
        self.assertIn("profile.yaml: overrides.fish: 'shared.fish' resolves outside the profile", message)
        (supplement / 'profiles/alice/notes/shared.fish').unlink()
        (supplement / 'profiles/alice/notes/shared.fish').write_text('# unlocked\n')
        self.assertIn('profiles/alice/notes/shared.fish: not in profiles/lock.json', self.refused(supplement))

    def test_a_supplement_reference_into_profiles_is_refused(self):
        supplement = self.listing('alice/notes', extra='overrides:\n  fish: profiles/alice/notes/a.fish\n')
        self.vendor(supplement, 'alice/notes', files={'a.fish': '# alice\n'})
        self.assertIn("overrides.fish: 'profiles/alice/notes/a.fish' leads into profiles/", self.refused(supplement))
        (supplement / 'config.yaml').write_text(self.HEAD + 'dotfiles: profiles/alice\n')
        self.assertIn("dotfiles: 'profiles/alice' leads into profiles/", self.refused(supplement))

    def test_an_effective_problem_names_the_profile_that_set_the_entry(self):
        supplement = self.listing('alice/notes')
        self.vendor(
            supplement,
            'alice/notes',
            'ai:\n  mcp_servers:\n    notes:\n      command: a\n      url: "http://127.0.0.1:1/mcp"\n',
        )
        self.assertIn(
            f'{supplement}/profiles/alice/notes/profile.yaml: ai.mcp_servers.notes: has both a command and a url',
            self.refused(supplement),
        )

    def test_unlisted_vendored_profiles_are_not_read(self):
        supplement = self.supplement(self.HEAD + 'overrides:\n  fish: own.fish\n')
        (supplement / 'own.fish').write_text('# the supplement\n')
        before = self.load(supplement=supplement)['shaffy']
        self.vendor(supplement, 'alice/notes', 'overrides:\n  fish: a.fish\n', {'a.fish': '# alice\n'})
        (supplement / 'profiles/lock.json').write_text('not even json\n')
        after = self.load(supplement=supplement)['shaffy']
        self.assertEqual(after['references'], before['references'])
        self.assertEqual(after['reference_paths'], ['own.fish'])
        self.assertEqual(after['profiles'], [])

    def test_the_cache_notices_an_edited_vendored_file(self):
        supplement = self.listing('alice/notes')
        self.vendor(supplement, 'alice/notes', 'overrides:\n  fish: a.fish\n', {'a.fish': '# alice\n'})
        cache = self.tmp / 'cache'
        first = load_data(ROOT, supplement=supplement, home=self.home, directory=cache)
        self.assertEqual(first['shaffy']['profiles'][0]['id'], 'alice/notes')
        self.assertEqual(load_data(ROOT, supplement=supplement, home=self.home, directory=cache), first)
        (supplement / 'profiles/alice/notes/a.fish').write_text('# edited\n')
        with self.assertRaisesRegex(SettingsError, 'differs from alice/notes'):
            load_data(ROOT, supplement=supplement, home=self.home, directory=cache)


class CachedPathTests(LoaderCase):
    def run_fast(self, *args, supplement=None):
        env = {'HOME': str(self.home), 'XDG_RUNTIME_DIR': str(self.tmp), 'PATH': os.environ['PATH']}
        choice = ['--supplement', str(supplement)] if supplement else ['--without-supplement']
        return subprocess.run(
            [
                '/usr/bin/python3',
                '-I',
                '-S',
                '-X',
                'importtime',
                str(ROOT / 'lib/shaffy/fastload.py'),
                *choice,
                *args,
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def hits_without_yaml(self, supplement=None):
        first = self.run_fast(supplement=supplement)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn('jsonschema', first.stderr)  # a miss runs the full loader
        second = self.run_fast(supplement=supplement)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout), json.loads(first.stdout))
        imported = [line.split('|')[-1].strip() for line in second.stderr.splitlines()]
        self.assertFalse({'yaml', 'jsonschema'} & set(imported), imported)
        return json.loads(second.stdout)['shaffy']

    def test_cache_hit_imports_neither_yaml_nor_jsonschema(self):
        self.hits_without_yaml()

    def test_cache_hit_with_a_vendored_profile_imports_neither_yaml_nor_jsonschema(self):
        supplement = self.supplement('schema_version: 1\nsupplement_id: t\nprofiles: [alice/notes]\n')
        vendor_profile(
            supplement,
            'alice/notes',
            {'profile.yaml': 'schema_version: 1\ndescription: x\noverrides:\n  fish: a.fish\n', 'a.fish': '# a\n'},
        )
        data = self.hits_without_yaml(supplement)
        self.assertEqual([asset['fragment'] for asset in data['assets']], ['fish'])

    def test_changed_input_invalidates_the_cache(self):
        path = self.supplement('schema_version: 1\nsupplement_id: t\n')
        first = load_data(ROOT, supplement=path, home=self.home, directory=self.tmp / 'cache')
        (path / 'config.yaml').write_text('schema_version: 1\nsupplement_id: changed\n')
        second = load_data(ROOT, supplement=path, home=self.home, directory=self.tmp / 'cache')
        self.assertEqual(first['shaffy']['selection']['supplement_id'], 't')
        self.assertEqual(second['shaffy']['selection']['supplement_id'], 'changed')

    def test_a_retargeted_asset_link_misses_the_cache(self):
        path = self.supplement('schema_version: 1\nsupplement_id: t\nassets:\n  "~/x.otf": current.otf\n')
        for name, data in (('one.otf', b'one'), ('two.otf', b'two')):
            (path / name).write_bytes(data)
            os.utime(path / name, ns=(1, 1))  # same size and times: only the link says which
        (path / 'current.otf').symlink_to('one.otf')
        first = load_data(ROOT, supplement=path, home=self.home, directory=self.tmp / 'cache')
        (path / 'current.otf').unlink()
        (path / 'current.otf').symlink_to('two.otf')
        second = load_data(ROOT, supplement=path, home=self.home, directory=self.tmp / 'cache')
        self.assertEqual([Path(a['path']).name for a in first['shaffy']['assets']], ['one.otf'])
        self.assertEqual([Path(a['path']).name for a in second['shaffy']['assets']], ['two.otf'])

    def test_an_edit_during_the_load_is_not_cached_under_the_old_inputs(self):
        from shaffy import loader

        path = self.supplement('schema_version: 1\nsupplement_id: before\n')
        original = loader.load

        def load_then_edit(*args, **kwargs):
            result = original(*args, **kwargs)
            (path / 'config.yaml').write_text('schema_version: 1\nsupplement_id: after\n')
            return result

        with mock.patch.object(loader, 'load', load_then_edit):
            load_data(ROOT, supplement=path, home=self.home, directory=self.tmp / 'cache')
        self.assertEqual(list((self.tmp / 'cache').glob('*.json')), [])
        (path / 'config.yaml').write_text('schema_version: 1\nsupplement_id: before\n')
        again = load_data(ROOT, supplement=path, home=self.home, directory=self.tmp / 'cache')
        self.assertEqual(again['shaffy']['selection']['supplement_id'], 'before')

    @serial
    @host_test('timing on this laptop; CI runners vary')
    def test_cache_hit_is_fast(self):
        self.run_fast()
        env = {'HOME': str(self.home), 'XDG_RUNTIME_DIR': str(self.tmp), 'PATH': os.environ['PATH']}
        command = ['/usr/bin/python3', '-I', '-S', str(ROOT / 'lib/shaffy/fastload.py'), '--without-supplement']
        times = []
        for _ in range(9):
            start = time.perf_counter()
            subprocess.run(command, capture_output=True, env=env, check=True)
            times.append((time.perf_counter() - start) * 1000)
        # The fastest run shows what the cached path costs; the median allows for a busy machine.
        self.assertLess(min(times), 25, times)
        self.assertLess(statistics.median(times), 60, times)


if __name__ == '__main__':
    unittest.main()
