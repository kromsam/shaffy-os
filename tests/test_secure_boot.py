"""Secure Boot setup against a fake CachyOS in a temporary root: Limine with CachyOS's entry tool and
boot hooks, and sbctl and the firmware's key store, whose commands act on the files there."""

import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
import runpy
import shutil
import ssl
import stat
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = runpy.run_path(str(ROOT / 'scripts/configure-secure-boot.py'))
HIBERNATION = runpy.run_path(str(ROOT / 'scripts/configure-hibernation.py'))
SecureBoot = SCRIPT['SecureBoot']
MACHINE_ID = '0123456789abcdef0123456789abcdef'
GUID = '01234567-89ab-cdef-0123-456789abcdef'  # another vendor's variables
EFI_GLOBAL = SCRIPT['EFI_GLOBAL']
KERNEL = 'linux-cachyos'
DEFAULTS = 'ESP_PATH="/boot"\nKERNEL_CMDLINE[default]+="quiet splash rw rootflags=subvol=/@"\n'
# The loader as limine-install deploys it: an empty slot for the configuration's checksum.
LOADER = b'MZ limine 12 [' + b'0' * 128 + b']'
SIGNATURE = b'|signed with '
MICROSOFT = {
    'KEK': ['Microsoft Corporation KEK CA 2011', 'Microsoft Corporation KEK 2K CA 2023'],
    'DB': ['Microsoft Windows Production PCA 2011', 'Microsoft UEFI CA 2023', 'Microsoft Option ROM UEFI CA 2023'],
}
VENDOR = {'PK': ['Vendor Platform Key'], 'KEK': ['Vendor KEK'], 'DB': ['Vendor DB']}
WRITES = ('create-keys', 'enroll-keys', 'sign', 'limine-mkinitcpio', 'limine-enroll-config')
# What this tool must never ask sbctl or the machine for.
FORBIDDEN = (
    '--firmware-builtin',
    '-f',
    '--yes-this-might-brick-my-machine',
    '--ignore-immutable',
    '--append',
    '--partial',
    'reset',
    'rotate-keys',
    'remove-file',
    'reboot',
    'tpm2_clear',
    'chattr',
)


def b2(data):
    return hashlib.blake2b(data).hexdigest()


