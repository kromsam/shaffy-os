#!/usr/bin/python3
"""Drive the kit's VM without its window (tests/vm/README.md, "Without a window").

run-vm.sh --headless gives the VM an offscreen display, a QMP socket for keys, a VNC
socket for what the display shows and SSH on 127.0.0.1:2222 with a throwaway key that
the headless install authorises for the synthetic account. This script uses them:

  wait-ssh              wait until the guest accepts the key
  login                 type the account's password at SDDM and wait for the Niri session
  screenshot FILE.png   save what the display shows
  keys KEY...           press keys (QEMU qcodes; join a chord with -, e.g. meta_l-spc)
  type TEXT             type text on the guest's keyboard
  ssh COMMAND           run a shell command in the guest
  stage STAGE           run a guest-validate.sh stage inside the Niri session
  poweroff              shut the guest down (through SSH; a Niri session suspends on the power button)

ssh and stage run under a pseudo-terminal and answer sudo's and Ansible's password
prompts with the synthetic password. They confirm a pacman transaction unless it
downgrades a package, which they decline. A stage's questions are answered by the first
--answer REGEX=REPLY whose pattern matches the question, after running the guest
command of the first matching --on REGEX=COMMAND; a question nothing matches is skipped
(s), so the stage records it as unanswered rather than passed.
"""
import argparse
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import socket
import struct
import subprocess
import sys
import time
import zlib

VM_DIR = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'shaffy/vm'
ACCOUNT, PASSWORD, PORT = 'demo', 'demo', 2222
PASSWORD_PROMPT = re.compile(r'(\[sudo\] password for \S+|BECOME password|Password): *$')
QUESTION = re.compile(r'([^\n]*)\[y/n/s\(kip\)\] *$')
PACMAN_CONFIRM = re.compile(r':: Proceed with installation\? \[Y/n\] *$')
KEYS = {' ': 'spc', '\n': 'ret', '\t': 'tab', '-': 'minus', '=': 'equal', '.': 'dot', ',': 'comma',
        '/': 'slash', ';': 'semicolon', "'": 'apostrophe', '[': 'bracket_left', ']': 'bracket_right',
        '\\': 'backslash', '`': 'grave_accent'}
SHIFTED = {'_': 'minus', '+': 'equal', '>': 'dot', '<': 'comma', '?': 'slash', ':': 'semicolon',
           '"': 'apostrophe', '{': 'bracket_left', '}': 'bracket_right', '|': 'backslash', '~': 'grave_accent',
           '!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8', '(': '9', ')': '0'}


def chords(text):
    """The qcode chords that type TEXT on a US layout."""
    for char in text:
        if char.isascii() and (char.islower() or char.isdigit()):
            yield [char]
        elif char.isascii() and char.isupper():
            yield ['shift', char.lower()]
        elif char in KEYS:
            yield [KEYS[char]]
        elif char in SHIFTED:
            yield ['shift', SHIFTED[char]]
        else:
            raise SystemExit(f'drive: cannot type {char!r}')


class QMPError(SystemExit):
    pass


