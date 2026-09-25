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
from shaffy.fastload import load_data  # noqa: E402
from shaffy.loader import SettingsError, check_reference, load  # noqa: E402
from shaffy.selection import SelectionError  # noqa: E402
from support import host_test  # noqa: E402

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
        self.assertEqual(data['present'], [])

    def test_minimal_example_is_valid_and_changes_nothing(self):
        data = self.load(supplement=ROOT / 'supplement.example')['shaffy']
        self.assertEqual(data['selection']['supplement_id'], 'my-desktop')
        self.assertEqual(data['settings'], self.load(without=True)['shaffy']['settings'])

    def test_comprehensive_fixture_overrides_and_extends(self):
        data = self.load(supplement=FIXTURES / 'comprehensive')['shaffy']
        servers = data['settings']['ai']['mcp_servers']
        self.assertEqual(servers['zotero']['env'],
                         {'ZOTERO_LOCAL': 'true', 'ZOTERO_LOCAL_WRITE': 'true'})
        self.assertEqual(servers['zotero']['command'], '~/.local/bin/zotero-mcp')
        self.assertEqual(servers['notes']['args'], ['--read-only'])
        self.assertIn('ai.mcp_servers.zotero.env.ZOTERO_LOCAL_WRITE', data['present'])
        self.assertNotIn('ai.mcp_servers.zotero.command', data['present'])

    def test_explicit_empty_list_is_kept(self):
        path = self.supplement('schema_version: 1\nsupplement_id: t\n'
                               'ai:\n  mcp_servers:\n    zotero:\n      args: []\n')
        servers = self.load(supplement=path)['shaffy']['settings']['ai']['mcp_servers']
        self.assertEqual(servers['zotero']['args'], [])


class InvalidInputTests(LoaderCase):
    def test_duplicate_key_names_line(self):
        message = self.problems('schema_version: 1\nsupplement_id: a\nsupplement_id: b\n')
        self.assertIn("duplicate key 'supplement_id'", message)
        self.assertIn('config.yaml:3:', message)

    def test_unknown_key(self):
        self.assertIn("unknown key 'colour'",
                      self.problems('schema_version: 1\nsupplement_id: t\ncolour: blue\n'))

    def test_wrong_type(self):
        message = self.problems('schema_version: 1\nsupplement_id: t\n'
                                'ai:\n  mcp_servers:\n    zotero:\n      args: serve\n')
        self.assertIn('ai.mcp_servers.zotero.args: must be of type array', message)

    def test_yaml_1_2_typing_keeps_yes_a_string_and_rejects_bare_true(self):
        # "yes" is a string in YAML 1.2, as editors read it; bare true is a boolean.
        text = ('schema_version: 1\nsupplement_id: t\nai:\n  mcp_servers:\n    zotero:\n'
                '      env:\n        ZOTERO_LOCAL: yes\n        ZOTERO_LOCAL_WRITE: {}\n')
        message = self.problems(text.replace('{}', 'true'))
        self.assertIn('ZOTERO_LOCAL_WRITE: must be of type string, found bool True', message)
        self.assertNotIn('ZOTERO_LOCAL:', message)

    def test_yaml_1_1_octal_and_sexagesimal_are_not_reinterpreted(self):
        from shaffy.loader import _Loader
        import yaml
        values = yaml.load('mode: 0600\ntime: 1:20\nday: 2026-09-23\nflag: on\n', Loader=_Loader)
        self.assertEqual(values, {'mode': 600, 'time': '1:20', 'day': '2026-09-23', 'flag': 'on'})

    def test_unsupported_version(self):
        self.assertIn('schema_version: 2 is not supported',
                      self.problems('schema_version: 2\nsupplement_id: t\n'))

    def test_missing_identity(self):
        self.assertIn("'supplement_id' is a required property", self.problems('schema_version: 1\n'))

    def test_null_and_empty_mappings_are_errors(self):
        self.assertIn('ai: must be of type object, found null',
                      self.problems('schema_version: 1\nsupplement_id: t\nai:\n'))
        self.assertIn('ai: must not be empty',
                      self.problems('schema_version: 1\nsupplement_id: t\nai: {}\n'))

    def test_display_and_keyboard_values_cannot_inject_kdl(self):
        message = self.problems('schema_version: 1\nsupplement_id: t\ndesktop:\n  displays:\n'
                                '    - name: \'eDP-1" { }\'\n  keyboard:\n    layouts: \'us" }\'\n')
        self.assertIn('desktop.displays.0.name', message)
        self.assertIn('desktop.keyboard.layouts', message)

    def test_override_references_stay_inside_the_supplement(self):
        message = self.problems('schema_version: 1\nsupplement_id: t\noverrides:\n'
                                '  niri: ../outside.kdl\n  fish: /etc/passwd\n')
        self.assertIn('overrides.niri', message)
        self.assertIn('overrides.fish', message)
        missing = self.problems('schema_version: 1\nsupplement_id: t\noverrides:\n  niri: absent.kdl\n')
        self.assertIn("overrides.niri: 'absent.kdl' does not exist in the supplement", missing)

    def test_override_paths_are_resolved_and_listed_as_references(self):
        data = self.load(supplement=FIXTURES / 'comprehensive')['shaffy']
        self.assertEqual(data['overrides']['fish'], str(FIXTURES / 'comprehensive/overrides/fish.fish'))
        self.assertEqual(len(data['references']), 8)  # five overrides and three asset files
        self.assertNotIn('overrides', data['settings'])

    def test_identity_folders_and_links_are_validated(self):
        message = self.problems('schema_version: 1\nsupplement_id: t\n'
                                'identity:\n  name: \'A "quoted" name\'\n  email: not-an-email\n'
                                'folders:\n  documents: \'Docs"\'\nfolder_links:\n  Elsewhere: documents\n')
        for key in ('identity.name', 'identity.email', 'folders.documents', "unknown key 'Elsewhere'"):
            self.assertIn(key, message)

    def test_new_server_needs_a_command(self):
        message = self.problems('schema_version: 1\nsupplement_id: t\n'
                                'ai:\n  mcp_servers:\n    other:\n      args: []\n')
        self.assertIn('ai.mcp_servers.other: needs a command', message)


