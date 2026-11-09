#!/usr/bin/python3
"""Hold the batteries to the supplement's charge limit through UPower; default to a check.

UPower reads a battery's charge levels from the udev hardware database (its own
60-upower-battery.hwdb names /etc/udev/hwdb.d/ for local entries), and switches the
limit on and keeps it on across boots. With levels, this script writes their entry and
switches the limit on for every battery that supports one. Without levels, it removes
the entry, and switches the limit off only where its record says it switched it on.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ENTRY = '61-shaffy-charge-limit.hwdb'
UPOWER = ['org.freedesktop.UPower']
DEVICE = 'org.freedesktop.UPower.Device'


def run(*command):
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout


def entry(start, end):
    return (
        "# Managed by Shaffy OS (the power role): the supplement's power.charge_limit.\n"
        'battery:*:*:dmi:*\n'
        f' CHARGE_LIMIT={"_" if start is None else start},{end}\n'
    )


def batteries(run=run):
    """Each battery UPower knows, with whether it supports a charge limit and has it on."""
    listed = json.loads(
        run('busctl', '--json=short', 'call', *UPOWER, '/org/freedesktop/UPower', UPOWER[0], 'EnumerateDevices')
    )
    found = []
    for path in listed['data'][0]:
        if '/battery_' not in path:
            continue
        lines = run(
            'busctl', '--json=short', 'get-property', *UPOWER, path, DEVICE,
            'ChargeThresholdSupported', 'ChargeThresholdEnabled',
        ).splitlines()  # fmt: skip
        supported, enabled = (json.loads(line)['data'] for line in lines)
        found.append((path, supported, enabled))
    return found


def switch(path, on, run=run):
    run('busctl', 'call', *UPOWER, path, DEVICE, 'EnableChargeThreshold', 'b', 'true' if on else 'false')


def reload(run=run):
    """Make udev and UPower read the changed entry."""
    run('systemd-hwdb', 'update')
    run('udevadm', 'trigger', '--action=change', '--subsystem-match=power_supply')
    run('udevadm', 'settle')
    run('systemctl', 'restart', 'upower.service')


def converge(levels, hwdb, state, apply, run=run, sysfs=Path('/sys/class/power_supply')):
    """Bring the entry and the limit to `levels` ((start, end), or None); return whether anything
    changed, or with apply=False would change, and the notes to print."""
    path = hwdb / ENTRY
    pending = state / 'charge-limit-pending'
    record = state / 'charge-limit.json'
    wanted = entry(*levels) if levels else None
    current = path.read_text() if path.exists() else None
    if not levels and current is None and not record.exists() and not pending.exists():
        return False, []
    changed = wanted != current or pending.exists()
    found = batteries(run)
    supported = [(p, enabled) for p, s, enabled in found if s]
    notes = [] if supported or not levels else ['no battery here supports a charge limit']
    to_switch = [p for p, enabled in supported if (bool(levels) != enabled) or (levels and changed)]
    if not levels and not record.exists():
        to_switch = []  # a limit someone else switched on stays
    if not apply:
        return changed or bool(to_switch) or (not levels and record.exists()), notes
    if wanted != current:
        # Written first: an interrupted apply finds it and reloads even when the entry is right.
        pending.write_text('Managed by Shaffy OS (power role); remove only after udev and UPower reload.\n')
        if wanted is None:
            path.unlink()
        else:
            hwdb.mkdir(parents=True, exist_ok=True)
            path.write_text(wanted)
            os.chmod(path, 0o644)
    if pending.exists():
        reload(run)
    if levels and to_switch:
        record.write_text(json.dumps({'switched_on': True}) + '\n')
    for battery in to_switch:
        switch(battery, bool(levels), run)
    if not levels:
        record.unlink(missing_ok=True)
    pending.unlink(missing_ok=True)
    if levels:
        for battery, _ in supported:
            limit = sysfs / battery.rsplit('battery_', 1)[1] / 'charge_control_end_threshold'
            if limit.exists() and limit.read_text().strip() != str(levels[1]):
                raise RuntimeError(f'{limit} reads {limit.read_text().strip()}, not {levels[1]}')
    return changed or bool(to_switch), notes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='report what would change; the default')
    mode.add_argument('--apply', action='store_true', help='write the entry and switch the limit')
    parser.add_argument('--start', type=int, help='start charging again below this level')
    parser.add_argument('--end', type=int, help='stop charging at this level; leave out to remove the limit')
    parser.add_argument('--hwdb-dir', type=Path, default=Path('/etc/udev/hwdb.d'))
    parser.add_argument('--state-dir', type=Path, default=Path('/var/lib/shaffy'))
    args = parser.parse_args(argv)
    if args.start is not None and args.end is None:
        parser.error('--start needs --end')
    levels = (args.start, args.end) if args.end is not None else None
    changed, notes = converge(levels, args.hwdb_dir, args.state_dir, args.apply)
    for note in notes:
        print(f'note: {note}')
    print(f'changed: {int(changed)}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(f'configure-charge-limit: {error}')
