# Working in this repository

This is the working contract for everyone who changes a Shaffy OS installation,
people and agents alike. `AGENTS.md` points here.

Configuration reaches the machine from two sources, and one way only. This
repository holds the public defaults and the mechanisms: the chezmoi source in
`home/`, the system roles in `site.yml` and `roles/`, `defaults/`, the schema
and the `./bin/shaffy` wrapper. The bound supplement, a private repository
beside the checkout, holds one person's values; `./bin/shaffy status` shows
which supplement is bound and where it is. `./bin/shaffy apply` renders the two
into the home and the system. Managed configuration is an output of both,
never copied back into either source. Platform-owned settings can remain with
the installation: standard home directories follow XDG in any language unless
the supplement explicitly overrides them.

## Product scope

The public default is the complete, opinionated Shaffy OS desktop: its chosen
applications, appearance, shortcuts and workflows. Keep that experience as the
default. Package omissions let a supplement customize it; they are not a reason
to turn the default application set into opt-in bundles.

Work from the supported fresh CachyOS Niri/Noctalia installation. Keep only the
configuration needed to produce the Shaffy OS differences, with the shared
structure those differences require. An already-installed package or an
already-correct setting should cause no change on apply.

## The rule

Make the change in the source that owns it, then apply it. Never configure the
machine by changing the running system and leaving the source behind. A live
file edited in place looks correct and is silently divergent: the source still
says something else, and the next `./bin/shaffy apply` restores the source and
discards the edit.

This forbids, as ways of making a change:

- editing `~/.config/...`, `~/.local/...` or `/etc/...` in place, whether by
  editor, `sed -i`, `tee` or a shell redirect;
- tool subcommands that rewrite their own configuration, such as `mise use`,
  version pins written straight into `~/.config/mise/config.toml`,
  `git config --global`, `gsettings set`, `dconf write`, `flatpak override` or
  `systemctl --user enable`;
- running `chezmoi apply`, or `ansible-playbook` with `site.yml`, instead of
  `./bin/shaffy apply`;
- installing packages ad hoc instead of declaring them.

Reading the live system is always fine, and is often the right first step.

## Decide first whether the value is personal

A value is personal when it identifies a person, a machine or an account, or
adapts the public desktop to one installation: names and e-mail addresses,
host names, server endpoints, display geometry, keyboard hardware mappings,
deliberate folder overrides, private wallpaper paths and extra or omitted packages.
The product's chosen applications, fonts, theme and general keybindings are
public defaults, even when they reflect its author's preferences.

- **Personal values go in the supplement:** fields of its `config.yaml`,
  validated by `schema/supplement.schema.json`; native fragments named under
  `overrides` (Niri, Noctalia, Fish, Alacritty, mise, and the system's keyboard
  map); personal files under `assets`, each a target under `~/` mapped to a
  file in the supplement, optionally `executable` or `private`; and the state
  of each integration (`managed`, `disabled` or `released`). Commit them in the
  supplement's own repository.
- **Public defaults and mechanisms go here:** templates under `home/`,
  `defaults/config.yaml`, `defaults/integrations.yml`, the roles and the
  schema, each tested with synthetic values (`tests/fixtures/supplements/`).
- **A personal need without a supplement mechanism is a public change:** add
  the schema field, the template or role that consumes it, and a test here,
  then set the value in the supplement. Never write the personal value into
  this repository, not even as a default or an example.

Packages follow the same split. Public ones belong in `defaults/packages/` and
the role that installs them; personal ones in the supplement's
`packages.extra`, `packages.omit` and `packages.remove`.

## Bringing a file under management

A file that is not managed yet is not an exception; it is the next thing to
bring in. Write its source by hand from the live file, as a template under
`home/` that reads its values from the settings, as a supplement override, or
as a supplement asset, and replace every personal value with a setting.

Do not let chezmoi copy deployed files into `home/`. `chezmoi add` and
`chezmoi re-add` write the rendered file, private values included, into public
source; `chezmoi add --force` can replace a template with its rendered output;
and accepting the destination's side in `chezmoi merge` does the same.