class ReferenceTests(LoaderCase):
    def test_reference_inside_symlinked_supplement_is_accepted(self):
        real = self.supplement('schema_version: 1\nsupplement_id: t\n', 'real')
        (real / 'overrides').mkdir()
        (real / 'overrides/niri.kdl').write_text('')
        link = self.tmp / 'linked'
        link.symlink_to(real)
        self.assertEqual(check_reference(link, 'overrides/niri.kdl', 'test'),
                         str(real / 'overrides/niri.kdl'))

    def test_escaping_references_fail_clearly(self):
        root = self.supplement('schema_version: 1\nsupplement_id: t\n')
        (self.tmp / 'outside.txt').write_text('secret')
        (root / 'escape').symlink_to(self.tmp / 'outside.txt')
        for reference, reason in (('../outside.txt', 'resolves outside'),
                                  ('escape', 'resolves outside'),
                                  (str(self.tmp / 'outside.txt'), 'must be relative'),
                                  ('missing.kdl', 'does not exist')):
            with self.subTest(reference=reference), self.assertRaises(SettingsError) as caught:
                check_reference(root, reference, 'overrides.niri')
            self.assertIn(reason, str(caught.exception))


class SelectionTests(LoaderCase):
    def test_both_choices_conflict(self):
        with self.assertRaises(SelectionError):
            self.load(supplement=ROOT / 'supplement.example', without=True)

    def test_missing_explicit_supplement_never_falls_back(self):
        with self.assertRaisesRegex(SelectionError, 'does not exist'):
            self.load(supplement=self.tmp / 'absent')

    def test_discovered_supplement_must_be_valid(self):
        checkout = self.tmp / 'checkout'
        shutil.copytree(ROOT / 'defaults', checkout / 'defaults')
        shutil.copytree(ROOT / 'schema', checkout / 'schema')
        (checkout / 'supplement').symlink_to(self.tmp / 'gone')
        with self.assertRaisesRegex(SelectionError, 'dangling symlink'):
            load(checkout, home=self.home)
        (checkout / 'supplement').unlink()
        (checkout / 'supplement').mkdir()
        with self.assertRaisesRegex(SelectionError, 'no config.yaml'):
            load(checkout, home=self.home)

    def test_missing_bound_supplement_fails(self):
        state = self.home / '.local/state/shaffy'
        state.mkdir(parents=True)
        (state / 'binding.json').write_text(json.dumps(
            {'binding': {'mode': 'supplement', 'path': str(self.tmp / 'moved'), 'supplement_id': 't'}}))
        with self.assertRaisesRegex(SelectionError, 'the bound supplement .* does not exist'):
            self.load()


