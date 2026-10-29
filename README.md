# Shaffy OS

Shaffy OS is an opinionated desktop setup for CachyOS with the Niri compositor
and the Noctalia shell, applied to a system that is already installed. It is
not a distribution and not an installer: it partitions, formats and encrypts
nothing.

The default is the full Shaffy OS desktop: its applications, appearance,
shortcuts and workflows. That includes the browsers and communication apps,
the AI and terminal workspace, writing and research tools, Docker and native
audio tools described in [Desktop applications](docs/DESKTOP-APPLICATIONS.md)
and [Workspace tools](docs/WORKSPACE-TOOLS.md). A supplement adapts that desktop
to its owner; it is not needed to select the default application set.

It has two parts. This public repository holds the desktop defaults, the
mechanisms that apply them, optional application integrations, tests and
documentation. A private supplement, a separate Git repository beside the
checkout, holds one person's values: identity, displays and keyboards,
endpoints, folder names, package choices, native overrides and personal files.
Without a supplement the public defaults apply on their own.

chezmoi renders the home from `home/`, Ansible configures the system through
`site.yml` and `roles/`, and `./bin/shaffy` runs both from one validated copy
of the settings, inside snapper snapshot pairs.

## Supported baseline

- An installed CachyOS system with the Niri and Noctalia desktop.
- Btrfs with the installer's default subvolume layout (`@`, `@home` and
  siblings), and snapper with a `root` configuration. The first system apply
  creates snapper's `home` configuration.
- A normal target account. Every command runs as that user from the checkout;
  sudo asks for the password in the terminal when the system needs root.

`./bin/shaffy check` reports each missing prerequisite and how to provide it.
Shaffy OS does not set up disk encryption, swap or hibernation, Secure Boot or
a boot splash.

## Get the source

Clone the released branch as the normal desktop user. If Git is missing,
install it first with `sudo pacman -S --needed git`.

```sh
git clone --branch main https://github.com/kromsam/shaffy-os.git
cd shaffy-os
```

## Bootstrap

Review the checkout, then install its prerequisites and trust its mise
configuration. Run these and all later commands from this checkout:

```sh
sudo pacman -S --needed mise uv chezmoi python-yaml python-jsonschema snapper git base-devel bubblewrap xdg-user-dirs
mise trust mise.toml
mise install ansible-core
mise exec ansible-core -- ansible-galaxy collection install -r requirements.yml -p collections
```

`mise.toml` selects `latest` for ansible-core; rerun its install command or
`mise upgrade ansible-core` to update the installed controller. All public mise
tools use `latest`: editing or applying their configuration alone does not
update installed binaries. `requirements.yml` still pins the Galaxy collections,
and `ansible.cfg` makes the playbooks read only `./collections`, which Git ignores.
If mise reports that it does not trust `mise.toml`, review it and run `mise
trust`. `pacman -S` refreshes nothing; a full `sudo pacman -Syu` stays a
separate, deliberate step. `./bin/shaffy check` names whatever is still missing.

## Choose the settings

For the public desktop defaults alone:

```sh
./bin/shaffy supplement attach --none
```

Or create a private supplement for identity, hardware and account settings:

```sh
cp -r supplement.example ../my-supplement      # set supplement_id, then add settings
git -C ../my-supplement init
./bin/shaffy supplement attach --supplement ../my-supplement
```

`attach` validates the settings, records the binding and applies nothing.
Later commands use the bound supplement without naming it. Choose one
binding above.
[Supplement](docs/SUPPLEMENT.md) describes every setting,
override, asset and integration a supplement can declare.

The supplement holds identifiers and references, never passwords, private keys
or tokens; those stay in their own stores, and private application data stays
outside both repositories.

Home folders follow the installation's XDG settings in any language. No
`folders` block or English directories are needed. Screenshots, recordings,
received files and notes use those existing paths.

Use `folders` only to deliberately override a directory. Create or migrate
that target before applying, as described in [Home folders](docs/HOME-FOLDERS.md).
Compatibility symlinks are needed only for saved paths that still use an old
name; XDG-aware applications do not require them.

## Apply and finish installation

```sh
./bin/shaffy check
./bin/shaffy diff
./bin/shaffy apply --system
./bin/shaffy apply --home
```

