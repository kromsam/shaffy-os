# Compound Engineering and agent-browser

The public defaults enable the native Compound Engineering plugin in Claude Code
and Codex. Both hosts use `EveryInc/compound-engineering-plugin`, tracking its
`main` branch. Native registries and downloaded plugin code remain application
data; the home sources own activation and marketplace settings. A supplement may
set `ai.compound_engineering.enabled: false` for project opt-in.

`npm:agent-browser` is declared in the managed mise configuration. The managed
launcher runs that tool explicitly. The setup script installs only Claude Code,
Codex and agent-browser, then downloads the browser runtime and refreshes CE in
both hosts. It preserves existing GBrain configuration and unrelated plugins.

Apply this checkout through the wrapper before running the installer:

```sh
./bin/shaffy check
./bin/shaffy diff
./bin/shaffy apply --home --checkout "$PWD"
bash scripts/install-agent-tools.sh
./bin/shaffy apply --home --checkout "$PWD"
```

The second apply should report zero changed targets. The installation record is
`~/.local/state/shaffy/compound-engineering/installed.json`. Native plugin lists
report installed versions and activation; restart agent sessions to load new
skills. Refresh CE alone with `python3 scripts/install-compound-engineering.py`.
Browser profiles, credentials and downloaded binaries remain private application
data. Setup does not create or change project instructions.

See [Compound Engineering](https://github.com/EveryInc/compound-engineering-plugin),
[agent-browser](https://github.com/vercel-labs/agent-browser) and
[OpenAI's plugin documentation](https://developers.openai.com/plugins/build/plugins).