def png(width, height, rgb):
    """A PNG file holding RGB, WIDTH by HEIGHT pixels of three bytes each."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    stride = width * 3
    rows = b''.join(b'\x00' + rgb[row * stride:(row + 1) * stride] for row in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows, 6)) + chunk(b'IEND', b''))


def vnc_screenshot(path, timeout=30):
    """The display as a PNG, read from QEMU's VNC socket at PATH.

    QMP's screendump cannot read the GL scanout Niri and SDDM draw on; egl-headless copies
    every frame into the surface the VNC server serves. This speaks just enough RFB 3.8 for
    one full frame: no authentication, 32-bit true colour, raw encoding.
    """
    with socket.socket(socket.AF_UNIX) as vnc:
        vnc.settimeout(timeout)
        vnc.connect(str(path))

        def read(size):
            data = bytearray()
            while len(data) < size:
                part = vnc.recv(size - len(data))
                if not part:
                    raise SystemExit('drive: the VNC server closed the connection')
                data += part
            return bytes(data)

        if not read(12).startswith(b'RFB 003.'):
            raise SystemExit('drive: the VNC socket does not speak RFB')
        vnc.sendall(b'RFB 003.008\n')
        types = read(read(1)[0])
        if 1 not in types:
            raise SystemExit(f'drive: the VNC server wants authentication (security types {list(types)})')
        vnc.sendall(b'\x01')
        if struct.unpack('>I', read(4))[0] != 0:
            raise SystemExit('drive: the VNC server refused the connection')
        vnc.sendall(b'\x01')  # ClientInit: share the display
        width, height = struct.unpack('>HH', read(4))
        read(16)
        read(struct.unpack('>I', read(4))[0])
        # SetPixelFormat: 32 bits per pixel, depth 24, little-endian true colour, red at bit 16.
        vnc.sendall(struct.pack('>B3xBBBBHHHBBB3x', 0, 32, 24, 0, 1, 255, 255, 255, 16, 8, 0))
        vnc.sendall(struct.pack('>BxHi', 2, 1, 0))  # SetEncodings: raw only
        vnc.sendall(struct.pack('>BBHHHH', 3, 0, 0, 0, width, height))  # the whole frame
        frame = bytearray(width * height * 4)
        received = 0
        while received < width * height:
            kind = read(1)[0]
            if kind == 0:  # FramebufferUpdate
                for _ in range(struct.unpack('>xH', read(3))[0]):
                    x, y, w, h, encoding = struct.unpack('>HHHHi', read(12))
                    if encoding != 0:
                        raise SystemExit(f'drive: unexpected VNC encoding {encoding}')
                    data = read(w * h * 4)
                    for row in range(h):
                        start = ((y + row) * width + x) * 4
                        frame[start:start + w * 4] = data[row * w * 4:(row + 1) * w * 4]
                    received += w * h
            elif kind == 1:  # SetColourMapEntries
                read(struct.unpack('>xHH', read(5))[1] * 6)
            elif kind == 2:  # Bell
                pass
            elif kind == 3:  # ServerCutText
                read(struct.unpack('>3xI', read(7))[0])
            else:
                raise SystemExit(f'drive: unexpected VNC message {kind}')
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = frame[2::4], frame[1::4], frame[0::4]
    return png(width, height, bytes(rgb))


class QMP:
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.connect(str(path))
        self.stream = self.socket.makefile('rw')
        json.loads(self.stream.readline())  # the greeting
        self.execute('qmp_capabilities')

    def execute(self, command, **arguments):
        self.stream.write(json.dumps({'execute': command, **({'arguments': arguments} if arguments else {})}) + '\n')
        self.stream.flush()
        while True:
            reply = json.loads(self.stream.readline())
            if 'return' in reply:
                return reply['return']
            if 'error' in reply:
                raise QMPError(f"drive: QMP {command}: {reply['error'].get('desc')}")

    def press(self, keys):
        self.execute('send-key', keys=[{'type': 'qcode', 'data': key} for key in keys], **{'hold-time': 60})
        time.sleep(0.08)


def ssh_command(vm_dir, remote):
    """ssh with the kit's key; REMOTE runs in bash whatever the account's login shell."""
    return ['ssh', '-i', str(vm_dir / 'ssh/id_ed25519'), '-p', str(PORT), '-tt',
            '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null', '-o', 'LogLevel=ERROR',
            '-o', 'ConnectTimeout=5', '-o', 'BatchMode=yes', f'{ACCOUNT}@127.0.0.1',
            'bash -c ' + shlex.quote(remote)]


def in_session(command):
    """A bash snippet running COMMAND under the account's user manager with the Niri session's variables."""
    return ('socket=$(ls /run/user/$(id -u)/niri.*.sock 2>/dev/null | head -n 1); '
            'display=$(systemctl --user show-environment | sed -n "s/^WAYLAND_DISPLAY=//p"); '
            'exec systemd-run --user --pty --wait --collect --same-dir --quiet '
            '-E NIRI_SOCKET="$socket" -E WAYLAND_DISPLAY="${display:-wayland-1}" '
            '-E XDG_SESSION_TYPE=wayland -E XDG_CURRENT_DESKTOP=niri -E TERM=xterm-256color ' + command)


def respond(buffer, answers, hooks, vm_dir):
    """The reply to a prompt at the end of BUFFER, or None when it ends in no prompt."""
    tail = buffer[-400:]
    if PASSWORD_PROMPT.search(tail):
        return PASSWORD + '\n'
    if PACMAN_CONFIRM.search(tail):
        # BUFFER holds the whole transaction since the last reply, with pacman's warnings.
        if 'downgrading package' in buffer:
            print('\n[drive] declining a pacman transaction that downgrades packages', flush=True)
            return 'n\n'
        print('\n[drive] confirming the pacman transaction', flush=True)
        return 'y\n'
    question = QUESTION.search(tail)
    if not question:
        return None
    text = question.group(1)
    for pattern, command in hooks:
        if re.search(pattern, text):
            print(f'\n[drive] running for "{pattern}": {command}', flush=True)
            subprocess.run(ssh_command(vm_dir, in_session('bash -c ' + shlex.quote(command))), check=False)
            break
    for pattern, reply in answers:
        if re.search(pattern, text):
            print(f'\n[drive] answering "{reply}" (rule "{pattern}")', flush=True)
            return reply + '\n'
    print('\n[drive] no rule answers this question; skipping it', flush=True)
    return 's\n'


def run_pty(argv, answers=(), hooks=(), vm_dir=VM_DIR, log=None):
    """Run ARGV under a pseudo-terminal, echoing its output and answering prompts."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(argv[0], argv)
    buffer = ''
    while True:
        ready, _, _ = select.select([fd], [], [], 1.0)
        if not ready:
            continue
        try:
            data = os.read(fd, 65536)
        except OSError:
            break
        if not data:
            break
        text = data.decode(errors='replace')
        sys.stdout.write(text)
        sys.stdout.flush()
        if log:
            log.write(text)
        buffer = (buffer + text)[-65536:]
        reply = respond(buffer, answers, hooks, vm_dir)
        if reply:
            os.write(fd, reply.encode())
            buffer = ''
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


