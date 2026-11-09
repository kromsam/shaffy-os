# Working in Shaffy OS

This is the working contract for everyone who changes a Shaffy OS installation
or this repository, people and agents alike. Codex and most agents read this
file directly; Claude Code reads it through `CLAUDE.md`, which imports it. The
agent tooling lives in `.agents/` ([Agent tooling](#agent-tooling)).

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
  file in the supplement, optionally `executable` or `private`; a chezmoi
  source of the person's own, the folder named as `dotfiles`, for files
  Shaffy OS does not manage; and the state of each integration (`managed`,
  `disabled` or `released`). Commit them in the supplement's own repository.
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
`home/` that reads its values from the settings, as a supplement override, as
a supplement asset, or as a file in the supplement's dotfiles, and replace
every personal value with a setting.

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
comments whose date has passed; they are deleted on `dev` with everything they
name.

## Check, review, apply

```sh
./bin/shaffy lint [--fix] [--supplement]    # formatters and linters (STYLE.md), here or in the supplement
./bin/shaffy check                          # settings, a sandboxed render, prerequisites; changes nothing
./bin/shaffy diff                           # what a home apply would change
./bin/shaffy apply --home
./bin/shaffy apply --system --tags ROLE     # or every role without --tags
./bin/shaffy status                         # binding, unfinished work, integrations, drift
./bin/shaffy outdated                       # what can be updated
```

The person who owns the machine takes updates with `./bin/shaffy update`
([Updating](docs/UPDATING.md)); raising this repository's own pins is
maintainer work ([MAINTAINING.md](MAINTAINING.md)).

Write code as [STYLE.md](STYLE.md) says, and run `./bin/shaffy lint` and
`./bin/shaffy selftest` after changing it; confirm that a second apply changes
nothing. In Claude Code and Codex, hooks format each edited file and hand what
the linters still find back to the agent. The self-test also fails on personal-looking values in
public files and, with a supplement bound, on anything its `denylist.txt` or its
identifying settings name. It cannot recognise every personal value, so read the
diff of a public change for them before committing it.

## Branches

`main` holds the releases, each tagged `vYYYY.MM.DD`; changes land on `dev`.
Branch from `dev` and open a pull request against `dev`, never against `main`.
The checkout an installation is bound to stays on `main`, so an apply always
renders a release. To try another branch on the machine first, run
`./bin/shaffy apply` in its checkout with `--checkout PATH` naming that
checkout: the binding stays on the released checkout, and its next apply
returns the machine to `main`. Releasing `dev` into `main` is maintainer work
([MAINTAINING.md](MAINTAINING.md)).

## Leaving the repository

Anything that writes outside this working tree changes the machine:
`./bin/shaffy apply` (home or system), `./bin/shaffy update`,
`./bin/shaffy supplement attach` and `rebind`,
`./bin/shaffy integration NAME adopt` and `release`, the helpers in
`scripts/` that install, build or configure something or run with `--apply` or
`--resume`, a supplement's own `tools/` run with `--apply` or as root,
`restic-home init`, `backup` and `maintenance` (which prunes the repository),
`sudo`, package installs and `mise install`.
Propose those and let the person who owns the machine run or approve them; do
not reach into `$HOME` to make a change land faster. A system apply asks for
the sudo password in a terminal and refuses without one, so an agent never
starts it. `sudo` needs a real terminal: started without one it fails, and
each failed attempt counts towards the account's lockout (faillock), so an
agent never tries it, not even to test a password.

The supplement is source, like this repository: edit it the same way, and
commit or push in either only when asked.

## Bring it upstream

When work on an installation finds a bug in Shaffy OS, a personal need without
a supplement mechanism, or a supplement setting that would serve everyone as a
default, bring it upstream rather than only working around it: draft the issue
or the pull request as [CONTRIBUTING.md](CONTRIBUTING.md) describes, with every
personal value replaced, for the person to read and send. Never open, comment
on or push to an upstream repository on your own. A workaround the supplement
needs in the meantime names the issue it waits for.

## A fresh clone

In a checkout that no installation is bound to yet, an agent may read
everything and run `./bin/shaffy lint`, `./bin/shaffy selftest`,
`./bin/shaffy check --without-supplement` and `./bin/shaffy data
--without-supplement`; with a supplement at hand, `check --supplement PATH`
validates it. Installing the prerequisites, attaching a supplement and every
apply are for the person, in the order [the README](README.md#get-the-source) gives:
guide them through it and explain what each step will change.

## Keep private output private

`./bin/shaffy diff` shows the real files of the home, among them
`~/.claude.json` and `~/.codex/config.toml`, which hold accounts and tokens.
Name the changed targets instead of pasting a full diff into a chat, an issue
or a commit, and never print a secret's value. A diff limited to the paths in
question (`./bin/shaffy diff PATH`) is fine to read.

## Agent tooling

`.agents/` is the source of the tooling agents use here; the host-specific
folders only point to it.

- **Skills** in `.agents/skills/`: `shaffy-change-review` (where a change
  belongs and what it implies), `shaffy-risk-review` (what it could destroy or
  leave half done) and `shaffy-lean-review` (what can go). Codex reads them
  there; `.claude/skills/` links to them. The home apply also installs them
  for Claude Code and Codex in every directory, beside the `shaffy-os` skill
  that tells an agent anywhere on the machine how this desktop is managed, so
  they serve in a supplement too.
- **The reviewer** in `.agents/agents/shaffy-reviewer.md`: a read-only agent
  that applies one of those skills. `.claude/agents/` links to it, and
  `.codex/agents/shaffy-reviewer.toml` points Codex to it.
- **Hooks** in `.claude/settings.json` and `.codex/hooks.json`: both run
  `./bin/shaffy lint --hook`, which formats each edited file and hands what the
  linters still find back to the agent. `.claude/settings.json` also denies
  Claude Code the commands [the rule](#the-rule) forbids.
- **pstack**, the default workflow plugin for Claude Code and Codex
  ([workspace tools](docs/WORKSPACE-TOOLS.md#pstack)); the review skills use
  its principle skills where it is installed.

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
