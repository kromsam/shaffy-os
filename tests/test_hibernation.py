"""Swap and resume for hibernation, against a fake CachyOS in a temporary root: Btrfs on LUKS, Limine
and CachyOS's entry tool, whose commands act on the files there."""

import json
from pathlib import Path
import re
import runpy
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = runpy.run_path(str(ROOT / 'scripts/configure-hibernation.py'))
GIB = 1024**3
UUID = '01234567-89ab-cdef-0123-456789abcdef'
DEVICE = '/dev/mapper/luks-00000000-0000-0000-0000-000000000000'
MACHINE_ID = '0123456789abcdef0123456789abcdef'
KERNELS = {'6.18.1-1-cachyos': 'linux-cachyos', '6.12.60-1-cachyos-lts': 'linux-cachyos-lts'}
BASE = f'quiet splash rw rootflags=subvol=/@ root={DEVICE}'
OFFSET = 533760
OURS = [f'resume=UUID={UUID}', f'resume_offset={OFFSET}']
DROPIN = 'etc/limine-entry-tool.d/90-shaffy-hibernation.conf'
FSTAB = (
    '# /etc/fstab: static file system information.\n'
    '/dev/vda1 /boot vfat defaults,umask=0077 0 2\n'
    f'{DEVICE} / btrfs subvol=/@,defaults,noatime,compress=zstd:1 0 0\n'
    f'{DEVICE} /home btrfs subvol=/@home,defaults,noatime,compress=zstd:1 0 0\n'
    'tmpfs /tmp tmpfs defaults,noatime,mode=1777 0 0\n'
)
BLOCK = (
    '# BEGIN Managed by Shaffy OS (the power role): power.hibernation\n'
    f'UUID={UUID} /swap btrfs subvol=/@swap,noatime,nofail 0 0\n'
    '/swap/swapfile none swap defaults,pri=10 0 0\n'
    '# END Managed by Shaffy OS (the power role): power.hibernation\n'
)
WRITES = ('mount', 'umount', 'swapon', 'swapoff', 'systemctl', 'limine-mkinitcpio', 'create', 'mkswapfile')


