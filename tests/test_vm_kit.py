"""The VM validation kit in tests/vm/ (its README describes the run).

The VM itself runs by hand; these tests keep the kit honest without it: both scripts
parse, the host script's dry run starts the pinned VM with the required devices and
changes nothing, a real run without QEMU names the package, the guest stages call only
commands and options that ./bin/shaffy has, and the VM supplement is valid.
"""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import zlib

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy import cli
from shaffy.bootstrap import pacman_step

KIT = ROOT / 'tests/vm'
HOST = KIT / 'run-vm.sh'
GUEST = KIT / 'guest-validate.sh'
SUPPLEMENT = ROOT / 'tests/fixtures/supplements/vm'
# The functions ./bin/shaffy dispatches to; the parser is tested with each replaced.
COMMANDS = ('selftest', 'check', 'data', 'diff', 'apply', 'hook', 'status', 'attach', 'rebind', 'integration')
# A call of the guest's `shaffy` function: as a command, after capture/keep NAME, or in $(...).
INVOCATION = re.compile(
    r'(?:^\s*|\b(?:capture|keep|keep_output)\s+\S+\s+|\bif\s+!?\s*|&&\s*|\|\|\s*|\$\(\s*)'
    r'shaffy\s+([^;&|)<>#\n]+)'
)


def pin(name):
    return re.search(rf'^{name}=(\S+)$', HOST.read_text(), re.M).group(1).strip('"')


def parse(tokens):
    """Run ./bin/shaffy's own parser on the tokens, with every command replaced; (status, called)."""
    called = []
    with contextlib.ExitStack() as stack:
        for name in COMMANDS:
            stack.enter_context(mock.patch.object(cli, name, lambda args, name=name: called.append(name) or 0))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        try:
            status = cli.main(tokens)
        except SystemExit as error:
            status = error.code
    return status, called


def guest_function(name):
    """The source of one shell function of the guest script."""
    return re.search(rf'^{name}\(\) {{.*?^}}$', GUEST.read_text(), re.S | re.M).group(0)


def guest_python():
    return re.search(r"<<'PY'\n(.*?)\nPY\n", GUEST.read_text(), re.S).group(1)


def guest_invocations():
    """(line, tokens) of every call of the guest's `shaffy` function; a variable becomes VALUE."""
    found = []
    for number, line in enumerate(GUEST.read_text().splitlines(), 1):
        if line.lstrip().startswith('#'):
            continue
        for match in INVOCATION.finditer(line):
            arguments = re.sub(r'\s+\d$', '', match.group(1).rstrip(' \\'))  # a 2>&1 leaves its 2
            found.append((number, ['VALUE' if t.startswith('$') else t for t in shlex.split(arguments)]))
    return found


class ScriptTests(unittest.TestCase):
    def test_the_scripts_parse_and_are_executable(self):
        for script in (HOST, GUEST, KIT / 'install-headless.sh', KIT / 'vm-post-install.sh'):
            with self.subTest(script=script.name):
                result = subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(os.access(script, os.X_OK))

    def test_the_guest_python_checks_compile(self):
        compile(guest_python(), f'{GUEST.name} (Python checks)', 'exec')

    def test_the_installer_is_pinned_by_path_and_checksum(self):
        self.assertRegex(pin('ISO'), r'^\$HOME/Downloads/cachyos-desktop-linux-\d{6}\.iso$')
        self.assertRegex(pin('ISO_SHA256'), r'^[0-9a-f]{64}$')


class HostScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / 'home'
        self.home.mkdir()
        self.env = {'HOME': str(self.home), 'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8'}

    def host(self, *arguments, env=None):
        result = subprocess.run(
            ['/bin/bash', str(HOST), *arguments],
            capture_output=True,
            text=True,
            env=env or self.env,
            timeout=60,
            check=False,
        )
        self.assertEqual(list(self.home.iterdir()), [], 'the host script wrote into the home')
        return result

    def command(self, *arguments):
        """The QEMU command line of a dry run, as arguments."""
        result = self.host('--dry-run', *arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout, shlex.split(result.stdout.splitlines()[-1])

    def options(self, argv, flag):
        return [argv[i + 1] for i, word in enumerate(argv) if word == flag]

    def test_install_boots_the_pinned_installer_in_a_kvm_q35_uefi_machine(self):
        output, argv = self.command('install')
        vm = f'{self.home}/.cache/shaffy/vm'
        self.assertEqual(argv[0], 'qemu-system-x86_64')
        self.assertIn('q35,accel=kvm', self.options(argv, '-machine'))
        self.assertEqual(self.options(argv, '-m'), ['4096'])
        self.assertTrue(2 <= int(self.options(argv, '-smp')[0]) <= 4 or os.cpu_count() < 2)
        code, variables = [d for d in self.options(argv, '-drive') if d.startswith('if=pflash')]
        self.assertIn('readonly=on', code)
        self.assertRegex(code, r'file=/\S*OVMF_CODE')
        self.assertIn(f'file={vm}/OVMF_VARS', variables)
        self.assertIn('would run: install -m 0600 ', output)  # a private copy of the variables
        self.assertIn(f'qemu-img create -q -f qcow2 -o nocow=on {vm}/disk.qcow2 40G', output)
        installer = pin('ISO').replace('$HOME', str(self.home))
        self.assertIn(f'file={installer},media=cdrom,readonly=on', ' '.join(self.options(argv, '-drive')))
        self.assertIn(f'against its pinned SHA-256 {pin("ISO_SHA256")}', output)
        self.assertIn('ide-cd,drive=installer,bootindex=0', self.options(argv, '-device'))
        self.assertIn('-no-reboot', argv)

    def test_the_display_network_and_shares(self):
        _, argv = self.command('install')
        vm = f'{self.home}/.cache/shaffy/vm'
        self.assertIn('virtio-vga-gl', self.options(argv, '-device'))
        self.assertEqual(self.options(argv, '-display'), ['gtk,gl=on'])
        self.assertEqual(self.options(argv, '-nic'), ['user,model=virtio-net-pci'])
        share, evidence = self.options(argv, '-virtfs')
        self.assertTrue(share.startswith(f'local,path={vm}/share,mount_tag=shaffy-share,'))
        self.assertIn('readonly=on', share.split(','))
        self.assertTrue(evidence.startswith(f'local,path={vm}/evidence,mount_tag=shaffy-evidence,'))
        self.assertNotIn('readonly=on', evidence.split(','))

    def test_run_boots_the_installed_disk_with_the_chosen_size(self):
        _, argv = self.command('--memory', '6144', '--cpus', '1', 'run')
        self.assertEqual(self.options(argv, '-m'), ['6144'])
        self.assertEqual(self.options(argv, '-smp'), ['1'])
        self.assertIn('virtio-blk-pci,drive=disk,bootindex=1', self.options(argv, '-device'))
        self.assertNotIn('-no-reboot', argv)
        self.assertFalse(any('media=cdrom' in d for d in self.options(argv, '-drive')))

    def test_another_installer_needs_its_own_pin(self):
        self.assertEqual(self.host('--dry-run', '--iso', '/tmp/other.iso', 'install').returncode, 2)
        checksum = 'a' * 64
        output, argv = self.command('--iso', '/tmp/other.iso', '--sha256', checksum, 'install')
        self.assertIn(f'against its pinned SHA-256 {checksum}', output)
        self.assertIn('file=/tmp/other.iso,media=cdrom,readonly=on', ' '.join(self.options(argv, '-drive')))

    def test_the_share_is_built_from_the_checkout_head(self):
        result = self.host('--dry-run', 'share')
        self.assertEqual(result.returncode, 0, result.stderr)
        head = subprocess.run(
            ['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True
        ).stdout.strip()
        self.assertIn(f'would build the share {self.home}/.cache/shaffy/vm/share from commit {head}', result.stdout)

    def test_a_real_share_holds_the_bundle_the_guest_script_and_the_supplements(self):
        def git(*arguments):
            return subprocess.run(['git', '-C', str(ROOT), *arguments], capture_output=True, text=True, check=False)

        if git('cat-file', '-e', 'HEAD:tests/vm/guest-validate.sh').returncode:
            self.skipTest('the share is built from commits, and HEAD does not hold the kit yet')
        if git('rev-parse', '--is-shallow-repository').stdout.strip() == 'true':
            self.skipTest('a shallow clone has no history to bundle')
        vm = self.home.parent / 'vm'
        for _attempt in ('first', 'again'):  # the second replaces the contents in place
            result = self.host('--dir', str(vm), 'share')
            self.assertEqual(result.returncode, 0, result.stderr)
        share = vm / 'share'
        self.assertEqual(
            sorted(p.name for p in share.iterdir()),
            [
                'SHARE-INFO',
                'guest-validate.sh',
                'install-headless.sh',
                'shaffy-os.bundle',
                'supplements',
                'vm-install.json',
                'vm-post-install.sh',
            ],
        )
        self.assertTrue(os.access(share / 'vm-post-install.sh', os.X_OK))  # the installer runs it directly
        info = dict(line.split('=', 1) for line in (share / 'SHARE-INFO').read_text().splitlines())
        self.assertEqual(info['commit'], git('rev-parse', 'HEAD').stdout.strip())
        self.assertEqual(
            subprocess.run(
                ['git', 'bundle', 'list-heads', str(share / 'shaffy-os.bundle')],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.split()[0],
            info['commit'],
        )
        self.assertTrue(os.access(share / 'guest-validate.sh', os.X_OK))
        self.assertEqual(sorted(p.name for p in (share / 'supplements').iterdir()), ['example', 'vm'])
        self.assertEqual(
            (share / 'supplements/vm/config.yaml').read_bytes(),
            git('show', 'HEAD:tests/fixtures/supplements/vm/config.yaml').stdout.encode(),
        )
        self.assertEqual([p.name for p in vm.iterdir() if p.name.startswith('share-new')], [])

    def test_a_share_is_refused_before_anything_is_created_while_head_lacks_the_kit(self):
        checkout = self.home.parent / 'checkout'
        (checkout / 'tests/vm').mkdir(parents=True)
        shutil.copy2(HOST, checkout / 'tests/vm/run-vm.sh')
        env = dict(
            self.env,
            GIT_CONFIG_NOSYSTEM='1',
            GIT_AUTHOR_NAME='t',
            GIT_AUTHOR_EMAIL='t@example.invalid',
            GIT_COMMITTER_NAME='t',
            GIT_COMMITTER_EMAIL='t@example.invalid',
        )
        for arguments in (('init', '-q'), ('add', '-A'), ('commit', '-q', '-m', 'without the guest script')):
            subprocess.run(['git', '-C', str(checkout), *arguments], env=env, check=True, capture_output=True)
        vm = self.home.parent / 'vm'
        result = subprocess.run(
            ['/bin/bash', str(checkout / 'tests/vm/run-vm.sh'), '--dir', str(vm), 'share'],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('commit the kit first', result.stderr)
        self.assertFalse(vm.exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_a_real_run_without_qemu_refuses_and_names_the_package(self):
        tools = Path(tempfile.mkdtemp(dir=self.home.parent))
        for directory in ('/usr/bin', '/bin'):
            for entry in os.scandir(directory):
                if not entry.name.startswith('qemu-') and not os.path.lexists(tools / entry.name):
                    (tools / entry.name).symlink_to(entry.path)
        result = self.host('run', env=dict(self.env, PATH=str(tools)))
        self.assertEqual(result.returncode, 1)
        self.assertIn('install QEMU with: sudo pacman -S --needed qemu-desktop', result.stderr)


class GuestScriptTests(unittest.TestCase):
    def test_every_stage_calls_only_commands_and_options_the_cli_has(self):
        calls = guest_invocations()
        for number, tokens in calls:
            with self.subTest(line=number, call=' '.join(tokens)):
                status, called = parse(tokens)
                self.assertEqual(status, 0)
                self.assertEqual(len(called), 1)
        made = {' '.join(tokens) for _, tokens in calls}
        for expected in (
            'supplement attach --none',
            'check',
            'apply --system',
            'apply --home',
            'status',
            'supplement rebind --path VALUE --new-id',
            'check --supplement VALUE',
        ):
            self.assertIn(expected, made)

    def test_commands_the_guest_names_in_its_messages_exist(self):
        commands = '|'.join(('selftest', 'check', 'data', 'diff', 'apply', 'status', 'supplement', 'integration'))
        for match in re.finditer(rf'\./bin/shaffy((?: +(?:{commands}))(?: +[-\w.=/]+)*)', GUEST.read_text()):
            with self.subTest(command=match.group(0)):
                self.assertEqual(parse(match.group(1).split())[0], 0)

    def test_the_bootstrap_stage_runs_readme_s_bootstrap_commands(self):
        result = subprocess.run(
            ['bash', str(GUEST), '--show-bootstrap'],
            capture_output=True,
            text=True,
            env=dict(os.environ, SHAFFY_VM_CHECKOUT=str(ROOT)),
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], pacman_step())
        self.assertIn('mise install ansible-core', lines)
        self.assertTrue(
            any('ansible-galaxy collection install -r requirements.yml -p collections' in line for line in lines)
        )

    def test_the_bundle_check_works_outside_a_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            env = dict(
                os.environ,
                GIT_CEILING_DIRECTORIES=str(base),
                GIT_CONFIG_NOSYSTEM='1',
                GIT_CONFIG_GLOBAL=os.devnull,  # never the user's signing setup
                GIT_AUTHOR_NAME='t',
                GIT_AUTHOR_EMAIL='t@example.invalid',
                GIT_COMMITTER_NAME='t',
                GIT_COMMITTER_EMAIL='t@example.invalid',
            )
            for arguments in (
                ('init', '-q', 'repository'),
                ('-C', 'repository', 'commit', '-q', '--allow-empty', '-m', 'one'),
                ('-C', 'repository', 'bundle', 'create', '-q', '../good.bundle', 'HEAD'),
            ):
                subprocess.run(['git', *arguments], cwd=base, env=env, check=True, capture_output=True)
            (base / 'bad.bundle').write_text('not a bundle\n')
            (base / 'elsewhere').mkdir()
            for bundle, status in (('good.bundle', 0), ('bad.bundle', 1)):
                with self.subTest(bundle=bundle):
                    result = subprocess.run(
                        [
                            'bash',
                            '-c',
                            guest_function('bundle_valid') + '\nbundle_valid "$1"',
                            'bash',
                            str(base / bundle),
                        ],
                        cwd=base / 'elsewhere',
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(min(result.returncode, 1), status, result.stderr)

    def test_the_pair_check_reads_both_journals_and_a_resumed_home_operation(self):
        important = {'important': 'yes'}

        def snapshot(number, kind, description, pre=None):
            return {
                'number': number,
                'type': kind,
                'pre-number': pre,
                'description': description,
                'userdata': important,
            }

        user = [
            {'op': 'A', 'event': 'phase-start', 'phase': 'home', 'pre_snapshot': 5},
            {'op': 'A', 'event': 'phase-done', 'phase': 'home', 'post_snapshot': 6},
            {'op': 'B', 'event': 'phase-start', 'phase': 'home', 'pre_snapshot': 7},
            {'op': 'B', 'event': 'resume-start', 'pre_snapshot': 8},
            {'op': 'B', 'event': 'phase-done', 'phase': 'home', 'post_snapshot': 9},
            {'op': 'C', 'event': 'complete', 'scope': 'system'},
        ]
        root = [
            {'op': 'C', 'event': 'phase-start', 'phase': 'system', 'pre': {'root': 40, 'home': 10}},
            {'op': 'C', 'event': 'phase-done', 'phase': 'system', 'post': {'root': 41, 'home': 11}},
        ]
        lists = {
            'home': [
                snapshot(5, 'pre', 'shaffy A home'),
                snapshot(6, 'post', 'shaffy A home', 5),
                snapshot(7, 'pre', 'shaffy B home'),
                snapshot(8, 'pre', 'shaffy B home resume 1'),
                snapshot(9, 'post', 'shaffy B home', 8),
                snapshot(10, 'pre', 'shaffy C system'),
                snapshot(11, 'post', 'shaffy C system', 10),
            ],
            'root': [
                snapshot(40, 'pre', 'shaffy C system'),
                dict(snapshot(41, 'post', 'shaffy C system', 40), userdata=None),
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / 'state/shaffy').mkdir(parents=True)
            (base / 'bin').mkdir()
            (base / 'state/shaffy/journal.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in user))
            (base / 'root.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in root))
            (base / 'lists.json').write_text(json.dumps(lists))
            (base / 'bin/snapper').write_text(
                '#!/usr/bin/python3\nimport json, sys\nconfig = sys.argv[sys.argv.index("-c") + 1]\n'
                f'print(json.dumps({{config: json.load(open({str(base / "lists.json")!r}))[config]}}))\n'
            )
            (base / 'bin/sudo').write_text('#!/bin/sh\nexec "$@"\n')
            for stub in ('snapper', 'sudo'):
                (base / 'bin' / stub).chmod(0o755)
            source = guest_python()
            anchor = "Path(f'/var/lib/shaffy/targets/{os.getuid()}.journal.jsonl')"
            self.assertIn(anchor, source)
            (base / 'checks.py').write_text(source.replace(anchor, repr(str(base / 'root.jsonl'))))
            result = subprocess.run(
                [sys.executable, str(base / 'checks.py'), 'pairs', 'A', 'B', 'C'],
                env={'HOME': str(base), 'XDG_STATE_HOME': str(base / 'state'), 'PATH': f'{base / "bin"}:/usr/bin:/bin'},
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(result.stderr, '')
        self.assertEqual(
            result.stdout.splitlines(),
            [
                'PASS snapper home pair 5..6 "shaffy A home", important=yes',
                'PASS snapper home pair 8..9 "shaffy B home", important=yes',
                'PASS snapper home pair 10..11 "shaffy C system", important=yes',
                'FAIL snapper root pair 40..41 "shaffy C system", important=yes',  # its post is not important
            ],
        )

    def test_a_stage_refuses_outside_a_prepared_guest_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / 'evidence'
            result = subprocess.run(
                ['bash', str(GUEST), 'defaults'],
                capture_output=True,
                text=True,
                env=dict(os.environ, SHAFFY_VM_EVIDENCE=str(evidence)),
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertFalse(evidence.exists())


# The keys cachyos-installer 0.12.4 (the pinned image's) accepts; it rejects any other key.
INSTALLER_KEYS = set(
    """menus install_type headless_mode server_mode allow_auto_partition encrypt_swap hostcache
device fs_name mount_opts partitions subvolumes zfs_passphrase hostname locale xkbmap keymap timezone user_name
username user_pass user_password user_shell root_pass root_password kernel desktop bootloader post_install
server_profile net_profiles_path ssh_authorized_keys server_extra_packages server_extra_tcp_ports
server_extra_udp_ports autologin user_groups hw_clock chwd carry_network os_prober netinstall_groups
config_version""".split()
)


class HeadlessInstallTests(unittest.TestCase):
    def test_the_install_config_asks_for_the_supported_baseline_on_the_vm_disk(self):
        config = json.loads((KIT / 'vm-install.json').read_text())
        self.assertLessEqual(set(config), INSTALLER_KEYS)
        self.assertTrue(config['headless_mode'])
        self.assertEqual(
            (config['device'], config['fs_name'], config['subvolumes'], config['bootloader'], config['desktop']),
            ('/dev/vda', 'btrfs', 'default', 'limine', 'niri'),
        )
        self.assertEqual(
            [(p['name'], p['mountpoint'], p['type']) for p in config['partitions']],
            [('/dev/vda1', '/boot', 'boot'), ('/dev/vda2', '/', 'root')],
        )
        self.assertEqual((config['hostname'], config['user_name']), ('shaffy-vm', 'demo'))  # synthetic
        self.assertEqual(config['post_install'], '/run/shaffy-share/vm-post-install.sh')

    def test_the_post_install_step_lists_the_shares_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'etc').mkdir()
            (root / 'etc/fstab').write_text('UUID=x / btrfs subvol=/@ 0 0\n')
            for _ in range(2):
                subprocess.run(['bash', str(KIT / 'vm-post-install.sh'), str(root)], check=True, capture_output=True)
            fstab = (root / 'etc/fstab').read_text()
            self.assertEqual(fstab.count('shaffy-share     /mnt/shaffy-share     9p'), 1)
            self.assertEqual(fstab.count('shaffy-evidence  /mnt/shaffy-evidence  9p'), 1)
            self.assertIn('nofail', fstab)
            self.assertTrue((root / 'mnt/shaffy-share').is_dir() and (root / 'mnt/shaffy-evidence').is_dir())

    def test_the_headless_install_refuses_outside_the_installer_as_root(self):
        result = subprocess.run(
            ['bash', str(KIT / 'install-headless.sh')], capture_output=True, text=True, timeout=30, check=False
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('run it as root', result.stderr)


def drive():
    spec = importlib.util.spec_from_file_location('drive', KIT / 'drive.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HeadlessTests(unittest.TestCase):
    def test_a_headless_install_boots_the_image_kernel_into_the_install_alone(self):
        result = subprocess.run(
            ['bash', str(HOST), '--headless', '--dry-run', 'install'],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        command = result.stdout.strip().splitlines()[-1]
        for part in (
            '-display egl-headless',
            '-serial file:',
            '-qmp unix:',
            '-vnc unix:',
            'hostfwd=tcp:127.0.0.1:2222-:22',
            '-kernel ',
            '-initrd ',
            'console=ttyS0,115200',
            'copytoram=n',
            'install-headless.sh --at-boot',
            'systemd.run_success_action=poweroff',
            '-no-reboot',
            'systemd.unit=kernel-command-line.target',
        ):
            self.assertIn(part, command)
        self.assertNotIn('gtk', command)
        self.assertNotIn('bootindex=0', command)  # -kernel takes boot index 0; QEMU refuses a second

    def test_the_driver_types_on_a_us_layout_and_answers_only_known_prompts(self):
        module = drive()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(list(module.chords('dE-_')), [['d'], ['shift', 'e'], ['minus'], ['shift', 'minus']])
            self.assertEqual(module.respond('x\n[sudo] password for demo: ', [], [], None), 'demo\n')
            self.assertEqual(module.respond('BECOME password: ', [], [], None), 'demo\n')
            question = 'Manual check: the Noctalia bar and the wallpaper are shown? [y/n/s(kip)] '
            self.assertEqual(module.respond(question, [('wallpaper', 'y')], [], None), 'y\n')
            self.assertEqual(module.respond(question, [('main menu', 'y')], [], None), 's\n')
            self.assertIsNone(module.respond('running ansible', [], [], None))
            confirm = 'Total Download Size:  60.42 MiB\n\n:: Proceed with installation? [Y/n] '
            self.assertEqual(module.respond(confirm, [], [], None), 'y\n')
            downgrade = 'warning: downgrading package git (2.55.0-1.1 => 2.55.0-1)\n' + confirm
            self.assertEqual(module.respond(downgrade, [], [], None), 'n\n')

    def test_a_screenshot_is_the_vnc_frame_as_a_png(self):
        module = drive()
        # Two by two pixels, sent as two one-row rectangles in the BGRX order the driver asks for.
        rows = [bytes([0, 0, 255, 0, 0, 255, 0, 0]), bytes([255, 0, 0, 0, 255, 255, 255, 0])]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'vnc.sock'
            server = socket.socket(socket.AF_UNIX)
            server.bind(str(path))
            server.listen(1)
            received = []

            def serve():
                connection, _ = server.accept()
                with connection:
                    connection.sendall(b'RFB 003.008\n')
                    connection.recv(12)
                    connection.sendall(b'\x01\x01')  # one security type: none
                    connection.recv(1)
                    connection.sendall(struct.pack('>I', 0))
                    connection.recv(1)
                    connection.sendall(struct.pack('>HH16xI', 2, 2, 2) + b'vm')
                    data = b''
                    while len(data) < 38:  # SetPixelFormat, SetEncodings, FramebufferUpdateRequest
                        data += connection.recv(64)
                    received.append(data)
                    connection.sendall(
                        b'\x02'
                        + struct.pack('>BxH', 0, 2)
                        + struct.pack('>HHHHi', 0, 0, 2, 1, 0)
                        + rows[0]
                        + struct.pack('>HHHHi', 0, 1, 2, 1, 0)
                        + rows[1]
                    )

            thread = threading.Thread(target=serve)
            thread.start()
            image = module.vnc_screenshot(path, timeout=10)
            thread.join(10)
            server.close()
        self.assertEqual(received[0][:20], struct.pack('>B3xBBBBHHHBBB3x', 0, 32, 24, 0, 1, 255, 255, 255, 16, 8, 0))
        self.assertEqual(image[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II', image[16:24]), (2, 2))
        idat = image.index(b'IDAT')
        length = struct.unpack('>I', image[idat - 4 : idat])[0]
        pixels = zlib.decompress(image[idat + 4 : idat + 4 + length])
        # red, green; blue, white; each row led by filter type 0
        self.assertEqual(pixels, bytes([0, 255, 0, 0, 0, 255, 0, 0, 0, 0, 255, 255, 255, 255]))


class VMSupplementTests(unittest.TestCase):
    def test_every_integration_is_requested_or_its_absence_explained(self):
        catalogue = yaml.safe_load((ROOT / 'defaults/integrations.yml').read_text())
        text = (SUPPLEMENT / 'config.yaml').read_text()
        requested = yaml.safe_load(text)['integrations']
        self.assertEqual(set(requested.values()), {'managed'})
        self.assertLessEqual(set(requested), set(catalogue))
        readme = (KIT / 'README.md').read_text()
        for name in sorted(set(catalogue) - set(requested)):
            with self.subTest(integration=name):
                self.assertIn(f'`{name}`', readme)
                self.assertIn(name, text)

    @unittest.skipUnless(
        shutil.which('chezmoi') and all(importlib.util.find_spec(m) for m in ('yaml', 'jsonschema')),
        'check needs chezmoi, PyYAML and python-jsonschema',
    )
    def test_the_vm_supplement_passes_check(self):
        from shaffy import desktop_key

        if desktop_key.problems():
            self.skipTest('; '.join(desktop_key.problems()))
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                [str(ROOT / 'bin/shaffy'), 'check', '--supplement', str(SUPPLEMENT), '--no-render'],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                env={'HOME': home, 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Settings are valid: supplement vm-desktop', result.stdout)


if __name__ == '__main__':
    unittest.main()
