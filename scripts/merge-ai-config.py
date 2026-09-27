#!/usr/bin/env python3
"""Merge named MCP entries (None removes one), preserving unrelated AI settings."""
import copy
import json
import os
from pathlib import Path
import re
import sys
import tomllib

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))


def merge_codex(text, servers):
    original = tomllib.loads(text)
    if all(original.get('mcp_servers', {}).get(name) == spec for name, spec in servers.items()):
        return text
    result = text
    for name, spec in servers.items():
        if original.get('mcp_servers', {}).get(name) == spec:
            continue
        escaped = re.escape(name)
        # Remove only this server's explicit table and nested tables. Preserve all
        # other settings/comments; refuse unfamiliar inline forms rather than
        # rewriting the app's whole configuration.
        pattern = rf'(?m)^[ \t]*\[[ \t]*mcp_servers\.(?:{escaped}|"{escaped}"|\'{escaped}\')(?:\.[^\]\n]+)?[ \t]*\][^\n]*(?:\n|$)'
        while match := re.search(pattern, result):
            # Stop at the next table header line itself. A pattern starting with
            # \s* would also swallow the blank lines before that header, leaving
            # them behind and adding blank lines on every rewrite.
            end = re.search(r'(?m)^[ \t]*\[', result[match.end():])
            stop = match.end() + end.start() if end else len(result)
            result = result[:match.start()] + result[stop:]
        if name in tomllib.loads(result).get('mcp_servers', {}):
            raise ValueError(f'Review the existing inline MCP entry for {name}')
        if spec is None:
            continue
        result = result.rstrip() + f'\n\n[mcp_servers.{name}]\n'
        result += 'command = ' + json.dumps(spec['command']) + '\n'
        result += 'args = ' + json.dumps(spec.get('args', [])) + '\n'
        if spec.get('env'):
            result += f'\n[mcp_servers.{name}.env]\n'
            result += ''.join(f'{k} = {json.dumps(v)}\n' for k, v in spec['env'].items())
    expected = copy.deepcopy(original)
    for name, spec in servers.items():
        if spec is None:
            expected.get('mcp_servers', {}).pop(name, None)
        else:
            expected.setdefault('mcp_servers', {})[name] = spec
    parsed = tomllib.loads(result)
    # Removing the last explicit server table can also remove its implicit parent.
    if expected.get('mcp_servers') == {} and 'mcp_servers' not in parsed:
        del expected['mcp_servers']
    assert parsed == expected, 'Unrelated Codex settings changed'
    return result


def merge_claude(text, servers):
    result = json.loads(text) if text.strip() else {}
    original = copy.deepcopy(result)
    for name, spec in servers.items():
        if spec is None:
            result.get('mcpServers', {}).pop(name, None)
        else:
            result.setdefault('mcpServers', {})[name] = {'type': 'stdio', **spec}
    if result == original and text.strip():
        return text
    # A modifier may be rendering a new file. Empty TOML is valid; empty JSON
    # is not, even when every requested server is disabled.
    return json.dumps(result, indent=2, ensure_ascii=False) + '\n'


def mcp_servers():
    """Effective MCP entries, with ~/ expanded and disabled entries marked for removal."""
    from shaffy.fastload import load_data
    servers = load_data(REPO)['shaffy']['settings']['ai']['mcp_servers']
    entries = {}
    for name, spec in servers.items():
        if not spec.get('enabled', True):
            entries[name] = None
            continue
        entry = {key: value for key, value in spec.items() if key != 'enabled'}
        entry['command'] = os.path.expanduser(spec['command'])
        entry.setdefault('args', [])
        entries[name] = entry
    return entries


if __name__ == '__main__':
    merge = {'codex': merge_codex, 'claude': merge_claude}[sys.argv[1]]
    text = sys.stdin.read()
    try:
        servers = mcp_servers()
    except Exception as error:  # refuse to rewrite the file on invalid settings
        sys.exit(f'merge-ai-config: cannot read the Shaffy OS settings: {error}')
    sys.stdout.write(merge(text, servers))
