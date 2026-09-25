# Omarchy parity

Shaffy OS reproduces the behaviour and geometry of Omarchy's Quattro desktop on
Niri and Noctalia 5.1. It keeps Noctalia's built-in Rosé Pine palette in light
mode rather than Omarchy's default Tokyo Night or its Matte Black theme, so the
colours differ while the layout, controls and shortcuts follow the pinned
reference (see [sources](#sources)). Manual locking, the supplement's wallpapers,
keyboard layouts and displays are deliberate departures from a fresh Omarchy
installation. Everyday use is in [desktop setup](DESKTOP-SETUP.md), the palette in
[theme and controls](NOCTALIA-INTEGRATION.md).

## Configuration

| Area | Implementation |
| --- | --- |
| Window opacity | General windows 0.985 active and 0.96 inactive; browsers 1.0 and 0.985. Video players, image viewers, remote desktops, virtual machines, game windows and picture-in-picture stay opaque. Alacritty's own background stays opaque, so the two opacities do not multiply. |
| Borders and geometry | Square corners, no shadows or blur, 2 px borders: the palette's primary colour when active, Omarchy's `#595959aa` when inactive. Borders do not fill the window background. Gaps are 10 logical pixels; new columns take 0.49 of the width. |
| Animations | Workspace switching is not animated. Windows scale between 87% and 100%, entering over 410 ms on Omarchy's quintic Bézier and leaving over 149 ms linearly. Niri fades inside the window shader, so Hyprland's separate fade curves are not reproduced exactly. |
| Terminal | Alacritty with JetBrainsMono Nerd Font 9, 14×14 padding, no decorations and its default steady block cursor. Shift+Return and Alt+Shift+Return send distinct CSI-u sequences; Ctrl+Insert and Shift+Insert copy and paste. |
| Typography | JetBrainsMono Nerd Font for the shell and as the generic monospace family; the document font substitutions are separate. |
| Bar and panels | 26 logical pixels high with a 12/14 font scale, square solid panels, and a 50% dark backdrop without blur. The recorder indicator hides while idle; the tray is a drawer, and Bluetooth and notifications show only when relevant. |
| OSD | Bottom centre, 67 logical pixels from the edge, opacity 0.97. Noctalia 5.1 has no setting for Omarchy's 1200 ms display time. |
| Icons | Yaru and Yaru-yellow from a checked upstream release, installed per user by `scripts/install-yaru-icons.py` (see [sources](#sources)). The GTK settings and GSettings both select Yaru-yellow. |
| Applications | Noctalia's btop template, and the Niri, Alacritty, GTK, Qt and Neovim colours in [theme and controls](NOCTALIA-INTEGRATION.md). Super+Alt+Return opens tmux, as in Omarchy; tmux and workmux are described in [workspace tools](WORKSPACE-TOOLS.md). |
| Input | Repeat after 250 ms at 40 per second, the pointer warps to focus, clickfinger, natural scrolling off and a touchpad scroll factor of 0.4. Omarchy's terminal-specific touchpad factors are not copied, because Niri's per-window factor would also change the mouse wheel. |
| Main menu | Super+Space opens a nested Noctalia dmenu: Applications, Capture, Style, Controls, Tools, System. Super+Alt+Space opens the application launcher directly. |
| Clock and gestures | Clock: left-click calendar, right-click cycles the format, middle-click world clocks. The launcher button's right-click opens a terminal; right-click on volume mutes, on Bluetooth toggles it, on the battery toggles its label. |
| Capture | Print selects a region in Noctalia and opens its annotation, saving to a file and the clipboard. The capture menu adds recording, a colour picker, text recognition in every installed language, and QR and barcode reading; decoded content is copied, and no link is opened. |
| Timer | Noctalia's official timer plugin: one countdown, with a bar widget, a panel and a desktop widget. |
| Recording | Noctalia's official recorder plugin with `gpu-screen-recorder`: the portal chooses the source; H.264 and AAC in MP4 under `<videos>/Recordings`; system audio by default, with the microphone, both or none in the plugin's settings; replay off. Alt+Print or the capture menu starts and stops it; the bar indicator, shown only while recording, stops it. |
| Utilities | LocalSend, and a conversion menu to H.264/AAC MP4, PNG, JPEG or WebP. A conversion writes `NAME.converted.EXT` beside its source, never changes the source, and refuses when that file exists. |
| Clipboard | Super+C, X and V send fixed copy, cut and paste chords to the focused window through `shaffy-desktop-key`, a small Wayland virtual-keyboard client with a full evdev keymap; terminals get the Insert chords. It needs no root or device permissions and never reads the clipboard. |

## Shortcuts

`Super` means the Windows key. The complete map is
`home/dot_config/niri/cfg/keybinds.kdl.tmpl`; Super+K opens Niri's shortcut
overlay. These are the Omarchy additions to the everyday shortcuts in
[desktop setup](DESKTOP-SETUP.md#everyday-shortcuts):

| Shortcut | Result |
| --- | --- |
| Super+Space / Super+Alt+Space | Main menu / applications |
| Super+C / X / V | Copy / cut / paste; in terminals, copy and paste use the Insert chords |
| Super+Ctrl+V / Super+Ctrl+E | Clipboard history / emoji |
| Super+Ctrl+Space / Super+Ctrl+Shift+Space | Wallpapers / theme and shell settings |
| Super+Backspace | Toggle the focused window's opacity rules |
| Super+Shift+Backspace | Toggle gaps and borders |
| Super+Ctrl+Backspace | Toggle a square window size, restoring the previous one |
| Super+Ctrl+Alt+F | Fullscreen inside the existing tile (Super+Ctrl+F keeps Niri's column expansion) |
| Super+O / Super+Alt+O | Float or return to tiling / Niri overview |
| Super+G / Super+Alt+G | Tabbed column / expel the column's bottom window |
| Super+Alt+Left/Right | Consume into or expel from the adjacent column |
| Super+Alt+Up/Down | Reorder within the column |
| Super+Ctrl+Alt+arrows | Focus another monitor |
| Super+S / Super+Grave | Show or hide the `scratchpad` workspace, opening an Alacritty there on first use |
| Super+Ctrl+A/W/B/D/P | Audio / network / Bluetooth / display / power |
| Super+Ctrl+Alt+D | Calendar |
| Super+Ctrl+1…9 | Fixed panel map: audio, network, Bluetooth, display, power, calendar, notifications, media, system |
| Super+Ctrl+R / Super+Ctrl+Alt+R | Timer panel |
| Super+Ctrl+Shift+R | Reset the countdown (see [remaining differences](#remaining-differences)) |
| Super+Shift+Comma | Dismiss the visible notifications, keeping their history |
| Super+Ctrl+Comma / Super+Alt+Comma | Do Not Disturb / open the latest notification |
| Super+Comma / Super+Shift+Alt+Comma | Notification history |
| Super+Ctrl+N | Night light at 4000 K on demand; the next press restores normal output |
| Super+Ctrl+C | Capture menu |
| Print / Ctrl+Print | Region with annotation / monitor screenshot |
| Alt+Print | Start or stop recording |
| Super+Print / Super+Ctrl+Print | Colour picker / text recognition |
| Super+Ctrl+S / Super+Ctrl+Period | LocalSend / conversion menu |
| Super+Ctrl+T | Floating btop |
| Super+Shift+N | Neovim |
| Super+Escape / Ctrl+Alt+Escape | Session menu / toggle the keyboard-shortcut inhibitor of an application that captures the keys |

Niri actions keep alternative bindings: Super+Alt+C centres a column,
Super+Ctrl+Alt+C centres the visible columns, Super+Ctrl+F expands a column,
Super+Alt+R resets a window's height and Super+Alt+Shift+R cycles it, and
Super+Ctrl+Alt+Comma consumes the next window into the column. Super+Shift+digits
still move windows to workspaces. Super+J and Super+L keep Niri's navigation,
because Niri has no dwindle or split mode for them to switch. Ctrl+Alt+Delete
opens the session menu rather than closing every window.

## Packages

The `desktop` package group (`defaults/packages/desktop.yml`) holds what these
features need: `ttf-jetbrains-mono-nerd`, `gpu-screen-recorder`, `grim`, `slurp`,
`tesseract` with its English data, `zbar`, `localsend`, `ddcutil` and `zenity`. Text
recognition reads every installed Tesseract language, English first; a supplement adds
one with `packages.extra`, for example `tesseract-data-nld`.
The `desktop` role installs them from the already synchronized databases, without
a partial `pacman -Sy`. From the checkout:

```sh
./bin/shaffy apply --system --tags desktop
```

Each home apply builds `shaffy-desktop-key`, so its build tools must be present:
`gcc`, `wayland-scanner` (package `wayland`), `pkg-config` (package `pkgconf`) and
the wayland-client and xkbcommon development files. `./bin/shaffy check` names any
that are missing.

Noctalia's DDC control of external monitors stays at its default, off. `ddcutil`
is installed so that `ddcutil detect` and a brightness read can establish whether
an external monitor supports DDC before the supplement's Noctalia fragment turns
it on:

```toml
[brightness]
enable_ddcutil = true
```

## Apply and runtime files

`./bin/shaffy apply --home` applies the desktop configuration. Its preflight builds
`shaffy-desktop-key` from `files/desktop-key/` in
`~/.cache/shaffy/build/desktop-key/`, never under the checkout. It then takes a
snapper pre snapshot of `/home`, copies every file it will change into
`~/.local/state/shaffy/runs/<op>/before/`, applies, installs the key sender into
`~/.local/bin/` when the build differs, validates Niri, Noctalia and Alacritty in
the real home, and takes the post snapshot. In a running Niri session it finally
has Noctalia reload its configuration and apply its templates. Noctalia fetches
and updates the plugins itself; the apply neither installs nor restarts them.

`~/.config/niri/omarchy-overrides.kdl` and `~/.config/noctalia/99-runtime.toml`
are create-only: the apply creates each once, and `shaffy-desktop` owns it
afterwards (the gaps-and-borders toggle; the clock format and battery label). The
helper writes them atomically with mode 0644 and keeps its toggle state private
(mode 0600) in `~/.local/state/shaffy/desktop-controls/`.

## Recovery

To undo a change, revert it in the supplement or the source, then run
`./bin/shaffy diff` and `./bin/shaffy apply --home`. To restore selected files as
they were before an apply, undo them from the operation's snapper pair or copy
them back from its `before/` folder, then reload Niri and Noctalia:

```sh
snapper -c home undochange PRE..POST ~/.config/niri/cfg/rules.kdl
niri msg action load-config-file
noctalia msg config-reload
```

Keep the source in step with a restored file, or a later apply brings the change
back. Packages are removed separately: the supplement's `packages.omit` leaves a
group package out without uninstalling it, and listing it in `packages.remove`
as well removes it with the next system apply; `packages.remove` alone refuses a
package that a group still installs.

## Remaining differences

- **Palette.** Omarchy installs Tokyo Night by default and ships Matte Black as an
  alternative [O1], [O2]; Shaffy OS reproduces behaviour and geometry, not those
  theme colours. The Matte Black palette remains selectable in Noctalia.
- **Alt+Tab already exists.** Niri's recent-windows switcher has its own default
  bindings, which do not appear in `keybinds.kdl.tmpl`. It is a functional
  equivalent with different switching and previews [N1].
- **Tiled fullscreen** uses Niri's native `toggle-windowed-fullscreen` on
  Super+Ctrl+Alt+F [N2].
- **Universal clipboard is more than three commands.** Omarchy sends
  modifier-specific key events to the focused surface and special-cases
  terminals, and its source describes the trouble a naive `wtype` sender has
  while Super is held [O15]. Check a new application with Super held, in
  terminals, graphical applications and layer-shell text fields.
- **Dwindle and per-workspace layout switching are not Niri features.** Niri uses
  scrolling columns by design; this is a platform boundary, not a missing option.
- **Scratchpad and sticky pop-outs are approximations.** `scratchpad` is a named
  workspace that Super+S shows and hides, and it appears in Niri's workspace list.
  Niri's floating windows belong to their workspace, so Super+O floats a window
  within its workspace and nothing is pinned across workspaces [N3].
- **Picture-in-picture** windows float at 600×338, 20 px from the bottom-right
  corner, without a border and opaque, as in Omarchy [O3]; they do not follow
  across workspaces.
- **Workspaces and groups.** Workspaces are dynamic and per monitor, with no
  global 1–5 indicator policy [O12]; Niri's tabbed columns stand in for Hyprland's
  groups; tiled windows in one column share its width, so square sizing follows
  Niri's constraints.
- **The shell is Noctalia's.** Its panel rendering, tray interaction, notification
  history and timeouts, OSD timing and lock screen are its own. There is no
  dismiss-one notification (Noctalia 5.1 has no command for it), no
  hover-revealed centre status group, CachyOS update badge, webcam overlay while
  recording or list of reminders, and no Tensaku annotation workflow. Omarchy's
  QML shell cannot be loaded as Noctalia configuration or plugins; porting a
  component means implementing it against Noctalia's API.
- **Idle.** Omarchy starts a screensaver after 150 s and locks after 300 s [O13];
  Shaffy OS locks only on request and before suspend.
- **Timer.** The official timer has no reset command, so Super+Ctrl+Shift+R does
  nothing until it gains one; a change that adds it, with a reminder message and a
  deadline that survives a restart, is being proposed upstream.
- **Recorder.** The official recorder stops a recording by signalling every
  `gpu-screen-recorder` process, so do not run another recorder beside it.
- **Zen Browser** keeps its own theme: following the palette would need a browser
  extension, which Shaffy OS does not install.

## Sources

Omarchy reference: the `quattro` branch of `omacom/omarchy` at
[`45748a2812f42e32f915b053caf4074e150e2048`](https://github.com/omacom/omarchy/tree/45748a2812f42e32f915b053caf4074e150e2048),
whose `version` file says `4.0.0.alpha`: a pinned branch snapshot, not a numbered
release. The files compared:

- [O1] `install/user/theme.sh`: the theme chosen at installation.
- [O2] `default/themed/`: the theme templates and shell tokens.
- [O3] `default/hypr/apps/`: application exceptions, including picture-in-picture.
- [O4] `config/`: the terminal configurations.
- [O5] `default/hypr/looknfeel.lua`: gaps, borders, corners and animations.
- [O6] `manual/38-fonts.md`: fonts.
- [O7] `bin/omarchy-theme-set-gnome`: GTK mode and the Yaru icon variant.
- [O8] `default/hypr/bindings/utilities.lua`: menus, panels and notifications.
- [O9] `shell/plugins/`: the shell components, such as the OSD and notifications.
- [O10] `default/hypr/apps/omarchy-shell.lua`: the shell's layer rules.
- [O11] `default/hypr/input.lua`: keyboard repeat, pointer and touchpad.
- [O12] `manual/05-the-top-bar.md`: the top bar.
- [O13] `manual/13-toggles-idle-screensaver.md`: toggles, idle and screensaver.
- [O14] `default/hypr/bindings/tiling.lua`: tiling bindings.
- [O15] `default/hypr/bindings/clipboard.lua`: the universal clipboard.
- [O16] `manual/12-screenshots-recording.md`: screenshots and recording.
- [O17] `manual/09-reminders.md`: reminders.
- [O18] `manual/11-text-extraction-dictation.md`: text extraction and dictation.
- [O19] `default/hypr/windows.lua`: the general window rules, opacity included.

Niri: [N1] recent windows, [N2] fullscreen and maximize, [N3] floating windows,
and for the implementation
[window rules and opacity](https://niri-wm.github.io/niri/Configuration:-Window-Rules.html#opacity),
[input](https://niri-wm.github.io/niri/Configuration:-Input.html) and
[animations with custom shaders](https://niri-wm.github.io/niri/Configuration:-Animations.html).
Hyprland measures [animation speed in 100 ms units](https://wiki.hypr.land/Configuring/Advanced-and-Cool/Animations/),
so Omarchy's 4.1 and 1.49 become 410 and 149 ms.

Noctalia: the API reference is **v5.1.0**, commit
[`c7b9197af77ff22bfb9a83c52a95643a1d90ca86`](https://github.com/noctalia-dev/noctalia/tree/c7b9197af77ff22bfb9a83c52a95643a1d90ca86),
with its [widget actions](https://docs.noctalia.dev/noctalia/bar/actions/),
[surface IPC](https://docs.noctalia.dev/noctalia/ipc/surfaces/) and
[night light](https://docs.noctalia.dev/noctalia/services/night-light/). The
recorder, timer and world clock come from Noctalia's
[official plugins](https://github.com/noctalia-dev/official-plugins), whose
manifests declare the MIT licence; Noctalia fetches and updates them. The Wayland
virtual-keyboard protocol file in `files/desktop-key/` is copied unchanged from
Noctalia and keeps its copyright and licence; the key sender itself is an
independent implementation that uses a full evdev keymap, as Noctalia's own paste
service does.

Yaru: the official release `26.04.5.1ubuntu`, SHA-256
`4e35b71d37aedb44302c811f63d325f4abd7699961f76f130323b361d4c6a7d7`.
`scripts/install-yaru-icons.py ARCHIVE` checks the tarball against that digest,
copies the Yaru and Yaru-yellow assets into `~/.local/share/icons/` and runs the
upstream index generator. Neither apply runs it: run it once per user with the
downloaded release tarball. It skips a folder it installed at this version and
refuses to replace any other existing Yaru folder. No GNOME desktop, GTK theme suite or session package is installed.

`THIRD-PARTY.md` lists every file adapted from these projects with its licence.

[O1]: https://github.com/omacom/omarchy/blob/45748a2812f42e32f915b053caf4074e150e2048/install/user/theme.sh
[O2]: https://github.com/omacom/omarchy/tree/45748a2812f42e32f915b053caf4074e150e2048/default/themed
[O3]: https://github.com/omacom/omarchy/tree/45748a2812f42e32f915b053caf4074e150e2048/default/hypr/apps
[O4]: https://github.com/omacom/omarchy/tree/45748a2812f42e32f915b053caf4074e150e2048/config
[O5]: https://github.com/omacom/omarchy/blob/45748a2812f42e32f915b053caf4074e150e2048/default/hypr/looknfeel.lua
[O6]: https://github.com/omacom/omarchy/blob/45748a2812f42e32f915b053caf4074e150e2048/manual/38-fonts.md
[O7]: https://github.com/omacom/omarchy/blob/45748a2812f42e32f915b053caf4074e150e2048/bin/omarchy-theme-set-gnome
[O8]: https://github.com/omacom/omarchy/blob/45748a2812f42e32f915b053caf4074e150e2048/default/hypr/bindings/utilities.lua
[O9]: https://github.com/omacom/omarchy/tree/45748a2812f42e32f915b053caf4074e150e2048/shell/plugins
[O10]: https://github.com/omacom/omarchy/blob/45748a2812f42e32f915b053caf4074e150e2048/default/hypr/apps/omarchy-shell.lua
[O11]: https://github.com/omacom/omarchy/blob/45748a2812f42e32f915b053caf4074e150e2048/default/hypr/input.lua
[O12]: https://github.com/omacom/omarchy/blob/45748a2812f42e32f915b053caf4074e150e2048/manual/05-the-top-bar.md
[O13]: https://github.com/omacom/omarchy/blob/45748a2812f42e32f915b053caf4074e150e2048/manual/13-toggles-idle-screensaver.md
[O14]: https://github.com/omacom/omarchy/blob/45748a2812f42e32f915b053caf4074e150e2048/default/hypr/bindings/tiling.lua
[O15]: https://github.com/omacom/omarchy/blob/45748a2812f42e32f915b053caf4074e150e2048/default/hypr/bindings/clipboard.lua
[O16]: https://github.com/omacom/omarchy/blob/45748a2812f42e32f915b053caf4074e150e2048/manual/12-screenshots-recording.md
[O17]: https://github.com/omacom/omarchy/blob/45748a2812f42e32f915b053caf4074e150e2048/manual/09-reminders.md
[O18]: https://github.com/omacom/omarchy/blob/45748a2812f42e32f915b053caf4074e150e2048/manual/11-text-extraction-dictation.md
[O19]: https://github.com/omacom/omarchy/blob/45748a2812f42e32f915b053caf4074e150e2048/default/hypr/windows.lua
[N1]: https://niri-wm.github.io/niri/Configuration:-Recent-Windows.html
[N2]: https://niri-wm.github.io/niri/Fullscreen-and-Maximize.html
[N3]: https://niri-wm.github.io/niri/Floating-Windows.html