class FakeMachine:
    """A fresh CachyOS installation in `root`; called as the script's runner, it acts on it."""

    def __init__(self, root):
        self.root, self.calls, self.top = root, [], None
        self.subvolumes = ['@', '@home']
        self.mounts = {
            '/': {'source': f'{DEVICE}[/@]', 'fstype': 'btrfs', 'fsroot': '/@', 'uuid': UUID},
            '/boot': {'source': '/dev/vda1', 'fstype': 'vfat', 'fsroot': '/', 'uuid': None},
        }
        self.swaps = {'/dev/zram0': 100}
        self.swapfiles = set()  # the files mkswapfile made, which blkid and map-swapfile accept
        self.store = root / '.subvolume-swap'  # what @swap holds while it is not mounted at /swap
        self.fail, self.skip, self.duplicate, self.partial = set(), set(), False, False
        self.cmdlines = {}
        files = {
            'etc/fstab': FSTAB,
            'etc/default/limine': f'ESP_PATH="/boot"\nKERNEL_CMDLINE[default]+="{BASE}"\nBOOT_ORDER="*, *lts"\n',
            'etc/limine-entry-tool.conf': '#KERNEL_CMDLINE[default]+=resume=/dev/example\nENABLE_VERIFICATION=yes\n',
            'etc/mkinitcpio.conf': 'MODULES=()\nHOOKS=(base systemd autodetect microcode kms modconf block keyboard '
            'sd-vconsole plymouth sd-encrypt filesystems)\n',
            'etc/mkinitcpio.conf.d/10-limine-snapper-sync.conf': 'HOOKS+=(sd-btrfs-overlayfs)\n',
            'etc/machine-id': MACHINE_ID + '\n',
            'etc/os-release': 'NAME="CachyOS Linux"\nPRETTY_NAME="CachyOS"\n',
            'etc/snapper/configs/root': 'SUBVOLUME="/"\n',
            'etc/boot/hooks/post.d/90-limine-enroll-config': '#!/bin/sh\n',
            'usr/bin/limine-entry-tool': '',
            'usr/bin/limine-mkinitcpio': '',
            'proc/cmdline': BASE + '\n',
            'proc/meminfo': 'MemTotal:        1048576 kB\n',
            'sys/power/resume': '0:0\n',
            'sys/power/resume_offset': '0\n',
            f'sys/fs/btrfs/{UUID}/devices/dm-0': '',
            **{f'usr/lib/modules/{version}/modules.builtin': '' for version in KERNELS},
        }
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        (root / 'etc/boot/hooks/post.d/90-limine-enroll-config').chmod(0o755)
        for folder in ('etc/limine-entry-tool.d', 'run', 'boot'):
            (root / folder).mkdir(exist_ok=True)
        self.write_swaps()
        self.regenerate()

    def host(self, path):
        return self.root / path.lstrip('/')

    def write_swaps(self):
        lines = ['Filename\tType\tSize\tUsed\tPriority'] + [f'{n}\tfile\t1\t0\t{p}' for n, p in self.swaps.items()]
        self.host('/proc/swaps').write_text('\n'.join(lines) + '\n')

    def cmdline(self, kernel):
        """The entry tool's command line: a key's = replaces and += appends, in its load order."""
        lines = {}
        for path in [*sorted(self.host('/etc/limine-entry-tool.d').glob('*.conf')), self.host('/etc/default/limine')]:
            for line in path.read_text().splitlines():
                match = re.fullmatch(r'KERNEL_CMDLINE\[([\w.-]+)\](\+?)=(.*)', line.strip())
                if match:
                    value = match[3].strip().removeprefix('"').removesuffix('"')
                    lines[match[1]] = f'{lines.get(match[1], "")} {value}'.strip() if match[2] else value
        return lines.get(kernel, lines.get('default', self.host('/proc/cmdline').read_text().strip()))

    def regenerate(self):
        """limine-mkinitcpio: every kernel's entry anew, but a kernel it fails to build keeps its old one."""
        for kernel in KERNELS.values():
            if kernel not in self.skip:
                self.cmdlines[kernel] = self.cmdline(kernel) + (f' resume=UUID={UUID}' if self.duplicate else '')
        lines = ['timeout: 5', '', '/+CachyOS', f'comment: machine-id={MACHINE_ID}', '']
        for kernel, cmdline in sorted(self.cmdlines.items()):
            lines += [f'  //{kernel}', f'  comment: kernel-id={kernel}', '  protocol: linux']
            lines += [f'  path: boot():/{MACHINE_ID}/{kernel}/vmlinuz-{kernel}', f'  cmdline: {cmdline}', '']
        lines += ['  //Snapshots', '    ///1 | 2026-11-06', '    protocol: linux']
        lines += ['    cmdline: quiet rw rootflags=subvol=/@/.snapshots/1/snapshot', '']
        lines += ['/Windows Boot Manager', 'protocol: efi', 'path: boot():/EFI/Microsoft/Boot/bootmgfw.efi']
        self.host('/boot/limine.conf').write_text('\n'.join(lines) + '\n')

    def mount_swap(self):
        mountpoint = self.host('/swap')
        if mountpoint.exists():
            assert not any(mountpoint.iterdir()), 'mounting over a folder with files in it'
            mountpoint.rmdir()
        self.store.rename(mountpoint)
        self.mounts['/swap'] = {'source': f'{DEVICE}[/@swap]', 'fstype': 'btrfs', 'fsroot': '/@swap', 'uuid': UUID}

    def unmount_swap(self):
        self.host('/swap').rename(self.store)
        self.host('/swap').mkdir()
        del self.mounts['/swap']

    def make_swap(self, size):
        """@swap mounted at /swap with a switched-on swap file, as someone might have made them by hand."""
        self.subvolumes.append('@swap')
        self.store.mkdir()
        with open(self.store / 'swapfile', 'wb') as handle:
            handle.truncate(size * GIB)
        (self.store / 'swapfile').chmod(0o600)
        self.swapfiles.add('/swap/swapfile')
        self.mount_swap()
        self.swaps['/swap/swapfile'] = 10
        self.write_swaps()

    def boot(self):
        """The default entry's command line, and the resume device and offset systemd gives the kernel."""
        cmdline = self.cmdlines['linux-cachyos']
        offset = re.search(r'resume_offset=(\d+)', cmdline)
        self.host('/proc/cmdline').write_text(cmdline + '\n')
        self.host('/sys/power/resume').write_text('253:0\n' if offset else '0:0\n')
        self.host('/sys/power/resume_offset').write_text(f'{offset[1] if offset else 0}\n')

    def reboot(self):
        """A new boot: the mounts and swaps fstab names, and the default entry's command line."""
        if '/swap' in self.mounts:
            self.unmount_swap()
        self.swaps = {'/dev/zram0': 100}
        fstab = self.host('/etc/fstab').read_text()
        if re.search(r'(?m)^\S+ /swap btrfs ', fstab):
            self.mount_swap()
        if re.search(r'(?m)^/swap/swapfile none swap ', fstab):
            self.swaps['/swap/swapfile'] = 10
        self.write_swaps()
        self.boot()

    def __call__(self, *command):
        self.calls.append(command)
        if self.fail & set(command):
            raise subprocess.CalledProcessError(1, command, '', 'failed')
        match command:
            case ('findmnt', '--json', '--mountpoint', target, *_):
                if target not in self.mounts:
                    raise subprocess.CalledProcessError(1, command)
                return json.dumps({'filesystems': [{'target': target, **self.mounts[target]}]})
            case ('lsblk', *_, device):
                assert device == DEVICE, device
                return '253:0 crypt\n'
            case ('btrfs', 'subvolume', 'list', '/'):
                return ''.join(
                    f'ID {256 + i} gen 9 top level 5 path {name}\n' for i, name in enumerate(self.subvolumes)
                )
            case ('mount', '-t', 'btrfs', '-o', 'subvolid=5', device, top):
                assert device == DEVICE, device
                self.top = top
            case ('btrfs', 'subvolume', 'create', path):
                assert self.top and path == f'{self.top}/@swap', path
                self.subvolumes.append('@swap')
                self.store.mkdir()
            case ('mount', '-t', 'btrfs', '-o', 'subvol=/@swap,noatime', spec, '/swap'):
                assert spec == f'UUID={UUID}' and '@swap' in self.subvolumes, spec
                self.mount_swap()
            case ('umount', '/swap'):
                self.unmount_swap()
            case ('umount', top):
                assert top == self.top, top
                self.top = None
            case ('btrfs', 'filesystem', 'mkswapfile', '--size', size, path):
                host = self.host(path)
                assert '/swap' in self.mounts and not host.exists(), path
                with open(host, 'wb') as handle:
                    handle.truncate(4096 if self.partial else int(size.removesuffix('G')) * GIB)
                host.chmod(0o600)
                if self.partial:
                    raise subprocess.CalledProcessError(1, command, '', 'interrupted')
                self.swapfiles.add(path)
            case ('blkid', '-p', '-s', 'TYPE', '-o', 'value', path):
                if path not in self.swapfiles or not self.host(path).exists():
                    raise subprocess.CalledProcessError(2, command)
                return 'swap\n'
            case ('btrfs', 'inspect-internal', 'map-swapfile', '-r', path):
                if path not in self.swapfiles:
                    raise subprocess.CalledProcessError(1, command)
                return f'{OFFSET}\n'
            case ('swapon', '--priority', priority, path):
                self.swaps[path] = int(priority)
                self.write_swaps()
            case ('swapoff', path):
                del self.swaps[path]
                self.write_swaps()
            case ('systemctl', 'daemon-reload'):
                pass
            case ('pacman', '-Qqo', path):
                return KERNELS[path.split('/')[4]] + '\n'
            case ('limine-entry-tool', '--get-cmdline', kernel):
                return 'Reading the configuration\n' + self.cmdline(kernel) + '\n'
            case ('limine-mkinitcpio',):
                self.regenerate()
            case _:
                raise AssertionError(f'unexpected command {command}')
        return ''

    def writes(self):
        return [c for c in self.calls if c[0] in WRITES or (len(c) > 2 and c[2] in WRITES)]


class HibernationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root, self.state = Path(directory.name, 'root'), Path(directory.name, 'state')
        self.root.mkdir()
        self.state.mkdir()
        self.machine = FakeMachine(self.root)

    def converge(self, size, apply=True):
        return SCRIPT['converge'](size, self.state, apply, run=self.machine, root=self.root, free=lambda path: 9 * GIB)

    def read(self, name):
        path = self.root / name
        return path.read_text() if path.exists() else None

    def record(self):
        path = self.state / 'hibernation.json'
        return json.loads(path.read_text()) if path.exists() else None

    def resume_in_entries(self):
        entries = SCRIPT['kernel_entries'](self.read('boot/limine.conf'), MACHINE_ID, [])
        self.assertEqual(sorted(title for title, _ in entries), sorted(KERNELS.values()))
        for title, parameters in entries:
            self.assertEqual(SCRIPT['without_resume'](parameters), sorted(BASE.split()), title)
        return {title: SCRIPT['resume_of'](parameters) for title, parameters in entries}

    def assertNothingWritten(self, fstab=FSTAB):
        self.assertEqual(self.machine.writes(), [])
        self.assertEqual(self.read('etc/fstab'), fstab)
        self.assertIsNone(self.read(DROPIN))
        self.assertEqual(list(self.state.iterdir()), [])

    def test_a_fresh_setup_is_checked_made_and_then_left_alone(self):
        changed, notes = self.converge(1, apply=False)
        self.assertTrue(changed)
        self.assertIn('would create the subvolume @swap', notes)
        self.assertIn('would create the 1 GiB swap file /swap/swapfile', notes)
        self.assertNothingWritten()

        changed, notes = self.converge(1)
        self.assertTrue(changed)
        self.assertIn('@swap', self.machine.subvolumes)
        swapfile = (self.root / 'swap/swapfile').stat()
        self.assertEqual((swapfile.st_size, stat.S_IMODE(swapfile.st_mode)), (GIB, 0o600))
        self.assertEqual(self.machine.swaps, {'/dev/zram0': 100, '/swap/swapfile': 10})
        self.assertEqual(self.read('etc/fstab'), FSTAB + '\n' + BLOCK)
        dropin = self.read(DROPIN)
        self.assertTrue(dropin.startswith('# Managed by Shaffy OS (the power role)'))
        self.assertIn(f'KERNEL_CMDLINE[default]+="{" ".join(OURS)}"', dropin)
        self.assertEqual(self.resume_in_entries(), {kernel: OURS for kernel in KERNELS.values()})
        self.assertIn('quiet rw rootflags=subvol=/@/.snapshots/1/snapshot\n', self.read('boot/limine.conf'))
        self.assertEqual(self.record(), {'creating': False, 'filesystem': UUID, 'regenerate': False, 'resume': OURS,
                                         'swap_gib': 1})  # fmt: skip
        copies = list(self.state.glob('hibernation-copies/*/etc/fstab'))
        self.assertEqual([copy.read_text() for copy in copies], [FSTAB])
        self.assertIn('this boot does not resume from /swap/swapfile yet: reboot, then test a hibernation', notes)

        self.machine.calls.clear()
        self.assertEqual(self.converge(1), (False, [notes[-1]]))
        self.assertEqual(self.machine.writes(), [])
        self.machine.reboot()
        self.assertEqual(self.converge(1, apply=False), (False, []))
        self.assertEqual(self.converge(1), (False, []))
        self.assertEqual(self.machine.writes(), [])

    def test_the_installers_replacing_command_line_becomes_an_addition(self):
        # CachyOS's installer writes KERNEL_CMDLINE[default]= , which would replace the drop-in's resume.
        default = '/etc/default/limine'
        installed = f'ESP_PATH="/boot"\nKERNEL_CMDLINE[default]="{BASE}"\nBOOT_ORDER="*, *lts"\n'
        self.machine.host(default).write_text(installed)
        changed, notes = self.converge(1, apply=False)
        self.assertIn('would change KERNEL_CMDLINE[default]= to += in /etc/default/limine', notes)
        self.assertEqual(self.read(default.lstrip('/')), installed)
        self.converge(1)
        self.assertEqual(self.read(default.lstrip('/')), installed.replace('[default]="', '[default]+="'))
        self.assertEqual(self.resume_in_entries(), {kernel: OURS for kernel in KERNELS.values()})
        copies = list(self.state.glob('hibernation-copies/*/etc/default/limine'))
        self.assertEqual([copy.read_text() for copy in copies], [installed])
        self.assertEqual(self.converge(1)[0], False)

    def test_an_unsupported_machine_is_refused_before_anything_is_written(self):
        def append(name, text):
            return lambda machine: machine.host(name).write_text(machine.host(name).read_text() + text)

        default = '/etc/default/limine'
        cases = {
            '/ is ext4, not Btrfs': lambda machine: machine.mounts['/'].update(fstype='ext4'),
            '/usr/bin/limine-entry-tool is missing': lambda machine: machine.host(
                '/usr/bin/limine-entry-tool'
            ).unlink(),
            'HOOKS lack systemd and sd-encrypt': lambda machine: machine.host('/etc/mkinitcpio.conf').write_text(
                'HOOKS=(base udev autodetect block encrypt filesystems)\n'
            ),
            'ENABLE_UKI=yes': append(default, 'ENABLE_UKI=yes\n'),
            'no hook in /etc/boot/hooks/post.d/ runs limine-enroll-config': lambda machine: (
                append(default, 'ENABLE_ENROLL_LIMINE_CONFIG=yes\n')(machine),
                machine.host('/etc/boot/hooks/post.d/90-limine-enroll-config').rename(
                    machine.host('/etc/boot/hooks/post.d/90-limine-enroll-config.disabled')
                ),
            ),
            # Another file sets the default command line too, so += would add its settings to the boot.
            '/etc/default/limine sets KERNEL_CMDLINE[default]= , which replaces what drop-ins add': lambda machine: (
                machine.host(default).write_text(f'ESP_PATH="/boot"\nKERNEL_CMDLINE[default]="{BASE}"\n'),
                machine.host('/etc/limine-entry-tool.d/50-extra.conf').write_text(
                    'KERNEL_CMDLINE[default]+="nomodeset"\n'
                ),
            ),
            'nothing in /etc/default/limine or a drop-in sets KERNEL_CMDLINE[default]': lambda machine: machine.host(
                default
            ).write_text('ESP_PATH="/boot"\n'),
        }
        for message, change in cases.items():
            with self.subTest(message):
                self.setUp()
                change(self.machine)
                with self.assertRaisesRegex(RuntimeError, re.escape(message)):
                    self.converge(1)
                self.assertNothingWritten()

    def test_a_resume_or_swap_configured_elsewhere_is_named_and_refused(self):
        other = 'KERNEL_CMDLINE[default]+="resume=/dev/vda2"\n'
        cases = {
            '/etc/limine-entry-tool.d/50-resume.conf sets resume= or resume_offset=': (
                '/etc/limine-entry-tool.d/50-resume.conf',
                other,
            ),
            '/etc/default/limine sets resume= or resume_offset=': (
                '/etc/default/limine',
                f'ESP_PATH="/boot"\nKERNEL_CMDLINE[default]+="{BASE} resume_offset=1"\n',
            ),
            '/etc/kernel/cmdline sets resume= or resume_offset=': ('/etc/kernel/cmdline', f'{BASE} resume=/dev/vda2\n'),
            'the running boot has resume=/dev/vda2 (/proc/cmdline), which is not /swap/swapfile': (
                '/proc/cmdline',
                f'{BASE} resume=/dev/vda2\n',
            ),
            '/etc/fstab switches on /swap/swapfile': ('/etc/fstab', FSTAB + '/swap/swapfile none swap defaults 0 0\n'),
        }
        for message, (name, text) in cases.items():
            with self.subTest(message):
                self.setUp()
                self.machine.host(name).parent.mkdir(parents=True, exist_ok=True)
                self.machine.host(name).write_text(text)
                with self.assertRaisesRegex(RuntimeError, re.escape(message)):
                    self.converge(1)
                self.assertNothingWritten(fstab=text if name == '/etc/fstab' else FSTAB)

    def test_a_swap_set_up_by_hand_is_handed_over_and_adopted(self):
        machine = self.machine
        machine.make_swap(1)
        theirs = machine.host('/etc/limine-entry-tool.d/90-other-resume.conf')
        theirs.write_text(f'# Set up by hand.\nKERNEL_CMDLINE[default]+="resume={DEVICE} resume_offset={OFFSET}"\n')
        lines = f'UUID={UUID} /swap btrfs subvol=/@swap,noatime 0 0\n/swap/swapfile none swap defaults,pri=10 0 0\n'
        fstab = FSTAB + '\n# BEGIN by hand\n' + lines + '# END by hand\n'
        machine.host('/etc/fstab').write_text(fstab)
        machine.regenerate()
        machine.boot()
        inode = machine.host('/swap/swapfile').stat().st_ino
        machine.calls.clear()
        with self.assertRaises(RuntimeError) as refusal:
            self.converge(1)
        message = str(refusal.exception)
        for place in (
            '/etc/limine-entry-tool.d/90-other-resume.conf sets resume= or resume_offset=',
            '/etc/fstab mounts /swap',
            '/etc/fstab switches on /swap/swapfile',
            'To hand it over, delete those lines and files, keep /swap mounted and its swap file on',
        ):
            self.assertIn(place, message)
        self.assertNotIn('/proc/cmdline', message)  # the running boot resumes from the very same file
        self.assertNothingWritten(fstab=fstab)

        theirs.unlink()
        machine.host('/etc/fstab').write_text(FSTAB)
        changed, notes = self.converge(1, apply=False)
        self.assertEqual(notes[0], 'would adopt /swap/swapfile')
        changed, notes = self.converge(1)
        self.assertTrue(changed)
        self.assertFalse([c for c in machine.calls if 'mkswapfile' in c or 'create' in c])
        self.assertEqual(machine.host('/swap/swapfile').stat().st_ino, inode)
        self.assertEqual(self.record()['filesystem'], UUID)
        self.assertEqual(self.read('etc/fstab'), FSTAB + '\n' + BLOCK)
        self.assertEqual(self.resume_in_entries(), {kernel: OURS for kernel in KERNELS.values()})
        self.assertEqual(notes, [])  # no reboot is needed: this boot already resumes from the file

    def test_a_swap_file_of_another_size_is_refused(self):
        self.machine.make_swap(2)
        self.machine.calls.clear()
        with self.assertRaisesRegex(RuntimeError, 'has 2 GiB, not power.hibernation.swap_gib 1, and an active swap'):
            self.converge(1)
        self.assertNothingWritten()
        self.assertEqual(self.machine.host('/swap/swapfile').stat().st_size, 2 * GIB)

    def test_an_interrupted_regeneration_finishes_on_the_next_run(self):
        self.machine.fail = {'limine-mkinitcpio'}
        with self.assertRaises(subprocess.CalledProcessError):
            self.converge(1)
        self.assertTrue(self.record()['regenerate'])
        self.assertIsNotNone(self.read(DROPIN))
        self.machine.fail = set()
        self.machine.calls.clear()
        self.assertTrue(self.converge(1)[0])
        self.assertIn(('limine-mkinitcpio',), self.machine.calls)
        self.assertFalse(self.record()['regenerate'])
        self.assertEqual(self.resume_in_entries(), {kernel: OURS for kernel in KERNELS.values()})
        self.machine.calls.clear()
        self.assertFalse(self.converge(1)[0])
        self.assertEqual(self.machine.writes(), [])

    def test_an_interrupted_swap_file_is_made_again(self):
        self.machine.partial = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.converge(1)
        self.assertTrue(self.record()['creating'])
        self.machine.partial = False
        changed, notes = self.converge(1)
        self.assertTrue(changed)
        self.assertEqual(self.machine.host('/swap/swapfile').stat().st_size, GIB)
        self.assertFalse(self.record()['creating'])
        self.assertIn('/swap/swapfile', self.machine.swaps)

    def test_an_unfinished_swap_file_that_is_switched_on_is_never_removed(self):
        self.machine.partial = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.converge(1)
        self.machine.swaps['/swap/swapfile'] = -2
        self.machine.write_swaps()
        with self.assertRaisesRegex(RuntimeError, 'unfinished but switched on'):
            self.converge(1)
        self.assertEqual(self.machine.host('/swap/swapfile').stat().st_size, 4096)

    def test_the_regenerated_entries_are_read_back(self):
        for change, message in (
            (lambda machine: machine.skip.add('linux-cachyos-lts'), 'linux-cachyos-lts has no resume'),
            (lambda machine: setattr(machine, 'duplicate', True), f'linux-cachyos has resume=UUID={UUID} resume=UUID'),
        ):
            with self.subTest(message):
                self.setUp()
                change(self.machine)
                with self.assertRaisesRegex(RuntimeError, re.escape(message)):
                    self.converge(1)
                self.assertTrue(self.record()['regenerate'])

    def test_a_drop_in_that_misses_a_kernel_is_put_back_before_regenerating(self):
        default = self.machine.host('/etc/default/limine')
        default.write_text(default.read_text() + f'KERNEL_CMDLINE[linux-cachyos-lts]="{BASE}"\n')
        with self.assertRaisesRegex(RuntimeError, 'limine-entry-tool --get-cmdline linux-cachyos-lts gives'):
            self.converge(1)
        self.assertIsNone(self.read(DROPIN))
        self.assertNotIn(('limine-mkinitcpio',), self.machine.calls)
        self.assertFalse(self.record()['regenerate'])

    def test_removing_the_setting_undoes_the_boot_and_fstab_but_keeps_the_swap_file(self):
        self.converge(1)
        self.machine.reboot()
        self.machine.calls.clear()
        changed, notes = self.converge(None, apply=False)
        self.assertTrue(changed)
        self.assertIn('would switch off /swap/swapfile', notes)
        self.assertEqual(self.machine.writes(), [])

        changed, notes = self.converge(None)
        self.assertTrue(changed)
        self.assertIsNone(self.read(DROPIN))
        self.assertEqual(self.resume_in_entries(), {kernel: [] for kernel in KERNELS.values()})
        self.assertEqual(self.read('etc/fstab'), FSTAB)
        self.assertEqual(self.machine.swaps, {'/dev/zram0': 100})
        self.assertIsNone(self.record())
        self.assertEqual(self.machine.host('/swap/swapfile').stat().st_size, GIB)
        self.assertIn('@swap', self.machine.subvolumes)
        self.assertIn('/swap/swapfile and the subvolume @swap stay', notes[-1])

        self.machine.calls.clear()
        self.assertEqual(self.converge(None), (False, []))
        self.assertEqual(self.machine.calls, [])

    def test_without_the_setting_or_anything_owned_nothing_is_done(self):
        self.assertEqual(self.converge(None, apply=False), (False, []))
        self.assertEqual(self.converge(None), (False, []))
        self.assertEqual(self.machine.calls, [])

    def test_the_kept_swap_file_is_taken_up_again(self):
        self.converge(1)
        self.converge(None)
        self.machine.reboot()
        self.assertNotIn('/swap', self.machine.mounts)
        changed, notes = self.converge(1, apply=False)
        self.assertIn('would mount the existing subvolume @swap at /swap and adopt its swap file', notes)
        self.machine.calls.clear()
        self.assertTrue(self.converge(1)[0])
        self.assertFalse([c for c in self.machine.calls if 'mkswapfile' in c])
        self.assertEqual(self.resume_in_entries(), {kernel: OURS for kernel in KERNELS.values()})
        self.assertEqual(self.read('etc/fstab'), FSTAB + '\n' + BLOCK)

    def test_an_os_entry_without_a_machine_id_is_found_by_its_title(self):
        conf = '/+CachyOS\n  //linux-cachyos\n  protocol: linux\n  kernel_cmdline: rw quiet\n/Other\n  //x\n  protocol: linux\n'
        self.assertEqual(SCRIPT['kernel_entries'](conf, MACHINE_ID, ['CachyOS']), [('linux-cachyos', ['rw', 'quiet'])])


if __name__ == '__main__':
    unittest.main()
