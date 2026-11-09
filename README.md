# Shaffy OS

Shaffy OS is an opinionated desktop for CachyOS with the Niri compositor and
the Noctalia shell. It is not a distribution and not an installer: you install
CachyOS first, and Shaffy OS then installs its applications and writes its
configuration on top. It partitions, formats and encrypts nothing.

The desktop is complete: browsers and communication apps, an AI and terminal
workspace, writing and research tools, Docker and audio tools, with their own
appearance, shortcuts and workflows ([desktop applications](docs/DESKTOP-APPLICATIONS.md),
[workspace tools](docs/WORKSPACE-TOOLS.md)).

It comes in two parts:

- **This repository** holds the public defaults and the mechanisms that apply
  them. chezmoi renders the home from `home/`, Ansible configures the system
  through `site.yml` and `roles/`, and `./bin/shaffy` drives both.
- **A supplement** is your own private Git repository beside the checkout. It
  holds what makes the desktop yours: your name and e-mail for Git, displays
  and keyboard, networks, folders, extra or omitted packages, native
  configuration fragments, personal files, your own chezmoi dotfiles if you
  keep any, and the optional integrations you switch on. Whatever it leaves out
  comes from the public defaults.

Every apply reads one validated copy of the settings and runs between snapper
snapshots, so you can see what it changed and undo it.

## Supported baseline

Shaffy OS is tested on a fresh CachyOS installation made with these installer
choices. [The VM validation](tests/vm/README.md) installs one the same way.

