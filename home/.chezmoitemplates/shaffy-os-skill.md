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
   supplement, and
   `chezmoi --config chezmoi.toml --source home managed --include files`
   lists the home's managed files. A file that is not listed is the user's own
   to edit. For a path under `/etc`, search the checkout's roles
   (`grep -rn PATH roles/`): a file or line a role manages changes through it.
2. Personal or public? A value that identifies a person, a machine or an
   account, or adapts the desktop to this installation, belongs in the
   supplement: its `config.yaml`, its native `overrides` or its `assets`. A
   change to the product's defaults belongs in the checkout. Read
   `{{ $checkout }}/CLAUDE.md` before changing either.
3. Apply from the checkout. Show `./bin/shaffy diff`, then propose
   `./bin/shaffy apply --home` and let the owner run or approve it. A system
   apply needs sudo in the owner's terminal: propose
   `./bin/shaffy apply --system`, never run it.

Never make a change with `chezmoi apply`, `mise use`, `git config --global`,
`gsettings set`, `dconf write`, `flatpak override` or `systemctl --user enable`,
and never install a package ad hoc: each bypasses the sources. Reading anything
is always fine.
