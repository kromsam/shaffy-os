# The supplement

A supplement holds one person's settings for Shaffy OS: identity, displays and
keyboards, folder names, endpoints, package choices, native configuration
fragments and personal files. Whatever it leaves out comes from the public
defaults in `defaults/config.yaml`, and an installation also works without one.
This document is the reference for its layout and for every setting it can
declare. How `./bin/shaffy` selects, freezes and applies it is described in
[the architecture](ARCHITECTURE.md).

## Layout

A supplement is a directory with a `config.yaml`, normally a private Git
repository with its own history and remote:

```text
my-supplement/
├── config.yaml     # the settings
├── overrides/      # native fragments named under `overrides`
├── assets/         # personal files named under `assets`
├── tools/          # optional: personal helpers
├── tests/          # optional: their tests
├── docs/           # optional: their notes
├── denylist.txt    # private values that must never reach public files
└── .gitignore      # keeps credential files out of Git
```

Shaffy OS reads `config.yaml` and exactly the files it names under `overrides`
and `assets`; only the denylist test also reads `denylist.txt`.
`overrides/` and `assets/` are conventions; any path inside the
supplement works. A reference is relative to the supplement's root and must
resolve inside it after links are followed, so a link that leads out of the
supplement is refused. Nothing else in the directory, `tools/`, `tests/` and
`docs/` included, is read, captured or copied by an apply.

Keep the supplement beside the checkout, for example `~/Projects/shaffy-os` and
`~/Projects/my-supplement`, and start from the synthetic example:

```sh
cp -r ~/Projects/shaffy-os/supplement.example ~/Projects/my-supplement
git -C ~/Projects/my-supplement init
```

