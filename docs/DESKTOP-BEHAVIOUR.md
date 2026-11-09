# Desktop behaviour

This is what the Shaffy OS desktop does on Niri and Noctalia: how windows look
and move, the terminal, the bar and panels, capture, the clipboard and every
shortcut, with the packages and files behind them and the limits of the
platform. Its layout, controls and shortcuts take Omarchy's Quattro desktop as
their design reference (see [sources](#sources)); its colours are Noctalia's
built-in Rosé Pine palette in light mode. Ownership and personal settings are in
[desktop setup](DESKTOP-SETUP.md), the palette in
[theme and controls](NOCTALIA-INTEGRATION.md).

## Configuration

| Area | Implementation |
| --- | --- |
| Window opacity | General windows 0.985 active and 0.96 inactive; browsers 1.0 and 0.985. Video players, image viewers, remote desktops, virtual machines, game windows and picture-in-picture stay opaque. Alacritty's own background stays opaque, so the two opacities do not multiply. |
| Borders and geometry | Square corners, no shadows or blur, 2 px borders: the palette's primary colour when active, a translucent grey (`#595959aa`) when inactive. Borders do not fill the window background. Gaps are 10 logical pixels; new columns take 0.49 of the width. |
| Picture-in-picture | Floats at 600×338, 20 px from the bottom-right corner, without a border. |
| Animations | Workspace switching is not animated. Windows scale between 87% and 100%, entering over 410 ms on an ease-out-quint curve and leaving over 149 ms linearly; they fade with the scaling, in the same shader. |
| Terminal | Alacritty with JetBrainsMono Nerd Font 9, 14×14 padding, no decorations and its default steady block cursor. Shift+Return and Alt+Shift+Return send distinct CSI-u sequences; Ctrl+Insert and Shift+Insert copy and paste. |
| Typography | JetBrainsMono Nerd Font for the shell and as the generic monospace family; the document font substitutions are separate. |
| Bar and panels | 26 logical pixels high with a 12/14 font scale, square solid panels, and a 50% dark backdrop without blur. The recorder indicator hides while idle; the tray is a drawer, and Bluetooth and notifications show only when relevant. |
| OSD | Bottom centre, 67 logical pixels from the edge, opacity 0.97; it hides after Noctalia's default delay (`osd.hide_delay_ms`). |
| Icons | Yaru and Yaru-yellow from a checked upstream release, installed per user by `scripts/install-yaru-icons.py` (see [sources](#sources)). The GTK settings and GSettings both select Yaru-yellow. |
| Applications | Noctalia's btop template, and the Niri, Alacritty, GTK, Qt and Neovim colours in [theme and controls](NOCTALIA-INTEGRATION.md). Super+Alt+Return opens tmux; tmux and workmux are described in [workspace tools](WORKSPACE-TOOLS.md). |
| Input | Repeat after 250 ms at 40 per second. Focus follows the pointer onto windows already fully on screen, and the pointer warps to the focused window. The touchpad has tap-to-click, clickfinger, natural scrolling off and a scroll factor of 0.4 in every window: a per-window factor in Niri would also change the mouse wheel. |
| Idle | The session locks after five minutes without input, after a three-second dim that any input cancels, and the screens turn off 30 s after any lock. There is no screensaver. Caffeine (Super+Ctrl+I) pauses the idle lock; [wallpaper and lock screen](DESKTOP-SETUP.md#wallpaper-and-lock-screen) says how a supplement changes the timings. |
| Launcher and main menu | Super+Space opens Noctalia's native compact launcher, ready to type and search applications, open windows and configured controls; no additional plugin is needed. Super+Alt+Space opens the nested main menu: Applications, Capture, Style, Controls, Tools, System. |
| Clock and gestures | Clock: left-click calendar, right-click cycles the format, middle-click world clocks. The launcher button opens application search; its right-click opens a terminal. Right-click on volume mutes, on Bluetooth toggles it, on the battery toggles its label. |
| Capture | Print selects a region in Noctalia and opens its annotation, saving to a file and the clipboard. The capture menu adds recording, a colour picker, text recognition in every installed language, and QR and barcode reading; decoded content is copied, and no link is opened. |
| Private windows | Password managers (Bitwarden, 1Password, KeePassXC, Proton Pass, Secrets, Seahorse) and the keyring and pinentry prompts are blocked out of screen shares, recordings and screenshots with Niri's `block-out-from "screen-capture"`. |
| Timer | Noctalia's official timer plugin: one countdown, with a bar widget, a panel and a desktop widget. |
| Recording | Noctalia's official recorder plugin with `gpu-screen-recorder`: the portal chooses the source; H.264 and AAC in MP4 under `<videos>/Recordings`; system audio by default, with the microphone, both or none in the plugin's settings; replay off. Alt+Print or the capture menu starts and stops it; the bar indicator, shown only while recording, stops it. |
| Utilities | LocalSend, and a conversion menu to H.264/AAC MP4, PNG, JPEG or WebP. A conversion writes `NAME.converted.EXT` beside its source, never changes the source, and refuses when that file exists. |
| Clipboard | Super+C, X and V send fixed copy, cut and paste chords to the focused window through `shaffy-desktop-key`, a small Wayland virtual-keyboard client with a full evdev keymap; terminals get the Insert chords. It needs no root or device permissions and never reads the clipboard. |

## Shortcuts

`Super` means the Windows key. `home/dot_config/niri/cfg/keybinds.kdl.tmpl`
defines every shortcut below; **Super+K** opens Niri's overlay of the main
ones and those given a title. A supplement rebinds keys in its Niri fragment
([override layers](DESKTOP-SETUP.md#override-layers)).

### Applications and menus

| Shortcut | Result |
| --- | --- |
| Super+Space / Super+Ctrl+Return | Launcher: type to search applications, open windows and desktop controls |
| Super+Alt+Space | Main menu: Applications, Capture, Style, Controls, Tools, System |
| Super+Return | Alacritty |
| Super+Alt+Return | Alacritty attached to the tmux session `Work` |
| Super+B / Super+Shift+B / Super+Shift+Return | Zen Browser |
| Super+Shift+Alt+B | Private Zen Browser window |
| Super+E | File manager (Nautilus) |
| Super+Shift+E / Super+Shift+N | Neovim (LazyVim) in Alacritty |
| Super+Shift+M | Launch or focus Spotify |
| Super+Ctrl+T | btop in a floating window |
| Super+Ctrl+S / Super+Ctrl+Period | LocalSend / conversion menu |
| Super+Ctrl+V / Super+Ctrl+E | Clipboard history / emoji |
| Super+Ctrl+Space | Wallpaper picker |
| Super+Shift+S / Super+Ctrl+Shift+Space | Noctalia's settings, for the theme and the shell (the first also closes them) |
| Super+Shift+Space | Show or hide the bar |

### Windows and columns

| Shortcut | Result |
| --- | --- |
| Super+Q / Super+W | Close the window |
| Super+arrows, Super+H/J/L | Focus the column to the left or right, or the window above or below |
| Super+Shift+arrows, Super+Ctrl+arrows, Super+Ctrl+H/J/K | Move the column or window |
| Super+Home / Super+End | Focus the first or last column |
| Super+Ctrl+Home / Super+Ctrl+End | Move the column to the first or last place |
| Super+[ / Super+], Super+Alt+Left/Right | Move the window into or out of the neighbouring column |
| Super+Alt+Up/Down | Reorder the windows within the column |
| Super+Ctrl+Alt+Comma | Take the window on the right into the column |
| Super+Period / Super+Alt+G | Expel the column's bottom window |
| Super+G / Super+Alt+W | Show the column as tabs, or as a stack again |
| Super+R / Super+Shift+R | Cycle the preset column widths forwards / backwards |
| Super+Minus / Super+Equal | Column width −10% / +10% |
| Super+Shift+Minus / Super+Shift+Equal | Window height −10% / +10% |
| Super+Alt+R / Super+Alt+Shift+R | Reset / cycle the window height |
| Super+Alt+C / Super+Ctrl+Alt+C | Centre the column / the visible columns |
| Super+F / Super+Ctrl+F | Maximize the column / expand it to the free width |
| Super+M | Maximize the window to the screen edges |
| Super+Shift+F / Super+Ctrl+Alt+F | Fullscreen / fullscreen inside the existing tile |
| Super+T / Super+O | Float the window, or return it to tiling |
| Super+Shift+V | Switch focus between floating and tiled windows |
| Super+Alt+O | Niri's overview |
| Super+Backspace | Toggle the focused window's opacity rules |
| Super+Shift+Backspace | Toggle gaps and borders |
| Super+Ctrl+Backspace | Toggle a square window size, restoring the previous one |

Alt+Tab is Niri's recent-windows switcher [N1]; its bindings are Niri's
defaults, so they are not in `keybinds.kdl.tmpl`. Fullscreen inside the tile is
Niri's windowed fullscreen [N2].

### Workspaces and monitors

| Shortcut | Result |
| --- | --- |
| Super+1–9 / Super+0 | Workspace 1–10 when it exists; the current number returns to the previous workspace |
| Super+Shift+1–9 / Super+Shift+0 | Move the window to that workspace |
| Super+Tab / Super+Shift+Tab | Next or previous workspace |
| Super+Ctrl+Tab | Previously focused workspace |
| Super+mouse wheel / Super+Ctrl+mouse wheel | Next or previous workspace / move the column there |
| Super+Shift+mouse wheel, or Super with horizontal scrolling | Focus the column to the left or right; with Ctrl, move it |
| Super+S / Super+Grave | Show or hide the `scratchpad` workspace, opening an Alacritty there when none is open |
| Super+Ctrl+Alt+arrows | Focus another monitor |
| Super+Shift+Ctrl+arrows | Move the column to another monitor |
| Super+Shift+P | Turn the monitors off |

### Panels, notifications and the session

| Shortcut | Result |
| --- | --- |
| Super+Ctrl+A/W/B/D/P | Audio / network / Bluetooth / display / power |
| Super+Ctrl+Alt+D | Calendar |
| Super+Ctrl+1…9 | Fixed panel map: audio, network, Bluetooth, display, power, calendar, notifications, media, system |
| Super+Ctrl+R / Super+Ctrl+Alt+R | Timer panel |
| Super+Ctrl+Shift+R | Reset the countdown (see [limitations](#limitations)) |
| Super+Shift+Comma | Dismiss the visible notifications, keeping their history |
| Super+Ctrl+Comma / Super+Alt+Comma | Do Not Disturb / open the latest notification |
| Super+Comma / Super+Shift+Alt+Comma | Notification history |
| Super+Ctrl+N | Night light at 4000 K on demand; the next press restores normal output |
| Super+Ctrl+I | Stay awake: caffeine pauses the idle lock until pressed again |
| Super+Alt+K | Next keyboard layout |
| Super+Alt+L / Super+Ctrl+L | Lock |
| Super+Shift+Q / Super+Escape / Ctrl+Alt+Delete | Session menu |
| Super+K / Super+Shift+Escape | Shortcut overlay |
| Ctrl+Alt+Escape | Toggle the keyboard-shortcut inhibitor of an application that captures the keys |

### Clipboard and capture

| Shortcut | Result |
| --- | --- |
| Super+C / X / V | Copy / cut / paste; in terminals, copy and paste use the Insert chords |
| Super+Ctrl+C | Capture menu |
| Print / Ctrl+Print | Screenshot of a region, with annotation / of the monitor |
| Ctrl+Shift+1 / 2 / 3 | Screenshot of a region / the monitor / the window |
| Alt+Print | Start or stop recording |
| Super+Print / Super+Ctrl+Print | Colour picker / text recognition |

### Hardware keys

| Shortcut | Result |
| --- | --- |
| Volume, microphone, media and brightness keys | Work as labelled, on the lock screen too |
| Alt+volume or brightness keys | Change in 1% steps |

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

LocalSend finds other devices and receives from them on port 53317, over UDP and
TCP, which ufw's default incoming policy drops. The `localsend` role opens it
only for the interface and network named in the supplement's `localsend`
settings (see [Supplement](SUPPLEMENT.md#localsend)); without them LocalSend can
only send.

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

`./bin/shaffy apply --home` applies the desktop configuration; validation and
recovery are in [desktop setup](DESKTOP-SETUP.md#validation-and-recovery). Its
preflight builds `shaffy-desktop-key` from `files/desktop-key/` in
`~/.cache/shaffy/build/desktop-key/`, never under the checkout, and the apply
installs the key sender into `~/.local/bin/` when the build differs. Noctalia
fetches and updates the plugins itself; the apply neither installs nor restarts
them.

`~/.config/niri/runtime-overrides.kdl` and `~/.config/noctalia/99-runtime.toml`
are create-only: the apply creates each once, and `shaffy-desktop` owns it
afterwards (the gaps-and-borders toggle; the clock format and battery label). The
helper writes them atomically with mode 0644 and keeps its toggle state private
(mode 0600) in `~/.local/state/shaffy/desktop-controls/`.

## Limitations

- **Columns only.** Niri tiles in scrolling columns by design and has no dwindle
  or split layout, so Super+H/J/L only move the focus. Groups are tabbed
  columns, and tiled windows in one column share its width, so a square window
  size stays within what the column allows.
- **The scratchpad is a workspace.** `scratchpad` is a named workspace that
  Super+S shows and hides, and it appears in Niri's workspace list. Floating
  windows belong to their workspace [N3], so Super+O floats a window within its
  workspace, and neither it nor a picture-in-picture window follows to another
  workspace.
- **Workspaces are per monitor.** They are Niri's dynamic workspaces: the number
  keys reach existing workspaces and do not create ten permanent global ones.
- **The universal clipboard sends key chords.** Super+C, X and V reach the
  focused window as chords pressed while Super is still held. Check them with
  Super held in each new application, in terminals, graphical applications and
  layer-shell text fields.
- **The shell is Noctalia's.** Its panel rendering, tray interaction,
  notification history and timeouts, OSD timing and lock screen are its own, and
  a new shell component is written against Noctalia's plugin API. Noctalia has no
  command to dismiss a single notification (`notification-clear-active`
  dismisses all visible ones), so Super+Comma opens the history instead.
- **Timer.** The official timer has no reset command, so Super+Ctrl+Shift+R does
  nothing until it gains one; a change that adds it, with a reminder message and a
  deadline that survives a restart, is being proposed upstream.
- **Recorder.** The official recorder stops a recording by signalling every
  `gpu-screen-recorder` process, so do not run another recorder beside it.
- **Zen Browser** keeps its own theme: following the palette would need a browser
  extension, which Shaffy OS does not install.

## Sources

The design reference is Omarchy, David Heinemeier Hansson's Hyprland desktop:
the `quattro` branch of `omacom/omarchy` at
[`45748a2812f42e32f915b053caf4074e150e2048`](https://github.com/omacom/omarchy/tree/45748a2812f42e32f915b053caf4074e150e2048),
whose `version` file says `4.0.0.alpha`: a pinned branch snapshot, not a numbered
release. The layout, timings, controls and shortcuts follow these files in it:

- `default/hypr/looknfeel.lua`: gaps, borders, corners and animations.
- `default/hypr/windows.lua` and `default/hypr/apps/`: window opacity and the
  application exceptions, picture-in-picture and the shell's layer rules
  included.
- `default/hypr/input.lua`: keyboard repeat, pointer and touchpad.
- `default/hypr/bindings/`: the tiling bindings, the menus, panels and
  notifications, and the universal clipboard, whose `clipboard.lua` describes
  the trouble a plain `wtype` sender has while Super is held.
- `shell/plugins/`: the shell components, such as the OSD and notifications.
- `config/`: the terminal configurations.
- `bin/omarchy-theme-set-gnome`: GTK mode and the Yaru icon variant.
- `manual/`: fonts, the top bar, idle, screenshots and recording, and text
  extraction.

Hyprland measures [animation speed in 100 ms units](https://wiki.hypr.land/Configuring/Advanced-and-Cool/Animations/),
so the reference's 4.1 and 1.49 become 410 and 149 ms.

Niri: [N1] recent windows, [N2] fullscreen and maximize, [N3] floating windows,
and for the implementation
[window rules and opacity](https://niri-wm.github.io/niri/Configuration:-Window-Rules.html#opacity),
[input](https://niri-wm.github.io/niri/Configuration:-Input.html) and
[animations with custom shaders](https://niri-wm.github.io/niri/Configuration:-Animations.html).

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

Yaru: the official release `26.10.3ubuntu`, SHA-256
`2418ac22adafc4e7027375f87f4c93d7e86215d125c62c37f97219e877604619`.
`scripts/install-yaru-icons.py ARCHIVE` checks the tarball against that digest,
copies the Yaru and Yaru-yellow assets into `~/.local/share/icons/` and runs the
upstream index generator. Neither apply runs it: run it once per user with the
downloaded release tarball. It skips a folder it installed at this version and
refuses to replace any other existing Yaru folder. No GNOME desktop, GTK theme suite or session package is installed.

`THIRD-PARTY.md` lists every file adapted from these projects with its licence.

[N1]: https://niri-wm.github.io/niri/Configuration:-Recent-Windows.html
[N2]: https://niri-wm.github.io/niri/Fullscreen-and-Maximize.html
[N3]: https://niri-wm.github.io/niri/Floating-Windows.html