class FakeMachine:
    """A fresh CachyOS installation in `root`, its firmware in Setup Mode; called as the script's runner,
    it acts on it."""

    def __init__(self, root, windows=False, fwupd=False):
        self.root, self.calls, self.windows = root, [], windows
        self.mounts = {'/': {'fstype': 'btrfs', 'fsroot': '/@'}, '/boot': {'fstype': 'vfat', 'fsroot': '/'}}
        self.version, self.fail_enrol, self.option_rom = 'Limine 12.9.6', False, False
        self.own = None  # this machine's certificates once sbctl made them
        self.firmware = {'PK': [], 'KEK': [], 'DB': []}
        self.database = []
        self.hook_before_keys = None
        files = {
            'etc/default/limine': DEFAULTS,
            'etc/limine-entry-tool.conf': '#ENABLE_ENROLL_LIMINE_CONFIG=no\nENABLE_VERIFICATION=yes\nENABLE_UKI=no\n',
            'etc/mkinitcpio.conf': 'HOOKS=(base systemd autodetect microcode kms modconf block keyboard '
            'sd-vconsole plymouth sd-encrypt filesystems)\n',
            'etc/boot/hooks/pre.d/10-limine-reset-enroll': '#!/bin/sh\n',
            'etc/boot/hooks/post.d/89-warn-missing-file-hashes': '#!/bin/sh\n',
            'etc/boot/hooks/post.d/90-limine-enroll-config': '#!/bin/sh\n',
            'etc/boot/hooks/post.d/91-example-esp-set-ro.disabled': '#!/bin/sh\n',
            'sys/kernel/security/lockdown': '[none] integrity confidentiality\n',
            'sys/power/resume': '0:0\n',
            'sys/power/resume_offset': '0\n',
            'sys/power/disk': '[platform] shutdown reboot suspend test_resume\n',
            **{f'usr/bin/{tool}': '' for tool in SCRIPT['TOOLS'] + ('sbctl',)},
            'boot/limine-splash.png': 'png',
            f'boot/{MACHINE_ID}/{KERNEL}/vmlinuz-{KERNEL}': 'MZ kernel',
            f'boot/{MACHINE_ID}/{KERNEL}/initramfs-{KERNEL}': 'initramfs',
        }
        if windows:
            files['boot/EFI/Microsoft/Boot/bootmgfw.efi'] = 'MZ windows'
        if fwupd:
            files['usr/lib/fwupd/efi/fwupdx64.efi'] = 'MZ fwupd'
            files['etc/fwupd/fwupd.conf'] = '[fwupd]\n# use `man 5 fwupd.conf` for documentation\n'
        for name, text in files.items():
            self.host(name).parent.mkdir(parents=True, exist_ok=True)
            self.host(name).write_text(text)
        for hook in self.host('etc/boot/hooks').rglob('*'):
            hook.chmod(0o755)
        self.host('boot/EFI/limine').mkdir(parents=True)
        self.host('boot/EFI/limine/limine_x64.efi').write_bytes(LOADER)
        self.host('var/lib/pacman').mkdir(parents=True)
        self.set_flags(secure_boot=False, setup=True)
        self.regenerate()

    def host(self, path):
        return self.root / path.lstrip('/')

    def set_flags(self, secure_boot, setup):
        efivars = self.host('sys/firmware/efi/efivars')
        efivars.mkdir(parents=True, exist_ok=True)
        (efivars / f'SecureBoot-{EFI_GLOBAL}').write_bytes(bytes([6, 0, 0, 0, int(secure_boot)]))
        (efivars / f'SetupMode-{EFI_GLOBAL}').write_bytes(bytes([6, 0, 0, 0, int(setup)]))
        (efivars / f'SecureBoot-{GUID}').write_bytes(bytes([6, 0, 0, 0, 1]))  # a vendor's own, never read
        for name in ('PK', 'KEK'):
            (efivars / f'{name}-{EFI_GLOBAL}').write_bytes(repr(self.firmware[name]).encode())
        (efivars / f'db-{GUID}').write_bytes(repr(self.firmware['DB']).encode())

    def vendor_keys(self):
        """The firmware's own keys, as it ships them, out of Setup Mode."""
        self.firmware = {db: [(name.encode(), name) for name in names] for db, names in VENDOR.items()}
        self.set_flags(secure_boot=False, setup=False)

    def settings(self):
        values = {}
        for name in ('etc/limine-entry-tool.conf', *sorted(self.host('etc/limine-entry-tool.d').glob('*.conf'))):
            path = self.host(str(name))
            if path.exists():
                values.update(re.findall(r'(?m)^\s*(\w+)=(\S*)', path.read_text()))
        values.pop('ENABLE_ENROLL_LIMINE_CONFIG', None)
        values.update(re.findall(r'(?m)^\s*(\w+)=(\S*)', self.host('etc/default/limine').read_text()))
        return values

    def regenerate(self):
        """limine-mkinitcpio: the entries anew, with hashes under ENABLE_VERIFICATION, then the post hooks:
        enrol limine.conf in a clean loader when ENABLE_ENROLL_LIMINE_CONFIG says so, and sign it once
        sbctl has keys. Top-level options, such as the wallpaper, stay as they are."""
        settings, conf = self.settings(), self.host('boot/limine.conf')
        top = conf.read_text().split('\n/', 1)[0].splitlines() if conf.exists() else ['timeout: 5']
        if not any(line.startswith('wallpaper:') for line in top):
            top.append('wallpaper: boot():/limine-splash.png')
        verify = settings.get('ENABLE_VERIFICATION') == 'yes'

        def file(name):
            data = self.host(f'boot/{MACHINE_ID}/{KERNEL}/{name}').read_bytes()
            return f'boot():/{MACHINE_ID}/{KERNEL}/{name}' + (f'#{b2(data)}' if verify else '')

        lines = [*top, '', '/+CachyOS', f'comment: machine-id={MACHINE_ID}', f'  //{KERNEL}', '  protocol: linux']
        lines += [f'  path: {file(f"vmlinuz-{KERNEL}")}', f'  module_path: {file(f"initramfs-{KERNEL}")}']
        lines += ['  cmdline: quiet splash rw rootflags=subvol=/@', '']
        if self.windows:
            lines += ['/Windows', 'protocol: efi', 'path: boot():/EFI/Microsoft/Boot/bootmgfw.efi', '']
        conf.write_text('\n'.join(lines))
        self.enrol_config()

    def enrol_config(self):
        """The reset and enrolment hooks, and limine-enroll-config: a clean loader, limine.conf's checksum in
        it when ENABLE_ENROLL_LIMINE_CONFIG says so, and the signature once sbctl has keys."""
        loader = LOADER
        if self.settings().get('ENABLE_ENROLL_LIMINE_CONFIG') == 'yes' and not self.fail_enrol:
            loader = loader.replace(b'0' * 128, b2(self.host('boot/limine.conf').read_bytes()).encode())
        if self.own and not self.fail_enrol:
            loader += SIGNATURE + self.own['DB']
        self.host('boot/EFI/limine/limine_x64.efi').write_bytes(loader)

    def create_keys(self):
        self.hook_before_keys = self.host(SCRIPT['KERNEL_HOOK']).is_file()
        self.own = {db: f'this machine {db}'.encode() for db in ('PK', 'KEK', 'DB')}
        for db, name in (('PK', 'PK'), ('KEK', 'KEK'), ('DB', 'db')):
            folder = self.host(f'var/lib/sbctl/keys/{name}')
            folder.mkdir(parents=True)
            (folder / f'{name}.pem').write_text(ssl.DER_cert_to_PEM_cert(self.own[db]))
            (folder / f'{name}.key').write_text('private')
            (folder / f'{name}.key').chmod(0o400)

    def enrol(self, arguments):
        if not self.flag('SetupMode'):
            raise RuntimeError('sbctl enroll-keys failed: the firmware is not in Setup Mode')
        if self.option_rom and '--microsoft' not in arguments:
            raise RuntimeError('sbctl enroll-keys failed: found an option ROM in the TPM event log')
        extra = MICROSOFT if '--microsoft' in arguments else {}
        self.firmware = {
            db: [(self.own[db], f'own {db}')] + [(n.encode(), n) for n in extra.get(db, [])] for db in self.own
        }
        self.set_flags(secure_boot=False, setup=False)

    def flag(self, name):
        return next(self.host('sys/firmware/efi/efivars').glob(f'{name}-*')).read_bytes()[4] == 1

    def signed(self, path):
        return self.own is not None and self.host(path).read_bytes().endswith(SIGNATURE + self.own['DB'])

    def __call__(self, *command):
        self.calls.append(command)
        assert not set(command) & set(FORBIDDEN), command
        match command:
            case ('findmnt', '--json', '--mountpoint', target, *_):
                if target not in self.mounts:
                    raise RuntimeError(f'findmnt: {target} is not mounted')
                return json.dumps({'filesystems': [{'source': '/dev/x', **self.mounts[target]}]})
            case ('limine', '--version'):
                return f'{self.version}\nCopyright (C) 2019-2026 Mintsuki and contributors.\n'
            case ('sbctl', 'status', '--json'):
                return json.dumps({'installed': self.own is not None, 'setup_mode': self.flag('SetupMode')})
            case ('sbctl', 'create-keys'):
                self.create_keys()
                return 'Created Owner UUID\nCreating secure boot keys...✓\n'
            case ('sbctl', 'list-enrolled-keys', '--json'):
                return json.dumps(
                    {
                        db: [{'Raw': base64.b64encode(der).decode(), 'Subject': {'CommonName': n}} for der, n in keys]
                        for db, keys in self.firmware.items()
                    }
                )
            case ('sbctl', 'enroll-keys', *arguments):
                self.enrol(arguments)
                return 'Enrolling keys to EFI variables...✓\n'
            case ('sbctl', 'verify', '--json', path):
                return json.dumps([{'file_name': path, 'is_signed': 1 if self.signed(path) else 0}])
            case ('sbctl', 'list-files', '--json'):
                return json.dumps(
                    [{**entry, 'is_signed': self.signed(entry['output_file'])} for entry in self.database]
                )
            case ('sbctl', 'sign', '-s', '-o', output, source):
                self.host(output).write_bytes(self.host(source).read_bytes() + SIGNATURE + self.own['DB'])
                self.database.append({'file': source, 'output_file': output})
            case ('limine-mkinitcpio',):
                self.regenerate()
            case ('limine-enroll-config',):
                self.enrol_config()
            case _:
                raise AssertionError(f'unexpected command {command}')
        return ''

    def writes(self):
        return [c for c in self.calls if c[0] in WRITES or (len(c) > 1 and c[1] in WRITES)]

    def files(self):
        """Every file under the root with its content and mode, but the lock the tools share."""
        return {
            str(p.relative_to(self.root)): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
            for p in sorted(self.root.rglob('*'))
            if p.is_file() and not p.is_symlink() and 'run/lock' not in str(p)
        }


