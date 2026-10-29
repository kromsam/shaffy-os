# Workspace tools

The command-line tools for AI-assisted work and terminal workspaces. tmux,
zoxide, uv, the GitHub CLI (`gh`), Node.js and npm are packages in the
`desktop_apps` group. The other tools
are declared in chezmoi's `~/.config/mise/config.toml` (source
`home/dot_config/mise/config.toml`) or in `scripts/install-workspace-tools.sh`,
which installs them as the desktop user. Launchers in `~/.local/bin` run the
mise-managed binaries, so desktop services, tmux, Niri and agent hooks find them
before mise activates in a shell.

| Component | Source and version |
| --- | --- |
| Claude Code | mise `claude = "latest"`; the launcher turns off its own updater |
| Codex CLI | mise `codex = "latest"` |
| Proton Pass CLI (`pass-cli`) | mise `"aqua:protonpass/pass-cli" = "latest"`; needs a paid Pass plan |
| workmux | mise `"github:raine/workmux" = "latest"`, using the latest published prebuilt release |
| Bun | mise `bun = "latest"`, for JavaScript tooling |
| Ruby | mise `ruby = "latest"`, for LazyVim's Ruby language tools and formatters |
| pstack | Native Claude Code and Codex plugin from `michael-denyer/pstack-claude`, tracking `main` |
| Zotero MCP | mise `"pypi:zotero-mcp-server"` at `latest`, with the `semantic,pdf` extras, on the system Python and CPU-only PyTorch |
| Quarto | mise `"aqua:quarto-dev/quarto-cli" = "latest"` |
| tmux, zoxide, GitHub CLI | the `desktop_apps` package group (`github-cli`) |

Project-specific runtime choices belong in a project's `mise.toml` or the
supplement's mise fragment (`overrides.mise`), which renders as
`~/.config/mise/conf.d/90-supplement.toml` beside the public configuration.

## Install and update

```sh
./bin/shaffy apply --system --tags desktop_apps   # tmux, zoxide, uv, gh, Node.js and npm
./bin/shaffy apply --home                        # mise configuration, launchers, AI settings
bash scripts/install-workspace-tools.sh          # mise tools, Zotero MCP and pstack
./bin/shaffy status
```

The script refuses to run as root. It installs every tool
`home/dot_config/mise/config.toml` names, with the version and options of the
home's mise configuration, which the home apply renders from that file; all
public mise tools use `latest`.
`./bin/shaffy update` runs it again to install updates, after the packages
([Updating](UPDATING.md)); running it by hand does the same. Applying the home changes the
configuration and launchers, but does not download new tool versions. The
launchers use the versions installed through mise; Claude's launcher also sets
`DISABLE_AUTOUPDATER=1`.

`latest` means the newest release that is at least three days old
(`minimum_release_age` in the same file), for every mise tool, including the
Ansible controller. A compromised release is usually withdrawn within that
time. The public file wins over the supplement's mise fragment; to take a fix
that is younger once, run the script as
`MISE_MINIMUM_RELEASE_AGE=0 bash scripts/install-workspace-tools.sh`.

pstack refreshes from upstream `main` through both hosts' native
plugin managers; home apply never downloads it. Use this install path instead of
the tools' own update commands, so mise keeps ownership of its binaries and the
other tools retain their declared versions. Zotero MCP was a `uv tool` until
2026-10; the script removes that copy before mise installs the server.
<!-- Remove after 2027-01-29, with the agent-browser retirement in the script. -->
agent-browser was Compound Engineering's browser fallback until 2026-10; the
script uninstalls it and deletes the browsers it downloaded to `~/.agent-browser`.

The Proton Pass CLI comes from mise's aqua backend, which checks each release
against the checksum Proton publishes beside it.

## AI tools and MCP servers

Run `claude` or `codex` in a project. The home apply merges the MCP servers of
`ai.mcp_servers` into `~/.claude.json`, Claude Desktop's
`~/.config/Claude/claude_desktop_config.json` and `~/.codex/config.toml`, and
keeps every other setting in those files: Claude Desktop's preferences, its
extensions and the claude.ai connectors stay the app's own. Restart running
sessions, and Claude Desktop, to see a change.

