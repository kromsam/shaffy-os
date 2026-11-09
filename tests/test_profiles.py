"""The profile format and the vendored state the loader reads, without a supplement."""

import fnmatch
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy import profiles
from shaffy.loader import parse, validate

SCHEMA = json.loads((ROOT / 'schema/supplement.schema.json').read_text())
HWDB_MAPS = sorted(Path('/usr/lib/udev/hwdb.d').glob('*keyboard*.hwdb'))


class FormatTests(unittest.TestCase):
    def test_every_supplement_section_is_allowed_or_excluded_with_a_reason(self):
        sections = {name: spec.get('properties', {}) for name, spec in SCHEMA['properties'].items()}
        for section, members in sections.items():
            with self.subTest(section=section):
                if section in profiles.PROFILE_KEYS or section in profiles.PROFILE_EXCLUDED:
                    continue
                if section in profiles.PROFILE_SECTIONS:
                    allowed = profiles.PROFILE_SECTIONS[section]
                    if allowed is not None:
                        self.assertTrue(set(allowed) <= set(members), f'{section}: {allowed} are not all in the schema')
                        for member in set(members) - set(allowed):
                            self.assertIn(f'{section}.{member}', profiles.PROFILE_EXCLUDED)
                    continue
                self.assertTrue(members, f'{section}: neither allowed nor excluded, and it has no sub-sections')
                for member in members:
                    self.assertIn(f'{section}.{member}', profiles.PROFILE_EXCLUDED)
        for key, reason in profiles.PROFILE_EXCLUDED.items():
            with self.subTest(key=key):
                section, _, member = key.partition('.')
                self.assertIn(section, sections, 'a stale reason')
                if member:
                    self.assertIn(member, sections[section], 'a stale reason')
                self.assertTrue(reason.strip())

    def test_an_excluded_key_is_explained_with_both_ways_out(self):
        for key, reason in profiles.PROFILE_EXCLUDED.items():
            with self.subTest(key=key):
                text = profiles.explain(key, 'alice/thesis')
                self.assertTrue(text.startswith(f'{key}: a profile cannot set this: {reason}'))
                self.assertIn('Set it in config.yaml.', text)
                self.assertIn(f'./bin/shaffy profile propose alice/thesis --to shaffy --key {key}', text)
                self.assertIn(f'./bin/shaffy profile propose --to shaffy --key {key}', profiles.explain(key))
        self.assertIsNone(profiles.explain('ai.mcp_servers'))
        self.assertIsNone(profiles.explain('colour'))

    def test_the_profile_schema_keeps_the_supplements_patterns_and_leaves_out_the_rest(self):
        schema = profiles.profile_schema(SCHEMA)
        self.assertEqual(schema['required'], ['schema_version', 'description'])
        self.assertEqual(
            set(schema['properties']),
            {*profiles.PROFILE_KEYS, *profiles.PROFILE_SECTIONS},
            'the format and the sections',
        )
        self.assertEqual(list(schema['properties']['ai']['properties']), ['mcp_servers', 'claude_plugins'])
        self.assertEqual(list(schema['properties']['packages']['properties']), ['extra'])
        self.assertEqual(schema['properties']['overrides'], SCHEMA['properties']['overrides'])
        self.assertEqual(schema['properties']['assets'], SCHEMA['properties']['assets'])
        self.assertEqual(
            schema['properties']['requires']['items']['pattern'],
            SCHEMA['properties']['integrations']['propertyNames']['pattern'],
        )
        self.assertEqual(set(schema['$defs']), {'mcp_server', 'reference', 'package_list'})
        for key in ('desktop', 'supplement_id', 'integrations', 'dotfiles', 'profiles'):
            self.assertNotIn(key, schema['properties'])

    def test_profile_schema_file_is_derived(self):
        self.assertEqual(
            json.loads((ROOT / 'schema/profile.schema.json').read_text()),
            profiles.profile_schema(SCHEMA),
            'regenerate it: /usr/bin/python3 lib/shaffy/profiles.py > schema/profile.schema.json',
        )

    def test_every_place_lies_under_the_config_folder(self):
        for tool, pattern in profiles.PLACES.items():
            with self.subTest(tool=tool):
                self.assertTrue(pattern.startswith('.config/'))
                target = profiles.place(tool, 2, 'kromsam/ruby')
                self.assertIn('02-kromsam-ruby.', target)
                self.assertTrue(fnmatch.fnmatchcase(target, profiles.places()[tool]), (target, profiles.places()[tool]))
        self.assertEqual(profiles.place('fish', 2, 'kromsam/ruby'), '.config/fish/profiles/02-kromsam-ruby.fish')
        self.assertEqual(profiles.places()['mise'], '.config/mise/conf.d/80-profile-*.toml')

    def test_an_id_is_owner_slash_name_in_lower_case(self):
        self.assertEqual(profiles.parse_id('kromsam/model-m'), ('kromsam', 'model-m'))
        for text in ('Kromsam/writing', 'writing', 'a/b/c', '-a/b', 'a/', '', None):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'OWNER/NAME'):
                profiles.parse_id(text)

    def test_the_authors_issues_page_is_github_only(self):
        locked = profiles.Locked('a/b', 'https://github.com/a/profiles/', 'b', 'HEAD', '0' * 40, {})
        self.assertEqual(profiles.issues(locked), 'https://github.com/a/profiles/issues')
        other = profiles.Locked('a/b', 'https://codeberg.org/a/profiles', 'b', 'HEAD', '0' * 40, {})
        self.assertEqual(profiles.issues(other), 'https://codeberg.org/a/profiles')


