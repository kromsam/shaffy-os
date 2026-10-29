import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('libreoffice_layout', ROOT / 'scripts/configure-libreoffice.py')
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)

OOR = layout.OOR
BASE = layout.ROOT


def registry(application_path):
    """A saved profile with Tabbed selected and one unrelated setting."""
    items = [
        f'<item oor:path="{BASE}"><prop oor:name="Active{app}" oor:op="fuse">'
        f'<value>notebookbar.ui</value></prop></item>'
        for app in layout.APPS
    ]
    items += [
        f'<item oor:path="{application_path(app)}"><prop oor:name="Active" oor:op="fuse">'
        f'<value>notebookbar.ui</value></prop></item>'
        for app in layout.APPS
    ]
    items.append(
        '<item oor:path="/org.openoffice.Office.Common/Misc">'
        '<prop oor:name="FirstRun" oor:op="fuse"><value>false</value></prop></item>'
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="{OOR}">' + ''.join(items) + '</oor:items>'
    ).encode()


class LibreOfficeLayoutTests(unittest.TestCase):
    def test_profile_saved_by_libreoffice_is_already_tabbed(self):
        # LibreOffice rewrites Applications/<app> into its long path form on save.
        before = registry(lambda app: layout.application_paths(app)[0])
        after, changed = layout.tabbed_config(before)
        self.assertFalse(changed)
        self.assertEqual(after, before)

    def test_short_path_form_is_also_recognised(self):
        before = registry(lambda app: f'{BASE}/Applications/{app}')
        self.assertFalse(layout.tabbed_config(before)[1])

    def test_empty_profile_gains_all_eight_settings_once(self):
        after, changed = layout.tabbed_config(None)
        self.assertTrue(changed)
        text = after.decode()
        self.assertEqual(text.count('notebookbar.ui'), 8)
        for app in layout.APPS:
            self.assertIn(layout.application_paths(app)[0], text)
        again, changed_again = layout.tabbed_config(after)
        self.assertFalse(changed_again)
        self.assertEqual(again, after)

    def test_classic_layout_is_switched_without_touching_unrelated_settings(self):
        before = registry(lambda app: layout.application_paths(app)[0]).replace(
            b'<value>notebookbar.ui</value>', b'<value>Default</value>'
        )
        after, changed = layout.tabbed_config(before)
        self.assertTrue(changed)
        self.assertEqual(after.decode().count('notebookbar.ui'), 8)
        self.assertNotIn('Default', after.decode())
        self.assertIn('FirstRun', after.decode())
        self.assertEqual(after.decode().count('<item'), before.decode().count('<item'))


if __name__ == '__main__':
    unittest.main()