Set `ai.mcp_servers.NAME.enabled: false` in the supplement to remove that named
server from every client on a home apply. Omitting its name leaves an existing
entry alone.

A server is a command the client starts (`command`, `args`, `env`) or the
address of a streamable HTTP server that runs on its own (`url`). Claude
Desktop's configuration holds commands only, so it leaves address servers out;
it reaches those through its own connectors and extensions.

The public defaults are two servers. Beeper's, at
`http://127.0.0.1:23373/v0/mcp`, answers while Beeper Desktop runs with its
Desktop API switched on; each client signs in to it the first time it is used
(`/mcp` in Claude Code, `codex mcp login beeper` in Codex). In Claude Desktop,
Beeper's own Beeper Desktop extension does the same.

Zotero MCP is a mise tool; each client starts it with
`mise exec pypi:zotero-mcp-server -- zotero-mcp serve`, and in a shell where mise
is active `zotero-mcp` is on the path. It reads the local library through
Zotero's local API with writes switched off (`ZOTERO_LOCAL=true`, `ZOTERO_LOCAL_WRITE=false`). The `zotero`
integration switches the local API on in Zotero's profile, and Zotero must be
running. Reading the library and
semantic search need no cloud API key. Semantic search needs an index built with
`zotero-mcp update-db`; nothing reindexes automatically, and write authorisation
is not granted. A supplement switches local writes on with
`ai.mcp_servers.zotero.env.ZOTERO_LOCAL_WRITE: "true"`.

To opt out, set `ai.mcp_servers.zotero.enabled: false` in the supplement and
run `./bin/shaffy apply --home`. This removes only the `zotero` MCP entry from
every client and is independent of the `zotero` home integration and the
`zotero` package selection. Set `enabled: true`, or remove the override,
to restore the public default. The same switch works for other named MCP
servers. Merely deleting a custom server from the supplement leaves an
existing client entry alone. An unfamiliar inline TOML entry is refused for
review rather than rewriting the whole Codex configuration.

## Proton Pass CLI

