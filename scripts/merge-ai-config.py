#!/usr/bin/python3
"""Merge owned MCP, plugin and hook entries, preserving unrelated agent configuration."""

import copy
import json
import os
from pathlib import Path
import re
import sys
import tomllib

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))
PSTACK_REPOSITORY = 'michael-denyer/pstack-claude'
PSTACK_MARKETPLACE = 'pstack-claude'
PSTACK_PLUGIN = f'pstack@{PSTACK_MARKETPLACE}'
# Compound Engineering, which pstack replaced: its entries are dropped, and
# scripts/install-pstack.py uninstalls it. Remove after 2027-01-29, with the CE parts
# of both scripts and of tests/test_pstack.py.
CE_MARKETPLACE = 'compound-engineering-plugin'
CE_PLUGIN = f'compound-engineering@{CE_MARKETPLACE}'
# What marks the hooks this script owns, whichever checkout they name.
SUPPLEMENT_HOOK = 'lint --hook --supplement'


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
            end = re.search(r'(?m)^[ \t]*\[', result[match.end() :])
            stop = match.end() + end.start() if end else len(result)
            result = result[: match.start()] + result[stop:]
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


def merge_codex(text, servers, pstack=None):
    text = merge_tables(text, 'mcp_servers', servers)
    if pstack is None:
        return text
    existing = tomllib.loads(text)
    plugin = {**existing.get('plugins', {}).get(PSTACK_PLUGIN, {}), 'enabled': pstack}
    text = merge_tables(text, 'plugins', {PSTACK_PLUGIN: plugin, CE_PLUGIN: None})
    marketplace = {'source_type': 'git', 'source': f'https://github.com/{PSTACK_REPOSITORY}.git', 'ref': 'main'}
    return merge_tables(text, 'marketplaces', {PSTACK_MARKETPLACE: marketplace, CE_MARKETPLACE: None})


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


def supplement_hooks(checkout, codex=False):
    """The hooks that format and lint the bound supplement in an agent session there; they say
    nothing in any other directory (lib/shaffy/lint.py)."""
    command = f'"{checkout}/bin/shaffy" {SUPPLEMENT_HOOK}'

    def group(timeout, matcher=None):
        hooks = {'hooks': [{'type': 'command', 'command': command, 'timeout': timeout}]}
        return {'matcher': matcher, **hooks} if matcher else hooks

    edits = '^(apply_patch|Edit|Write)$' if codex else 'Edit|Write|MultiEdit'
    return {'SessionStart': group(60), 'PostToolUse': group(120, edits), 'Stop': group(300)}


def bound_hooks(data, codex=False):
    """The supplement's hooks, which exist only while a supplement is bound."""
    return supplement_hooks(REPO, codex) if data['selection']['mode'] == 'supplement' else {}


def merge_hooks(settings, hooks):
    """Replace this script's own hook groups, append them after every other one, which keeps
    the others' places (Codex trusts a hook by its place), and drop them when `hooks` is empty."""
    events = settings.get('hooks', {})
    for event in sorted(set(events) | set(hooks)):
        groups = [
            group
            for group in events.get(event, [])
            if not any(SUPPLEMENT_HOOK in hook.get('command', '') for hook in group.get('hooks', []))
        ]
        groups += [hooks[event]] if event in hooks else []
        if groups:
            events[event] = groups
        else:
            events.pop(event, None)
    if events:
        settings['hooks'] = events
    else:
        settings.pop('hooks', None)
    return settings


def merge_codex_hooks(text, hooks):
    result = json.loads(text) if text.strip() else {}
    original = copy.deepcopy(result)
    merge_hooks(result, hooks)
    if result == original:
        return text
    return json.dumps(result, indent=2, ensure_ascii=False) + '\n'


def claude_plugins(extra=None):
    """The Claude Code plugins to install, each with the GitHub repository of its marketplace:
    pstack and the supplement's `ai.claude_plugins`."""
    return {PSTACK_PLUGIN: PSTACK_REPOSITORY, **(extra or {})}


def merge_claude_settings(text, pstack, hooks=None, plugins=None):
    """`plugins` are the supplement's other Claude Code plugins, always enabled."""
    result = json.loads(text) if text.strip() else {}
    original = copy.deepcopy(result)
    declared = claude_plugins(plugins)
    result.setdefault('enabledPlugins', {}).update({**dict.fromkeys(declared, True), PSTACK_PLUGIN: pstack})
    result['enabledPlugins'].pop(CE_PLUGIN, None)
    marketplaces = result.setdefault('extraKnownMarketplaces', {})
    for plugin, repository in declared.items():
        marketplaces[plugin.partition('@')[2]] = {'source': {'source': 'github', 'repo': repository}}
    marketplaces.pop(CE_MARKETPLACE, None)
    merge_hooks(result, hooks or {})
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

        data = load_data(REPO)['shaffy']
        enabled = data['settings']['ai'].get('pstack', {}).get('enabled')
        servers = mcp_servers()
        if sys.argv[1] == 'claude-settings':
            plugins = data['settings']['ai'].get('claude_plugins')
            output = merge_claude_settings(text, enabled, bound_hooks(data), plugins)
        elif sys.argv[1] == 'codex-hooks':
            output = merge_codex_hooks(text, bound_hooks(data, codex=True))
        elif sys.argv[1] == 'codex':
            output = merge_codex(text, servers, enabled)
        elif sys.argv[1] == 'claude':
            output = merge_claude(text, servers)
        elif sys.argv[1] == 'claude-desktop':
            output = merge_claude(text, servers, typed=False)
        else:
            raise ValueError('expected claude, claude-desktop, claude-settings, codex or codex-hooks')
    except Exception as error:  # refuse to rewrite the file on invalid settings
        sys.exit(f'merge-ai-config: cannot read the Shaffy OS settings: {error}')
    sys.stdout.write(output)