class SecureBootCase(unittest.TestCase):
    def setUp(self, **machine):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root, self.state = Path(directory.name, 'root'), Path(directory.name, 'state')
        self.root.mkdir()
        self.machine = FakeMachine(self.root, **machine)

    def tool(self):
        return SecureBoot(self.root, self.machine, self.state)

    def quietly(self, step, *arguments):
        """Run a step of a new tool, returning its result, notes and what it printed to stderr."""
        tool, err = self.tool(), io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            result = getattr(tool, step)(*arguments)
        return result, tool.notes, err.getvalue()

    def check(self):
        lines, problems = self.tool().check()
        return '\n'.join(lines), problems

    def set_up(self, microsoft=True):
        """Prepare, enrol and start with Secure Boot on, as a person would."""
        self.quietly('prepare')
        self.quietly('enroll', microsoft)
        self.machine.set_flags(secure_boot=True, setup=False)

    def read(self, name):
        return self.machine.host(name).read_text()

    def state_files(self):
        return {str(p): p.read_bytes() for p in sorted(self.state.rglob('*')) if p.is_file()}

    def assertRefusedUnchanged(self, step, *arguments, message=''):
        before, state = self.machine.files(), self.state_files()
        self.machine.calls.clear()
        with self.assertRaisesRegex(RuntimeError, message):
            self.quietly(step, *arguments)
        self.assertEqual(self.machine.writes(), [])
        self.assertEqual(self.machine.files(), before)
        self.assertEqual(self.state_files(), state)


