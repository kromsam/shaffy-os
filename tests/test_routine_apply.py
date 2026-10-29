"""Routine applies cannot replay restoration, rename personal folders, enroll firmware keys or
convert disks; those stay explicit, guarded commands."""

from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
# Commands and markers of the sensitive operations, which only explicit tools may contain.
SENSITIVE = (
    'configure-swap',
    'configure-secure-boot',
    'bootstrap-secure-boot',
    'sbctl',
    'configure-plymouth',
    'apply-plymouth-theme',
    'migrate-xdg-dutch',
    'move-zotero-profile',
    'retire-migration-staging',
    'prepare-login-keyring',
    'cryptsetup',
    'mkfs',
    'mkswap',
    'btrfs subvolume',
    'restic restore',
    'migration-staging',
    'inspect-nextcloud-runtime',
)


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
            if word in path.read_text(errors='replace')
        ]
        self.assertEqual(found, [])

    def test_the_keyring_migration_runs_only_when_named(self):
        play = yaml.safe_load((ROOT / 'site.yml').read_text())[0]
        entry = next(e for e in play['roles'] if e['role'] == 'login_keyring_migration')
        self.assertIn('never', entry['tags'])
        self.assertNotIn('login_keyring_migration', routine_roles())

    def test_every_sensitive_user_command_takes_a_snapshot_pair_first(self):
        for script in ('move-zotero-profile.py', 'inspect-nextcloud-runtime.py'):
            with self.subTest(script=script):
                self.assertIn('home_pair(', (ROOT / 'scripts' / script).read_text())


if __name__ == '__main__':
    unittest.main()