The system apply asks for sudo in your terminal and comes first: it prepares
snapper's `home` configuration. It installs the complete package selection,
including the AUR build, and enables Docker. Expect substantial downloads,
including TeX Live and Wine. `diff` previews home changes; the system package
selection is in [defaults/packages](defaults/packages/README.md).

The home apply installs configuration and launchers. Finish the user-level
tools with the repository's installers, without sudo:

```sh
bash scripts/install-workspace-tools.sh
python3 scripts/install-silverbullet-desktop.py
python3 scripts/install-beeper-desktop.py
mise exec -- nvim --headless -l scripts/bootstrap-lazyvim.lua
bash scripts/build-audio-bridge.sh
```

These install the mise tools, Zotero MCP among them, pstack, SilverBullet Desktop, Beeper Desktop, LazyVim's language tools and the Windows VST2 bridge. They are separate from
an ordinary apply and download or build software in the user's home. For the
Yaru icon theme selected by the desktop:

```sh
curl --fail --location --output /tmp/shaffy-yaru.tar.gz \
  https://github.com/ubuntu/yaru/archive/refs/tags/26.10.3ubuntu.tar.gz
python3 scripts/install-yaru-icons.py /tmp/shaffy-yaru.tar.gz
```

The icon installer verifies the pinned checksum before extracting anything.
Log out and back into Niri after the first installation, then check:

```sh
./bin/shaffy check
./bin/shaffy status
```

Press **Super+Space** and type to launch an application; **Super+Return** opens
Alacritty, and **Super+K** shows the shortcuts. Accounts and libraries still
need the applications' own onboarding. Startup services and profile changes
are controlled by the supplement's [integrations](docs/SUPPLEMENT.md#integrations);
for example, Zotero MCP needs Zotero running with its local API enabled by the
`zotero` integration. [Maintenance](docs/MAINTENANCE.md) covers adopting
existing profiles and synchronization services.

## Update

[Updating](docs/UPDATING.md) describes how to take a release, how `./bin/shaffy update` upgrades the packages, the AUR, the
mise tools and the user installs, and how `./bin/shaffy outdated` shows what is
behind. Keep an existing private supplement updated in its own Git repository.

