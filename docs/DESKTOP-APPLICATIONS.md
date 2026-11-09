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
| Zen Browser | `zen-browser-bin`, `speech-dispatcher`, `espeak-ng` | The default browser unless `desktop.browser` names another; Niri's browser shortcuts start Zen. speech-dispatcher and eSpeak NG give it text to speech, such as reading a page aloud in Reader View. |
| Chromium | `chromium` | A second browser for sites that need it. |
| Spotify | `spotify-launcher` | Super+Shift+M launches Spotify or focuses its existing window; the launcher downloads and updates the official client. |
| Proton Pass | `proton-pass` | The desktop password manager; Niri blocks its window out of screen capture. See [Proton Pass](#proton-pass). |
| Claude Desktop | `claude-desktop` | The home apply merges the MCP servers of `ai.mcp_servers` into its configuration, as for Claude Code; its preferences, extensions and account are its own. See [AI tools and MCP servers](WORKSPACE-TOOLS.md#ai-tools-and-mcp-servers). |
| ChatGPT | `chatgpt-desktop-bin` | The official ChatGPT app, which includes the Codex desktop app; Office documents keep opening in LibreOffice (see [Default browser and document types](#default-browser-and-document-types)). |
| Beeper | upstream AppImage | Chats from several networks in one app; see [Beeper](#beeper). |
| SilverBullet Desktop | upstream AppImage | Markdown notes in local spaces that can sync with a SilverBullet Server; see [SilverBullet](#silverbullet). |
| Proton VPN | `proton-vpn-gtk-app` | Account and server selection stay in its own window. |
| Tailscale | `tailscale` | The `tailnet` role, only with `tailnet` settings; see [Tailnet](#tailnet). |
| Remmina, FreeRDP | `remmina`, `freerdp` | Noctalia's Remmina plugin lists saved connections; profiles stay private. |
| Neovim with LazyVim | `neovim`, `lazygit`, `tree-sitter-cli` | chezmoi's `~/.config/nvim`; see [LazyVim](#lazyvim). |
| mpv | `mpv` | Plays video and audio; Niri keeps its window opaque. |
| Document Scanner | `simple-scan`, `sane-airscan` | Scans from network scanners without a vendor driver; see [Scanning](#scanning). |
| Microsoft core fonts | AUR `ttf-ms-fonts` | Arial, Times New Roman and the other core fonts. |
| tmux, zoxide, uv, GitHub CLI | `tmux`, `zoxide`, `uv`, `github-cli` | See [workspace tools](WORKSPACE-TOOLS.md). |

The `desktop_apps` group also holds `nodejs`, `npm` and `yay`, the AUR helper
that `./bin/shaffy update` uses for the AUR packages. `tailscale` is the group's
`required` package: the settings loader refuses a supplement that omits it. The
writing applications are in [Writing tools](#writing-tools).

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

Launching or focusing on one key is the approach of Omarchy's Spotify
[launcher](https://github.com/omacom/omarchy/blob/quattro/bin/omarchy-launch-spotify),
which offers its [installer](https://github.com/omacom/omarchy/blob/quattro/bin/omarchy-install-service-spotify)
when the package is missing. Here the system apply installs the package, so the
shortcut only launches or focuses. See the [spotify-launcher documentation](https://github.com/kpcyrd/spotify-launcher)
for how the official client is downloaded and updated.

## Proton Pass

The Proton Pass desktop app, the CachyOS repository package `proton-pass`, is
the password manager; search **Proton Pass** in the launcher. Niri blocks its
window (app id `proton-pass`) out of screen shares, recordings and screenshots.
Closing the window hides it to the tray, and the app keeps running.
Its account, settings and vault cache are the app's own and stay outside both
repositories. `packages.omit: [proton-pass]` leaves it out. The browser
extension comes from the browser's own add-on store and signs in separately.
The managed SSH integration uses its desktop agent; see [SSH setup](SSH.md)
for plan requirements, private key import, unlocking and verification.
The vault's command line is in [workspace tools](WORKSPACE-TOOLS.md#proton-pass-cli).

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
use at work needs a Pro licence.

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

The account and profile in `~/.config/BeeperTexts` are Beeper's own. Beeper's
MCP server is a public default that a supplement switches on; see [AI tools
and MCP servers](WORKSPACE-TOOLS.md#ai-tools-and-mcp-servers).

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

`~/.config/nvim` is the LazyVim starter with the Neo-tree, Docker, JSON,
Markdown, TOML, YAML and Tailwind extras; a supplement enables more with
`lazyvim.extras`, such as `lazyvim.plugins.extras.lang.ruby`. It adds Harper with British English
for Markdown and Git commit messages, markdownlint settings, the remote
clipboard, absolute line numbers and no animated scrolling. `lazy-lock.json`
pins the plugins. Startup captures those versions before LazyVim's staged
plugin discovery, so a first installation keeps the managed lock unchanged.
The colours follow Noctalia's palette through a pinned
base16 adapter, with its Rosé Pine Dawn before the first palette exists; see
[theme and controls](NOCTALIA-INTEGRATION.md). Super+Shift+E opens LazyVim in
Alacritty.

The language tools install through Mason after the system packages and
`scripts/install-workspace-tools.sh` have supplied npm and any runtime a
supplement's extra needs, such as Ruby for the Ruby extra:

```sh
mise exec -- nvim --headless -l scripts/bootstrap-lazyvim.lua
```

It installs the tools and language servers that the enabled extras, the
supplement's among them, ask Mason for, writes the result to
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

The `writing` group installs Zettlr, Pandoc, qpdf, TeX Live with its European
languages, LibreOffice, Vale, Harper and English spelling and hyphenation
dictionaries; a supplement adds other languages' packages with
`packages.extra`, for example
`libreoffice-fresh-nl`, `hunspell-nl` and `hyphen-nl`. Zotero is the CachyOS
repository's `zotero` package. Reapply with
`./bin/shaffy apply --system --tags writing`.

The `zettlr` integration has Zettlr export through the packaged Pandoc and
hide its toolbar in distraction-free mode. Export profiles, citation styles,
Pandoc filters and fonts are a house style: a supplement installs its own as
`assets`, for example under `~/.config/Zettlr/defaults/` and
`~/.local/share/pandoc/`. An asset under `~/.config/Zettlr/` needs the `zettlr`
integration managed, and one under `~/.local/share/zotero-data/` the `zotero`
integration; `check` and the home apply refuse it otherwise.

Zettlr's citations come from Zotero through the Better BibTeX add-on. While the `zotero`
integration is managed, `scripts/install-workspace-tools.sh` installs a checked
Better BibTeX release into Zotero's profile when the profile has none, and
Zotero updates it from then on; one removed in Zotero comes back with the next
`./bin/shaffy update` while the integration is managed. The profile's `user.js` lets Zotero enable an
add-on found in the profile, which it otherwise keeps disabled. The home apply
creates `~/.local/share/zotero-library`, and with the `zettlr` integration
managed as well, Zettlr cites from `~/.local/share/zotero-library/My Library.json`.

Better BibTeX writes that file after a one-time setup in Zotero:

1. Start Zotero after the installer has run; a Zotero that was already running
   loads Better BibTeX when it next starts.
2. Choose **File → Export Library…**, select the **Better CSL JSON** format,
   tick **Keep updated** and choose **OK**.
3. Save the export as `~/.local/share/zotero-library/My Library.json`. Type
   the path in the file dialog, which hides `~/.local`.

Better BibTeX keeps the export's setup in Zotero's data directory, so a
restored home keeps it, and rewrites the file whenever the library changes.

The `zotero` integration and the Zotero MCP server are described in
[maintenance](MAINTENANCE.md#integrations) and
[workspace tools](WORKSPACE-TOOLS.md).

## Noctalia plugins

`~/.config/noctalia/95-desktop-apps.toml` enables the Tailnet, Syncthing,
Phone Connect and Remmina plugins, with the screen recorder, timer and world
clock. They come from Noctalia's
[official](https://github.com/noctalia-dev/official-plugins) and
[community](https://github.com/noctalia-dev/community-plugins) sources, and
Noctalia installs and updates them with its default `auto_update = "all"`. The
fragment only enables them and declares their settings; this repository ships no
plugin code.
The home apply reloads a running Noctalia.

- Tailnet's panel links to `tailnet.login_server` as its admin page and saves
  Taildrop files in the downloads folder.
- Syncthing reads the user's local Syncthing configuration for its API key, which
  never enters the source, and posts no event notifications.
- The Proton Pass command line is in [workspace tools](WORKSPACE-TOOLS.md),
  and Phone Connect in [KDE Connect](KDE-CONNECT.md).
- Remmina lists the profiles in `~/.local/share/remmina`.

[Desktop setup](DESKTOP-SETUP.md#plugins-in-the-launcher) says where each
plugin sits and lists the launcher entries and prefixes.

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

Docker runs [rootless](https://docs.docker.com/engine/security/rootless/): the
engine is the target user's own service, so `docker` and `docker compose` need
neither sudo nor the `docker` group, and a container can reach only what the user
can. The `workflows` role installs Docker, Compose, Buildx and the rootless
launcher. The home enables the user service `docker.service` and points
`DOCKER_HOST` at its socket, for the session and for Fish. No containers start by
themselves.

The engine runs while the user is logged in, and containers with a restart policy
come back at the next login. Its images and volumes are in `~/.local/share/docker`
and stay out of the [home backup](HOME-BACKUP.md). The system apply creates that
directory as a Btrfs subvolume of its own before the engine first starts, so the
home's snapshots and an undo of the home leave the containers' data alone; a
directory that already exists stays as it is. Inside a container, root is the
user, so what it writes to a bind mount belongs to the user. Any other container
user is a sub-UID on the host, whose files the user cannot always read; remove them
with `rootlesskit rm -rf PATH`.

A published port is an ordinary socket of the user's `rootlesskit` process, so UFW
filters it. `~/.config/docker/daemon.json` also binds a port published without a
host address to loopback: `ip` sets this for the default bridge, and
`default-network-opts` for every bridge network, such as the ones Compose creates.
A project's `"5433:5432"` then listens on `127.0.0.1:5433` only. To offer a port to
the network on purpose, name the address in the mapping, such as
`"0.0.0.0:8080:80"`, and allow it in UFW. A container with `network_mode: host`
shares the rootless engine's network namespace, not the host's: what it listens on
cannot be reached from the host, so publish the port instead. A port below 1024 is
refused; map it to a higher host port, such as `"8080:80"`. After a
change to `daemon.json`, run `systemctl --user restart docker.service`, which stops
running containers; those with a restart policy come back.

<!-- Remove after 2027-02-06, this passage down to the commands that free the old space. -->
An earlier Shaffy OS ran the system engine, as root. A system apply stops and
disables it, but keeps its data and its configuration, which the rootless engine
cannot see. Published images download again. To carry a project's volumes over,
have Compose create them empty, or `docker volume create NAME` for a volume outside
a project, then copy each one while the system engine is stopped. Copy before the
project's first start: a database that has started in the new volume has made a
fresh one there, which the copy would mix with the old; remove it with
`docker compose down --volumes` and begin again. Use a terminal opened after the
home apply, so that `docker` reaches the rootless engine:

```sh
systemctl --user daemon-reload
systemctl --user start docker.service                      # or log in again
sudo ls /var/lib/docker/volumes                            # what there is to carry
docker compose up --no-start                               # in the project
docker volume ls                                           # the new, empty volumes
sudo tar --numeric-owner -C /var/lib/docker/volumes/OLD/_data -cf - . |
  docker run --rm -i -v NEW:/to alpine tar -C /to -xf -    # for each volume
docker compose up -d
```

A bind-mounted data directory keeps the owners the system engine gave it, which mean
nothing to the rootless engine. Run `sudo chown -R "$USER": DIRECTORY` and, for a
container that runs as another user,
`docker run --rm -v "$PWD/DIRECTORY:/d" alpine chown -R UID:GID /d`.

Only volumes and bind mounts carry over. A locally built image must be built again,
and files a container kept outside them stay with the old engine. A Buildx builder
with the `docker-container` driver, such as the ones Kamal creates, keeps the system
engine's socket and fails without it. `docker buildx ls` lists them;
`docker buildx rm NAME` reports that it cannot reach the old engine and removes the
builder all the same, and Kamal creates its builders again at the next build.

To rescue something else, such as a database dump or an image with `docker save`,
start the old engine for a moment with `sudo systemctl start docker.service`; its
kept configuration still publishes ports on loopback only. Once the projects work
and nothing else you need is there, delete the old engine's data and configuration,
after a reboot: a container that did not stop in time can leave its file system
mounted under `/var/lib/docker`, and `rm` would stop there half done. The root
snapshots taken before still hold the data, so the space returns as snapper
removes them:

```sh
sudo systemctl stop docker.socket docker.service containerd.service
sudo rm -rf /var/lib/docker /var/lib/containerd /etc/docker
```

## AUR packages

Two packages come from the Arch User Repository: Moby's rootless Docker launcher
(`docker-rootless-extras`), which Arch's `docker` package leaves out, and the
Microsoft core fonts (`ttf-ms-fonts`). The repository keeps no recipes.

When one is missing, the `aur_package` role installs it from the AUR's current
recipe, without an AUR helper and without passwordless sudo:
`scripts/build-aur-package.sh` clones the recipe into
`~/.cache/shaffy/build/NAME/` as the target user and reports the repository
packages it needs, which root installs as dependencies; the same script builds it
with `makepkg`, as the target user. Root copies the package into the operation
directory under `/var/lib/shaffy/ops/`, checks the copy's SHA-256 and name,
records the recipe's AUR commit beside it and installs only that copy. Nothing is
built as root. An installed package is left alone by an apply; `./bin/shaffy
update` upgrades it with `yay -Sua`, after the repository packages
([Updating](UPDATING.md#the-machine)). If a recipe asks for a newer dependency
than the installed one, pacman refuses the package and the system apply stops
unfinished; run `sudo pacman -Syu`, then `./bin/shaffy apply --resume`.

```sh
./bin/shaffy update   # repository packages, then the AUR packages, showing recipe changes
```

Read what yay shows before confirming: an AUR recipe is code its maintainer can
change at any time. Installing `ttf-ms-fonts` accepts Microsoft's licence, which
the package installs; the set has no Calibri, Aptos or Segoe UI. Origins and
licences are in [THIRD-PARTY.md](../THIRD-PARTY.md).