`pass-cli` reaches the vault of the [Proton Pass app](DESKTOP-APPLICATIONS.md#proton-pass)
from a terminal. `pass-cli login` prints an address to open in the browser,
where the password, second factor and security key are entered, so no
credential passes through a command line or its arguments. Its session keys
stay in the kernel keyring, outside both repositories. Proton offers the CLI on
Pass Plus, Family and Professional and on the Proton bundles, not on the free
plan; see [Proton Pass CLI documentation](https://protonpass.github.io/pass-cli/).

## Directory jumping in Fish

Omarchy makes `cd` fall back to zoxide: an existing path changes directory as
usual, and anything else is handed to zoxide, which ranks the directories
visited before by frequency and recency.
`home/dot_config/fish/conf.d/60-zoxide.fish` does the same in Fish, wrapping
Fish's own `cd` so `cd -` and the directory-history keys still work:

```sh
cd shaffy           # ~/Projects/shaffy-os, once visited before
cd supp             # a partial name is enough: ~/Projects/my-supplement
cd proj shaffy      # several words narrow the match, in path order
z / zi              # zoxide's own jump and interactive (fzf) picker
```

Matching is by substring, not by spelling correction: `cd shaffy` finds
`shaffy-os`, `cd sahffy` does not. A path that exists and is not in the
database still works, and an unknown name prints an error instead of guessing.
The wrapper is active only when `zoxide` is installed, so a shell without it
keeps the plain `cd`.

## fzf keys in Fish

`home/dot_config/fish/conf.d/65-fzf.fish` loads fzf's own Fish key bindings,
as Omarchy does in Bash: **Ctrl+R** searches the command history, **Ctrl+T**
inserts file paths below the current directory and **Alt+C** changes into one
of its directories. fzf comes with CachyOS's Fish configuration; a shell
without it keeps Fish's own keys.

## Git defaults

`~/.config/git/config` (source `home/dot_config/git/config.tmpl`) carries
Omarchy's Git defaults: `git pull` rebases local commits, the first `git push`
of a branch sets its upstream, diffs use the histogram algorithm and colour
moved lines, `rerere` remembers conflict resolutions, `git branch` lists the
most recently committed branches first and `git tag` the newest versions
first, and `git commit` shows the diff in the message editor. The name, e-mail
and SSH signing come from the supplement's `identity` settings. Git reads
`~/.gitconfig` and each repository's own configuration after this file, so
their entries win.

## Worktrees and tmux with workmux

[workmux](https://github.com/raine/workmux) gives each task its own Git worktree
and tmux window. Inside the window, `~/.config/workmux/config.yaml` applies
Omarchy's tdl geometry: the editor in the upper left 70%, the agent in the upper
right 30%, and a full-width Fish shell in the bottom 15%.

```sh
wm add fix-login              # worktree + window: nvim left, claude right, shell below
wm add fix-login -l codex     # Codex instead of Claude
wm add fix-login -l duo       # Claude above Codex, like Omarchy's tdl claude codex
wm add fix-login -p "…"       # start the agent with a prompt
wm list                       # worktrees, windows and agent status
wm dashboard                  # TUI overview of running agents
wm merge fix-login            # merge into the base branch, drop worktree, window and branch
wm remove fix-login           # drop without merging
```

`wm` is the Fish alias for `workmux`; completions load on first use from
`completions/workmux.fish`. Worktrees live beside the project in
`<project>__worktrees/`. Branch names become window names, prefixed with a Nerd
Font branch icon (`nerdfont: true` skips the first-run font question). A project
can replace the panes with its own `.workmux.yaml`; the global file sets only the
default layout, the `codex` and `duo` layouts and `agent: claude`. Omarchy's
`tds` layout (it needs `hunk` and `opencode`) and its swarm layout are not
ported; `wm add NAME -a claude -a codex` creates one worktree per agent instead.

mise's GitHub backend downloads workmux's latest published release binary for
the machine's platform, so workmux needs no Rust toolchain to install.
`~/.local/bin/workmux` runs the mise binary for tmux, Niri and agent hooks.

Start tmux first: **Super+Alt+Return** attaches to or creates the `Work` session;
**Super+Return** still opens plain Alacritty. On the first `wm add`, workmux
offers to install its agent status hooks and skills, and `workmux setup` offers
the same later. The hooks show whether an agent is working, waiting or done in
the window name. Claude's hooks go to `~/.claude/settings.json`, Codex's to
`~/.codex/hooks.json` with `hooks = true` in its configuration. Both are private
files; the home apply's Codex merge keeps settings it does not own.

Earlier versions deployed Zellij layouts (`tdl`, `tdlm` and `tsl`);
`home/.chezmoiremove` deletes their configuration, scripts and generated layouts.

### Keys

`home/dot_config/tmux/tmux.conf` is Omarchy's tmux configuration; only the `?`
popup differs, listing tmux's own bindings instead of the Omarchy menu. Colours
are the terminal's named colours, so Noctalia's Alacritty palette applies without
a template. The status bar sits at the top. Keys without the prefix:

| Key | Action |
| --- | --- |
| Alt+Enter / Alt+Shift+Enter | Split below / beside |
| Alt+Escape | Close pane |
| Ctrl+Alt+arrows | Focus a pane |
| Ctrl+Alt+Shift+arrows | Resize a pane |
| Alt+1 to 9 | Go to window |
| Alt+Left / Right | Previous / next window |
| Alt+Shift+Left / Right | Move window left / right |
| Alt+Up / Down | Previous / next session |

Press **Ctrl+Space** (or Ctrl+B), then:

| Key | Action |
| --- | --- |
| h / v | Split below / beside |
| x | Close current pane |
| z | Zoom or unzoom the current pane |
| c / k / r | New / close / rename window |
| C / K / R | New / kill / rename session |
| N / P | Next / previous session |
| d | Detach, keeping the session |
| [ | Copy mode with vi keys: `v` selects, `y` copies |
| q | Reload the configuration |
| ? | Popup listing every binding |

Inside tmux, Alt+Left/Right and Alt+Up/Down shadow Fish's directory-history and
token-search bindings, as in Omarchy.

`./bin/shaffy check` renders the home in a sandbox; when tmux is installed it
sources the rendered `tmux.conf` on a private tmux server
(`scripts/validate-tmux-config.py`), and it parses every YAML file, including
workmux's configuration. The home apply keeps a copy of every file it changes
under `~/.local/state/shaffy/runs/`.

## pstack

[pstack](https://github.com/cursor/plugins/tree/main/pstack), Lauren Tan's skill
stack, is the default workflow plugin for Claude Code and Codex, through Michael
Denyer's [port](https://github.com/michael-denyer/pstack-claude) for both hosts.
Public settings declare `ai.pstack.enabled: true`; a supplement may set it to
false. This is global activation: no pstack files are added to repositories.
With false, repositories start with pstack off unless native project settings
explicitly enable it. Linked worktrees follow the host-specific settings rules
described below.
The home apply merges the plugin and marketplace entries into Claude's
`~/.claude/settings.json` and Codex's `~/.codex/config.toml`, preserving other
plugins, hooks, permissions and MCP servers. Their native registries and caches
remain application data.

After home apply, the workspace installer materializes or refreshes the declared
plugin in both hosts. `scripts/install-pstack.py` can refresh only pstack; it
refuses until the declared settings have been applied. The install record at
`~/.local/state/shaffy/pstack/installed.json` captures the hosts' reported
versions for diagnosis. Upstream `main` advances on explicit refresh; there is
no private fork or exported duplicate skill tree. Restart sessions to load the
updated plugin.

The plugin's SessionStart hook tells each session to route work that spans
files, involves a design choice or has an unknown cause through
`pstack:poteto-mode`. A project's `CLAUDE.md` or `AGENTS.md` takes precedence
over pstack's own rules, so a repository's contract still governs applies,
commits, shipping and deployment. Codex runs the hook only once it is trusted
through `/hooks`; trust it after the first install and whenever Codex asks
again.

Without a model sheet, pstack uses its own defaults, a panel of Claude models in
Claude Code. `setup-pstack` writes model choices and a `session hook` line
(`off` stops the routing) to `~/.claude/pstack-models.md` and an `@` line that
includes it in `~/.claude/CLAUDE.md` (Codex: `~/.codex/pstack-models.md` and
`~/.codex/AGENTS.md`). Those choices are personal: declare the files as private
supplement assets instead of letting the skill write them.

For opt-in, set `ai.pstack.enabled: false` in the private supplement. pstack
remains installed. Native installers enable it globally, so the refresh helper
reapplies the declared policy through `./bin/shaffy apply --home`, including when
an installation command fails. Close agent sessions before refreshing.

Enable pstack for one local checkout with these native settings. Declare personal
files as private supplement assets, and ignore them through a supplement-owned
`~/.config/git/ignore` or the repository's local Git excludes. Keep any existing
settings and permission rules when bringing a file under management.

```json
{
  "enabledPlugins": {
    "pstack@pstack-claude": true
  }
}
```

The JSON goes in `.claude/settings.local.json`. Codex reads this TOML from
`.codex/config.toml`:

```toml
[plugins."pstack@pstack-claude"]
enabled = true
```

Codex only reads project configuration in trusted projects. Do not grant trust
automatically to activate pstack. Claude Code uses the main checkout's local
settings for linked worktrees on supported versions; Codex needs its project
configuration in each opted-in worktree. A tracked `.claude/settings.json` can
also explicitly enable pstack. See
[Claude settings](https://code.claude.com/docs/en/settings) and
[Codex project plugins](https://developers.openai.com/plugins/build/plugins).

<!-- Remove after 2027-01-29, with retire_compound_engineering in scripts/install-pstack.py. -->
pstack replaced Compound Engineering in 2026-10. The home apply drops CE's plugin
and marketplace entries, and the workspace installer uninstalls the user's CE
from both hosts and deletes its install record. A project that installs CE in
its own `.claude/settings.json` keeps it, and Claude keeps CE's marketplace for
it. Lessons CE wrote under a project's `docs/solutions/` stay with the project.
A supplement that set `ai.compound_engineering.enabled` renames it to
`ai.pstack.enabled`: the apply refuses the old key, and deleting it instead turns
pstack on.

<!-- Remove after 2027-01-16, with the gbrain marker in defaults/config.yaml. -->
The GBrain integration is retired. Its MCP entry is explicitly disabled by the
public default, and home apply removes its old launchers, proxy and service.
Private databases and memory are preserved. Follow
[the retirement runbook](MIGRATING-AGENT-TOOLS.md) before applying this change to
an installation that previously managed GBrain.
