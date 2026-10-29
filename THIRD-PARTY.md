# Third-party material

Shaffy OS's own files are MIT-licensed (`LICENSE`). The files below come from
other projects and keep their licences; `REUSE.toml` records each file's
copyright and licence, and `LICENSES/` holds the licence texts. Upstream
revisions are the ones the files were compared with.

## Included in this repository

| Component | Licence | Files | Local changes |
| --- | --- | --- | --- |
| [Omarchy](https://github.com/omacom/omarchy) at 45748a28 (Quattro), © David Heinemeier Hansson | MIT | `home/dot_config/tmux/tmux.conf`; `home/dot_config/nvim/lua/plugins/theme.lua` (from `themes/matte-black/neovim.lua`); `home/dot_config/fish/conf.d/60-zoxide.fish` (a Fish port of its zoxide-backed `cd`) | tmux: a header, and `?` lists the key bindings with `tmux list-keys -N`. The theme and Fish files are adapted. |
| [omarchy-pkgs](https://github.com/omacom/omarchy-pkgs) `pkgbuilds/omarchy-nvim` at b4e99359, dc0b77dd and d69da9ea, © David Heinemeier Hansson | MIT | `home/dot_config/nvim/lua/config/remote_clipboard.lua`, `lua/plugins/disable-news-alert.lua`, `lua/plugins/snacks-animated-scrolling-off.lua`; adapted: `lazyvim.json`, `lua/config/options.lua` | `lazyvim.json` adds extras; `options.lua` sets up the remote clipboard and turns relative numbers off. |
| [LazyVim starter](https://github.com/LazyVim/starter) at 803bc181, © Folke Lemaitre and LazyVim contributors | Apache-2.0 | `home/dot_config/nvim/init.lua`, `lua/config/keymaps.lua`, `stylua.toml`; modified: `lua/config/options.lua`, `lua/config/autocmds.lua`, `lua/config/lazy.lua` | The modified files say so in a comment: `autocmds.lua` reloads the Noctalia palette on focus; `lazy.lua` preserves managed versions during first-install plugin discovery. |
| [cachyos-niri-noctalia](https://github.com/CachyOS/cachyos-niri-noctalia) 1.4.0 (19672c5c), © CachyOS | GPL-3.0-only | `home/dot_config/niri/config.kdl` and `cfg/{animation,layout,rules}.kdl`, `cfg/{input,keybinds,misc}.kdl.tmpl` | Modified throughout for Shaffy OS (Omarchy-style bindings, layout and rules, settings from the supplement); each file carries a notice. These files stay GPL-3.0-only. |
| [Noctalia](https://github.com/noctalia-dev/noctalia) 5.1.0 (c7b9197a), © 2026 noctalia-dev | MIT | `home/dot_config/noctalia/templates/niri.kdl`; `files/desktop-key/virtual-keyboard-unstable-v1.xml` (the Wayland protocol Noctalia ships, © Kristian Høgsberg, Intel Corporation, Collabora and Purism) | The template drops the focus ring and uses Omarchy's inactive border colour; the protocol file is unchanged. |
| [pagebreak](https://github.com/pandoc-ext/pagebreak) at b4ef2c42, © Benct Philip Jonsson and Albert Krewinkel | ISC (in the file), MIT (repository) | `home/private_dot_local/private_share/pandoc/filters/pagebreak.lua`, `pagebreak-LICENSE` | None; the licence is installed beside the filter. |
| [CSL styles](https://github.com/citation-style-language/styles) `chicago-notes-bibliography-subsequent-ibid.csl` at 47ae27f0, by Andrew Dunning | CC-BY-SA-3.0 | `home/private_dot_local/private_share/zotero-data/styles/chicago-notes-bibliography-subsequent-ibid-no-url.csl` | Bibliography entries omit the DOI or URL; the style has its own id and title and links the original as its template. |

## Downloaded or installed, not distributed

These are fetched at install or first start, under their own licences; the
repository holds only version declarations and scripts.

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
- Quarto and Zotero MCP, selected as `latest` in the mise configuration;
  `scripts/install-workspace-tools.sh` installs or updates them.
- Yaru icons, installed from a checked upstream release by `scripts/install-yaru-icons.py`
  (icons CC BY-SA 4.0, scripts GPL-3.0).
- Neovim plugins, which lazy.nvim downloads at first start (`lazy-lock.json` pins them).
- Ansible collections (`requirements.yml`), installed into the ignored `collections/`.
- Fonts, from distribution packages; Noctalia's default wallpaper, which the
  `noctalia` package installs.