class PackageTests(LoaderCase):
    def packages(self, text):
        return self.load(supplement=self.supplement('schema_version: 1\nsupplement_id: t\n' + text))[
            'shaffy']['packages']

    def test_the_public_groups_are_consistent(self):
        from shaffy.loader import parse
        from shaffy.packages import load_groups
        groups, retired, problems = load_groups(ROOT, parse)
        self.assertEqual(problems, [])
        self.assertEqual(set(groups), {'desktop', 'desktop_apps', 'home_backup', 'kde_connect', 'sync',
                                       'workflows', 'writing'})
        import yaml
        site = {entry['role']: entry.get('tags', []) for entry in yaml.safe_load((ROOT / 'site.yml').read_text())[0]['roles']}
        for name in groups:  # each group is installed by the role of the same name, which site.yml runs
            self.assertIn(f'shaffy.packages.groups.{name}', (ROOT / f'roles/{name}/tasks/main.yml').read_text())
            self.assertEqual(site.get(name), [name], f'site.yml does not run the {name} role under its tag')
        self.assertEqual(site.get('packages'), ['packages'])
        self.assertEqual(retired, [])

    def test_defaults_install_every_group_and_remove_only_the_retired(self):
        packages = self.load()['shaffy']['packages']
        self.assertIn('docker', packages['groups']['workflows'])
        self.assertEqual(packages['extra'], [])
        self.assertEqual(packages['remove'], [])  # nothing public is retired yet

    def test_choices_add_omit_and_remove(self):
        packages = self.packages('packages:\n  extra: [htop, zellij]\n  omit: [reaper]\n  remove: [reaper]\n')
        self.assertNotIn('reaper', packages['groups']['workflows'])
        self.assertEqual(packages['extra'], ['htop', 'zellij'])
        self.assertEqual(packages['remove'], ['reaper'])  # only listed packages are removed

    def test_contradictory_choices_fail(self):
        for text, message in (
                ('packages:\n  omit: [docker]\n', 'packages.omit: docker is required by the workflows group'),
                ('packages:\n  omit: [htop]\n', 'packages.omit: htop is in no package group'),
                ('packages:\n  extra: [docker]\n', 'packages.extra: docker is already in the workflows group'),
                ('packages:\n  remove: [reaper]\n', 'packages.remove: reaper is also installed by the workflows'),
                ('packages:\n  extra: [htop]\n  remove: [htop]\n', 'packages.remove: htop is also installed by packages.extra'),
                ('packages:\n  omit: [mise]\n  remove: [mise]\n', 'packages.remove: mise is needed by Shaffy OS itself'),
                ('packages:\n  remove: [sudo]\n', 'packages.remove: sudo is needed by Shaffy OS itself'),
                ('packages:\n  remove: [linux-cachyos]\n', 'packages.remove: linux-cachyos is needed by Shaffy OS itself'),
                ('packages:\n  extra: [Htop]\n', 'packages.extra'),
                ('packages:\n  extra: [htop, htop]\n', 'packages.extra'),
                ('packages: {}\n', 'packages')):
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


