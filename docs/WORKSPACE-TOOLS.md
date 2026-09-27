# Workspace tools

The command-line tools for AI-assisted work and terminal workspaces. tmux,
zoxide, uv, Node.js and npm are packages in the `desktop_apps` group. The other tools
are declared in chezmoi's `~/.config/mise/config.toml` (source
`home/dot_config/mise/config.toml`) or in `scripts/install-workspace-tools.sh`,
which installs them as the desktop user. Launchers in `~/.local/bin` run the
mise-managed binaries, so desktop services, tmux, Niri and agent hooks find them
before mise activates in a shell.

| Component | Source and version |
| --- | --- |
| Claude Code | mise `claude = "latest"`; the launcher turns off its own updater |
| Codex CLI | mise `codex = "latest"` |
| Bitwarden CLI (`bw`) | mise `bitwarden = "latest"` |
| workmux | mise `"github:raine/workmux" = "latest"`, using the latest published prebuilt release |
| Bun | mise `bun = "latest"`, the runtime of GBrain |
| Ruby | mise `ruby = "latest"`, for LazyVim's Ruby language tools and formatters |
| GBrain | `garrytan/gbrain` at revision `d13aa742fd68b71bfd6c98be3dda5813791f1d6c`, in `~/.local/share/gbrain/source` |
| Zotero MCP | `zotero-mcp-server[semantic,pdf]` 0.12.4 through uv, with CPU-only PyTorch |
| tmux, zoxide | the `desktop_apps` package group |
| Sticky Notes | Noctalia's community plugin `ahmedhossamdev/sticky-notes`, updated by Noctalia |

Project-specific runtime choices belong in a project's `mise.toml` or the
supplement's mise fragment (`overrides.mise`), which renders as
`~/.config/mise/conf.d/90-supplement.toml` beside the public configuration.

## Install and update

```sh
./bin/shaffy apply --system --tags desktop_apps   # tmux, zoxide, uv, Node.js and npm
./bin/shaffy apply --home                        # mise configuration, launchers, AI settings
bash scripts/install-workspace-tools.sh          # mise tools, Zotero MCP, GBrain's source
./bin/shaffy status
```

The script refuses to run as root. It reads each mise tool's version selector
from `home/dot_config/mise/config.toml`; all public mise tools use `latest`.
Run the script again to install updates. Applying the home changes the
configuration and launchers, but does not download new tool versions. The
launchers use the versions installed through mise; Claude's launcher also sets
`DISABLE_AUTOUPDATER=1`.

The GBrain revision and Zotero MCP version remain pinned in the script itself;
change those pins before rerunning it to update either tool. Use this install
path instead of the tools' own update commands, so mise keeps ownership of its
binaries and the other tools retain their declared versions. Quarto also uses
`latest` in the mise configuration and is installed or updated by
`scripts/install-publishing-tools.sh`; that script retains qpdf's separate pin.

The Bitwarden CLI comes from mise because the distribution's `bitwarden-cli`
package depends on Node.js 22 (`nodejs-lts-jod`), which would replace the
`nodejs` package the `desktop_apps` group installs.

## AI tools and MCP servers

Run `claude` or `codex` in a project. The home apply merges the MCP servers of
`ai.mcp_servers` into `~/.claude.json` and `~/.codex/config.toml` and keeps
every other setting in both files; restart running sessions to see a change.

The public default is one server, Zotero MCP, reading the local library through
Zotero's local API with writes switched off (`ZOTERO_LOCAL=true`,
`ZOTERO_LOCAL_WRITE=false`). The `zotero` integration switches the local API on
in Zotero's profile, and Zotero must be running. Reading the library and
semantic search need no cloud API key. Semantic search needs an index built with
`zotero-mcp update-db`; nothing reindexes automatically, and write authorisation
is not granted. A supplement switches local writes on with
`ai.mcp_servers.zotero.env.ZOTERO_LOCAL_WRITE: "true"`.

To opt out, set `ai.mcp_servers.zotero.enabled: false` in the supplement and
run `./bin/shaffy apply --home`. This removes only the `zotero` MCP entry from
both clients and is independent of the `zotero` home integration and the
`zotero-bin` package selection. Set `enabled: true`, or remove the override,
to restore the public default. The same switch works for other named MCP
servers. Merely deleting a custom server from the supplement leaves an
existing client entry alone. An unfamiliar inline TOML entry is refused for
review rather than rewriting the whole Codex configuration.

## Bitwarden