The real exceptions are outcomes rather than configuration, such as installed
packages and tool binaries, caches, chezmoi's state database and Shaffy OS's
records, plus secrets and private application data, which stay outside both
repositories. The supplement holds identifiers and references, never
passwords, private keys or tokens.

## Retirements and migrations

Removing what an earlier release created, or helping an installation move off
an old tool, is needed only until every installation has applied it. Mark each
such change with a comment that says `Remove after YYYY-MM-DD`, three months
after the release that ships it, and name what goes with it: its test, its
runbook, the links to them. A home file is retired in `home/.chezmoiremove`
and a package in `defaults/packages/retired.yml`; the self-test refuses an
entry in either without that comment. `./bin/shaffy outdated` lists the
comments whose date has passed; delete them on dev with everything they name.

## Check, review, apply

```sh
./bin/shaffy check                          # settings, a sandboxed render, prerequisites; changes nothing
./bin/shaffy diff                           # what a home apply would change
./bin/shaffy apply --home
./bin/shaffy apply --system --tags ROLE     # or every role without --tags
./bin/shaffy status                         # binding, unfinished work, integrations, drift
./bin/shaffy outdated                       # what can be updated, this repository's pins included
```

Updates follow [Updating](docs/UPDATING.md): the owner runs `./bin/shaffy update`
for the machine, and the pins are raised on dev with `scripts/refresh-pins.py`.

Run `./bin/shaffy selftest` after changing code, and confirm that a second
apply changes nothing. The self-test also fails on personal-looking values in
public files and, with a supplement bound, on anything its `denylist.txt` or its
identifying settings name. It cannot recognise every personal value, so read the
diff of a public change for them before committing it.

## Branches

`main` is the released version, and the checkout the machine is bound to stays
on it, so an apply always renders a release. Changes are made on `dev`, in a
separate worktree, and reach `main` when `dev` is merged into it after its
self-test and CI pass. Never commit to `main` directly. Each merge into `main`
is a release: tag the new `main` with a signed tag named after the day,
`vYYYY.MM.DD` (`vYYYY.MM.DD.1` and so on for another release that day), push
the tag with `main`, and publish a GitHub release from it. `workshop`, in its own
worktree, is for quickly trying something on the installation: it starts from
`main`, and whatever should stay is carried to `dev`. To try `dev` or
`workshop` on the machine before a release, run `./bin/shaffy apply` in its
worktree with `--checkout PATH` naming it: the binding stays on the released
checkout, and its next apply returns the machine to `main`. No other branches
are kept; delete a branch once it is merged.

## Leaving the repository

Anything that writes outside this working tree changes the machine:
`./bin/shaffy apply` (home or system), `./bin/shaffy update`,
`./bin/shaffy supplement attach` and `rebind`,
`./bin/shaffy integration NAME adopt` and `release`, the helpers in
`scripts/` that install, build or configure something or run with `--apply` or
`--resume`, `restic-home init`, `backup` and `maintenance` (which prunes the
repository), `sudo`, package installs and `mise install`.
Propose those and let the owner of the machine run or approve them; do not
reach into `$HOME` to make a change land faster. A system apply asks for the
sudo password in a terminal and refuses without one, so an agent never starts
it.

The supplement is source, like this repository: edit it the same way, and
commit or push in either only when asked.

## Worked examples

A tool was once upgraded by editing its version pin in the live
`~/.config/mise/config.toml`. The upgrade worked and the source was left
untouched, so the source still carried the old pin and the next apply would
have reverted the machine. The change belonged in the source first:
`home/dot_config/mise/config.toml` for a tool Shaffy OS's own launchers run,
or the supplement's mise fragment (`overrides.mise`) for a personal runtime,
followed by `./bin/shaffy apply --home`.

A new monitor's scale and position are personal: they go in the supplement's
`desktop.displays`, never into the Niri files under `home/`.
