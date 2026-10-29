#!/usr/bin/python3
"""Merge owned MCP and CE entries, preserving unrelated agent configuration."""
import copy
import json
import os
from pathlib import Path
import re
import sys
import tomllib

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))
CE_REPOSITORY = 'EveryInc/compound-engineering-plugin'
CE_MARKETPLACE = 'compound-engineering-plugin'
CE_PLUGIN = f'compound-engineering@{CE_MARKETPLACE}'


def merge_tables(text, namespace, entries):
    original = tomllib.loads(text)
    if all(original.get(namespace, {}).get(name) == spec for name, spec in entries.items()):
        return text
    result = text
    for name, spec in entries.items():
        if original.get(namespace, {}).get(name) == spec:
            continue
        escaped = re.escape(name)
        # Remove only this server's explicit table and nested tables. Preserve all
        # other settings/comments; refuse unfamiliar inline forms rather than
        # rewriting the app's whole configuration.
        pattern = rf'(?m)^[ \t]*\[[ \t]*{namespace}\.(?:{escaped}|"{escaped}"|\'{escaped}\')(?:\.[^\]\n]+)?[ \t]*\][^\n]*(?:\n|$)'
        while match := re.search(pattern, result):
            # Stop at the next table header line itself. A pattern starting with
            # \s* would also swallow the blank lines before that header, leaving
            # them behind and adding blank lines on every rewrite.
            end = re.search(r'(?m)^[ \t]*\[', result[match.end():])
            stop = match.end() + end.start() if end else len(result)
            result = result[:match.start()] + result[stop:]
        if name in tomllib.loads(result).get(namespace, {}):
            kind = 'MCP' if namespace == 'mcp_servers' else namespace
            raise ValueError(f'Review the existing inline {kind} entry for {name}')
        if spec is None:
            continue
        result = result.rstrip() + '\n\n' + toml_table(f'{namespace}.{toml_key(name)}', spec)
    expected = copy.deepcopy(original)
    for name, spec in entries.items():
        if spec is None:
            expected.get(namespace, {}).pop(name, None)
        else:
            expected.setdefault(namespace, {})[name] = spec
    parsed = tomllib.loads(result)
    # Removing the last explicit server table can also remove its implicit parent.
    if expected.get(namespace) == {} and namespace not in parsed:
        del expected[namespace]
    assert parsed == expected, 'Unrelated Codex settings changed'
    return result


def toml_key(key):
    return key if re.fullmatch(r'[A-Za-z0-9_-]+', key) else json.dumps(key)


def toml_table(header, values):
    text = f'[{header}]\n'
    for key, value in values.items():
        if not isinstance(value, dict):
            text += f'{toml_key(key)} = {json.dumps(value)}\n'
    for key, value in values.items():
        if isinstance(value, dict):
            text += '\n' + toml_table(f'{header}.{toml_key(key)}', value)
    return text


def merge_codex(text, servers, compound_engineering=None):
    text = merge_tables(text, 'mcp_servers', servers)
    if compound_engineering is None:
        return text
    existing = tomllib.loads(text)
    plugin = {**existing.get('plugins', {}).get(CE_PLUGIN, {}),
              'enabled': compound_engineering}
    text = merge_tables(text, 'plugins', {CE_PLUGIN: plugin})
    marketplace = {'source_type': 'git',
                   'source': f'https://github.com/{CE_REPOSITORY}.git', 'ref': 'main'}
    return merge_tables(text, 'marketplaces', {CE_MARKETPLACE: marketplace})


def merge_claude(text, servers, typed=True):
    """Claude Code's entries name their transport. Claude Desktop's file holds commands only,
    without one; it reaches HTTP servers through its own connectors and extensions."""
    result = json.loads(text) if text.strip() else {}
    original = copy.deepcopy(result)
    for name, spec in servers.items():
        if spec is None:
            result.get('mcpServers', {}).pop(name, None)
        elif typed:
            transport = 'http' if 'url' in spec else 'stdio'
            result.setdefault('mcpServers', {})[name] = {'type': transport, **spec}
        elif 'command' in spec:
            result.setdefault('mcpServers', {})[name] = spec
    if result == original and text.strip():
        return text
    # A modifier may be rendering a new file. Empty TOML is valid; empty JSON
    # is not, even when every requested server is disabled.
    return json.dumps(result, indent=2, ensure_ascii=False) + '\n'


def merge_claude_settings(text, compound_engineering):
    result = json.loads(text) if text.strip() else {}
    original = copy.deepcopy(result)
    result.setdefault('enabledPlugins', {})[CE_PLUGIN] = compound_engineering
    result.setdefault('extraKnownMarketplaces', {})[CE_MARKETPLACE] = {
        'source': {'source': 'github', 'repo': CE_REPOSITORY}}
    if result == original and text.strip():
        return text
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
        if 'command' in entry:
            entry['command'] = os.path.expanduser(spec['command'])
            entry.setdefault('args', [])
        entries[name] = entry
    return entries


if __name__ == '__main__':
    text = sys.stdin.read()
    try:
        from shaffy.fastload import load_data
        enabled = load_data(REPO)['shaffy']['settings']['ai'].get('compound_engineering', {}).get('enabled')
        servers = mcp_servers()
        if sys.argv[1] == 'claude-settings':
            output = merge_claude_settings(text, enabled)
        elif sys.argv[1] == 'codex':
            output = merge_codex(text, servers, enabled)
        elif sys.argv[1] == 'claude':
            output = merge_claude(text, servers)
        elif sys.argv[1] == 'claude-desktop':
            output = merge_claude(text, servers, typed=False)
        else:
            raise ValueError('expected claude, claude-desktop, claude-settings or codex')
    except Exception as error:  # refuse to rewrite the file on invalid settings
        sys.exit(f'merge-ai-config: cannot read the Shaffy OS settings: {error}')
    sys.stdout.write(output)
