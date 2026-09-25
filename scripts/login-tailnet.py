#!/usr/bin/env python3
"""Open the Tailnet account login privately, without printing its token.

The login server is tailnet.login_server from the bound settings; sudo asks for the
password in this terminal.
"""
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))
from shaffy.fastload import load_data  # noqa: E402

account = pwd.getpwuid(os.getuid())
if os.getuid() == 0 or str(Path.home()) != account.pw_dir:
    raise SystemExit('Run as the desktop user with their own HOME, without sudo.')
server = load_data(REPO)['shaffy']['settings'].get('tailnet', {}).get('login_server')
if not server:
    raise SystemExit('Set tailnet.login_server in the supplement first.')
server = server.rstrip('/')
status = json.loads(subprocess.check_output(['tailscale', 'status', '--json'], text=True))
if status.get('BackendState') == 'Running':
    print('Tailnet is already signed in; existing network settings preserved.')
    raise SystemExit(0)
# First restore only the mesh connection. The exit node can be chosen in Tailnet
# after sign-in and reachability checks; do not disrupt the current LAN/DNS.
p = subprocess.Popen(['sudo', '/usr/bin/tailscale', 'up', f'--login-server={server}',
                      f'--operator={account.pw_name}', '--accept-dns=false', '--accept-routes=false',
                      '--timeout=5m'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
opened = False
messages = []
for line in p.stdout:
    messages.append(re.sub(r'https?://[^\s]+', '[private URL]', line))
    for url in re.findall(re.escape(server) + r'/[^\s]+', line):
        if not opened:
            subprocess.run(['gio', 'open', url], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print('The Tailnet sign-in opened privately in your browser.', flush=True)
            opened = True
code = p.wait()
if code:
    print(''.join(messages).strip())
print('Tailnet authentication completed.' if code == 0 else 'Tailnet sign-in still needs completion; use this helper again.')
raise SystemExit(code)