Noctalia's Bitwarden plugin is disabled until upstream can sign in without
putting the API secret on a command line; the plugin's intended settings stay as
comments in `95-desktop-apps.toml`. The `bw` command line and the sign-in
helper work without it.

`bitwarden-login` signs in from a private terminal: credentials are typed only
there and never appear in command arguments. With `bitwarden.server_url` set it
first points `bw` at that server; without it `bw` keeps its own default server.
After signing in it locks the vault again.

```yaml
bitwarden:
  server_url: "https://vault.example.com"
```

Use the vault with `bw unlock` and `bw get` in a terminal; see
[Bitwarden's CLI documentation](https://bitwarden.com/help/cli/). The CLI's
sign-in state and vault cache are private and stay outside both repositories.

## Sticky Notes

The Sticky Notes plugin keeps plain Markdown notes, without colours, in
`sticky-notes` inside the installation's XDG documents directory, so they travel
with whatever synchronises that folder. An explicit `folders.documents`
override takes precedence. It is not on the bar; search **Sticky Notes** in the
launcher.

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

## GBrain

The `gbrain` integration serves a local [GBrain](https://github.com/garrytan/gbrain)
knowledge base to the AI tools as a read-only MCP server. It installs the
launchers `~/.local/bin/gbrain` and `~/.local/bin/gbrain-mcp`, the proxy
`~/.local/lib/shaffy/gbrain-mcp-proxy.py` and the user unit `gbrain-mcp.service`.
The brain itself, in `~/.gbrain`, is GBrain's own data: Shaffy OS never creates,
migrates or overwrites it. The supplement requests the integration, names the
one source the AI tools may read and registers the server:

```yaml
integrations:
  gbrain: managed
ai:
  gbrain:
    source: my-notes
  mcp_servers:
    gbrain:
      command: "~/.local/bin/gbrain-mcp"
```

Then install, apply and scope it, as the desktop user:

```sh
bash scripts/install-workspace-tools.sh   # latest Bun and GBrain's pinned source revision
./bin/shaffy apply --home                 # launchers, proxy, unit and the MCP entry
bash scripts/configure-gbrain-mcp.sh      # a read-only client for ai.gbrain.source
```

The install script checks out the pinned revision and installs its dependencies
from the frozen lockfile without running package scripts, since upstream's
postinstall can migrate a database or start setup work. Schema upgrades are a
deliberate GBrain command, never a side effect of installing.

`scripts/configure-gbrain-mcp.sh` stops the service, sets GBrain's MCP search to
keyword-only and turns ambient writeback off. `scripts/prepare-gbrain-mcp.py`
then registers the client `shaffy-ai-readonly` with read scope for
`ai.gbrain.source` only. Before that first registration it copies `~/.gbrain` to
`~/.local/state/shaffy/runs/TIME-gbrain-before-mcp`. The client's credentials go
to `~/.config/gbrain-mcp/client.json` (mode 0600) and are never printed; an
existing client is kept. The script finishes with a home apply and starts the
service.

At run time the unit serves GBrain on 127.0.0.1:3131, without any model API keys
in its environment, and starts only while `~/.config/gbrain-mcp/client.json`
exists. It is not enabled at login: the proxy starts it on demand, so one
process owns the PGLite database for every AI client. The proxy runs with the
Zotero MCP tool environment's Python, authenticates with the client credentials
and exposes only eleven read tools. No embeddings, paid model calls or background
automation are configured, so GBrain's own doctor reports those capabilities as
missing. Do not initialise a new brain or overwrite the existing one to silence
those reports.

Close the AI clients and stop the service before maintaining the database with
the GBrain command line:

```sh
systemctl --user stop gbrain-mcp.service
```

`scripts/verify-gbrain-mcp.py` runs two MCP clients at once through the proxy and
checks listing and keyword search, and that writes and other sources are denied.
It prints only counts and results, never brain contents:

```sh
~/.local/share/uv/tools/zotero-mcp-server/bin/python scripts/verify-gbrain-mcp.py
```

### Recovery

Setting the integration to `disabled` stops managing its files; since it
enabled nothing, nothing is switched off, and the brain stays as it is. To
withdraw the AI tools' access, stop the service and delete
`~/.config/gbrain-mcp/client.json`: the unit no longer starts and the proxy has
no credentials. Set `ai.mcp_servers.gbrain.enabled: false` and run a home
apply to remove its entry from both AI clients as well. Taking `gbrain` out
of `ai.mcp_servers` alone leaves the existing entries in those files.

The copy made before the first registration is the brain as it was then. Restore
it only with the service stopped, after keeping any later changes.