def rules(values):
    parsed = []
    for value in values or []:
        pattern, separator, action = value.partition('=')
        if not separator:
            raise SystemExit(f'drive: {value!r} is not REGEX=VALUE')
        parsed.append((pattern, action))
    return parsed


def wait_ssh(vm_dir, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if subprocess.run(ssh_command(vm_dir, 'true'), capture_output=True, check=False).returncode == 0:
            return True
        time.sleep(5)
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--dir', type=Path, default=VM_DIR, help='the VM folder (default ~/.cache/shaffy/vm)')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('wait-ssh').add_argument('--timeout', type=int, default=600)
    commands.add_parser('login').add_argument('--timeout', type=int, default=120)
    commands.add_parser('screenshot').add_argument('file', type=Path)
    commands.add_parser('keys').add_argument('keys', nargs='+')
    commands.add_parser('type').add_argument('text')
    run = commands.add_parser('ssh')
    run.add_argument('remote', nargs=argparse.REMAINDER)
    stage = commands.add_parser('stage')
    stage.add_argument('stage')
    stage.add_argument('--answer', action='append', help='REGEX=REPLY for a stage question (y, n or s)')
    stage.add_argument('--on', action='append', help='REGEX=COMMAND run in the guest session before answering')
    commands.add_parser('poweroff')
    args = parser.parse_args(argv)
    vm_dir = args.dir

    if args.command == 'wait-ssh':
        ok = wait_ssh(vm_dir, args.timeout)
        print('drive: the guest accepts the key' if ok else 'drive: the guest did not answer on SSH in time')
        return 0 if ok else 1
    if args.command == 'screenshot':
        args.file.write_bytes(vnc_screenshot(vm_dir / 'vnc.sock'))
        print(f'drive: saved {args.file}')
        return 0
    if args.command in ('keys', 'type', 'login'):
        qmp = QMP(vm_dir / 'qmp.sock')
        if args.command == 'keys':
            for chord in args.keys:
                qmp.press(chord.split('-'))
        elif args.command == 'type':
            for chord in chords(args.text):
                qmp.press(chord)
        else:
            for chord in chords(PASSWORD):
                qmp.press(chord)
            qmp.press(['ret'])
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                if subprocess.run(ssh_command(vm_dir, f'pgrep -u {ACCOUNT} -x niri >/dev/null'),
                                  capture_output=True, check=False).returncode == 0:
                    print('drive: the Niri session is running')
                    return 0
                time.sleep(3)
            print('drive: no Niri session appeared; take a screenshot to see the login screen')
            return 1
        return 0
    if args.command == 'ssh':
        return run_pty(ssh_command(vm_dir, ' '.join(args.remote)), vm_dir=vm_dir)
    if args.command == 'stage':
        logs = vm_dir / 'drive-logs'
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / f'{args.stage}-{time.strftime("%Y%m%dT%H%M%S")}.log', 'w') as log:
            command = in_session(f'bash /mnt/shaffy-share/guest-validate.sh {shlex.quote(args.stage)}')
            return run_pty(ssh_command(vm_dir, command), rules(args.answer), rules(args.on), vm_dir, log)
    if args.command == 'poweroff':
        # Through systemd, not the power button: Niri takes that over and suspends instead.
        if wait_ssh(vm_dir, 10):
            run_pty(ssh_command(vm_dir, 'sudo systemctl poweroff'), vm_dir=vm_dir)
            print('drive: asked the guest to power off')
            return 0
        QMP(vm_dir / 'qmp.sock').execute('system_powerdown')
        print('drive: the guest does not answer on SSH, so its power button was pressed; in a Niri session that suspends it')
        return 0
    return 2


if __name__ == '__main__':
    sys.exit(main())