For an interrupted apply, start with `./bin/shaffy status` and
`./bin/shaffy apply --resume`. `--abandon` clears an operation that cannot be
resumed and identifies its snapshots and copies; it does not roll back files.
See [recovery](docs/DESKTOP-SETUP.md#validation-and-recovery) before restoring anything.

## Commands

| Command | What it does |
| --- | --- |
| `./bin/shaffy check [--no-render] [--no-home]` | Validates the settings, renders the home for a synthetic user in a sandbox and checks the result, and reports missing system prerequisites. `--no-home` leaves out this machine's home and system, as a supplement's CI does. Changes nothing. |
| `./bin/shaffy data` | Prints the effective settings as JSON. |
| `./bin/shaffy diff [PATH...]` | Shows what a home apply would change, optionally for some targets only. |
| `./bin/shaffy apply --home` | Applies the home; `--home` is the default scope. |
| `./bin/shaffy apply --system [--tags ROLE,...]` | Applies the system through Ansible, all roles or only the tagged ones. Refuses without a terminal. |
| `./bin/shaffy apply --resume` | Finishes an interrupted home or system operation from its captured settings. |
| `./bin/shaffy apply [--system] --abandon` | Gives up an operation that cannot be resumed and names the snapshots and copies to review; deletes nothing. |
| `./bin/shaffy status [--no-drift]` | Reports the binding, the checkout, unfinished work, integrations and drift from the source. |
| `./bin/shaffy outdated [--no-pins]` | Reports what can be updated: packages, the AUR, mise tools, user installs, LazyVim plugins, the Shaffy OS release and this repository's pins. Changes nothing. |
| `./bin/shaffy update [--skip-packages]` | Updates the packages, the AUR, the mise tools and the user installs, in order; stops at the first failure. See [Updating](docs/UPDATING.md). |
| `./bin/shaffy supplement attach --supplement PATH` or `--none` | Binds an unbound installation to a supplement, or to the public defaults alone; applies nothing. `--adopt-system-record` recognises the root record of an earlier system apply. |
| `./bin/shaffy supplement rebind --path PATH` or `--checkout PATH` | Follows a supplement or a checkout that moved; `--new-id` accepts a changed `supplement_id`. |
| `./bin/shaffy integration NAME adopt` or `release` | `adopt` records an integration that is already active here as managed; `release` stops tracking it and changes nothing. |
| `./bin/shaffy lint [--fix] [--changed] [--supplement [PATH]] [PATH...]` | Runs one formatter and linter per language, as [STYLE.md](STYLE.md) says; `--fix` lets the formatters rewrite the files and `--supplement` lints the bound supplement, or the one named, by the same rules. Needs the development tools of `mise.dev.toml`. |
| `./bin/shaffy selftest [--skip-host] [-j N] [-v]` | Runs the test suite in parallel, one worker per CPU; `--skip-host` leaves out tests bound to this machine, as CI does, and `-j 1` runs plain unittest in order. |

`check`, `data` and `diff` accept `--supplement PATH` or
`--without-supplement` to try other settings than the bound ones; `diff` then
previews them from a frozen copy. `apply --checkout PATH` applies once from a
checkout other than the bound one.

## What an apply does

Each apply first captures the settings, so every step reads the same validated
copy, and holds a lock against a second apply.

The home apply checks the capture in a sandboxed render, takes a snapper pre
snapshot of `/home` and keeps copies of the files it will change under
`~/.local/state/shaffy/runs/`. It then writes the home with chezmoi, validates
the Niri, Noctalia and Alacritty configuration in the real home, takes the post
snapshot and reloads a running Noctalia.

The system apply runs the roles of `site.yml` as the target user, with root
per task through sudo. Its first root steps copy and verify the capture under
`/var/lib/shaffy/ops/`, take snapper pre snapshots of `/` and `/home` and hold
the system lock; its last step takes the post snapshots. The roles read their
settings only from that verified copy, so `site.yml` is not run with
`ansible-playbook` directly.

An interrupted apply is finished with `./bin/shaffy apply --resume`;
`./bin/shaffy status` shows where it stopped and which snapshots and copies
belong to it. A second apply should change nothing. Routine applies never
format storage, enrol firmware keys or reboot.

chezmoi stays useful for reading through the tracked configuration, for
example `chezmoi --config chezmoi.toml --source home status`. Its hooks refuse
`apply`, `update`, `init`, `destroy`, `purge` and `edit --apply`, which would
bypass the snapshots and records.

## Changing the configuration

Make every change in the source that owns it, then apply it: personal values
in the supplement, public defaults and mechanisms here. Editing the live home
or `/etc` in place only lasts until the next apply. [CLAUDE.md](CLAUDE.md) is
the working contract for people and agents; it says how to decide where a
value belongs and which commands change the machine.

## Documentation

- [Architecture](docs/ARCHITECTURE.md): the settings loader, binding, records
  and the home and system operations.
- [Supplement](docs/SUPPLEMENT.md): the supplement's layout and settings.
- [Desktop setup](docs/DESKTOP-SETUP.md): Niri and Noctalia, shortcuts and
  configuration ownership.
- [Theme and desktop controls](docs/NOCTALIA-INTEGRATION.md) and
  [Omarchy behaviour](docs/OMARCHY-IMPLEMENTATION.md).
- [Desktop applications](docs/DESKTOP-APPLICATIONS.md) and
  [workspace tools](docs/WORKSPACE-TOOLS.md).
- [Home folders](docs/HOME-FOLDERS.md), [keyboard](docs/KEYBOARD-SETUP.md),
  [KDE Connect](docs/KDE-CONNECT.md) and
  [login keyring](docs/LOGIN-KEYRING.md).
- [Home backup](docs/HOME-BACKUP.md).
- [Updating](docs/UPDATING.md): the machine, this repository's pins and releases.
- [Maintenance](docs/MAINTENANCE.md): recurring commands, integrations and
  synchronization.

## Licence

MIT; see [LICENSE](LICENSE). Files taken from other projects keep their own
licences; [THIRD-PARTY.md](THIRD-PARTY.md) lists them.