class KeyboardMapTests(unittest.TestCase):
    @unittest.skipUnless(HWDB_MAPS, 'systemd keyboard maps are not installed here')
    def test_systemds_keyboard_maps_name_no_unit(self):
        for path in HWDB_MAPS:
            with self.subTest(map=path.name):
                text = path.read_text()
                self.assertIn(':phys:', text, 'the maps match device names through phys; the rule must let them pass')
                self.assertEqual(profiles.hwdb_unit_matches(text), [])

    def test_port_unique_id_and_serial_matches_name_one_unit(self):
        text = '\n'.join(
            [
                '# A comment that names usb-1 and a serial is not a match line',
                'evdev:input:b0003v046Dp4049*',
                ' KEYBOARD_KEY_70039=leftmeta',
                'evdev:name:Example Keyboard:phys:usb-0000:00:14.0-1/input0:*',
                ' KEYBOARD_KEY_70039=leftmeta',
                'evdev:name:Example Keyboard:phys:pci-0000:00:1f.3*',
                'evdev:name:Example Keyboard:phys:0000:00:14.0*',
                'evdev:name:Example Keyboard:uniq:0123456789ab:*',
                'evdev:name:Example Keyboard:serial:ab12:*',
                'evdev:name:gpio-keys:phys:gpio-keys/input0:*',
                'evdev:name:AT Translated Set 2 keyboard:phys:sp/serio0/input0:*',
                ' KEYBOARD_KEY_serial=unused',
            ]
        )
        self.assertEqual(profiles.hwdb_unit_matches(text), [4, 6, 7, 8, 9])


class ReadProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.too_new = None

    def read(self, text, profile_id='alice/thesis'):
        (self.folder / 'profile.yaml').write_text(text)
        return profiles.read_profile(
            self.folder,
            SCHEMA,
            'profile.yaml',
            parse=parse,
            validate=validate,
            release_problem=lambda wanted: self.too_new,
            profile_id=profile_id,
        )

    def test_a_valid_profile_reads_back_without_problems(self):
        data, problems = self.read(
            'schema_version: 1\ndescription: Example writing tools.\nrequires: [zotero]\n'
            'packages:\n  extra: [example-tool]\nassets:\n  "~/.local/share/example/x": files/x\n'
        )
        self.assertEqual(problems, [])
        self.assertEqual(data['requires'], ['zotero'])
        self.assertEqual(data['packages'], {'extra': ['example-tool']})

    def test_too_new_a_profile_reports_only_that(self):
        self.too_new = 'minimum_release: profile alice/thesis needs Shaffy OS v2099.01.01 or later'
        _, problems = self.read('schema_version: 1\ndescription: x\nminimum_release: v2099.01.01\ncolour: blue\n')
        self.assertEqual(problems, ['profile.yaml: ' + self.too_new])

    def test_an_excluded_key_gets_its_reason_instead_of_unknown_key(self):
        _, problems = self.read(
            'schema_version: 1\ndescription: x\ndesktop:\n  browser: example.desktop\n'
            'packages:\n  extra: [example-tool]\n  omit: [reaper]\nintegrations:\n  zotero: managed\n'
        )
        self.assertEqual(
            [problem.split(':')[1].strip() for problem in problems],
            ['desktop.browser', 'packages.omit', 'integrations'],
        )
        self.assertNotIn('unknown key', '\n'.join(problems))
        self.assertIn('./bin/shaffy profile propose alice/thesis --to shaffy --key desktop.browser', problems[0])

    def test_the_schema_reports_what_the_reasons_do_not_cover(self):
        _, problems = self.read('schema_version: 1\ndescription: x\ncolour: blue\nlazyvim:\n  extras: [ruby]\n')
        self.assertIn("profile.yaml: (top level): unknown key 'colour'", problems)
        self.assertTrue(any('lazyvim.extras.0' in problem for problem in problems), problems)
        _, missing = self.read('schema_version: 1\n')
        self.assertIn("'description' is a required property", '\n'.join(missing))
        _, version = self.read('schema_version: 2\ndescription: x\n')
        self.assertIn('schema_version: 2 is not supported', '\n'.join(version))

    def test_an_asset_under_a_refused_target_is_refused(self):
        for prefix in profiles.PROFILE_REFUSED_TARGETS:
            for target in (prefix + 'file', prefix.rstrip('/')):
                with self.subTest(target=target):
                    _, problems = self.read(f'schema_version: 1\ndescription: x\nassets:\n  "{target}": files/x\n')
                    self.assertEqual(len(problems), 1, problems)
                    self.assertIn(f'assets.{target}: a profile may not write under {prefix}', problems[0])
        _, fine = self.read('schema_version: 1\ndescription: x\nassets:\n  "~/.local/share/x": files/x\n')
        self.assertEqual(fine, [])


class ImportTests(unittest.TestCase):
    def run_python(self, *flags, code):
        result = subprocess.run(
            ['/usr/bin/python3', *flags, '-c', f'import sys; sys.path.insert(0, {str(ROOT / "lib")!r}); ' + code],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_profiles_imports_only_the_standard_library(self):
        # -I -S leaves out site-packages, where PyYAML and jsonschema live, so any such import fails.
        out = self.run_python(
            '-I',
            '-S',
            code='import shaffy.profiles; print(sorted(m for m in sys.modules if m.startswith("shaffy")))',
        )
        self.assertEqual(json.loads(out.replace("'", '"')), ['shaffy', 'shaffy.profiles'])

    def test_loader_and_profiles_import_in_either_order(self):
        for order in ('shaffy.loader, shaffy.profiles', 'shaffy.profiles, shaffy.loader'):
            with self.subTest(order=order):
                self.run_python(code=f'import {order}; print("ok")')


if __name__ == '__main__':
    unittest.main()