class GuardTests(SecureBootCase):
    KERNEL_PATH = f'/boot/{MACHINE_ID}/{KERNEL}/vmlinuz-{KERNEL}'
    # What breaks the baseline or the step, and the refusal that names it.
    BREAKERS = {
        'did not start through UEFI': lambda m: shutil.rmtree(m.host('sys/firmware/efi')),
        'limine-entry-tool is missing': lambda m: m.host('usr/bin/limine-entry-tool').unlink(),
        'older than 12': lambda m: setattr(m, 'version', 'Limine 11.4.1'),
        'post.d/ runs limine-enroll-config': lambda m: m.host('etc/boot/hooks/post.d/90-limine-enroll-config').unlink(),
        'pre.d/ runs limine-reset-enroll': lambda m: m.host('etc/boot/hooks/pre.d/10-limine-reset-enroll').rename(
            m.host('etc/boot/hooks/pre.d/10-limine-reset-enroll.disabled')
        ),
        r'not mounted at /boot \(ESP_PATH /efi\)': lambda m: (
            m.host('etc/default/limine').write_text(DEFAULTS.replace('/boot', '/efi')),
            m.mounts.update({'/efi': {'fstype': 'vfat', 'fsroot': '/'}}),
        ),
        r'not mounted at /boot \(ESP_PATH /boot\)': lambda m: m.mounts.update(
            {'/boot': {'fstype': 'btrfs', 'fsroot': '/@boot'}}
        ),
        'ENABLE_UKI=yes': lambda m: m.host('etc/limine-entry-tool.conf').write_text('ENABLE_UKI=yes\n'),
        'HOOKS lack systemd': lambda m: m.host('etc/mkinitcpio.conf').write_text(
            'HOOKS=(base udev autodetect modconf block encrypt filesystems fsck)\n'
        ),
        'runs from a snapshot': lambda m: m.mounts.update(
            {'/': {'fstype': 'btrfs', 'fsroot': '/@/.snapshots/4/snapshot'}}
        ),
        'package transaction is running': lambda m: m.host('var/lib/pacman/db.lck').write_text(''),
        'sbctl is not installed': lambda m: m.host('usr/bin/sbctl').unlink(),
        'sbctl.conf keeps the keys in /elsewhere': lambda m: (
            m.host('etc/sbctl').mkdir(),
            m.host('etc/sbctl/sbctl.conf').write_text('keydir: /elsewhere\n'),
        ),
        'Limine reads it instead': lambda m: m.host('boot/EFI/limine/limine.conf').write_text('timeout: 1\n'),
        'not in Setup Mode': lambda m: m.vendor_keys(),
        'sets ENABLE_ENROLL_LIMINE_CONFIG=no outside': lambda m: m.host('etc/default/limine').write_text(
            DEFAULTS + 'ENABLE_ENROLL_LIMINE_CONFIG=no\n'
        ),
        'malformed': lambda m: m.host('etc/default/limine').write_text(DEFAULTS + SCRIPT['BEGIN']),
        'does more than exit': lambda m: (
            m.host('etc/initcpio/post').mkdir(parents=True),
            m.host(SCRIPT['KERNEL_HOOK']).write_text('#!/bin/sh\nsbctl sign "$1"\n'),
        ),
        'sbctl signs .*vmlinuz': lambda m: m.database.append(
            {'file': GuardTests.KERNEL_PATH, 'output_file': GuardTests.KERNEL_PATH}
        ),
    }

    def test_every_refusal_of_prepare_comes_before_any_write(self):
        for refusal, breaker in self.BREAKERS.items():
            with self.subTest(refusal=refusal):
                self.setUp()
                breaker(self.machine)
                self.assertRefusedUnchanged('prepare', message=refusal)

    def test_sbctls_configuration_with_its_usual_key_folder_is_accepted(self):
        self.machine.host('etc/sbctl').mkdir()
        self.machine.host('etc/sbctl/sbctl.conf').write_text('keydir: /var/lib/sbctl/keys\n')
        self.assertTrue(self.quietly('prepare')[0])

    def test_a_shim_setting_kept_on_purpose_is_refused_before_any_write(self):
        self.setUp(fwupd=True)
        self.machine.host('etc/fwupd/fwupd.conf').write_text('[uefi_capsule]\nDisableShimForSecureBoot=false\n')
        self.assertRefusedUnchanged('prepare', message='DisableShimForSecureBoot=false')

    def test_enrolment_needs_the_keys_and_a_prepared_boot_chain(self):
        self.assertRefusedUnchanged('enroll', True, message='run --prepare first')
        self.quietly('prepare')
        self.machine.host('boot/EFI/limine/limine_x64.efi').write_bytes(LOADER)  # unsigned, as before
        self.assertRefusedUnchanged('enroll', True, message='run --prepare first')

    def test_enrolment_needs_setup_mode_and_deletes_no_keys_to_get_there(self):
        self.quietly('prepare')
        self.machine.vendor_keys()
        self.machine.calls.clear()
        with self.assertRaisesRegex(RuntimeError, 'not in Setup Mode'):
            self.quietly('enroll', True)
        self.assertEqual(self.machine.writes(), [])
        self.assertEqual([der for der, _ in self.machine.firmware['PK']], [b'Vendor Platform Key'])

    def test_main_runs_as_root_only_and_takes_microsoft_with_enroll_only(self):
        main = SCRIPT['main']
        with (
            mock.patch('os.geteuid', return_value=1000),
            contextlib.redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit),
        ):
            main(['--check'])
        self.assertIn('run it as root', err.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            main(['--prepare', '--no-microsoft'])
        self.assertIn('go with --enroll', err.getvalue())


class PrepareTests(SecureBootCase):
    def test_prepare_signs_the_boot_chain_with_new_keys(self):
        changed, notes, _ = self.quietly('prepare')
        self.assertTrue(changed)
        self.assertTrue(self.machine.hook_before_keys, 'the kernel hook comes before the keys')
        hook = self.machine.host(SCRIPT['KERNEL_HOOK'])
        self.assertEqual((hook.read_text(), stat.S_IMODE(hook.stat().st_mode)), (SCRIPT['KERNEL_HOOK_TEXT'], 0o755))
        self.assertEqual(
            self.read('etc/default/limine'),
            DEFAULTS
            + '\n'
            + SCRIPT['BEGIN']
            + 'ENABLE_ENROLL_LIMINE_CONFIG=yes\nENABLE_VERIFICATION=yes\nENABLE_UKI=no\n'
            + SCRIPT['END'],
        )
        splash = b2(self.machine.host('boot/limine-splash.png').read_bytes())
        self.assertIn(f'wallpaper: boot():/limine-splash.png#{splash}\n', self.read('boot/limine.conf'))
        loader = self.machine.host('boot/EFI/limine/limine_x64.efi').read_bytes()
        self.assertIn(b2(self.machine.host('boot/limine.conf').read_bytes()).encode(), loader)
        self.assertTrue(self.machine.signed('/boot/EFI/limine/limine_x64.efi'))
        self.assertNotIn(('sbctl', 'enroll-keys', '--microsoft'), self.machine.calls)
        self.assertEqual(self.machine.firmware['PK'], [], 'preparing enrols nothing')
        self.assertIn('next: --enroll, with the firmware still in Setup Mode', notes)
        self.assertEqual(self.check()[1], [])

    def test_prepare_copies_each_file_first_and_records_the_result(self):
        before = self.machine.files()
        self.quietly('prepare')
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        (copies,) = self.state.glob('before-*')
        self.assertEqual(stat.S_IMODE(copies.stat().st_mode), 0o700)
        for name in ('etc/default/limine', 'boot/limine.conf', 'boot/EFI/limine/limine_x64.efi'):
            self.assertEqual((copies / name).read_bytes(), before[name][0], name)
        record = json.loads((self.state / 'secure-boot.json').read_text())
        self.assertIn('created the keys in /var/lib/sbctl/keys', record['prepared']['changes'])

    def test_a_repeated_prepare_changes_nothing(self):
        self.quietly('prepare')
        before, record = self.machine.files(), (self.state / 'secure-boot.json').read_text()
        self.machine.calls.clear()
        changed, _, _ = self.quietly('prepare')
        self.assertFalse(changed)
        self.assertEqual(self.machine.writes(), [])
        self.assertEqual(self.machine.files(), before)
        self.assertEqual(len(list(self.state.glob('before-*'))), 1)
        self.assertEqual((self.state / 'secure-boot.json').read_text(), record)

    def test_a_failed_enrolment_hook_stops_prepare_and_the_next_one_finishes(self):
        self.machine.fail_enrol = True
        with self.assertRaisesRegex(RuntimeError, 'after limine-mkinitcpio: .*not signed'):
            self.quietly('prepare')
        self.machine.fail_enrol = False
        changed, _, _ = self.quietly('prepare')
        self.assertTrue(changed)
        self.assertEqual(self.check()[1], [])

    def test_a_changed_wallpaper_is_enrolled_at_once_after_the_edit(self):
        self.set_up()
        self.machine.host('boot/limine-splash.png').write_text('another png')
        self.machine.calls.clear()
        self.assertTrue(self.quietly('prepare')[0])
        writes = [c[0] for c in self.machine.writes()]
        self.assertEqual(writes, ['limine-enroll-config', 'limine-mkinitcpio'])
        self.assertEqual(self.check()[1], [])

    def test_a_failed_rebuild_after_enrolment_warns_against_starting_with_secure_boot(self):
        self.set_up()
        self.machine.host(SCRIPT['KERNEL_HOOK']).unlink()
        self.machine.fail_enrol = True
        with self.assertRaisesRegex(RuntimeError, 'do not start the machine with Secure Boot on'):
            self.quietly('prepare')

    def test_the_private_keys_are_never_read(self):
        real = open

        def guarded(file, mode='r', *arguments, **options):
            if str(file).endswith('.key') and mode.startswith('r'):
                raise AssertionError(f'read {file}')
            return real(file, mode, *arguments, **options)

        with mock.patch('builtins.open', guarded), mock.patch('io.open', guarded):
            self.set_up()
            self.check()

    def test_after_enrolment_prepare_restores_its_files_without_setup_mode(self):
        self.set_up()
        self.machine.host(SCRIPT['KERNEL_HOOK']).unlink()
        self.machine.calls.clear()
        changed, _, _ = self.quietly('prepare')
        self.assertTrue(changed)
        self.assertTrue(self.machine.host(SCRIPT['KERNEL_HOOK']).is_file())
        self.assertNotIn(('sbctl', 'create-keys'), self.machine.calls)
        self.assertFalse(any(c[:2] == ('sbctl', 'enroll-keys') for c in self.machine.calls))

    def test_an_earlier_setup_that_does_the_same_is_accepted_as_it_is(self):
        """Secure Boot set up by hand or by a supplement's tool: its own lines and an override that only exits."""
        self.set_up()
        theirs = '# BEGIN another tool\nENABLE_ENROLL_LIMINE_CONFIG=yes\nENABLE_VERIFICATION=yes\nENABLE_UKI=no\n'
        self.machine.host('etc/default/limine').write_text(DEFAULTS + theirs + '# END another tool\n')
        self.machine.host(SCRIPT['KERNEL_HOOK']).write_text('#!/bin/bash\n# Another tool.\nexit 0\n')
        before = self.machine.files()
        self.machine.calls.clear()
        changed, _, _ = self.quietly('prepare')
        self.assertFalse(changed)
        self.assertEqual((self.machine.writes(), self.machine.files()), ([], before))
        report, problems = self.check()
        self.assertEqual(problems, [])
        self.assertIn(
            'Limine settings: ENABLE_ENROLL_LIMINE_CONFIG=yes ENABLE_VERIFICATION=yes ENABLE_UKI=no\n', report
        )
        self.assertIn('not written by Shaffy OS', report)

    def test_power_hibernation_still_regenerates_with_this_configuration(self):
        self.set_up()
        hibernation = HIBERNATION['Hibernation'](self.root, self.machine, self.state, False, lambda path: 0)
        self.assertEqual(HIBERNATION['limine_settings'](hibernation.path)['ENABLE_ENROLL_LIMINE_CONFIG'], 'yes')
        self.assertEqual(hibernation.limine(), '/boot/limine.conf')
        # A regeneration, such as power.hibernation or the boot splash runs, enrols and signs again.
        self.machine.host(f'boot/{MACHINE_ID}/{KERNEL}/initramfs-{KERNEL}').write_text('initramfs with a resume')
        self.machine('limine-mkinitcpio')
        self.assertEqual(self.check()[1], [])


class EnrolTests(SecureBootCase):
    def enrolments(self):
        return [c for c in self.machine.calls if c[:2] == ('sbctl', 'enroll-keys')]

    def test_enrolment_keeps_microsofts_keys_by_default(self):
        self.quietly('prepare')
        changed, notes, err = self.quietly('enroll', True)
        self.assertTrue(changed)
        self.assertEqual(self.enrolments(), [('sbctl', 'enroll-keys', '--microsoft')])
        self.assertEqual(err, '')
        self.assertEqual([der for der, _ in self.machine.firmware['PK']], [self.machine.own['PK']])
        self.assertIn('Microsoft UEFI CA 2023', [n for _, n in self.machine.firmware['DB']])
        record = json.loads((self.state / 'secure-boot.json').read_text())
        self.assertTrue(record['enrolled']['microsoft'])
        self.assertNotIn('enrolling', record)
        self.assertTrue(any('turn Secure Boot on' in note for note in notes))
        (copies,) = (c for c in self.state.glob('before-*') if (c / 'sys').exists())
        for name in (f'SetupMode-{EFI_GLOBAL}', f'PK-{EFI_GLOBAL}', f'KEK-{EFI_GLOBAL}', f'db-{GUID}'):
            self.assertTrue((copies / 'sys/firmware/efi/efivars' / name).is_file(), name)

    def test_without_microsofts_keys_it_warns_and_enrols_only_this_machines(self):
        self.quietly('prepare')
        changed, _, err = self.quietly('enroll', False)
        self.assertTrue(changed)
        self.assertEqual(self.enrolments(), [('sbctl', 'enroll-keys')])
        self.assertIn("warning: Enrolling without Microsoft's keys", err)
        self.assertIn('option ROMs', err)
        self.assertEqual({n for db in self.machine.firmware.values() for _, n in db}, {'own PK', 'own KEK', 'own DB'})

    def test_without_microsofts_keys_windows_is_refused(self):
        self.setUp(windows=True)
        self.quietly('prepare')
        self.machine.calls.clear()
        with self.assertRaisesRegex(RuntimeError, "Windows's boot manager"):
            self.quietly('enroll', False)
        self.assertEqual(self.enrolments(), [])

    def test_sbctls_safeguard_is_never_forced(self):
        self.quietly('prepare')
        self.machine.option_rom = True
        with self.assertRaisesRegex(RuntimeError, 'option ROM'):
            self.quietly('enroll', False)
        self.assertEqual(self.enrolments(), [('sbctl', 'enroll-keys')])
        self.assertNotIn('enrolling', json.loads((self.state / 'secure-boot.json').read_text()))

    def test_an_enrolment_that_finished_unrecorded_is_recorded_by_the_next_run(self):
        self.quietly('prepare')
        self.tool().keep(enrolling={'time': '2026-11-09T12:00:00+00:00', 'microsoft': True})
        self.machine('sbctl', 'enroll-keys', '--microsoft')  # then the terminal closed
        self.assertTrue(any('was not recorded as done' in p for p in self.check()[1]))
        changed, _, _ = self.quietly('enroll', True)
        self.assertFalse(changed)
        record = json.loads((self.state / 'secure-boot.json').read_text())
        self.assertNotIn('enrolling', record)
        self.assertTrue(record['enrolled']['microsoft'])
        self.assertEqual(self.check()[1], [])

    def test_enrolment_names_the_certificates_it_replaces(self):
        self.quietly('prepare')
        self.machine.firmware = {'PK': [], 'KEK': [(b'Vendor KEK', 'Vendor KEK')], 'DB': [(b'Vendor DB', 'Vendor DB')]}
        _, _, err = self.quietly('enroll', True)
        self.assertIn('no longer trust Vendor DB, Vendor KEK', err)

    def test_a_disk_unlocked_by_the_tpm_is_warned_about(self):
        self.machine.host('etc/crypttab').write_text('cryptroot /dev/nvme0n1p2 none tpm2-device=auto\n')
        self.quietly('prepare')
        _, _, err = self.quietly('enroll', True)
        self.assertIn('systemd-cryptenroll', err)
        tool = self.tool()
        tool.check()
        self.assertTrue(any('unlocks with the TPM' in note for note in tool.notes))

    def test_a_repeated_enrolment_writes_no_keys_again(self):
        self.quietly('prepare')
        self.quietly('enroll', True)
        # Some firmware reports Setup Mode until the next start; that is no reason to enrol again.
        self.machine.set_flags(secure_boot=False, setup=True)
        changed, notes, _ = self.quietly('enroll', True)
        self.assertFalse(changed)
        self.assertEqual(len(self.enrolments()), 1)
        self.assertIn('the firmware already holds these keys; nothing was enrolled', notes)

    def test_the_command_line_keeps_microsofts_keys_unless_told_not_to(self):
        asked = []

        class Recorder:
            def __init__(self, *arguments):
                self.notes = []

            def enroll(self, with_microsoft):
                asked.append(with_microsoft)
                return True

        main = SCRIPT['main']
        with (
            mock.patch.dict(main.__globals__, {'SecureBoot': Recorder}),
            mock.patch('os.geteuid', return_value=0),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            for argv in (['--enroll'], ['--enroll', '--microsoft'], ['--enroll', '--no-microsoft']):
                main(argv)
        self.assertEqual(asked, [True, True, False])

    def test_every_enrolment_is_one_of_the_two_allowed_commands(self):
        for microsoft in (True, False):
            with self.subTest(microsoft=microsoft):
                self.setUp()
                self.set_up(microsoft)
                self.assertIn(
                    self.enrolments(), ([('sbctl', 'enroll-keys', '--microsoft')], [('sbctl', 'enroll-keys')])
                )


class CheckTests(SecureBootCase):
    def test_a_fresh_machine_has_nothing_set_up_and_no_problems(self):
        report, problems = self.check()
        self.assertEqual(problems, [])
        self.assertIn('Secure Boot: off; Setup Mode: on', report)
        self.assertIn('Keys: none yet; --prepare creates them', report)
        self.assertIn("Kernel images: signed in place by sbctl's mkinitcpio hook", report)
        self.assertIn('Hibernation: this boot has no resume device', report)

    def test_a_set_up_machine_with_hibernation(self):
        self.set_up()
        self.machine.host('sys/power/resume').write_text('253:0\n')
        self.machine.host('sys/power/resume_offset').write_text('533760\n')
        report, problems = self.check()
        self.assertEqual(problems, [])
        self.assertIn('Secure Boot: on; Setup Mode: off', report)
        self.assertIn("Firmware keys: this machine's; also Microsoft", report)
        self.assertIn('Loader: signed, with limine.conf enrolled', report)
        self.assertIn('Boot files: 3 hashed and matching', report)  # the wallpaper, the kernel, the initramfs
        self.assertIn("(Shaffy OS's lines)", report)
        self.assertIn('Kernel lockdown: none', report)
        self.assertIn('Hibernation: resumes from 253:0 at offset 533760; lockdown none', report)
        self.assertIn('Recorded: enrolled', report)

    def test_the_check_changes_nothing(self):
        self.set_up()
        before, state = self.machine.files(), self.state_files()
        self.machine.calls.clear()
        self.check()
        self.assertEqual((self.machine.writes(), self.machine.files(), self.state_files()), ([], before, state))

    def test_a_file_on_another_volume_needs_a_hash_as_well(self):
        found = self.tool().hashes(
            '/A\nprotocol: linux\npath: fslabel(DATA):/vmlinuz\n'
            f'module_path: fslabel(DATA):/initramfs#{"ab" * 64}\n'
            '/B\nprotocol: efi\npath: fslabel(DATA):/EFI/other.efi\n'
        )
        self.assertEqual(found['missing'], ['fslabel(DATA):/vmlinuz'])
        self.assertEqual(found['elsewhere'], [f'fslabel(DATA):/initramfs#{"ab" * 64}'])

    def test_an_unsigned_limine_fallback_is_a_note(self):
        self.set_up()
        self.machine.host('boot/EFI/BOOT').mkdir(parents=True)
        self.machine.host('boot/EFI/BOOT/BOOTX64.EFI').write_bytes(LOADER)
        tool = self.tool()
        self.assertEqual(tool.check()[1], [])
        self.assertTrue(any('fallback loader' in note for note in tool.notes))

    def test_what_would_stop_the_boot_is_a_problem(self):
        kernel = f'boot/{MACHINE_ID}/{KERNEL}/vmlinuz-{KERNEL}'
        cases = {
            'does not match its hash': lambda m: m.host(kernel).write_text('MZ kernel, changed'),
            'is not signed': lambda m: m.host('boot/EFI/limine/limine_x64.efi').write_bytes(LOADER),
            'does not hold the checksum': lambda m: m.host('boot/limine.conf').write_text(
                m.host('boot/limine.conf').read_text() + '\ntimeout: 3\n'
            ),
            'sbctl signs': lambda m: m.database.append({'file': f'/{kernel}', 'output_file': f'/{kernel}'}),
            'turns hibernation off': lambda m: (
                m.host('sys/kernel/security/lockdown').write_text('none [integrity] confidentiality\n'),
                m.host('sys/power/resume').write_text('253:0\n'),
            ),
            'malformed': lambda m: m.host('etc/default/limine').write_text(SCRIPT['END'] + SCRIPT['BEGIN']),
            'fslabel(DATA):/vmlinuz without a hash': lambda m: m.host('boot/limine.conf').write_text(
                m.host('boot/limine.conf').read_text() + '\n/Other\nprotocol: linux\npath: fslabel(DATA):/vmlinuz\n'
            ),
        }
        for problem, breaker in cases.items():
            with self.subTest(problem=problem):
                self.setUp()
                self.set_up()
                breaker(self.machine)
                self.assertTrue(any(problem in p for p in self.check()[1]), self.check()[1])

    def test_windows_without_microsofts_keys_is_a_problem(self):
        self.setUp(windows=True)
        self.quietly('prepare')
        self.machine.firmware = {
            db: [(self.machine.own[db], f'own {db}')] for db in ('PK', 'KEK', 'DB')
        }  # as if enrolled by hand without them
        self.assertTrue(any('refuses Windows' in p for p in self.check()[1]))

    def test_the_wallpaper_without_a_hash_is_only_a_note(self):
        tool = self.tool()
        self.assertEqual(tool.check()[1], [])
        self.assertTrue(any('limine-splash.png has no matching hash' in note for note in tool.notes))


class FwupdTests(SecureBootCase):
    def test_prepare_signs_fwupd_registers_it_and_turns_shim_off(self):
        self.setUp(fwupd=True)
        self.quietly('prepare')
        efi = '/usr/lib/fwupd/efi/fwupdx64.efi'
        self.assertEqual(
            [c for c in self.machine.calls if c[:2] == ('sbctl', 'sign')],
            [('sbctl', 'sign', '-s', '-o', efi + '.signed', efi)],
        )
        self.assertTrue(self.machine.signed(efi + '.signed'))
        self.assertTrue(self.read('etc/fwupd/fwupd.conf').startswith('[fwupd]\n# use `man 5 fwupd.conf`'))
        self.assertIn(
            SCRIPT['BEGIN'] + '[uefi_capsule]\nDisableShimForSecureBoot=true\n' + SCRIPT['END'],
            self.read('etc/fwupd/fwupd.conf'),
        )
        self.assertIn('fwupd: its EFI program signed; without shim', self.check()[0])
        self.machine.calls.clear()
        self.assertFalse(self.quietly('prepare')[0])
        self.assertEqual(self.machine.writes(), [])

    def test_the_shim_setting_counts_only_in_its_section(self):
        self.setUp(fwupd=True)
        self.machine.host('etc/fwupd/fwupd.conf').write_text('[fwupd]\nDisableShimForSecureBoot=true\n')
        self.quietly('prepare')
        self.assertIn('[uefi_capsule]\nDisableShimForSecureBoot=true\n', self.read('etc/fwupd/fwupd.conf'))
        self.assertIn('fwupd: its EFI program signed; without shim', self.check()[0])

    def test_without_fwupd_nothing_else_is_signed(self):
        self.quietly('prepare')
        self.assertEqual([c for c in self.machine.calls if c[:2] == ('sbctl', 'sign')], [])
        self.assertFalse(self.machine.host('etc/fwupd').exists())


class ManagedLinesTests(unittest.TestCase):
    SETTINGS, BODY = {'KEY': 'yes'}, 'KEY=yes\n'

    def block(self, text):
        return SCRIPT['with_block'](text, '/etc/example', self.SETTINGS, self.BODY)

    def test_the_block_goes_after_what_is_there_and_stays_one(self):
        once = self.block('ESP_PATH="/boot"\n')
        self.assertEqual(once, 'ESP_PATH="/boot"\n\n' + SCRIPT['BEGIN'] + 'KEY=yes\n' + SCRIPT['END'])
        self.assertEqual(self.block(once), once)
        stale = once.replace('KEY=yes', 'KEY=no')
        self.assertEqual(self.block(stale + 'LATER=1\n'), once + 'LATER=1\n')

    def test_a_setting_outside_the_block_is_kept_when_alike_and_refused_when_not(self):
        self.assertEqual(self.block('KEY=yes\n'), 'KEY=yes\n')
        self.assertEqual(self.block('KEY="yes"\n'), 'KEY="yes"\n')
        with self.assertRaisesRegex(RuntimeError, 'sets KEY=no outside'):
            self.block('KEY=no\n')
        self.assertIn(SCRIPT['BEGIN'], self.block('# KEY=no\n'))

    def test_malformed_markers_are_refused(self):
        begin, end = SCRIPT['BEGIN'], SCRIPT['END']
        for text in (begin, end, end + begin, begin + end + begin + end, begin + begin + end):
            with self.subTest(text=text), self.assertRaisesRegex(RuntimeError, 'malformed'):
                self.block(text)

    def test_the_kernel_hook_overrides_sbctls_and_only_exits(self):
        text = SCRIPT['KERNEL_HOOK_TEXT']
        self.assertEqual(SCRIPT['KERNEL_HOOK'], '/etc/initcpio/post/sbctl')  # the name of sbctl's own hook
        self.assertTrue(text.startswith('#!/bin/sh\n# Managed by Shaffy OS'))
        self.assertEqual([line for line in text.splitlines() if not line.startswith('#')], ['exit 0'])
        self.assertTrue(SCRIPT['inert'](text))
        self.assertTrue(SCRIPT['inert']('#!/bin/bash\n# Another tool.\n\nexit 0\n'))
        self.assertFalse(SCRIPT['inert']('#!/usr/bin/bash\nsbctl sign "$1"\n'))
        self.assertFalse(SCRIPT['inert']('exit 0\n'))


if __name__ == '__main__':
    unittest.main()
