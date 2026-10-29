# Desktop applications

The system apply installs the applications below from package groups in
`defaults/packages/`, one group per role, in the configured CachyOS and Arch
repository priority and without refreshing the package databases. The
Microsoft core fonts come from the AUR ([AUR packages](#aur-packages)).
SilverBullet Desktop and Beeper Desktop are installed per user from their own
release channels ([SilverBullet](#silverbullet), [Beeper](#beeper)).
Application profiles, accounts and credentials are the applications' own and
stay outside both the repository and the supplement.

A supplement changes the selection with `packages`: `omit` leaves group
packages out without uninstalling them, `extra` installs more and `remove`
removes listed packages (see `defaults/packages/README.md`). These rules cover
both repository and AUR packages. For example, `omit: [ttf-ms-fonts]` leaves
the Microsoft fonts out; adding `remove: [ttf-ms-fonts]` also uninstalls them on
the next system apply. Application profiles remain untouched.

## Applications

| Application | Package | Configuration |
| --- | --- | --- |
| Zen Browser | `zen-browser-bin` | The default browser unless `desktop.browser` names another; Niri's browser shortcuts start Zen. |
| Chromium | `chromium` | A second browser for sites that need it. |
| Spotify | `spotify-launcher` | Super+Shift+M launches Spotify or focuses its existing window; the launcher downloads and updates the official client. |
| Proton Pass | `proton-pass` | The desktop password manager; Niri blocks its window out of screen capture. See [Proton Pass](#proton-pass). |
| Claude Desktop | `claude-desktop` | The home apply merges the MCP servers of `ai.mcp_servers` into its configuration, as for Claude Code; its preferences, extensions and account are its own. See [AI tools and MCP servers](WORKSPACE-TOOLS.md#ai-tools-and-mcp-servers). |
| ChatGPT | `chatgpt-desktop-bin` | The official ChatGPT app, which includes the Codex desktop app; Office documents keep opening in LibreOffice (see [Default browser and document types](#default-browser-and-document-types)). It conflicts with `chatgpt-bin` from OpenAI's install script: remove that package first, which also drops OpenAI's pacman repository. |
| Beeper | upstream AppImage | Chats from several networks in one app; see [Beeper](#beeper). |
| SilverBullet Desktop | upstream AppImage | Markdown notes in local spaces that can sync with a SilverBullet Server; see [SilverBullet](#silverbullet). |
| Proton VPN | `proton-vpn-gtk-app` | Account and server selection stay in its own window. |
| Tailscale | `tailscale` | The `tailnet` role, only with `tailnet` settings; see [Tailnet](#tailnet). |
| Remmina, FreeRDP | `remmina`, `freerdp` | Noctalia's Remmina plugin lists saved connections; profiles stay private. |
| Neovim with LazyVim | `neovim`, `lazygit`, `tree-sitter-cli` | chezmoi's `~/.config/nvim`; see [LazyVim](#lazyvim). |
| mpv | `mpv` | Plays video and audio; Niri keeps its window opaque, as Omarchy does. |
| Document Scanner | `simple-scan`, `sane-airscan` | Scans from network scanners without a vendor driver; see [Scanning](#scanning). |
| Microsoft core fonts | AUR `ttf-ms-fonts` | Arial, Times New Roman and the other core fonts. |
| tmux, zoxide, uv, GitHub CLI | `tmux`, `zoxide`, `uv`, `github-cli` | See [workspace tools](WORKSPACE-TOOLS.md). |

The `desktop_apps` group also holds `nodejs`, `npm` and `yay`, the AUR helper that
updates the AUR packages. `tailscale` is the group's `required` package: the
settings loader refuses a supplement that omits it. The writing applications are
in [Writing tools](#writing-tools).

## Reapply

```sh
./bin/shaffy check
./bin/shaffy apply --system --tags desktop_apps
./bin/shaffy apply --home
./bin/shaffy status
```

The system apply asks for the sudo password in the terminal. The `desktop_apps`
role installs the group, then includes the `aur_package` role for each AUR
package that is missing; see [AUR packages](#aur-packages).

## Spotify

Spotify uses the repository package `spotify-launcher`, available in CachyOS
and Arch's Extra repository. On first launch it downloads the official Spotify
client into the user's home; later launches check for client updates. Those
binaries, the profile and the account remain application data outside both
repositories. `packages.omit: [spotify-launcher]` leaves it out of the default
application set.

**Super+Shift+M** launches the client or focuses an existing Spotify window.
The application launcher also lists Spotify through the package's desktop entry.
No Spotify theme or extra launch flags are configured.

Omarchy's [Spotify installer](https://github.com/omacom/omarchy/blob/quattro/bin/omarchy-install-service-spotify)
installs its own prebuilt `spotify` package and opens it. Its
[launcher](https://github.com/omacom/omarchy/blob/quattro/bin/omarchy-launch-spotify)
focuses an existing window or offers installation when the package is missing.
Shaffy OS declares its package for the system apply instead; the shortcut only
launches or focuses. See the [spotify-launcher documentation](https://github.com/kpcyrd/spotify-launcher)
for how the official client is downloaded and updated.

## Proton Pass

The Proton Pass desktop app, the CachyOS repository package `proton-pass`, is
the password manager; search **Proton Pass** in the launcher. Niri blocks its
window (app id `Proton Pass`) out of screen shares, recordings and screenshots.
Its account, settings and vault cache are the app's own and stay outside both
repositories. `packages.omit: [proton-pass]` leaves it out. The browser
extension comes from the browser's own add-on store and signs in separately.
The vault's command line is in [workspace tools](WORKSPACE-TOOLS.md#proton-pass-cli).
[Migrating to Proton Pass and
SilverBullet](MIGRATING-PASSWORDS-AND-NOTES.md) moves a Bitwarden vault.

## SilverBullet

SilverBullet Desktop keeps notes as Markdown pages: each space is a folder on
the computer, and a space can sync both ways with a SilverBullet Server, which
a phone or another computer then reaches in a browser. Upstream publishes it as
an AppImage outside the repositories and the AUR, so it is installed per user,
without sudo:

```sh
python3 scripts/install-silverbullet-desktop.py
```

The script downloads the AppImage from upstream's stable channel, checks its
size and SHA-512 against the channel's manifest, the file the app's own updater
reads, and installs it with its icons as
`~/.local/lib/silverbullet-desktop/SilverBullet.AppImage`. An installed copy is
left alone: the app downloads new versions in the background and installs one
when **Restart to Update** is chosen in its dashboard. The home apply installs
the launcher `~/.local/share/applications/silverbullet.desktop`; search
**SilverBullet**. Unlike the AppImage's own entry, it does not register
SilverBullet for Markdown files, which keep opening where they did. Without
the installer the launcher does nothing; deleting the AppImage's folder
uninstalls the app.

Spaces, their sync settings and the server account are the app's own and stay
outside both repositories. Create a space from a folder with **New space**,
connect one to a server space with **Join Space**, or add sync later under the
space's gear icon. SilverBullet Desktop is free for personal use and education;
use at work needs a Pro licence. [Migrating to Proton Pass and
SilverBullet](MIGRATING-PASSWORDS-AND-NOTES.md) converts a Logseq graph.

## Beeper

Beeper Desktop brings chats from several networks into one app. Beeper
publishes it for Linux as an AppImage, so it is installed per user, without
sudo:

```sh
python3 scripts/install-beeper-desktop.py
```

The script downloads the AppImage from Beeper's stable channel, checks its size
and SHA-512 against the channel's update feed, the file the app's own updater
reads, and installs it with its icon as `~/.local/lib/beeper/Beeper.AppImage`.
An installed copy is left alone: the app updates itself. The home apply creates
the launcher `~/.local/share/applications/Beeper.desktop` once; Beeper rewrites
that file each time it starts, registers itself for `beeper:` links and keeps
Chromium's sandbox. Without the installer the launcher stays hidden; deleting
the AppImage's folder uninstalls the app.

The account and profile in `~/.config/BeeperTexts` are Beeper's own; an
installation that had the earlier AUR package `beeper-v4-bin` keeps using that
profile. Beeper's MCP server is a public default; see [AI tools and MCP
servers](WORKSPACE-TOOLS.md#ai-tools-and-mcp-servers).

## Scanning

GNOME's Document Scanner (`simple-scan`) scans through SANE. The `sane-airscan`
backend talks to network scanners that support eSCL (AirScan, sold as AirPrint
or Mopria scanning) or WSD, which covers most current multifunction printers,
and finds them on the local network by itself. Neither a vendor driver nor a
device setting is needed: a scanner on the same network appears in the
Document Scanner's device list by its model name. `avahi-browse -rt _uscan._tcp`
shows which eSCL scanners the network announces, and `scanimage -L` lists the
devices SANE can use.

## Default browser and document types

The `browser` role runs two scripts as the target user:

- `scripts/configure-web-browser.sh` makes the desktop entry in
  `desktop.browser` (default `zen.desktop`) the default for `http`, `https`,
  `text/html` and `application/xhtml+xml`. It refuses an entry that is not
  installed under `/usr/share/applications/`.
- `scripts/configure-office-apps.sh` keeps Word, Excel and PowerPoint documents,
  including their macro and template variants, and CSV and TSV opening in
  LibreOffice Writer, Calc and Impress. The ChatGPT desktop entry registers
  itself for these types and would otherwise win.

Both leave the ChatGPT callback handler and every other association alone.
Without an argument they only check and return 1 when something needs applying;
`--apply` writes. Reapply with `./bin/shaffy apply --system --tags browser`.

`desktop.browser` sets the default for web links and HTML only. Niri's browser
shortcuts (Super+B, Super+Shift+B, Super+Shift+Return, and Super+Shift+Alt+B for
a private window) start Zen; a supplement that prefers another browser rebinds
them in its Niri fragment.

## LazyVim

`~/.config/nvim` is the LazyVim starter with the Neo-tree, Ruby, Docker, JSON,
Markdown, TOML, YAML and Tailwind extras. It adds Harper with British English
for Markdown and Git commit messages, markdownlint settings, the remote
clipboard, absolute line numbers and no animated scrolling. `lazy-lock.json`
pins the plugins. Startup captures those versions before LazyVim's staged
plugin discovery, so a first installation keeps the managed lock unchanged.
The colours follow Noctalia's palette through a pinned
base16 adapter, with its Rosé Pine Dawn before the first palette exists; see
[theme and controls](NOCTALIA-INTEGRATION.md). Super+Shift+E opens LazyVim in
Alacritty.

The language tools install through Mason after the system packages and
`scripts/install-workspace-tools.sh` have supplied npm and Ruby:

```sh
mise exec -- nvim --headless -l scripts/bootstrap-lazyvim.lua
```

It installs the 18 tools the script lists, writes the result to
`~/.local/state/nvim/desktop-tools.json` and exits non-zero when one fails.
Plugins and Mason's tools are runtime data; only the configuration and the plugin
lock are in the repository.

## LibreOffice layout

The `writing` role selects LibreOffice's **Tabbed** interface for Writer, Calc,
Impress and Draw, the full notebook bar described in
[LibreOffice's interface documentation](https://help.libreoffice.org/latest/en-GB/text/shared/01/notebook_bar.html).
To check or apply it by hand, close LibreOffice and run as the desktop user,
without sudo:

```sh
python3 scripts/configure-libreoffice.py --check
python3 scripts/configure-libreoffice.py --apply
```

The check is the default, changes nothing and returns 1 when the preference needs
applying. `--apply` changes only the eight layout properties in
`~/.config/libreoffice/4/user/registrymodifications.xcu` and keeps every other
setting. LibreOffice writes the per-application entries in a longer path form
when it saves its profile; the script recognises both forms. It refuses while
LibreOffice is running or its profile is locked, and refuses a linked registry
file. Before a change it keeps a copy under
`~/.local/state/shaffy/runs/libreoffice/`; an already configured profile is not
written and gains no copy.

To switch layouts by hand, use **View → User Interface**, or **☰ → User
Interface** from the Tabbed layout, and choose **Standard Toolbar** for the
classic layout. The next system apply that runs the `writing` role selects
Tabbed again.

## Writing tools

The `writing` group installs Zettlr, Pandoc, qpdf, TeX Live, LibreOffice, Vale,
Harper, English spelling and hyphenation dictionaries and the Fira fonts; a
supplement adds other languages' packages with `packages.extra`, for example
`libreoffice-fresh-nl`, `hunspell-nl`, `hyphen-nl` and `texlive-langeuropean`
(Dutch babel and hyphenation for the PDF export). Zotero is the CachyOS
repository's `zotero` package. Reapply with
`./bin/shaffy apply --system --tags writing`.

The `zettlr` integration renders Zettlr's DOCX and PDF (LuaLaTeX) export
profiles. They use the Pandoc filters, LaTeX header and citation style that the
home apply installs under `~/.local/share/pandoc/`: a Chicago notes and
bibliography style whose entries leave out URLs and DOIs. Two settings fill in
the profiles:

```yaml
writing:
  language: en-GB                        # lang of every export: hyphenation and quotes
  docx_reference: "~/Templates/reference.docx"   # styles for DOCX exports
```

Without `writing.language` the exports set no language, and without
`writing.docx_reference` DOCX exports use Pandoc's default styles. The
`zotero` integration and the Zotero MCP server are described in
[maintenance](MAINTENANCE.md#integrations) and
[workspace tools](WORKSPACE-TOOLS.md).

## Noctalia plugins

`~/.config/noctalia/95-desktop-apps.toml` enables the Tailnet, Syncthing,
Phone Connect and Remmina plugins, with the screen recorder, timer and world
clock. They come from Noctalia's
[official](https://github.com/noctalia-dev/official-plugins) and
[community](https://github.com/noctalia-dev/community-plugins) sources with
`auto_update = "all"`, so Noctalia installs and updates them. The fragment only
enables them and declares their settings; this repository ships no plugin code.
The home apply reloads a running Noctalia.

- Tailnet's panel links to `tailnet.login_server` as its admin page and saves
  Taildrop files in the downloads folder.
- Syncthing reads the user's local Syncthing configuration for its API key, which
  never enters the source, and posts no event notifications.
- The Proton Pass command line is in [workspace tools](WORKSPACE-TOOLS.md),
  and Phone Connect in [KDE Connect](KDE-CONNECT.md).
- Remmina lists the profiles in `~/.local/share/remmina`.

Only the screen recorder and the timer sit on the bar, and the world clocks open
from the clock. [Desktop setup](DESKTOP-SETUP.md#plugins-in-the-launcher) lists
the others' launcher entries and prefixes.

## Tailnet

`tailscale` is always installed; the `tailnet` role configures it only when the
supplement names a Tailnet (Tailscale or a Headscale server):

```yaml
tailnet:
  login_server: "https://headscale.example.com"
  address: "192.0.2.10"         # optional: the address the Tailnet gave this node (placeholder)
```

Without `tailnet` settings the role leaves the client alone. With them it
enables `tailscaled.service`, lets the target user operate the client, and then
requires the machine to be signed in, with its Tailnet host name equal to the
system host name and, when `tailnet.address` is set, holding that address. On a
new machine that check stops the first system apply. Sign in, then finish the
operation:

```sh
python3 scripts/login-tailnet.py
./bin/shaffy apply --resume
```

`scripts/login-tailnet.py` runs `tailscale up` against `tailnet.login_server`
through sudo, opens the sign-in page in the browser without printing its URL,
and leaves DNS and routes off until the apply. Never copy authentication keys or
sign-in URLs into either repository or a log.

The role then connects NetworkManager and systemd-resolved through the standard
stub: it keeps the original `/etc/resolv.conf`, when that is a regular file, as
`/var/lib/shaffy/tailnet/resolv.conf.before`, links `/etc/resolv.conf` to
`/run/systemd/resolve/stub-resolv.conf`, reloads DNS without disconnecting Wi-Fi
and restarts `tailscaled` once so it detects the resolver again. Finally it
accepts the Tailnet's DNS settings and subnet routes. No exit node is selected;
Internet traffic stays direct. See
[Tailscale's Linux DNS documentation](https://tailscale.com/docs/reference/linux-dns).

Tailscale looks up accepted subnet routes before the main routing table, so a
subnet route that covers the network the machine is on would send traffic for
that network through the Tailnet: devices on the same Wi-Fi would see it arrive
from the subnet router, and replies such as LocalSend's would go there. Before
accepting routes the role therefore installs and enables
`shaffy-tailnet-local-networks.service`, which adds the routing rule
`priority 5260 lookup main suppress_prefixlength 0` just before Tailscale's own
(5270). A directly connected network is then reached directly; away from it, the
subnet route still applies.

### Recovery

Removing the `tailnet` settings stops later applies from changing the client; it
does not undo earlier changes. To stop using the Tailnet's DNS and routes, and to
put the original resolver file back:

```sh
tailscale set --accept-dns=false --accept-routes=false
sudo rm /etc/resolv.conf
sudo cp /var/lib/shaffy/tailnet/resolv.conf.before /etc/resolv.conf
sudo nmcli general reload dns-full
```

Remove the link before copying, since copying onto it would write through to
systemd-resolved's stub file. To send directly connected networks through
accepted subnet routes again, run
`sudo systemctl disable --now shaffy-tailnet-local-networks.service`. The system operation's snapper pre snapshot of `/`
also holds the earlier `/etc`.

## Docker

The `workflows` role installs Docker, Compose and Buildx and enables
`docker.service`; it starts no containers. It does not add the target user to
the `docker` group: members of that group control the engine, which runs as
root, so membership would give any program running as the user root access
without a password. Run Docker through sudo instead:

```sh
sudo docker ps
sudo docker compose up -d
```

An earlier Shaffy OS added the target user to the group, and a system apply
does not undo that. To leave it, run `sudo gpasswd -d "$USER" docker` and log
out and back in; `id -nG` then no longer lists `docker`.

Docker filters published ports in its own firewall chains, before UFW sees them,
so UFW's default incoming policy does not protect a published port. The role
therefore installs `/etc/docker/daemon.json` (source
`roles/workflows/files/daemon.json`), which binds a port published without a host
address to loopback: `ip` sets this for the default bridge, and
`default-network-opts` for every bridge network created afterwards, such as the
ones Compose creates. A project's `"5433:5432"` then listens on `127.0.0.1:5433`
only. Containers with `network_mode: host` publish nothing and stay behind UFW.
See [Docker's port publishing documentation](https://docs.docker.com/engine/network/port-publishing/).

To offer a port to the network on purpose, name the address in the mapping, such
as `"0.0.0.0:8080:80"`; UFW does not filter it either way. A network created before the
configuration keeps the binding it was created with; `docker compose down`
followed by `up` recreates a project's network with the new default, and keeps
its volumes. A changed `daemon.json` restarts the engine, which stops running
containers; those with a restart policy come back.

## AUR packages

One package comes from the Arch User Repository: the Microsoft core fonts
(`ttf-ms-fonts`). The repository keeps no recipes.

When it is missing, the `aur_package` role installs it from the AUR's current
recipe, without an AUR helper and without passwordless sudo:
`scripts/build-aur-package.sh` clones the recipe into
`~/.cache/shaffy/build/NAME/` as the target user and reports the repository
packages it needs, which root installs as dependencies; the same script builds it
with `makepkg`, as the target user. Root copies the package into the operation
directory under `/var/lib/shaffy/ops/`, checks the copy's SHA-256 and name,
records the recipe's AUR commit beside it and installs only that copy. Nothing is
built as root. An installed package is left alone: updates are yay's.

```sh
yay            # repository packages and AUR packages together, showing recipe changes
```

Read what yay shows before confirming: an AUR recipe is code its maintainer can
change at any time. Installing `ttf-ms-fonts` accepts Microsoft's licence, which
the package installs; the set has no Calibri, Aptos or Segoe UI. Origins and
licences are in [THIRD-PARTY.md](../THIRD-PARTY.md).