| Installer choice | Value |
| --- | --- |
| Desktop | Niri with the Noctalia shell: the `cachyos-niri-noctalia` profile, with SDDM as the login screen. |
| Partitions | Btrfs with the installer's default subvolumes (`@`, `@home` and siblings). |
| Boot loader | Limine, the default. On Btrfs it brings `cachyos-snapper-support`, which sets up snapper's `root` configuration, and `limine-snapper-sync`, which lists snapshots in the boot menu. |
| Account | A normal user with administrator rights, so it can use sudo. Every command runs as this user. |
| Disk encryption | **Encrypt system**, recommended: the disk password then protects everything at rest, and automatic login (`login.autologin`) requires it. Adding encryption later means moving all data. |
| Windows beside it | Optional. Install Windows first and reuse its EFI system partition, which CachyOS mounts at `/boot` and fills with the kernels and initramfs images: enlarge it to a few GiB before installing, since Windows makes 100 to 300 MiB ([dual booting Windows and CachyOS](https://wiki.cachyos.org/installation/installation_on_root/#dual-booting-windows-and-cachyos)). |

The first system apply installs many gigabytes of packages, among them TeX
Live, LibreOffice, Wine and Docker, and snapper keeps snapshots of `/` and
`/home` beside them. The validation VM uses a 40 GiB disk. The machine needs a
network connection throughout.

Shaffy OS sets its own boot splash unless the supplement names another
(`desktop.boot_splash`), and sets up swap and hibernation only when the
supplement asks for them (`power.hibernation`). Secure Boot has a tool that you
run by hand, never an apply. Shaffy OS does not set up network profiles, and
leaves firmware updates for you to install. [Machine setup](docs/MACHINE-SETUP.md)
says how to do each, and how it meets Shaffy OS.

`./bin/shaffy check` lists every missing system prerequisite:

| `check` reports | What provides it |
| --- | --- |
| `missing packages: …` | The `pacman` command in [Bootstrap](#bootstrap). |
| `mise does not trust …` | Review the file it names, then `mise trust` it. |
| `ansible-core is not installed` | `mise install ansible-core`, in the checkout. |
| `Galaxy collections not installed as pinned` | The `ansible-galaxy` command in [Bootstrap](#bootstrap). |
| `/ is not a Btrfs subvolume`, or the same for `/home` | Nothing: reinstall CachyOS with Btrfs. |
| `snapper has no root configuration` | Reinstall with Limine, or create it with `sudo snapper -c root create-config /` before the first system apply. |

## Get the source

Clone the released branch as your normal desktop user. If Git is missing,
install it first with `sudo pacman -S --needed git`.

```sh
git clone --branch main https://github.com/kromsam/shaffy-os.git
cd shaffy-os
```

Keep this checkout on `main`, so that updates arrive with `git pull --ff-only`
([Updating](docs/UPDATING.md)). Run every `./bin/shaffy` command from here.

## Bootstrap

Review the checkout, then install its prerequisites and trust its mise
configuration:

```sh
sudo pacman -S --needed mise uv chezmoi python-yaml python-jsonschema snapper git base-devel bubblewrap xdg-user-dirs
mise trust mise.toml
mise install ansible-core
mise exec ansible-core -- ansible-galaxy collection install -r requirements.yml -p collections
```

`mise.toml` selects `latest` for ansible-core, and every public mise tool uses
`latest` too. `requirements.yml` pins the Galaxy collections, and `ansible.cfg`
makes the playbooks read only `./collections`, which Git ignores. If mise
reports that it does not trust `mise.toml`, review it and run `mise trust`.
`pacman -S` refreshes nothing; a full `sudo pacman -Syu` stays a separate,
deliberate step.

Run `./bin/shaffy check` to see what is still missing. Before a supplement is
attached, it checks the public defaults.

## A first supplement

### Create it

Copy the example beside the checkout and make it a Git repository of its own:

```sh
cp -r supplement.example ../my-supplement
git -C ../my-supplement init
```

The copy holds:

- `config.yaml`, the settings. Set `supplement_id` to a name for them, such as
  `jdoe-laptop`: lowercase letters, digits and hyphens. Its first line lets an
  editor with the YAML language server check the file against the schema in
  `../shaffy-os/schema/`.
- `.gitignore`, which keeps the usual credential files out of its history.
- `denylist.txt`, the values that must never appear in public files (see
  [Keep it private](#keep-it-private)).
- `.github/workflows/shaffy.yml`, an optional check on GitHub (see
  [Keep it private](#keep-it-private)).

### What to set first

A typical first supplement sets a few fields. The values below are examples;
[the supplement reference](docs/SUPPLEMENT.md#field-reference) describes every
field and what applies without it.

```yaml
# yaml-language-server: $schema=../shaffy-os/schema/supplement.schema.json
schema_version: 1
supplement_id: jdoe-laptop
identity:                  # Git's user.name and user.email
  name: Jane Doe
  email: jane@example.com
desktop:
  displays:                # without them, Niri configures the outputs itself
    - name: eDP-1
      scale: 1.5
  keyboard:                # without it, Niri follows localectl
    layouts: "us,de"
    variants: "intl,"
kde_connect:               # phones on this network can reach KDE Connect
  interface: wlan0
  subnet: 192.0.2.0/24
localsend:                 # devices on this network can send files with LocalSend
  interface: wlan0
  subnet: 192.0.2.0/24
integrations:
  ssh: managed
  syncthing: managed
```

- `identity` gives Git your name and e-mail address. `identity.signing.key`
  can add an SSH key that signs commits.
- `desktop.displays` sets each monitor's mode, scale and position; `niri msg
  outputs` names the connectors. `desktop.keyboard` sets the layouts
  ([keyboard setup](docs/KEYBOARD-SETUP.md)).
- `kde_connect` and `localsend` open the firewall for one network on one
  interface, so a phone or another computer there can find this one and send
  files to it. Without them the firewall keeps both closed to incoming
  connections ([KDE Connect](docs/KDE-CONNECT.md#firewall)). Both need an
  active ufw firewall. `ip -brief address` shows each interface with its
  address, such as `192.0.2.15/24` on the network `192.0.2.0/24`.
- `integrations` switches on optional application integrations, each with
  `managed`. Leave out what you do not use.

| Integration | What it does |
| --- | --- |
| `ssh` | Writes `~/.ssh/config`, which uses `~/.ssh/id_ed25519` and adds keys to the agent, and starts the SSH agent socket. |
| `syncthing` | Starts Syncthing's user service. Pairing devices and sharing folders stay in Syncthing. |
| `nextcloud` | Starts the Nextcloud client at login. The account and folders stay in the client. |
| `zettlr` | Makes Zettlr use the packaged Pandoc and, with `zotero` managed, cite from Better BibTeX's export. |
| `zotero` | Manages Zotero's profile, the library location, the local API that Zotero MCP reads, and Better BibTeX's export folder. |
| `backup` | A daily verified backup of the home with restic over SFTP, with weekly maintenance and a monthly full check. Needs the `backup` settings, and its timers start only after a first backup made and verified by hand ([home backup](docs/HOME-BACKUP.md)). |
| `reaper` | REAPER's audio through PipeWire and its bridge for Windows plugins. |

On a fresh installation none of these is active yet. On a machine that already
runs one, adopt it first ([integrations](docs/SUPPLEMENT.md#integrations)).

Home folders follow the installation's XDG settings in any language, so no
`folders` block is needed. Use `folders` only to move a standard folder on
purpose, and create or migrate the target first ([home folders](docs/HOME-FOLDERS.md)).

Personal values belong in the supplement, never in this repository, and
passwords, private keys and tokens belong in neither
([AGENTS.md](AGENTS.md#bringing-a-file-under-management)).

### Keep it private

- **Remote.** Push the supplement to a private repository of your own, so
  that it survives a lost machine. An apply keeps a copy of `config.yaml` and
  the files it names, but not the rest of the repository.
- **Denylist.** Put your account name, host names, domains and other values
  that identify you in `denylist.txt`, one regular expression per line.
  `./bin/shaffy selftest` then fails when a public file of the checkout holds
  one, so none slips into a change you contribute. Your identifying settings
  are always checked; the denylist adds what they do not name.
- **CI.** `.github/workflows/shaffy.yml` checks the supplement on GitHub at
  every push: the settings and the home they render, the lint, the
  supplement's own tests and a secret scan of its history. It needs no
  secret. It is optional: `./bin/shaffy check` validates the same settings on
  the machine. Delete the folder if the supplement is not on GitHub.

### Attach it

Check the supplement, then bind this installation to it:

```sh
./bin/shaffy check --supplement ../my-supplement
./bin/shaffy supplement attach --supplement ../my-supplement
```

`attach` validates the settings, records the binding and applies nothing.
Later commands use the bound supplement without naming it. Commit the
supplement once it checks.

To try the public defaults alone first, bind to no supplement instead:

```sh
./bin/shaffy supplement attach --none
```

Every apply refuses until the installation is bound one way or the other. To
move from `--none` to a supplement later, run
`./bin/shaffy supplement rebind --path ../my-supplement --new-id`; `--new-id`
accepts that the binding now has a `supplement_id`.

## First apply

```sh
./bin/shaffy check
./bin/shaffy diff
./bin/shaffy apply --system
./bin/shaffy apply --home
```

- `check` validates the settings, renders the home for a synthetic user in a
  sandbox and reports missing prerequisites. It changes nothing.
- `diff` shows what the home apply would change. The first time, that is most
  of the home, so expect a long list. It shows the real files, among them
  `~/.claude.json` and `~/.codex/config.toml` once they hold accounts and
  tokens: never paste it into a chat, an issue or a commit.
  `./bin/shaffy diff PATH` limits it to some targets.
- `apply --system` runs in your own terminal, because it asks for the sudo
  password; it refuses without one. It comes first, because it creates
  snapper's `home` configuration, which every home apply needs. It installs
  the complete package selection ([defaults/packages](defaults/packages/README.md)),
  including the AUR builds, so the first run takes a long time.
- `apply --home` writes the configuration and launchers, validates Niri,
  Noctalia and Alacritty and reloads a running Noctalia.

Each apply takes a snapper pre and post snapshot, keeps copies of the home
files it changes under `~/.local/state/shaffy/runs/`, and records its progress.
An interrupted apply is finished with `./bin/shaffy apply --resume`;
`./bin/shaffy status` shows where it stopped. A second apply should change
nothing. [The architecture](docs/ARCHITECTURE.md) describes the protocol.

## Finish the installation

The home apply installs configuration and launchers, not the user-level tools.
Install those with the repository's installers, as yourself, without sudo,
after the home apply:

| Installer | What it installs | Needed |
| --- | --- | --- |
| `bash scripts/install-workspace-tools.sh` | The public mise tools (Claude Code, Codex, the Proton Pass CLI, workmux, Bun, Zotero MCP) and those of the supplement's mise fragment, pstack and the supplement's other Claude Code plugins, and Better BibTeX when the `zotero` integration is managed. | Required: the launchers in `~/.local/bin` run these tools. |
| `mise exec -- nvim --headless -l scripts/bootstrap-lazyvim.lua` | LazyVim's language tools. | Recommended, for Neovim. |
| The Yaru commands below | The icon theme the desktop selects. | Recommended; without it, applications show other icons. |
| `python3 scripts/install-silverbullet-desktop.py` | SilverBullet Desktop, for notes. | Optional. |
| `python3 scripts/install-beeper-desktop.py` | Beeper Desktop, for chats. | Optional. |
| `bash scripts/build-audio-bridge.sh` | yabridge, built from source, for REAPER's Windows VST2 plugins. | Only for those plugins. |

They download or build software in your home and are separate from an
ordinary apply. For the Yaru icon theme:

```sh
curl --fail --location --output /tmp/shaffy-yaru.tar.gz \
  https://github.com/ubuntu/yaru/archive/refs/tags/26.10.3ubuntu.tar.gz
python3 scripts/install-yaru-icons.py /tmp/shaffy-yaru.tar.gz
```

The icon installer verifies the pinned checksum before extracting anything.

Some tools need you to sign in once:

- **Claude Code and Codex:** start `claude` and `codex` in a terminal and
  sign in with your accounts.
- **Proton Pass CLI:** `pass-cli login`. It needs a paid Proton Pass plan
  ([Proton Pass CLI](docs/WORKSPACE-TOOLS.md#proton-pass-cli)).
- **GitHub:** `gh auth login`, which Git also uses for GitHub's HTTPS remotes.

Accounts and libraries otherwise need the applications' own onboarding;
[maintenance](docs/MAINTENANCE.md#synchronization) covers Nextcloud and
Syncthing. Log out and back in to Niri, then check:

```sh
./bin/shaffy check
./bin/shaffy status
```

Press **Super+Space** and type to launch an application; **Super+Return** opens
Alacritty, and **Super+K** shows the main shortcuts
([all shortcuts](docs/DESKTOP-BEHAVIOUR.md#shortcuts)).

## Updating

`./bin/shaffy update` upgrades the packages, the AUR, the mise tools and the
user installs; a new Shaffy OS release arrives with `git pull --ff-only` and an
apply. Read the notes of each release on
[GitHub](https://github.com/kromsam/shaffy-os/releases) before you take it:
they say what changes and what a supplement must change with it.
[Updating](docs/UPDATING.md) describes both, and `./bin/shaffy outdated` shows
what is behind.

## Changing the configuration

Change a setting in the supplement, then check, review and apply it
([changing settings](docs/SUPPLEMENT.md#changing-settings)). A file edited in
place is restored by the next apply ([the rule](AGENTS.md#the-rule)). A change
to the public defaults is a contribution ([Contributing](#contributing)).

## Undoing and leaving

### Undo a change

- Revert the change in the supplement and apply again.
- A refused apply has written nothing. An interrupted one is finished with
  `./bin/shaffy apply --resume`; `--abandon` gives it up, deletes nothing and
  names the snapshots and copies to review.
- To restore individual files from before an apply, see
  [desktop recovery](docs/DESKTOP-SETUP.md#validation-and-recovery) and
  [the architecture](docs/ARCHITECTURE.md#recovery).
- A bad package upgrade is undone from snap-pac's snapshots
  ([Updating](docs/UPDATING.md#the-machine)).
- The firewall rules stay when their settings go: [KDE Connect](docs/KDE-CONNECT.md#recovery)
  and [LocalSend](docs/SUPPLEMENT.md#localsend) say how to delete them.

### Return to the public defaults

`supplement rebind` cannot bind to `--none`, and `attach` works only on an
unbound installation. Instead, cut the supplement down to `schema_version` and
`supplement_id`, set each integration it managed to `disabled` or `released`,
and apply the system and the home. The home returns to the defaults, and the
roles remove automatic login, the keyboard map and the battery override.
Packages from `packages.extra`, assets, the dotfiles' files, folder overrides,
firewall rules and the Tailnet setup stay ([absence and removal](docs/ARCHITECTURE.md#absence-and-removal)).

### Stop using Shaffy OS

Nothing uninstalls Shaffy OS. Once you stop applying it, what it wrote stays
as ordinary files and settings; remove what you no longer want by hand:

- the configuration and launchers in the home, and the user services and
  autostart entries it enabled, rootless Docker's among them;
- `~/.local/state/shaffy/`: the binding, the journal, the ownership ledger,
  each apply's copies and captured settings, and chezmoi's state;
- `/var/lib/shaffy/`: root's record and journal, the copies of each system
  operation, and the original `/etc/resolv.conf` the `tailnet` role kept;
- files under `/etc` that the roles wrote, most with a `Managed by Shaffy OS`
  header naming the role, and snapper's `home` configuration with its
  snapshots;
- the ufw rules commented `shaffy KDE Connect` and `shaffy LocalSend`;
- `~/.local/share/docker`, a Btrfs subvolume of its own;
- with `tailnet` settings: `tailscaled` enabled, `/etc/resolv.conf` linked to
  systemd-resolved's stub, the Tailnet's DNS and routes accepted, and
  `shaffy-tailnet-local-networks.service`;
- every package, AUR build, mise tool and user install.

## Commands

| Command | What it does |
| --- | --- |
| `./bin/shaffy check [--no-render] [--no-home]` | Validates the settings, renders the home for a synthetic user in a sandbox and checks the result, and reports missing system prerequisites. `--no-render` validates the settings only; `--no-home` leaves out this machine's home and system, as a supplement's CI does. Changes nothing. |
| `./bin/shaffy data` | Prints the effective settings as JSON. |
| `./bin/shaffy diff [PATH...]` | Shows what a home apply would change, optionally for some targets only. |
| `./bin/shaffy apply --home` | Applies the home; `--home` is the default scope. |
| `./bin/shaffy apply --system [--tags ROLE,...]` | Applies the system through Ansible, all roles or only the tagged ones. Refuses without a terminal. |
| `./bin/shaffy apply --resume` | Finishes an interrupted home or system operation from its captured settings. |
| `./bin/shaffy apply [--system] --abandon` | Gives up an operation that cannot be resumed and names the snapshots and copies to review; deletes nothing. |
| `./bin/shaffy status [--no-drift]` | Reports the binding, the checkout, unfinished work, integrations and drift from the source. |
| `./bin/shaffy outdated [--no-pins]` | Reports what can be updated: packages, the AUR, firmware, mise tools, user installs, LazyVim plugins and the Shaffy OS release, and, without `--no-pins`, this repository's pins and expired transitions, which are the maintainers'. Changes nothing. |
| `./bin/shaffy update [--skip-packages]` | Updates the packages, the AUR, the mise tools and the user installs, in order; stops at the first failure. |
| `./bin/shaffy supplement attach --supplement PATH` or `--none` | Binds an unbound installation to a supplement, or to the public defaults alone; applies nothing. `--adopt-system-record` recognises the root record of an earlier system apply. |
| `./bin/shaffy supplement rebind --path PATH` or `--checkout PATH` | Follows a supplement or a checkout that moved; `--new-id` accepts a different `supplement_id`. |
| `./bin/shaffy integration NAME adopt` or `release` | `adopt` records an integration that is already active here as managed; `release` stops tracking it and changes nothing. |
| `./bin/shaffy lint [--fix] [--changed] [--supplement [PATH]] [PATH...]` | Runs one formatter and linter per language, as [STYLE.md](STYLE.md) says; `--fix` lets the formatters rewrite the files and `--supplement` lints the bound supplement, or the one named, by the same rules. Needs the development tools of `mise.dev.toml`. |
| `./bin/shaffy selftest [--skip-host] [-j N] [-v]` | Runs the test suite in parallel, one worker per CPU; `--skip-host` leaves out tests bound to this machine, as CI does, and `-j 1` runs plain unittest in order. |

`check`, `data` and `diff` accept `--supplement PATH` or
`--without-supplement` to try other settings than the bound ones; `diff` then
previews them from a frozen copy. `apply --checkout PATH` applies once from a
checkout other than the bound one. chezmoi can still read the tracked
configuration, for example `chezmoi --config chezmoi.toml --source home
status`, but its hooks refuse `apply`, `update`, `init`, `destroy`, `purge` and
`edit --apply`, which would bypass the snapshots and records.

## Documentation

- [Architecture](docs/ARCHITECTURE.md): the settings loader, binding, records
  and the home and system operations.
- [Supplement](docs/SUPPLEMENT.md): the supplement's layout and settings.
- [Desktop setup](docs/DESKTOP-SETUP.md): Niri and Noctalia, personal settings
  and configuration ownership.
- [Desktop behaviour](docs/DESKTOP-BEHAVIOUR.md): windows, the terminal, capture,
  the clipboard and every shortcut.
- [Theme and desktop controls](docs/NOCTALIA-INTEGRATION.md).
- [Desktop applications](docs/DESKTOP-APPLICATIONS.md) and
  [workspace tools](docs/WORKSPACE-TOOLS.md).
- [Home folders](docs/HOME-FOLDERS.md), [keyboard](docs/KEYBOARD-SETUP.md),
  [KDE Connect](docs/KDE-CONNECT.md), [login keyring](docs/LOGIN-KEYRING.md)
  and [SSH with Proton Pass](docs/SSH.md).
- [Home backup](docs/HOME-BACKUP.md).
- [Hibernation and battery](docs/POWER-MANAGEMENT.md): the swap file and
  resume, critical-battery hibernation and the charge limit.
- [Machine setup](docs/MACHINE-SETUP.md): encryption, hibernation, Secure Boot,
  the boot splash and the other choices that belong to the machine.
- [Supplement suggestions](docs/SUPPLEMENT-SUGGESTIONS.md): tools, files and
  fragments a supplement can carry beyond its settings.
- [Updating](docs/UPDATING.md): the machine and Shaffy OS releases.
- [Maintenance](docs/MAINTENANCE.md): recurring commands, integrations and
  synchronization.

## Contributing

Bug reports, features and configurations from your supplement are welcome
([CONTRIBUTING.md](CONTRIBUTING.md)); security problems go to
[SECURITY.md](SECURITY.md). Changes to the public defaults and mechanisms come
as pull requests against `dev`, made on a branch from `dev` in a fork. [AGENTS.md](AGENTS.md) is
the working contract for people and agents: where a change belongs, how it is
checked, and which commands change the machine. Try a branch on your machine
with `./bin/shaffy apply --checkout PATH`, run in its checkout; the binding
stays on your released checkout ([branches](AGENTS.md#branches)).
[MAINTAINING.md](MAINTAINING.md) describes the maintainers' work.

## Licence

MIT; see [LICENSE](LICENSE). Files taken from other projects keep their own
licences; [THIRD-PARTY.md](THIRD-PARTY.md) lists them.
