#!/usr/bin/env python3
"""Merge repository-owned MCP entries while preserving unrelated AI settings."""
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
        escaped = re.escape(name)
        # Remove only this server's explicit table and nested tables. Preserve all
        # other settings/comments; refuse unfamiliar inline forms rather than
        # rewriting the app's whole configuration.
        pattern = rf'(?m)^\[\s*mcp_servers\.(?:{escaped}|"{escaped}"|\'{escaped}\')(?:\.[^\]\n]+)?\s*\][^\n]*\n'
        while match := re.search(pattern, result):
            # Stop at the next table header line itself. A pattern starting with
            # \s* would also swallow the blank lines before that header, leaving
            # them behind and adding blank lines on every rewrite.
            end = re.search(r'(?m)^[ \t]*\[', result[match.end():])
            stop = match.end() + end.start() if end else len(result)
            result = result[:match.start()] + result[stop:]
        if name in tomllib.loads(result).get('mcp_servers', {}):
            raise ValueError(f'Review the existing inline MCP entry for {name}')
        result = result.rstrip() + f'\n\n[mcp_servers.{name}]\n'
        result += 'command = ' + json.dumps(spec['command']) + '\n'
        result += 'args = ' + json.dumps(spec.get('args', [])) + '\n'
        if spec.get('env'):
            result += f'\n[mcp_servers.{name}.env]\n'
            result += ''.join(f'{k} = {json.dumps(v)}\n' for k, v in spec['env'].items())
    expected = copy.deepcopy(original)
    expected.setdefault('mcp_servers', {}).update(servers)
    assert tomllib.loads(result) == expected, 'Unrelated Codex settings changed'
    return result


def merge_claude(text, servers):
    result = json.loads(text) if text.strip() else {}
    original = copy.deepcopy(result)
    result.setdefault('mcpServers', {}).update({k: {'type': 'stdio', **v} for k, v in servers.items()})
    if result == original:
        return text
    return json.dumps(result, indent=2, ensure_ascii=False) + '\n'


def mcp_servers():
    """The effective MCP servers of this checkout's settings, with ~/ expanded."""
    from shaffy.fastload import load_data
    servers = load_data(REPO)['shaffy']['settings']['ai']['mcp_servers']
    return {name: {**spec, 'command': os.path.expanduser(spec['command'])}
            for name, spec in servers.items()}


if __name__ == '__main__':
    merge = {'codex': merge_codex, 'claude': merge_claude}[sys.argv[1]]
    text = sys.stdin.read()
    try:
        servers = mcp_servers()
    except Exception as error:  # refuse to rewrite the file on invalid settings
        sys.exit(f'merge-ai-config: cannot read the Shaffy OS settings: {error}')
    sys.stdout.write(merge(text, servers))
