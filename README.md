# Shaffy OS

Shaffy OS is an opinionated desktop setup for CachyOS with the Niri compositor
and the Noctalia shell, applied to a system that is already installed. It is
not a distribution and not an installer: it partitions, formats and encrypts
nothing.

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

## Bootstrap

Shaffy OS needs Btrfs with a snapper `root` configuration and one step of
packages and pinned tools. Run it as the target user, in the checkout:

```sh
sudo pacman -S --needed mise uv chezmoi python-yaml python-jsonschema snapper git base-devel bubblewrap
mise install ansible-core
mise exec ansible-core -- ansible-galaxy collection install -r requirements.yml -p collections
```

`mise.toml` pins ansible-core and `requirements.yml` pins the Galaxy collections;
`ansible.cfg` makes the playbooks read only `./collections`, which Git ignores.
If mise reports that it does not trust `mise.toml`, review it and run `mise
trust`. `pacman -S` refreshes nothing; a full `sudo pacman -Syu` stays a
separate, deliberate step. `./bin/shaffy check` names whatever is still missing.

The GitHub CLI is not part of Shaffy OS; install `github-cli` separately and
authenticate with `gh auth login --hostname github.com --git-protocol https --web`.
Never paste credentials into chat or repository files.

## Supplement

Personal settings live in the supplement, never in this repository. Start one
from the synthetic example, keep it private, then validate, bind and apply it
from the checkout:

```sh
cp -r supplement.example ../my-supplement      # set supplement_id, then add settings
git -C ../my-supplement init
./bin/shaffy check --supplement ../my-supplement
./bin/shaffy supplement attach --supplement ../my-supplement
./bin/shaffy diff
./bin/shaffy apply --system
./bin/shaffy apply --home
```

`attach` records the binding and applies nothing; later commands use the bound
supplement without naming it. To use the public defaults alone, run
`./bin/shaffy supplement attach --none` instead. The system apply comes first
on a new installation, because it creates the snapper `home` configuration the
home apply needs. [Supplement](docs/SUPPLEMENT.md) describes every setting,
override, asset and integration a supplement can declare.

The supplement holds identifiers and references, never passwords, private keys
or tokens; those stay in their own stores, and private application data stays
outside both repositories.

## Commands

| Command | What it does |
| --- | --- |
| `./bin/shaffy check [--no-render]` | Validates the settings, renders the home for a synthetic user in a sandbox and checks the result, and reports missing system prerequisites. Changes nothing. |
| `./bin/shaffy data` | Prints the effective settings as JSON. |
| `./bin/shaffy diff [PATH...]` | Shows what a home apply would change, optionally for some targets only. |
| `./bin/shaffy apply --home` | Applies the home; `--home` is the default scope. |
| `./bin/shaffy apply --system [--tags ROLE,...]` | Applies the system through Ansible, all roles or only the tagged ones. Refuses without a terminal. |
| `./bin/shaffy apply --resume` | Finishes an interrupted home or system operation from its captured settings. |
| `./bin/shaffy apply [--system] --abandon` | Gives up an operation that cannot be resumed and names the snapshots and copies to review; deletes nothing. |
| `./bin/shaffy status [--no-drift]` | Reports the binding, the checkout, unfinished work, integrations and drift from the source. |
| `./bin/shaffy supplement attach --supplement PATH` or `--none` | Binds an unbound installation to a supplement, or to the public defaults alone; applies nothing. `--adopt-system-record` recognises the root record of an earlier system apply. |
| `./bin/shaffy supplement rebind --path PATH` or `--checkout PATH` | Follows a supplement or a checkout that moved; `--new-id` accepts a changed `supplement_id`. |
| `./bin/shaffy integration NAME adopt` or `release` | `adopt` records an integration that is already active here as managed; `release` stops tracking it and changes nothing. |
| `./bin/shaffy selftest [--skip-host] [-v]` | Runs the test suite; `--skip-host` leaves out tests bound to this machine, as CI does. |

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
- [Maintenance](docs/MAINTENANCE.md): recurring commands, integrations and
  synchronization.

## Licence

MIT; see [LICENSE](LICENSE). Files taken from other projects keep their own
licences; [THIRD-PARTY.md](THIRD-PARTY.md) lists them.