A clone at `supplement/` inside the checkout also works, and Git ignores that
path, but `git worktree remove` deletes an ignored nested repository together
with its uncommitted files (see
[the architecture](ARCHITECTURE.md#public-repository-and-private-supplement)).

### Credentials and the denylist

The supplement holds identifiers and trust material, such as host names, a
server's public host key or the path of a public signing key. It never holds
passwords, private keys or tokens; those stay in their own stores. A
`.gitignore` keeps the usual credential files out of its history:

```text
*.password
*.key
*.pem
.env
client.json
```

`denylist.txt` lists the values that must never appear in the public
repository: one Python regular expression per line, matched case-insensitively,
with blank lines and lines starting with `#` ignored.

```text
# The account and the machine
\bjdoe\b
\bexample-laptop\b
\bvault\.example\.com\b
```

`tests/test_export_denylist.py`, part of `./bin/shaffy selftest`, scans every
public file with these patterns and with the identifying values of the bound
settings: `supplement_id`, `identity.name` and `.email`, the backup host,
repository ID, server, user and host key, the Tailnet server and the Tailnet
address. It reports where a value matched,
never the value. It is skipped when no supplement is bound, when the bound one
has no `denylist.txt`, and under `selftest --skip-host`.

## config.yaml

The smallest valid supplement is the one in `supplement.example/`; copied beside
a checkout named `shaffy-os`, it reads:

```yaml
# yaml-language-server: $schema=../shaffy-os/schema/supplement.schema.json
schema_version: 1
supplement_id: my-desktop
```

- The comment lets an editor with the YAML language server check and complete
  the file against `schema/supplement.schema.json`, the schema the loader
  validates with. The path is relative to `config.yaml`:
  `../shaffy-os/schema/…` beside a checkout named `shaffy-os`, `../schema/…`
  for `supplement/` inside the checkout. Shaffy OS itself ignores the comment.
- `schema_version` is `1`. A version this Shaffy OS does not know is refused.
- `supplement_id` names these settings wherever they live: lowercase letters,
  digits and hyphens, starting with a letter or digit, at most 63 characters.
  The binding records it. An apply refuses a bound supplement whose
  `supplement_id` has changed, and `supplement rebind --path` refuses a
  different one unless `--new-id` is given. It is not a credential.
- `minimum_release`, optional, is the oldest Shaffy OS release the supplement
  works with, as its tag: `vYYYY.MM.DD`, or `vYYYY.MM.DD.1` and so on for a
  later release that day. `check` and `apply` refuse a checkout whose newest
  release tag is older, before they report anything else, so a supplement that
  uses a newer field gets that message rather than an unknown key. Raise it
  when the supplement starts to depend on a release; `dev` and `workshop` count
  as the last release they contain. `./bin/shaffy status` shows the checkout's
  release.

The file is read as YAML 1.2 core: only `true` and `false` are booleans, so
`no` and `yes` stay strings, an unquoted `0600` is the number 600, and there
are no timestamps. Quote values that only look like numbers. A duplicate key is
an error.

### Absent, false and empty

- **Absent** means "not requested": the public default applies, or, where there
  is none, the feature stays off. The field reference below says which.
- **Mappings merge** with the defaults key by key. A supplement can change or
  add a key inside a default mapping, never delete one.
- **Scalars and lists replace** the default whole. An explicit `false` or an
  empty list is kept as written: `args: []` replaces a default argument list.
- **Invalid:** `key: null` (also written `key:` or `key: ~`), an empty section
  such as `desktop: {}`, an unknown key, a value of the wrong type or shape, and
  a reference to a file the supplement does not contain. The loader reports
  every problem at once, each with its file and key.

`./bin/shaffy check` validates the settings and renders the home for a
synthetic user; `./bin/shaffy data` prints the effective settings as JSON,
including `present`, the keys the supplement itself set.

## Field reference

"Applied by" names the apply that uses a setting: the home apply
(`./bin/shaffy apply --home`), or a system role, run by
`./bin/shaffy apply --system` or alone with `--tags ROLE`. `./bin/shaffy diff`
shows home changes only.

### desktop

| Key | Value and effect | Without it | Applied by |
| --- | --- | --- | --- |
| `desktop.displays` | A list of Niri outputs: `name` (required; a connector such as `eDP-1`), `mode` (`WIDTHxHEIGHT` or `WIDTHxHEIGHT@RATE`), `scale` (above 0, at most 8) and `position: {x, y}` in logical pixels. | Niri configures outputs itself. | home |
| `desktop.keyboard` | XKB `layouts` (required), `variants` and `options`, each a comma-separated list. | Niri follows `localectl`. | home |
| `desktop.wallpaper` | `directory` for the picker and `image` for the default and initial wallpaper; each an absolute path or one starting with `~/`. | Noctalia's own wallpaper in `/usr/share/noctalia/assets/`. | home |
| `desktop.lock_screen.image` | The lock-screen background, a path. | The same wallpaper. | home |
| `desktop.browser` | The desktop entry that opens web links and HTML, such as `firefox.desktop`. | `zen.desktop` | `browser` |

[Desktop setup](DESKTOP-SETUP.md) shows what each setting changes, and
[keyboard setup](KEYBOARD-SETUP.md) covers layouts and key maps.

### folders and folder_links

Standard directories follow the installation's XDG settings, in any language;
no `folders` block is needed. `folders` optionally overrides individual roles
with names relative to the home, preserving all unlisted XDG entries.
`projects` is nonstandard and only supplies a target for optional links.
`folder_links` adds aliases such as `~/Pictures` for old saved paths, resolving
their targets from XDG or an explicit override. Both mechanisms are described
in [home folders](HOME-FOLDERS.md).

### identity

| Key | Value and effect | Without it | Applied by |
| --- | --- | --- | --- |
| `identity.name`, `identity.email` | Git's `user.name` and `user.email` in `~/.config/git/config`. | No `[user]` section. | home |
| `identity.signing.key` | The public SSH key that signs commits, as a path starting with `~/` or `/`. Signing is switched on only while the file exists; `check` notes a missing key. | Commits are not signed. | home |

### ai

| Key | Value and effect | Without it | Applied by |
| --- | --- | --- | --- |
| `ai.compound_engineering.enabled` | Global CE activation for Claude Code and Codex (default true). False keeps the plugin installed and requires native project opt-in; new repos start off. | Public default applies. | home |
| `ai.mcp_servers.NAME` | An MCP server, `{enabled, command, args, env}` or, for a streamable HTTP server that runs on its own, `{enabled, url}`, merged into `~/.claude.json`, Claude Desktop's `~/.config/Claude/claude_desktop_config.json` and `~/.codex/config.toml`. `enabled` defaults to `true`; `false` removes that named entry from every client. Claude Desktop's file takes no `url` servers. A leading `~/` in `command` means the home. A name the defaults already have (`beeper`, `zotero`) is changed key by key; a new enabled name needs a `command` or a `url`. Other entries stay, and deleting a name from the supplement alone does not remove an existing entry. | The default `beeper` and `zotero` servers. | home |

### tailnet

| Key | Value and effect | Without it | Applied by |
| --- | --- | --- | --- |
| `tailnet.login_server` | The coordination server, `https://HOST` with an optional port: the admin link in Noctalia's Tailnet panel, and the server `scripts/login-tailnet.py` signs in with. | No admin link. | home |
| `tailnet.address` | This node's Tailnet address, IPv4 or IPv6. The system apply checks that the running client holds it. | Not checked. | `tailnet` |

The `tailnet` role acts only when the section is present: it enables
`tailscaled`, lets the target user operate the client, requires the machine to
be signed in, points `/etc/resolv.conf` at systemd-resolved's stub and accepts
the Tailnet's DNS and routes. Without the section it changes nothing.

### kde_connect

| Key | Value and effect | Without it | Applied by |
| --- | --- | --- | --- |
| `kde_connect.interface`, `kde_connect.subnet` | Both required: a network interface such as `wlan0` and a network such as `192.0.2.0/24`. The `kde_connect` role opens KDE Connect's ports, 1714 to 1764 over TCP and UDP, in an active ufw firewall for that network on that interface only. | No firewall opening; the packages are still installed. | `kde_connect` |

See [KDE Connect](KDE-CONNECT.md).

### localsend

| Key | Value and effect | Without it | Applied by |
| --- | --- | --- | --- |
| `localsend.interface`, `localsend.subnet` | Both required: a network interface such as `wlan0` and a network such as `192.0.2.0/24`. The `localsend` role opens LocalSend's port, 53317 over TCP and UDP, in an active ufw firewall for that network on that interface only. | No firewall opening: LocalSend can send, but other devices cannot find it or send to it. The `desktop` role still installs it. | `localsend` |

The rules carry the comment `shaffy LocalSend`. Removing the section stops later
applies from adding them but does not delete them; delete them with
`sudo ufw status numbered` and `sudo ufw delete NUMBER`, highest number first.

### writing and login

| Key | Value and effect | Without it | Applied by |
| --- | --- | --- | --- |
| `writing.language` | A BCP 47 language such as `en-GB`, for hyphenation and quotation marks in Zettlr's DOCX and PDF export profiles. | No language in the profiles. | home |
| `writing.docx_reference` | A reference `.docx` whose styles DOCX exports take. Write it as a path under the home, starting with `~/`. | Pandoc's default styles. | home |
| `login.autologin` | `true` makes SDDM start Niri without a second password after the disk is unlocked, and unlocks the keyring with the disk password. Refused unless `/` is on an encrypted device. | SDDM's normal password login. | `login` |

The writing settings reach Zettlr only while the `zettlr` integration is
managed. Automatic login is described in [login keyring](LOGIN-KEYRING.md).

### backup

The home backup to a restic repository over SFTP: `host`, `tags` and
`repository` (with `sftp: {host, user, path, host_key}`, an optional `port` and,
once known, `id`) are required; `excludes` and `retention` are optional. Without the
section, the `restic-home` client, its configuration and its units are not
rendered. The `backup` integration switches the schedules on. The fields and the
lifecycle are in [home backup](HOME-BACKUP.md).

### packages

```yaml
packages:
  extra: [gimp, htop]    # also install these
  omit: [chromium, ttf-ms-fonts]  # leave these repository and AUR packages out
  remove: [nano]         # remove these
```

- `extra` installs repository packages that no public group in `defaults/packages/`
  contains. A package a group already installs is refused.
- `omit` leaves a group's repository or AUR package out of its installation request and never
  uninstalls it. A package that is in no group, or that its group lists as
  `required`, is refused.
- `remove` uninstalls exactly the listed packages with `pacman -R`, keeping
  their dependencies. A package that a group or `extra` still installs is
  refused (omit it as well), and so is one Shaffy OS itself runs on, such as
  `chezmoi`, `snapper` or `niri`.
- Pacman group names are refused in `extra` and `remove`; list the packages.

The `packages` role applies `extra` and `remove`; each group's role applies its
omissions. A package in the public retirement list,
`defaults/packages/retired.yml`, is removed unless `extra` asks for it. Nothing
is ever removed because it is missing from a list.

Package choices and home integrations have separate scopes. Omitting or
removing `zotero` does not change Zotero's home integration or the AI
clients' MCP entries. To remove that MCP connection on a home apply, set:

```yaml
ai:
  mcp_servers:
    zotero:
      enabled: false
```

### overrides

Supplements contain values and native fragments, not a second Ansible
application. Shared roles belong in this repository so their changes pass
through the same validation, capture, snapshots and recovery. For example,
`overrides.keyboard_hwdb` carries a private hardware mapping to the public
`keyboard` role; no private copy of the role is needed.

Where a setting is not enough, `overrides` names native fragments inside the
supplement. They are trusted configuration and may run commands: a Fish
fragment runs in every shell, and a Niri fragment can start programs.

| Setting | Scope | Rendered as | Precedence |
| --- | --- | --- | --- |
| `overrides.niri` | home | `~/.config/niri/user.kdl` | Included last by `config.kdl`, so its bindings replace public ones of the same key. Niri adds `output` blocks and other multipart sections instead of merging them, so leave outputs to `desktop.displays`. |
| `overrides.noctalia` | home | `~/.config/noctalia/97-supplement.toml` | After the public fragments, before `99-runtime.toml`, an unmanaged `config.toml` and Noctalia's state, which win for a key they also set. |
| `overrides.fish` | home | `~/.config/fish/supplement.fish` | Sourced at the end of `config.fish`, after `conf.d/`, so it can replace functions defined there. |
| `overrides.alacritty` | home | `~/.config/alacritty/supplement.toml` | The last import, so it wins over the generated colours and `shaffy.toml`. |
| `overrides.mise` | home | `~/.config/mise/conf.d/90-supplement.toml` | Loaded beside `config.toml`, which wins for a key both set: the fragment adds personal runtimes and settings but cannot change a public tool's `latest` selector. |
| `overrides.keyboard_hwdb` | system | `/etc/udev/hwdb.d/90-shaffy-keyboard.hwdb` | Keyboard key maps for systemd's hardware database, installed by the `keyboard` role (see [keyboard setup](KEYBOARD-SETUP.md)). |

A home fragment renders into a file that stays when the setting is removed:
the next apply empties it instead of deleting it. A fragment the settings name
but the supplement lacks fails validation. The system fragment is read as text
of at most 65,536 characters when the settings are loaded and travels inside
them, so the system apply reads it from its verified copy and root never opens
the supplement. [Desktop setup](DESKTOP-SETUP.md#override-layers) lists
Noctalia's full loading order and an example.

### assets

Personal files, such as fonts, pictures and launchers, are copied into the
home. Each key is a target under `~/`; each value is a file inside the
supplement, or `{file, executable, private}`:

```yaml
assets:
  "~/.local/share/fonts/Example-Regular.otf": assets/fonts/Example-Regular.otf
  "~/Pictures/Wallpapers/harbour.jpg": assets/wallpapers/harbour.jpg
  "~/.local/bin/example-launcher": {file: assets/bin/example-launcher, executable: true}
  "~/.config/example/hosts.conf": {file: assets/example/hosts.conf, private: true}
```

- The home apply copies each file from the operation's capture and checks it
  against its SHA-256. `executable: true` installs it with mode 0755;
  `private: true` removes group and world access.
- One entry is one file. A target may not contain `.` or `..` components, a
  double slash, control characters, `"` or `\`.
- `check` and the preflight of every home apply refuse, before anything is
  written, a target that is a real folder, a target whose path runs through a
  link or a file, a target the public source already manages, and a target
  under the files of an integration that is not managed, where it would never
  be installed.
- A changed source file replaces the target at the next apply, which keeps the
  previous version in its run's copies. Removing an entry stops managing the
  file and leaves it in place.

Assets suit small files that belong to the settings. A large or changing
collection, such as a synchronized wallpaper folder, stays with its sync
client; point `desktop.wallpaper.directory` at it instead.

### integrations

Optional integrations are listed in `defaults/integrations.yml`. Each renders
its files and creates its activation only while the supplement asks for it:

| Integration | What it manages |
| --- | --- |
| `backup` | The daily verified home backup, weekly maintenance and monthly full check, as user timers. Needs the `backup` settings. |
| `nextcloud` | The Nextcloud client's login autostart entry. |
| `reaper` | REAPER's PipeWire audio settings and the yabridge configuration. |
| `ssh` | The SSH client configuration and the SSH agent socket. |
| `syncthing` | Syncthing's user service. |
| `zettlr` | Zettlr's export profiles, its custom CSS, and two of its settings: the packaged Pandoc instead of the bundled one, and no toolbar in distraction-free mode. |
| `zotero` | Zotero's profile, the library location and the local API the MCP server reads. |

```yaml
integrations:
  syncthing: managed
  nextcloud: disabled
  zettlr: released
```

| State | Effect |
| --- | --- |
| absent | Never managed: left alone. Once managed, removing the key is refused until it is set to `disabled` or `released`. |
| `managed` | Its files render, its activation is created, and the ownership ledger (`~/.local/state/shaffy/ledger.json`) records that activation. |
| `disabled` | Its files are no longer rendered but stay in place; the apply removes exactly the wants links and autostart entries the ledger recorded. Configuration keys in files the application writes, and all application data, stay. |
| `released` | Shaffy OS stops tracking it and leaves files, activation and data as they are. |

- An integration that is already active on the machine, from before Shaffy OS
  or from another installation, is refused as `managed` until
  `./bin/shaffy integration NAME adopt` records the existing activation.
  `./bin/shaffy integration NAME release` removes its ledger entry and changes
  nothing else.
- Some integrations have preconditions. `zotero` is adopted only once the
  existing profile has been moved to the managed name (see
  [maintenance](MAINTENANCE.md#integrations)). `backup` activates only after
  one backup of the configured repository has been made and verified by hand:
  until then an apply installs the client, leaves the timers out and says why.
- `./bin/shaffy status`, `diff`, `supplement attach` and `rebind` show what the
  next apply would change, defer or refuse. The ledger is keyed by integration,
  not by supplement, so a rebind keeps ownership.

The ledger's format and transitions are described in
[the architecture](ARCHITECTURE.md#ownership-ledger).

## Binding

An installation applies the supplement it is bound to. Bind once, from the
checkout:

```sh
./bin/shaffy check --supplement ~/Projects/my-supplement
./bin/shaffy supplement attach --supplement ~/Projects/my-supplement
```

`attach` records the supplement's resolved path, its `supplement_id` and the
checkout in `~/.local/state/shaffy/binding.json`, and applies nothing.
`supplement attach --none` binds to the public defaults on purpose. From then on
`check`, `diff`, `apply` and `status` use the bound supplement without naming
it. `check`, `data` and `diff` accept `--supplement PATH` or
`--without-supplement` to try other settings, and `diff` previews them from a
frozen copy. An apply only ever uses the bound supplement; `apply --supplement
PATH` must name it.

A binding changes only on purpose:

```sh
./bin/shaffy supplement rebind --path ~/Projects/my-supplement   # the supplement moved
./bin/shaffy supplement rebind --path PATH --new-id              # accept another supplement_id
./bin/shaffy supplement rebind --checkout ~/Projects/shaffy-os   # apply from another checkout
```

To apply once from a checkout other than the bound one, run the apply in that
checkout and name it with `--checkout PATH`; the journal records the override
and the binding stays. A bound supplement that is missing or invalid makes every
command fail; nothing falls back to the defaults. When an earlier system apply
left a root record that the user record does not know, for example after the
home was restored, `supplement attach --supplement PATH --adopt-system-record`
(or `--none --adopt-system-record`) recognises it (see
[the architecture](ARCHITECTURE.md#selection-and-binding)).

## What an apply captures

Every apply freezes the supplement before it reads it. `config.yaml` and each
file it names are copied into
`~/.local/state/shaffy/runs/<op>/capture/supplement/`, readable by the user
only, under the paths `config.yaml` writes and with the content they resolve to;
the capture's manifest records the SHA-256 of each file and a digest of the
effective settings. Rendering, validation and, for a system apply, root's
verified copy under `/var/lib/shaffy/ops/<op>/` read that capture, never the
live supplement. Therefore:

- a supplement that changes while it is copied is refused, and an edit made
  during an apply waits for the next one;
- `./bin/shaffy apply --resume` finishes an interrupted operation from its own
  capture, whatever the supplement says by then;
- captures are private copies of the settings, which is one more reason to keep
  credentials out of the supplement. A completed home apply prunes completed
  runs beyond the newest ten; the runs of unfinished or abandoned operations
  are never pruned.

`check` and `diff --supplement PATH` take the same kind of capture and remove it
afterwards. The protocol is described in
[the architecture](ARCHITECTURE.md#capture).

## Changing settings

Edit the supplement, never the deployed files, then review and apply:

```sh
./bin/shaffy check                        # validate the settings and a sandboxed render
./bin/shaffy lint --supplement [--fix]    # its scripts, tests and documents (STYLE.md)
./bin/shaffy diff                         # what the home apply would change
./bin/shaffy apply --home
./bin/shaffy apply --system --tags ROLE   # for a setting a system role applies
```

A second apply should change nothing. Commit the change in the supplement's own
repository, new fragments and assets included.

### The same checks as this repository

A supplement's own files, its tools, tests, launchers and notes, follow
[STYLE.md](../STYLE.md) like this repository's. It needs no configuration of its
own for that:

- `./bin/shaffy lint --supplement` lints the bound supplement, or the one named
  after it, giving every tool this checkout's configuration.
- While a supplement is bound, the home apply adds a hook to Claude Code's and
  Codex's user settings, after any hooks already there. In a session inside the
  supplement it formats each file the agent edits and hands what the linters
  find back to it, as this repository's own hooks do; anywhere else it does
  nothing.
- `.github/workflows/supplement.yml` is a reusable workflow that checks a
  supplement in its own repository against the released `main`: the settings
  and the home they render, the lint, the supplement's tests under `tests/` and
  a secret scan of its history. Copy `supplement.example/.github/workflows/shaffy.yml`
  into the supplement to call it on every push. While the Shaffy OS repository is
  private, the run's own token cannot read it: allow its workflows to be used by
  repositories of the same owner, and store a fine-grained token that can only
  read its contents as the supplement repository's `SHAFFY_OS_TOKEN` secret.

Never bring deployed files back into the public `home/` source. `chezmoi add`
and `chezmoi re-add` write the rendered file, private values included, into
public source; `chezmoi add --force` can replace a template with its rendered
output; and accepting the destination's side in `chezmoi merge` does the same.
A personal need that no setting covers is a public change: add the schema
field, the template or role that consumes it and a test with synthetic values
to the public repository, then set the value in the supplement.

## Recovery

- Undo a change by reverting it in the supplement and applying again.
- A refused apply has written nothing; fix what its message names and run it
  again.
- To restore individual files from before an apply, use the snapper pair and
  the copies the apply reported (for an unfinished operation,
  `./bin/shaffy status` names them; see
  [the architecture](ARCHITECTURE.md#recovery)). Keep the supplement in step, or
  the next apply brings the change back.
- A supplement that moved is followed with
  `./bin/shaffy supplement rebind --path NEW_PATH`.
- A lost supplement is restored from its own remote. The capture of the last
  apply, `~/.local/state/shaffy/runs/<op>/capture/supplement/`, also holds
  `config.yaml` and every file it named, but not the rest of the repository.
