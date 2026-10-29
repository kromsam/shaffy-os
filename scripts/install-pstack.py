#!/usr/bin/python3
"""Materialize the pstack plugin declared by the home sources in both native hosts."""

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tomllib

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))
MERGE = runpy.run_path(str(REPO / 'scripts/merge-ai-config.py'))
MARKETPLACE = MERGE['PSTACK_MARKETPLACE']
PLUGIN = MERGE['PSTACK_PLUGIN']


def declared(home, enabled):
    """Refuse installation until source-owned settings have been applied."""
    for path, merge, parse in (
        (home / '.claude/settings.json', lambda text: MERGE['merge_claude_settings'](text, enabled), json.loads),
        (home / '.codex/config.toml', lambda text: MERGE['merge_codex'](text, {}, enabled), tomllib.loads),
    ):
        text = path.read_text() if path.exists() else ''
        existing = parse(text) if text.strip() else {}
        if existing != parse(merge(text)):
            raise ValueError('Apply the declared pstack settings with ./bin/shaffy apply --home first.')


def commands():
    return [
        ['claude', 'plugin', 'marketplace', 'update', MARKETPLACE],
        ['claude', 'plugin', 'install', PLUGIN, '--scope', 'user'],
        ['claude', 'plugin', 'update', PLUGIN],
        ['codex', 'plugin', 'marketplace', 'upgrade', MARKETPLACE],
        ['codex', 'plugin', 'add', PLUGIN],
    ]


def retire_compound_engineering(home, env):
    """Uninstall the user's Compound Engineering, which pstack replaced. A project's own CE
    install keeps its plugin and the Claude marketplace it comes from. Remove after 2027-01-29."""
    plugin, marketplace = MERGE['CE_PLUGIN'], MERGE['CE_MARKETPLACE']
    registry = home / '.claude/plugins/installed_plugins.json'
    installs = json.loads(registry.read_text()).get('plugins', {}).get(plugin, []) if registry.exists() else []
    if any(install.get('scope') == 'user' for install in installs):
        subprocess.run(
            ['mise', 'exec', 'claude', '--', 'claude', 'plugin', 'uninstall', plugin, '--scope', 'user'],
            check=True,
            env=env,
        )
    # Codex removes a plugin that is already gone without complaint, but not a marketplace.
    # Its list omits a marketplace whose table the home apply dropped; the downloaded copy stays.
    subprocess.run(['mise', 'exec', 'codex', '--', 'codex', 'plugin', 'remove', plugin], check=True, env=env)
    if (home / '.codex/.tmp/marketplaces' / marketplace).exists():
        subprocess.run(
            ['mise', 'exec', 'codex', '--', 'codex', 'plugin', 'marketplace', 'remove', marketplace],
            check=True,
            env=env,
        )
    shutil.rmtree(home / '.local/state/shaffy/compound-engineering', ignore_errors=True)


def settings_snapshot(home):
    return {
        'claude': json.loads((home / '.claude/settings.json').read_text()),
        'codex': tomllib.loads((home / '.codex/config.toml').read_text()),
    }


def reconcile_settings(home, enabled, original_settings):
    """Native installs enable the plugin; restore opt-in through the source apply."""
    current = settings_snapshot(home)
    if not enabled and (
        current['claude'].get('enabledPlugins', {}).get(PLUGIN) is not False
        or current['codex'].get('plugins', {}).get(PLUGIN, {}).get('enabled') is not False
    ):
        subprocess.run([str(REPO / 'bin/shaffy'), 'apply', '--home', '--checkout', str(REPO)], check=True, cwd=REPO)
    declared(home, enabled)
    if settings_snapshot(home) != original_settings:
        raise ValueError(
            'Native plugin refresh changed unrelated settings; review the source-owned diff before continuing.'
        )


def refresh(home, enabled, original_settings, env):
    registry = home / '.claude/plugins/known_marketplaces.json'
    known = json.loads(registry.read_text()) if registry.exists() else {}
    try:
        if MARKETPLACE not in known:
            subprocess.run(
                ['mise', 'exec', 'claude', '--', 'claude', 'plugin', 'marketplace', 'add', MERGE['PSTACK_REPOSITORY']],
                check=True,
                env=env,
            )
        for command in commands():
            subprocess.run(['mise', 'exec', command[0], '--', *command], check=True, env=env)
        # After the upgrade: Codex refuses plugin commands while a declared marketplace is
        # not yet downloaded.
        retire_compound_engineering(home, env)
    finally:
        # Restore source-owned activation even when a later native command fails.
        reconcile_settings(home, enabled, original_settings)


def main():
    from shaffy.loader import load

    enabled = load(REPO)['shaffy']['settings']['ai']['pstack']['enabled']
    home = Path.home()
    if os.getuid() == 0:
        raise ValueError('Run as the desktop user, without sudo.')
    declared(home, enabled)
    original_settings = settings_snapshot(home)
    env = {**os.environ, 'DISABLE_AUTOUPDATER': '1'}
    refresh(home, enabled, original_settings, env)
    versions = {}
    registry = home / '.claude/plugins/installed_plugins.json'
    if registry.exists():
        versions['claude'] = json.loads(registry.read_text()).get('plugins', {}).get(PLUGIN, [])
    result = subprocess.run(
        ['mise', 'exec', 'codex', '--', 'codex', 'plugin', 'list', '--json'],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    versions['codex'] = json.loads(result.stdout)
    target = home / '.local/state/shaffy/pstack'
    target.mkdir(parents=True, exist_ok=True)
    path = target / 'installed.json'
    path.write_text(json.dumps(versions, indent=2) + '\n')
    path.chmod(0o600)
    print(
        'pstack refreshed in both hosts; global activation is '
        + ('on' if enabled else 'off (project opt-in)')
        + '. Restart agent sessions, and trust its hook through /hooks in Codex.'
    )


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'install-pstack: {error}')
