# Third-party material

Shaffy OS's own files are MIT-licensed (`LICENSE`). The files below come from
other projects and keep their licences; `REUSE.toml` records each file's
copyright and licence, and `LICENSES/` holds the licence texts. Upstream
revisions are the ones the files were compared with.

## Included in this repository

| Component | Licence | Files | Local changes |
| --- | --- | --- | --- |
| [Omarchy](https://github.com/omacom/omarchy) at 45748a28 (Quattro), © David Heinemeier Hansson | MIT | `home/dot_config/tmux/tmux.conf`; `home/dot_config/nvim/lua/plugins/theme.lua` (from `themes/matte-black/neovim.lua`); `home/dot_config/fish/conf.d/60-zoxide.fish` (a Fish port of its zoxide-backed `cd`) | tmux: a header, `?` lists the key bindings with `tmux list-keys -N`, and prefix keys for workmux. The theme and Fish files are adapted. |
| [omarchy-pkgs](https://github.com/omacom/omarchy-pkgs) `pkgbuilds/omarchy-nvim` at b4e99359, dc0b77dd and d69da9ea, © David Heinemeier Hansson | MIT | `home/dot_config/nvim/lua/config/remote_clipboard.lua`, `lua/plugins/disable-news-alert.lua`, `lua/plugins/snacks-animated-scrolling-off.lua`; adapted: `lazyvim.json.tmpl`, `lua/config/options.lua` | `lazyvim.json.tmpl` adds extras and the supplement's; `options.lua` sets up the remote clipboard and turns relative numbers off. |
| [LazyVim starter](https://github.com/LazyVim/starter) at 803bc181, © Folke Lemaitre and LazyVim contributors | Apache-2.0 | `home/dot_config/nvim/init.lua`, `lua/config/keymaps.lua`, `stylua.toml`; modified: `lua/config/options.lua`, `lua/config/autocmds.lua`, `lua/config/lazy.lua` | The modified files say so in a comment: `autocmds.lua` reloads the Noctalia palette on focus; `lazy.lua` preserves managed versions during first-install plugin discovery. |
| [cachyos-niri-noctalia](https://github.com/CachyOS/cachyos-niri-noctalia) 1.4.0 (19672c5c), © CachyOS | GPL-3.0-only | `home/dot_config/niri/config.kdl` and `cfg/{animation,layout,rules}.kdl`, `cfg/{input,keybinds,misc}.kdl.tmpl` | Modified throughout for Shaffy OS (its bindings, layout and rules, and settings from the supplement); each file carries a notice. These files stay GPL-3.0-only. |
| [cachyos-plymouth-theme](https://github.com/CachyOS/cachyos-plymouth-theme) at 27c453a1 (package 1-2), © CachyOS | GPL-3.0-only | `roles/boot_splash/files/shaffy-os/shaffy-os.plymouth` (from `src/cachyos.plymouth`) | A notice, and its name, description and image folder. Its images are not included: the `boot_splash` role copies them from the installed package, and the wordmark beside the theme file, `watermark.png`, is Shaffy OS's own (`scripts/build-splash-wordmark.py`). The theme file stays GPL-3.0-only. |
| [Noctalia](https://github.com/noctalia-dev/noctalia) 5.1.0 (c7b9197a), © 2026 noctalia-dev | MIT | `home/dot_config/noctalia/templates/niri.kdl`; `files/desktop-key/virtual-keyboard-unstable-v1.xml` (the Wayland protocol Noctalia ships, © Kristian Høgsberg, Intel Corporation, Collabora and Purism) | The template drops the focus ring and uses Omarchy's inactive border colour; the protocol file is unchanged. |

## Downloaded or installed, not distributed

These are fetched at install or first start, under their own licences; the
repository holds only version declarations and scripts.

- The AUR package `docker-rootless-extras`: the `aur_package` role builds it from
  the AUR's current recipe on the machine when it is missing, and yay updates it;
  the repository holds no recipe. It installs Moby's rootless launcher (Apache-2.0),
  user units and a sysctl file that keeps unprivileged user namespaces on.
- The AUR package `ttf-ms-fonts`: the `aur_package` role builds it from the
  AUR's current recipe on the machine when it is missing, and yay updates it;
  the repository holds no recipe. Installing it accepts Microsoft's End User
  Licence Agreement for its core fonts, which the package installs.
- Beeper Desktop, downloaded from Beeper's stable channel by
  `scripts/install-beeper-desktop.py`; the app then updates itself. Beeper is
  proprietary.
- SilverBullet Desktop, downloaded from upstream's stable channel by
  `scripts/install-silverbullet-desktop.py`; the app then updates itself. It is
  built on the MIT-licensed SilverBullet and is free for personal use and
  education; use at work needs a Pro licence.
- The Proton Pass CLI, selected as `latest` in the mise configuration (GPL-3.0).
- yabridge, built from a pinned revision by `scripts/build-audio-bridge.sh` (GPL-3.0).
- Zotero MCP, selected as `latest` in the mise configuration;
  `scripts/install-workspace-tools.sh` installs or updates it.
- Better BibTeX for Zotero, installed from a checked upstream release by
  `scripts/install-better-bibtex.py`; Zotero then updates it (MIT).
- Yaru icons, installed from a checked upstream release by `scripts/install-yaru-icons.py`
  (icons CC BY-SA 4.0, scripts GPL-3.0).
- The images of CachyOS's Plymouth theme, from the `cachyos-plymouth-theme`
  package, which the `boot_splash` role installs and copies them from into the
  `shaffy-os` theme (GPL-3.0).
- Neovim plugins, which lazy.nvim downloads at first start (`lazy-lock.json` pins them).
- Ansible collections (`requirements.yml`), installed into the ignored `collections/`.
- Fonts, from distribution packages; Noctalia's default wallpaper, which the
  `noctalia` package installs.
