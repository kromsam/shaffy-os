# Supplement suggestions

The [settings](SUPPLEMENT.md#field-reference) cover what Shaffy OS knows about.
A supplement can carry more: its own tools, files and fragments. These patterns
come from supplements in daily use; adapt them to yours. If one would serve
everyone, propose it upstream with the **Configuration** form
([CONTRIBUTING.md](../CONTRIBUTING.md#propose-a-feature-or-a-configuration)).

## Machine tools

Some setup belongs to the machine, not the desktop
([machine setup](MACHINE-SETUP.md)). Swap and hibernation have a setting,
`power.hibernation`, the boot splash has `desktop.boot_splash`, and Secure Boot
has a public tool you run by hand, `scripts/configure-secure-boot.py`. A
supplement can keep scripts for what remains in `tools/`, with their tests in
`tests/` and a runbook in `docs/`. A Plymouth theme of your own can be such a
tool: it installs the theme under `/usr/share/plymouth/themes/`, and
`desktop.boot_splash` selects it and rebuilds the boot images. So can a boot
hook of the machine's own in `/etc/boot/hooks/post.d/`, which CachyOS's Limine
tools run after each regeneration; one that changes `limine.conf` must sort
before `90-limine-enroll-config`, which enrols and signs it. Shaffy OS never
reads or runs these tools; you run each one by hand, and an apply starts none.
They change what snapper cannot restore, such as the boot chain, so build them
carefully:

- **Report first.** Without arguments, or with `--check`, a tool prints its plan
  or the current state and changes nothing; it writes only with an explicit
  `--apply`.
- **Guard the machine.** Refuse unless the host, the disk, the encryption and
  the boot layout are the ones the tool was written for, and refuse existing
  state it does not recognise rather than overwrite it.
- **Own marked parts of shared files.** Write a block between
  `# BEGIN <supplement_id> …` and `# END <supplement_id> …` lines in files such
  as `/etc/fstab` or `/etc/default/limine`, or a drop-in where the program reads
  them (`/etc/limine-entry-tool.d/`). Refuse when the markers are malformed,
  and leave the blocks Shaffy OS marks as its own to it.
- **Copy, then record.** Copy every file before changing it, and record the
  result, under `/var/lib/<supplement_id>/`.
- **Verify what was written.** A read-only check reads it back: each boot
  entry's kernel command line, the signatures and hashes, the active swap.
- **Order the steps.** Swap and resume come before Secure Boot, because it
  signs what they set. A tool that rebuilds the boot images does so with
  `limine-mkinitcpio`, whose hooks renew the hashes and signatures. Say so in
  the runbook, and let a tool refuse when its prerequisite is missing.
- **Keep secrets out.** Signing keys stay on the encrypted disk, never in the
  repository. A package a tool needs goes in `packages.extra`, as `sbctl` does
  for Shaffy OS's Secure Boot tool.
- **Hold them to the same style.** `./bin/shaffy lint --supplement` lints the
  tools, and the supplement's CI runs their tests.

## A language's tooling

To work in a language the public desktop does not set up, combine four
settings. For Ruby, for example:

- the runtime in the mise fragment (`overrides.mise`), with a `postinstall`
  that installs its language server gem, so the workspace installer and
  `./bin/shaffy update` keep both current;
- the editor's support with `lazyvim.extras: [lazyvim.plugins.extras.lang.ruby]`,
  whose Mason tools the LazyVim bootstrap installs;
- the language server for Claude Code with `ai.claude_plugins`;
- the native libraries the projects' packages build against in
  `packages.extra`.

## A writing house style

Export profiles, a citation style, Pandoc filters and a LaTeX header are
personal: keep them as `assets`. Zettlr's profiles go under
`~/.config/Zettlr/defaults/`, Pandoc's data under `~/.local/share/pandoc/`, and
a style for Zotero under `~/.local/share/zotero-data/styles/`; those targets
need the `zettlr` and `zotero` integrations managed. Fonts the style names go in
`packages.extra`. A test in the supplement that exports a sample document with
real Pandoc catches a broken profile before you need it. A file copied from
another project keeps its licence; a `REUSE.toml` in the supplement records it
and keeps the linter away from its upstream form.

## Keyboards

An external keyboard with its own layout or keys needs two settings:
`desktop.keyboard` for the layouts, and `overrides.keyboard_hwdb` for a key map
such as Caps Lock as Super on that keyboard alone
([keyboard setup](KEYBOARD-SETUP.md)).

## Launchers for your own services

A remote desktop, a notes server or another service only you use gets a
launcher as `assets`: a script in `~/.local/bin/` (with `executable: true`), a
menu entry in `~/.local/share/applications/`, and a shortcut in the Niri
fragment (`overrides.niri`). Its settings that name hosts or accounts are
`private: true` assets.

## Tool versions for projects

Versions shared by a folder of projects belong in a mise file there, such as
`~/Projects/.mise.toml`, kept as an asset. A project's own pins that must not
enter its repository go in its `.mise.local.toml`, a private asset.

## Agents for one folder

An agent configuration for one folder, such as an MCP server that only a notes
folder uses, is a private asset in that folder: `.codex/config.toml` for Codex,
`.mcp.json` for Claude Code. When the server needs credentials, add a small
helper script as an executable asset that fetches them when the agent starts,
for example from your password manager, so no token enters the supplement.
