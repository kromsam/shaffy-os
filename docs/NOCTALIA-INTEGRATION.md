# Theme and desktop controls

One palette runs through Noctalia, Niri and the applications; the launcher reaches
the desktop controls by name; and the lock screen shows the supplement's image.
The bar, the launcher layout and the shortcuts are in
[desktop setup](DESKTOP-SETUP.md).

## Shared palette

The managed palette is Noctalia's built-in Rosé Pine in light mode, set in
`~/.config/noctalia/90-omarchy.toml`. Noctalia's template engine writes each
application's colours from it, as `92-app-themes.toml` configures:

| Component | Implementation | When colours update |
| --- | --- | --- |
| Noctalia | Built-in palette `Rosé Pine`, light mode | Immediately |
| Niri | Local template `templates/niri.kdl`, written to `~/.config/niri/noctalia.kdl`, which `config.kdl` includes after `cfg/`: borders and tab indicators in the palette's primary colour, with Omarchy's inactive border `#595959aa` | Niri's live configuration reload |
| Alacritty | Built-in template, written to `themes/noctalia.toml`, the first of Alacritty's imports | Alacritty's live configuration reload |
| btop | Built-in template; the seeded `btop.conf` selects the `noctalia` theme | Next launch |
| GTK 3 and GTK 4 | Built-in CSS, which the template's own hook imports into each `gtk.css`, plus the local `gtk-settings.ini` template, which selects adw-gtk3 or adw-gtk3-dark and the Yaru-yellow icons | Reopen applications that keep the old colours |
| Qt and KDE | Qt's GTK3 platform theme (`QT_QPA_PLATFORMTHEME` in Niri's environment) plus the built-in KDE colour-scheme template | Reopen applications that keep the old colours |
| Neovim with LazyVim | Local template `nvim-palette.lua`, written to `nvim/lua/noctalia_palette.lua` and read by the pinned `base16-nvim` | Next launch, or on focus or resume after regeneration |
| tmux | No template: its configuration uses the terminal's named colours, so Alacritty's palette applies | Together with Alacritty |

A palette chosen in Noctalia's settings (**Super+Ctrl+Shift+Space**) or its
wallpaper and theme panel regenerates these files at once, but it is Noctalia
state and wins over the managed fragment; `./bin/shaffy check` lists it. To keep
another palette or mode, set it in the supplement's Noctalia fragment
(`overrides.noctalia`, see [the override layers](DESKTOP-SETUP.md#override-layers)).
At the end of a home apply in a running Niri session, `./bin/shaffy` has Noctalia
apply its templates (`noctalia msg templates-apply`) and then checks Niri's
configuration again, because the template writes Niri's colour include. It
reports a failure in either step but never fails the apply for it.

Window geometry, the terminal's font and opacity, editor preferences and tmux
bindings do not depend on the palette. Applications with their own theme engines,
including many Electron applications and browsers, keep their own appearance.

GTK applications started under Niri do not take the theme name from GNOME's
settings, so the settings template writes `settings.ini` for GTK 3 and GTK 4,
following Noctalia's light or dark mode, and its hook sets the icon theme and the
monospace font in GSettings. Qt's GTK3 platform theme reads the same settings. The
`adw-gtk-theme` package supplies both GTK variants.

Neovim's `RRethy/base16-nvim` is pinned in its plugin specification
(`lua/plugins/theme.lua`) and in `lazy-lock.json`. Until Noctalia has written
the first palette, base16-nvim's own `rose-pine-dawn`, the light variant of the
default palette, stands in. On focus or resume the editor reloads a changed
palette, unless another colour scheme has been selected in it.

## Searchable controls

Open Noctalia's native launcher with **Super+Space** and type immediately to
search applications, open windows or an entry's description. The compact list
and configured controls need no additional plugin. A slash prefix narrows the
results to that entry:

| Search for | Prefix | Action |
| --- | --- | --- |
| Audio settings | `/audio` | Control centre, audio tab: speakers and microphone |
| Display settings | `/display` | Monitor tab: brightness and display controls |
| Network settings | `/network` | Wi-Fi and Ethernet controls |
| Bluetooth settings | `/bluetooth` | Bluetooth devices and connections |
| Do not disturb | `/dnd` | Toggle Do Not Disturb |
| Notification history | `/notifications` | Notification history |
| Choose wallpaper and theme | `/background` | Wallpaper and theme panel |
| Desktop settings | `/settings` | Noctalia settings |
| Capture | `/capture-menu` | Capture menu: screenshots, recording, colour, OCR and QR |
| Timer | `/timer-panel` | Timer panel |
| World clocks | `/world-clock` | World clock panel |
| Desktop tools | `/desktop-tools` | Tools menu: sharing, conversion, system monitor |

`94-desktop-controls.toml` defines these with Noctalia's configurable dmenu
providers. Every entry runs a fixed command; typed text is never interpolated
into a shell. Application search and the plugins' own prefixes stay available,
and no bar buttons are added. The Tailnet and Syncthing panel entries in
`95-desktop-apps.toml` follow the same pattern.

## Lock screen

The lock screen shows the supplement's `desktop.lock_screen.image`, which
`90-omarchy.toml` renders as `[lockscreen] wallpaper`; without it, Noctalia's own
wallpaper, `/usr/share/noctalia/assets/noctalia-wallpaper.png`. The lock-screen
widgets and their layout are Noctalia's own; the idle lock and its timings are in
[wallpaper and lock screen](DESKTOP-SETUP.md#wallpaper-and-lock-screen). Disk
unlock, login and the keyring are separate; see [login keyring](LOGIN-KEYRING.md).

## Ownership and recovery

chezmoi owns the application imports and settings, Noctalia's
`92-app-themes.toml` and `94-desktop-controls.toml`, and the local templates in
`~/.config/noctalia/templates/`. Noctalia owns the generated colour files. The
create-only placeholders for them (`niri/noctalia.kdl`,
`alacritty/themes/noctalia.toml` and btop's seeded `btop.conf`) make the first
start valid, and later applies leave them alone. Noctalia writes GTK's
`settings.ini` files and Neovim's palette directly from their templates: change
the templates under `home/dot_config/noctalia/templates/`, not their output.
Noctalia's built-in GTK templates also write each `gtk.css` and its import.

Validation and recovery are in
[desktop setup](DESKTOP-SETUP.md#validation-and-recovery). After restoring a
theme file there, run `noctalia msg templates-apply` as well, so Noctalia writes
the colour files from the current palette again.

References: [application theming](https://docs.noctalia.dev/noctalia/theming/app-theming/),
[GTK and Qt](https://docs.noctalia.dev/noctalia/templates/official/gtk-qt/),
[launcher providers](https://docs.noctalia.dev/noctalia/launcher/),
[surface IPC](https://docs.noctalia.dev/noctalia/ipc/surfaces/).
