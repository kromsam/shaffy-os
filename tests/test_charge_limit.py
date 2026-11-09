"""The battery charge limit: UPower's hardware-database entry and its switch, against a fake UPower."""

import json
from pathlib import Path
import runpy
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = runpy.run_path(str(ROOT / 'scripts/configure-charge-limit.py'))
BAT = '/org/freedesktop/UPower/devices/battery_BAT1'


class FakeUPower:
    def __init__(self, supported=True, enabled=False, sysfs=None):
        self.batteries = {BAT: {'supported': supported, 'enabled': enabled}}
        self.calls = []
        self.sysfs = sysfs

    def __call__(self, *command):
        self.calls.append(command)
        if 'EnumerateDevices' in command:
            return json.dumps(
                {'type': 'ao', 'data': [[*self.batteries, '/org/freedesktop/UPower/devices/line_power_AC']]}
            )
        if 'get-property' in command:
            battery = self.batteries[command[command.index('get-property') + 2]]
            return '\n'.join(json.dumps({'type': 'b', 'data': battery[key]}) for key in ('supported', 'enabled'))
        if 'EnableChargeThreshold' in command:
            self.batteries[command[command.index('call') + 2]]['enabled'] = command[-1] == 'true'
        return ''

    def writes(self):
        return [c for c in self.calls if 'EnableChargeThreshold' in c or c[0] in ('systemd-hwdb', 'systemctl')]


class ChargeLimitTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.hwdb, self.state, self.sysfs = self.tmp / 'hwdb.d', self.tmp / 'state', self.tmp / 'sysfs'
        self.state.mkdir()
        self.upower = FakeUPower()

    def converge(self, levels, apply=True):
        return SCRIPT['converge'](levels, self.hwdb, self.state, apply, run=self.upower, sysfs=self.sysfs)

    def entry(self):
        path = self.hwdb / '61-shaffy-charge-limit.hwdb'
        return path.read_text() if path.exists() else None

    def test_a_limit_is_written_switched_on_and_then_left_alone(self):
        self.assertEqual(self.converge((75, 80)), (True, []))
        self.assertIn(' CHARGE_LIMIT=75,80\n', self.entry())
        self.assertTrue(self.upower.batteries[BAT]['enabled'])
        self.assertIn(('systemctl', 'restart', 'upower.service'), self.upower.calls)
        self.upower.calls.clear()
        self.assertEqual(self.converge((75, 80)), (False, []))
        self.assertEqual(self.upower.writes(), [])

    def test_new_levels_reload_udev_and_upower_and_switch_again(self):
        self.converge((75, 80))
        self.upower.calls.clear()
        self.assertTrue(self.converge((None, 60))[0])
        self.assertIn(' CHARGE_LIMIT=_,60\n', self.entry())
        self.assertIn(('systemd-hwdb', 'update'), self.upower.calls)
        self.assertTrue(any('EnableChargeThreshold' in call for call in self.upower.calls))

    def test_removing_the_setting_undoes_only_what_it_switched_on(self):
        self.converge((75, 80))
        self.assertTrue(self.converge(None)[0])
        self.assertIsNone(self.entry())
        self.assertFalse(self.upower.batteries[BAT]['enabled'])
        self.upower.calls.clear()
        self.assertEqual(self.converge(None), (False, []))
        self.assertEqual(self.upower.calls, [])  # nothing left to do, so UPower is not even asked

    def test_a_limit_someone_else_switched_on_stays(self):
        self.upower.batteries[BAT]['enabled'] = True
        self.assertEqual(self.converge(None), (False, []))
        self.hwdb.mkdir()
        (self.hwdb / '61-shaffy-charge-limit.hwdb').write_text('old entry\n')
        self.assertTrue(self.converge(None)[0])
        self.assertIsNone(self.entry())
        self.assertTrue(self.upower.batteries[BAT]['enabled'])

    def test_an_interrupted_apply_reloads_when_the_entry_is_already_right(self):
        self.converge((75, 80))
        (self.state / 'charge-limit-pending').write_text('pending\n')
        self.upower.calls.clear()
        self.assertTrue(self.converge((75, 80))[0])
        self.assertIn(('systemctl', 'restart', 'upower.service'), self.upower.calls)
        self.assertFalse((self.state / 'charge-limit-pending').exists())

    def test_a_check_reports_without_writing_or_switching(self):
        self.assertEqual(self.converge((75, 80), apply=False), (True, []))
        self.assertIsNone(self.entry())
        self.assertEqual(self.upower.writes(), [])

    def test_a_battery_without_support_is_named_not_failed(self):
        self.upower.batteries[BAT]['supported'] = False
        changed, notes = self.converge((75, 80))
        self.assertEqual(notes, ['no battery here supports a charge limit'])
        self.assertFalse(any('EnableChargeThreshold' in call for call in self.upower.calls))

    def test_the_kernels_limit_is_read_back(self):
        (self.sysfs / 'BAT1').mkdir(parents=True)
        (self.sysfs / 'BAT1/charge_control_end_threshold').write_text('100\n')
        with self.assertRaisesRegex(RuntimeError, 'not 80'):
            self.converge((75, 80))


if __name__ == '__main__':
    unittest.main()
