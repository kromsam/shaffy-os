{{- /* The desktop's own agent skill, for Claude Code and Codex in any directory. It names the
       checkout that applied it; the supplement is found through that checkout's binding. */ -}}
{{- $checkout := joinPath .chezmoi.sourceDir ".." -}}
---
name: shaffy-os
description: REQUIRED before changing this desktop's configuration or software. Shaffy OS manages this home and system, so files under ~/.config, ~/.local and /etc may be outputs of its sources, and an edit made in place is undone by the next apply. Use for Niri, Noctalia, keybindings, displays, the terminal, Fish, Neovim, fonts, folders, mise tools, packages, services, backups and any other dotfile.
---

# Shaffy OS

This desktop is set up by Shaffy OS from the checkout at `{{ $checkout }}`.
A managed file is an output of two sources: that checkout, which holds the
public defaults, and the private supplement that holds this installation's
personal values. Change the source, then apply. Editing the live file looks
right until the next apply restores the source.

1. Is the file managed? In the checkout, `./bin/shaffy status` names the
   supplement and its dotfiles folder, and
   `chezmoi --config chezmoi.toml --source home managed --include files`
   lists the home's managed files. A file that is not listed may come from the
   supplement's dotfiles, the person's own chezmoi source:
   `chezmoi --source <supplement>/<dotfiles> source-path PATH` names its source
   there and changes nothing. If that fails on `.shaffy.settings`, which plain
   chezmoi lacks, look for the source by name (`dot_` for a leading dot). Change
   such a file in the dotfiles, then apply through `./bin/shaffy`; never apply
   the dotfiles with chezmoi. A file neither source manages is the user's own
   to edit. For a path under `/etc`, search the checkout's roles
   (`grep -rn PATH roles/`): a file or line a role manages changes through it.
   A file can also come from a profile, a public bundle the supplement vendors
   under `profiles/OWNER/NAME/`: its fragments are the `profiles/` folders
   beside a tool's own files and the `96-profile-*` and `80-profile-*` files,
   and `./bin/shaffy data` names each asset's `layer` and each value's
   `provenance`. Never change the machine by editing a vendored profile:
   override what it sets in the supplement's `config.yaml`, which wins. To
   draft the fix for its author, edit the copy, run
   `./bin/shaffy profile propose ID`, and restore the copy with
   `git restore profiles/OWNER/NAME` in the same sitting; never apply or
   commit the edited copy. A need no profile can express goes to Shaffy OS
   with `./bin/shaffy profile propose [ID] --to shaffy`. Show each draft to
   the person; never send it, and never run `profile add`, `update` or
   `remove` yourself.
2. Personal or public? A value that identifies a person, a machine or an
   account, or adapts the desktop to this installation, belongs in the
   supplement: its `config.yaml`, its native `overrides`, its `assets` or its
   `dotfiles`. A change to the product's defaults belongs in the checkout. A
   setting others would want but not everyone, one that names no person or
   machine, belongs in a profile (`docs/PROFILES.md` in the checkout). Read
   `{{ $checkout }}/AGENTS.md` before changing either; the
   `shaffy-change-review` skill checks a change against it, and
   `shaffy-practice-review` against the good practices of the tools it
   touches. In the checkout,
   `./bin/shaffy check` validates the supplement and
   `./bin/shaffy lint --supplement` holds its files to the checkout's style.
3. Apply from the checkout. Name the targets `./bin/shaffy diff` lists, then
   propose `./bin/shaffy apply --home` and let the person who owns the machine
   run or approve it. A system apply needs sudo in their terminal: propose
   `./bin/shaffy apply --system`, never run it, and never try `sudo` yourself.

The diff shows real files, among them the agents' own configuration with
accounts and tokens: name targets rather than pasting it, and never print a
secret.

Never make a change with `chezmoi apply`, `mise use`, `git config --global`,
`gsettings set`, `dconf write`, `flatpak override` or `systemctl --user enable`,
and never install a package ad hoc: each bypasses the sources. Reading anything
is always fine.
