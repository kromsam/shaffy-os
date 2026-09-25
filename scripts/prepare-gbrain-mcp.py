#!/usr/bin/env python3
"""Register a private local MCP client, read-only for one brain source, without printing its credentials.

Usage: prepare-gbrain-mcp.py SOURCE (configure-gbrain-mcp.sh passes ai.gbrain.source).
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def main():
    assert os.getuid() != 0 and len(sys.argv) == 2 and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', sys.argv[1])
    source = sys.argv[1]
    home = Path.home()
    state = Path(os.environ.get('XDG_STATE_HOME') or home / '.local/state') / 'shaffy/runs'
    backup = state / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-gbrain-before-mcp')
    config = home / '.config/gbrain-mcp/client.json'
    assert config.resolve() == config
    os.umask(0o077)
    if config.exists():
        assert {'client_id', 'client_secret'} <= json.loads(config.read_text()).keys()
        print('Existing private GBrain client retained')
        return
    assert subprocess.run(['systemctl', '--user', 'is-active', '--quiet', 'gbrain-mcp.service']).returncode != 0
    # A copy of the brain from just before the first registration.
    backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    subprocess.run(['cp', '--archive', '--reflink=auto', '--', str(home / '.gbrain'), str(backup)], check=True)
    result = subprocess.run([str(home / '.local/bin/gbrain'), 'auth', 'register-client', 'shaffy-ai-readonly',
        '--grant-types', 'client_credentials', '--scopes', 'read',
        '--source', source, '--federated-read', source],
        capture_output=True, text=True)
    assert result.returncode == 0, 'Client registration failed; credentials were not printed'
    values = {}
    for label, name in [('Client ID', 'client_id'), ('Client Secret', 'client_secret')]:
        match = re.search(r'^\s*' + label + r':\s+(\S+)', result.stdout, re.M)
        assert match, 'Unexpected registration output; inspect locally before retrying'
        values[name] = match[1]
    config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with config.open('x') as stream:
        json.dump(values, stream)
        stream.write('\n')
    config.chmod(0o600)
    print(f'Registered read-only access to {source}; no other source, write, admin or agent grant')


if __name__ == '__main__':
    main()