class AssetTests(LoaderCase):
    def test_assets_resolve_inside_the_supplement_with_their_digest(self):
        import hashlib
        supplement = self.supplement('schema_version: 1\nsupplement_id: t\n'
                                     'assets:\n  "~/.local/share/fonts/A B.otf": fonts/a.otf\n')
        (supplement / 'fonts').mkdir()
        (supplement / 'fonts/a.otf').write_bytes(b'font')
        data = self.load(supplement=supplement)['shaffy']
        path = str((supplement / 'fonts/a.otf').resolve())
        self.assertEqual(data['assets'], [{'target': '.local/share/fonts/A B.otf', 'path': path,
                                           'url': 'file://' + path.replace(' ', '%20'),
                                           'sha256': hashlib.sha256(b'font').hexdigest(),
                                           'executable': False, 'private': False}])
        self.assertIn(path, data['references'])
        self.assertIn('fonts/a.otf', data['reference_paths'])
        self.assertNotIn('assets', data['settings'])

    def test_system_fragments_travel_inside_the_settings(self):
        supplement = self.supplement('schema_version: 1\nsupplement_id: t\n'
                                     'overrides:\n  keyboard_hwdb: keyboard.hwdb\n')
        (supplement / 'keyboard.hwdb').write_text('evdev:input:b0003v0001p0002*\n KEYBOARD_KEY_70039=leftmeta\n')
        data = self.load(supplement=supplement)['shaffy']
        self.assertEqual(data['system_overrides'], {'keyboard_hwdb': (supplement / 'keyboard.hwdb').read_text()})
        self.assertEqual(self.load()['shaffy']['system_overrides'], {})

    def test_asset_attributes(self):
        supplement = self.supplement('schema_version: 1\nsupplement_id: t\nassets:\n'
                                     '  "~/.local/bin/tool": {file: tool, executable: true}\n'
                                     '  "~/.config/x.conf": {file: tool, private: true}\n')
        (supplement / 'tool').write_text('#!/bin/sh\n')
        assets = {a['target']: a for a in self.load(supplement=supplement)['shaffy']['assets']}
        self.assertEqual((assets['.local/bin/tool']['executable'], assets['.local/bin/tool']['private']), (True, False))
        self.assertEqual((assets['.config/x.conf']['executable'], assets['.config/x.conf']['private']), (False, True))
        self.assertIn('assets', self.problems('schema_version: 1\nsupplement_id: t\nassets:\n'
                                              '  "~/x": {file: tool, mode: 0755}\n'))

    def test_bad_assets_fail(self):
        self.assertIn('does not exist in the supplement',
                      self.problems('schema_version: 1\nsupplement_id: t\nassets:\n  "~/x": missing.otf\n'))
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
        return self.load(supplement=self.supplement('schema_version: 1\nsupplement_id: t\n' + text))[
            'shaffy']['integrations']

    def test_the_public_catalogue_is_consistent(self):
        from shaffy.integrations import load_catalogue
        from shaffy.loader import parse
        catalogue, problems = load_catalogue(ROOT, parse)
        self.assertEqual(problems, [])
        self.assertEqual(set(catalogue), {'backup', 'gbrain', 'nextcloud', 'reaper', 'ssh', 'syncthing', 'zettlr', 'zotero'})
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
                ('backup: {host: x, tags: [a], repository: {sftp: {host: h, user: u, path: p, host_key: "ssh-rsa AAA"}}}\n',
                 'backup.repository.sftp.host_key'),
                ('backup: {host: x, tags: [a], repository: {sftp: {host: h, user: u, path: p, host_key: "ssh-ed25519 AAA"}},'
                 ' excludes: [/etc]}\n', 'backup.excludes'),
                ('backup: {host: x, tags: [a], repository: {sftp: {host: h, user: u, path: p, host_key: "ssh-ed25519 AAA"}},'
                 ' excludes: ["~/.local/state/"]}\n', "where each backup's verification marker is written"),
                ('backup: {host: x, tags: [a], repository: {sftp: {host: h, user: u, path: p, host_key: "ssh-ed25519 AAA"}},'
                 ' excludes: ["~/.local"]}\n', "verification marker")):
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
            'outside: {scope: home, targets: [a], activation: [{kind: autostart, path: /etc/x}]}\n')
        problems = '\n'.join(load_catalogue(self.tmp / 'checkout', parse)[1])
        self.assertIn('Bad: an integration name', problems)
        self.assertIn('noscope: needs a scope', problems)
        self.assertIn("badlink: an activation record needs kind and exactly ['link', 'unit']", problems)
        self.assertIn('outside: a home activation path starts with ~/', problems)


class CachedPathTests(LoaderCase):
    def run_fast(self, *args):
        env = {'HOME': str(self.home), 'XDG_RUNTIME_DIR': str(self.tmp), 'PATH': os.environ['PATH']}
        return subprocess.run(['/usr/bin/python3', '-I', '-S', '-X', 'importtime',
                               str(ROOT / 'lib/shaffy/fastload.py'), '--without-supplement', *args],
                              capture_output=True, text=True, env=env, check=False)

    def test_cache_hit_imports_neither_yaml_nor_jsonschema(self):
        first = self.run_fast()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn('jsonschema', first.stderr)  # a miss runs the full loader
        second = self.run_fast()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout), json.loads(first.stdout))
        imported = [line.split('|')[-1].strip() for line in second.stderr.splitlines()]
        self.assertFalse({'yaml', 'jsonschema'} & set(imported), imported)

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

    @host_test('timing on this laptop; CI runners vary')
    def test_cache_hit_is_fast(self):
        self.run_fast()
        env = {'HOME': str(self.home), 'XDG_RUNTIME_DIR': str(self.tmp), 'PATH': os.environ['PATH']}
        command = ['/usr/bin/python3', '-I', '-S', str(ROOT / 'lib/shaffy/fastload.py'),
                   '--without-supplement']
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
