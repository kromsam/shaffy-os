"""Routine applies cannot restore a backup, move a personal profile, enroll firmware keys or
convert disks; those stay explicit, guarded commands. A swap file for hibernation is made only
when the supplement's power.hibernation asks for it."""

from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
# Commands and markers of the sensitive operations, which only explicit tools may contain. sbctl
# stands for creating and enrolling firmware keys, which only scripts/configure-secure-boot.py does,
# run by hand: limine-mkinitcpio, which the power and boot_splash roles run to rebuild the boot
# images, signs the loader through CachyOS's boot hooks with the keys already enrolled.
SENSITIVE = (
    'sbctl',
    'move-zotero-profile',
    'cryptsetup',
    'mkfs',
    'mkswap',
    'btrfs subvolume',
    'restic restore',
)
# The sensitive operations a routine apply may run, each only creating what is missing and deleting
# nothing: power.hibernation's swap file in a subvolume of its own, when the supplement asks for it,
# and the rootless Docker engine's data subvolume, created once before the engine first starts. The two
# scripts that rebuild the boot images also read sbctl, only as test_sbctl_is_only_read allows.
OPT_IN = {
    'scripts/configure-hibernation.py': {'mkswap', 'btrfs subvolume', 'sbctl'},
    'scripts/configure-boot-splash.py': {'sbctl'},
    'roles/workflows/tasks/docker-data.yml': {'btrfs subvolume'},
}
# What those scripts may ask sbctl after a rebuild: whether it has keys, and whether the loader is signed.
SBCTL_READS = {'status', 'verify'}


def included_roles(node):
    """Roles a task list includes or imports, at any depth."""
    found = set()
    if isinstance(node, list):
        for item in node:
            found |= included_roles(item)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key.split('.')[-1] in ('include_role', 'import_role') and isinstance(value, dict):
                found.add(value['name'])
            else:
                found |= included_roles(value)
    return found


def routine_roles():
    """The roles a system apply runs without naming a tag, with the roles they depend on or include."""
    play = yaml.safe_load((ROOT / 'site.yml').read_text())[0]
    roles = {entry['role'] for entry in play['roles'] if 'never' not in entry.get('tags', [])}
    roles |= {'operation'} | included_roles(play.get('pre_tasks', [])) | included_roles(play.get('post_tasks', []))
    pending = list(roles)
    while pending:
        role = ROOT / 'roles' / pending.pop()
        meta = role / 'meta/main.yml'
        found = {
            d['role'] if isinstance(d, dict) else d
            for d in ((yaml.safe_load(meta.read_text()) or {}).get('dependencies', []) if meta.exists() else [])
        }
        for tasks in role.glob('tasks/*.yml'):
            found |= included_roles(yaml.safe_load(tasks.read_text()))
        for name in found - roles:
            roles.add(name)
            pending.append(name)
    return roles


def routine_files():
    files = [ROOT / 'site.yml', ROOT / 'records.yml']
    for role in routine_roles():
        files += [p for p in (ROOT / 'roles' / role).rglob('*') if p.is_file()]
    files += [p for p in (ROOT / 'home').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    # The repository's files and scripts that a routine role or a home source names.
    named = set()
    for path in list(files):
        named |= set(re.findall(r"(?:^|[/'\" ])((?:files|scripts)/[\w./-]+)", path.read_text(errors='replace'), re.M))
    return files + [ROOT / name for name in sorted(named) if (ROOT / name).is_file()]


class RoutineApplyTests(unittest.TestCase):
    def test_nothing_a_routine_apply_runs_reaches_a_sensitive_operation(self):
        found = [
            (str(path.relative_to(ROOT)), word)
            for path in routine_files()
            for word in SENSITIVE
            # A command written as separate arguments ('btrfs', 'subvolume') counts as well.
            if re.search(r'[\'",\s]+'.join(map(re.escape, word.split())), path.read_text(errors='replace'))
            and word not in OPT_IN.get(str(path.relative_to(ROOT)), ())
        ]
        self.assertEqual(found, [])

    def test_sbctl_is_only_read(self):
        # Their one sbctl command is in sbctl_json, and every call of it names a subcommand that only reads.
        for name, words in OPT_IN.items():
            if 'sbctl' in words:
                text = (ROOT / name).read_text()
                self.assertEqual((text.count("'sbctl'"), text.count('"sbctl"')), (1, 0), name)
                self.assertIn("output = run('sbctl', *arguments)", text, name)
                self.assertEqual(text.count('/usr/bin/sbctl'), 1, name)  # only to see whether it is installed
                self.assertIn("path('/usr/bin/sbctl').is_file()", text, name)
                calls = re.findall(r"sbctl_json\(run, '([\w-]+)'", text)
                self.assertTrue(calls, name)
                self.assertLessEqual(set(calls), SBCTL_READS, name)
                self.assertEqual(text.count('sbctl_json('), len(calls) + 1, name)  # its definition and these calls

    def test_secure_boot_is_set_up_by_hand_only(self):
        # It runs sbctl, so the test above also fails once a routine role or a home source names it.
        script = ROOT / 'scripts/configure-secure-boot.py'
        self.assertIn('sbctl', script.read_text())
        self.assertNotIn(script, routine_files())

    def test_every_sensitive_user_command_takes_a_snapshot_pair_first(self):
        for script in ('move-zotero-profile.py',):
            with self.subTest(script=script):
                self.assertIn('home_pair(', (ROOT / 'scripts' / script).read_text())

    def test_root_runs_its_scripts_from_its_own_copies(self):
        # A script read from the checkout at run time could have changed since the operation began.
        import yaml

        prepare = yaml.safe_load((ROOT / 'roles/operation/tasks/prepare.yml').read_text())
        copied = {Path(item).name for task in prepare for item in task.get('loop', []) if isinstance(item, str)}
        scripts = {
            name: value
            for path in ROOT.glob('roles/*/defaults/main.yml')
            for name, value in (yaml.safe_load(path.read_text()) or {}).items()
            if name.endswith('_script')
        }
        self.assertTrue(scripts)
        for name, value in scripts.items():
            self.assertTrue(value.startswith('{{ shaffy_op_dir }}/'), f"{name} runs {value}, not root's copy")
            self.assertIn(Path(value).name, copied, f'{name}: prepare.yml does not copy {Path(value).name}')
            self.assertTrue((ROOT / 'scripts' / Path(value).name).is_file(), name)
        # Nothing lies beside root's copy: a script that looks for files next to itself finds none.
        for name in copied:
            source = ROOT / ('lib/shaffy' if name == 'root_op.py' else 'scripts') / name
            self.assertNotIn('__file__', source.read_text(), f'{name} looks beside itself, where its copy is alone')


if __name__ == '__main__':
    unittest.main()
