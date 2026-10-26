# Desktop setup

Shaffy OS configures Niri as the compositor and Noctalia as the shell, with an
Omarchy-inspired layout and Noctalia's built-in Rosé Pine palette in light mode.
This document covers ownership, personal settings and their override layers, the
bar and launcher, and the everyday shortcuts. Two companions go further:

- [Theme and controls](NOCTALIA-INTEGRATION.md): one palette across the shell,
  Niri and the applications, the searchable desktop controls and the lock screen.
- [Omarchy parity](OMARCHY-IMPLEMENTATION.md): the Omarchy behaviour reproduced on
  Niri, the complete shortcut map, and the differences that remain.

Keyboard layouts and key maps are in [keyboard setup](KEYBOARD-SETUP.md), the
selected applications in [desktop applications](DESKTOP-APPLICATIONS.md), and
tmux with workmux in [workspace tools](WORKSPACE-TOOLS.md).

## Ownership

- Ansible (`site.yml`, `roles/`) owns system configuration, applied with
  `./bin/shaffy apply --system`. The `desktop` role installs the package group in
  `defaults/packages/desktop.yml`: the terminal font and the capture, text
  recognition, sharing, monitor-control and dialog tools.
- chezmoi renders `home/` with the repository's `chezmoi.toml`. It owns Niri's
  modular configuration (`~/.config/niri/config.kdl` and `cfg/`), Noctalia's
  configuration fragments and local templates, the application imports, and the
  helper `~/.local/bin/shaffy-desktop`. The home apply builds the helper's key
  sender, `shaffy-desktop-key`, from `files/desktop-key/` and installs it beside
  it. The home changes only through `./bin/shaffy`; chezmoi's hooks refuse a
  direct `chezmoi apply`.
- Noctalia generates the colour files. chezmoi installs them as create-only
  placeholders, so the first start is valid and later applies keep Noctalia's
  colours.
- Noctalia keeps its own state in `~/.local/state/noctalia/settings.toml`: choices
  made in its settings window, the wallpaper selected on each monitor and the
  lock-screen widget layout. No state, session, secret or clipboard content
  enters Git.
- chezmoi's state database is `~/.local/state/shaffy/chezmoistate.boltdb`.

## Personal settings

The public configuration reads its personal values from the settings: the public
defaults in `defaults/config.yaml`, overridden by the bound supplement (see
[the supplement](SUPPLEMENT.md)). The desktop uses these:

| Setting | Effect | Without a supplement value |
| --- | --- | --- |
| `desktop.displays` | Niri output blocks: `name`, `mode`, `scale` and `position` in logical pixels | Niri configures outputs itself |
| `desktop.keyboard` | XKB `layouts`, `variants` and `options` | Niri follows `localectl` |
| `desktop.wallpaper.directory`, `.image` | The picker's folder, and the default and initial image | `/usr/share/noctalia/assets`, and Noctalia's own wallpaper there, `noctalia-wallpaper.png` |
| `desktop.lock_screen.image` | The lock-screen background | The same image |
| `desktop.browser` | Default handler for web links and HTML, set by the `browser` role | `zen.desktop` |
| `folders.pictures`, `folders.videos` | Screenshots in `<pictures>/Screenshots`, recordings in `<videos>/Recordings` | Installation's XDG pictures and videos directories |
| `folders.downloads` | Tailnet file transfers | Installation's XDG downloads directory |
| `tailnet.login_server` | The Tailnet panel's admin link | No admin link |

The folder names themselves are described in [home folders](HOME-FOLDERS.md). An
example with synthetic values:

```yaml
desktop:
  displays:
    - name: eDP-1
      mode: "2560x1600@60"
      scale: 2
      position: {x: 0, y: 0}
    - name: DP-1
      mode: "2560x1440@60"
      scale: 1
      position: {x: 1280, y: 0}
  keyboard:
    layouts: "us,de"
    variants: "intl,"
  wallpaper:
    directory: "~/Pictures/Wallpapers"
    image: "~/Pictures/Wallpapers/harbour.jpg"
  lock_screen:
    image: "~/Pictures/Wallpapers/night.jpg"
```

## Override layers

Where a setting is not enough, the supplement's `overrides` name native fragments
inside the supplement, relative to its root. They are trusted configuration and
may run commands. Each comes after the public configuration, and renders as an
empty file when unset:

| Application | Setting | Rendered as | Precedence |
| --- | --- | --- | --- |
| Niri | `overrides.niri` | `~/.config/niri/user.kdl` | Included last by `config.kdl`, so its bindings replace public ones of the same key. Niri adds `output` blocks and other multipart sections instead of merging them, so leave outputs to `desktop.displays`. |
| Noctalia | `overrides.noctalia` | `~/.config/noctalia/97-supplement.toml` | After the public fragments, before Noctalia's own runtime files (below). |
| Fish | `overrides.fish` | `~/.config/fish/supplement.fish` | Sourced at the end of `config.fish`, after `conf.d/`, so it can replace functions defined there. |
| Alacritty | `overrides.alacritty` | `~/.config/alacritty/supplement.toml` | The last import: it wins over the generated colours and `shaffy.toml`. |

For example, a supplement that opens another terminal and uses the palette's dark
mode:

```yaml
overrides:
  niri: overrides/niri.kdl
  noctalia: overrides/noctalia.toml
```

```kdl
// overrides/niri.kdl
binds {
    Mod+Return { spawn "foot"; }
}
```

```toml
# overrides/noctalia.toml
[theme]
mode = "dark"
```

Noctalia reads every `*.toml` in `~/.config/noctalia/` in alphabetical order, then
its state file:

1. `90-omarchy.toml`: palette, bar, panels, lock screen and wallpaper.
2. `92-app-themes.toml`: the application colour templates.
3. `94-desktop-controls.toml`: the searchable desktop controls.
4. `95-desktop-apps.toml`: plugins and their launcher entries.
5. `97-supplement.toml`: the supplement's fragment.
6. `99-runtime.toml`: the clock format and battery label that `shaffy-desktop`
   toggles (create-only, then owned by the helper).
7. `config.toml`: unmanaged, if Noctalia or a user created it.
8. `~/.local/state/noctalia/settings.toml`: choices made in Noctalia's settings.

A key that a managed fragment sets and one of the last three sets again silently
takes the runtime value; a palette chosen in Noctalia's settings is the usual
case. `./bin/shaffy check` lists each such key, so it can be moved into a fragment
(the supplement's, for a personal choice) or cleared deliberately in Noctalia.

Personal changes belong in the supplement. Adding, re-adding or merging deployed
files or Noctalia's state back into the public `home/` source copies personal
paths and choices into public files; do not capture a whole state file.

## Personal files

Fonts, pictures and launchers come from the supplement's `assets`: each key is a
target under `~/`, each value a file inside the supplement, or `{file, executable,
private}`. The home apply copies them, checked against their SHA-256, and refuses
when a real folder or a link is in the way.

```yaml
assets:
  "~/.local/share/fonts/Example-Regular.otf": assets/fonts/Example-Regular.otf
  "~/Pictures/Wallpapers/harbour.jpg": assets/wallpapers/harbour.jpg
```

A large synchronized collection, such as a wallpaper folder from a Nextcloud
account, stays with its sync client: point `desktop.wallpaper.directory` at the
synchronized folder. The `nextcloud` integration manages only the client's login
autostart entry; accounts, folder connections and pause states stay the client's.
Choosing a wallpaper never changes the image, but a sync client carries local
deletions to the server: remove the folder connection in the client before
deleting the folder.

## Commands

From the checkout, as the desktop user:

```sh
./bin/shaffy check          # validate the settings and a sandboxed render
./bin/shaffy diff           # preview what an apply would change in the home
./bin/shaffy apply --home   # snapshot, apply, validate, reload Noctalia
./bin/shaffy status         # binding, unfinished work and drift
```

A new installation first runs the README's bootstrap and binds its settings with
`./bin/shaffy supplement attach --supplement PATH` (or `--none`). Edit the
supplement or the files under `home/`, run the commands, then commit the reviewed
change. `private_`, `dot_`, `create_` and `empty_` in source names are chezmoi
attributes, not deployed names.

## Appearance and input

- Noctalia's built-in Rosé Pine palette in light mode; Niri's borders, GTK, Qt,
  Alacritty, btop and Neovim follow it. The custom Matte Black palette
  (`palettes/OmarchyMatteBlack.json`) remains selectable in Noctalia.
- JetBrainsMono Nerd Font for the shell, the terminal and the desktop monospace
  family.
- Niri: 10 px gaps, square corners, 2 px borders in the palette's accent, no
  shadows or window blur. New columns take 0.49 of the width in Niri's scrolling
  layout. A named workspace, `scratchpad`, backs Super+S.
- Keyboard repeat after 250 ms at 40 characters per second, Num Lock on at start.
  Focus follows the mouse and the pointer warps to the focused window. The
  touchpad has tap-to-click, clickfinger and a scroll factor of 0.4.
- Screenshots go to `<pictures>/Screenshots/` and to the clipboard.

## Bar, launcher and control centre

The bar is a flat, opaque, square 26 px bar at the top:

| Position | Widgets |
| --- | --- |
| Left | Launcher (click: application search; right-click: terminal), workspaces with minimal numeric labels |
| Centre | Screen recorder (hidden while idle), timer (an hourglass while idle), clock (weekday and time), keyboard layout |
| Right | Privacy (only while capturing), notifications (only with unread items), tray drawer, Bluetooth (only with a connected device), network, volume, battery, control centre |

- The clock's tooltip shows the full date and ISO week. Left-click opens the
  calendar, right-click cycles the clock format, middle-click opens world clocks.
- Right-click on volume mutes, on Bluetooth toggles it, on the battery toggles its
  percentage label, and on the control centre opens the session menu.
- The tray is one drawer button with the active applications inside (three
  columns, passive items hidden). Network and volume show icons with their normal
  tooltips.
- The control centre has a labelled sidebar and shortcut labels, with tabs for
  audio, display (brightness), network, Bluetooth, power, calendar,
  notifications, media and system, plus Noctalia's quick toggles. The calendar
  shows ISO week numbers; its events card is hidden.
- **Super+Space** opens Noctalia's native launcher, ready to type. Its compact
  list searches applications, open windows and the configured desktop controls,
  without categories or origin markers. This needs no additional plugin.
  **Super+Alt+Space** opens the nested main menu. The launcher, clipboard history,
  wallpaper picker and session menu float in the centre; the OSD sits at the
  bottom centre.
- At most three notifications show at once; more queue, and history keeps them
  all. There is no dock.

### Plugins in the launcher

The plugins (Tailnet, Syncthing, Phone Connect, Remmina, and the
screen recorder, timer and world clock) come from Noctalia's official and
community plugin sources, which Noctalia fetches and keeps updated. Only the
recorder and the timer sit on the bar, and the world clocks open from the clock.
Reach the others from the launcher, **Super+Space**:

| Tool | Launcher access |
| --- | --- |
| Tailnet panel | Search `Tailnet`, or `/tailnet` |
| Tailnet hosts | `/tn` |
| Syncthing panel | Search `Syncthing`, or `/syncthing` |
| Syncthing folders | `/st` |
| Phone Connect | Search `Phone Connect` (see [KDE Connect](KDE-CONNECT.md)) |
| Saved Remmina connections | `/rmn` |

The two panel entries and the [desktop controls](NOCTALIA-INTEGRATION.md#searchable-controls)
use Noctalia's configurable dmenu providers with fixed commands; typed text is
never interpolated into a shell. Noctalia 5.1 has no generic panel provider (panel
search reached upstream's main branch after that release, in
[PR #4442](https://github.com/noctalia-dev/noctalia/pull/4442)), and its
control-centre shortcut list accepts only built-in types, so plugin panels cannot
be placed there by configuration.

## Wallpaper and lock screen

Open the picker with **Super+Ctrl+Space**, `/background` in the launcher, or
Style, Wallpaper in the main menu; `/wall` in the launcher searches the images
directly. The picker shows the images in `desktop.wallpaper.directory`; automatic
rotation is off and a change fades over 300 ms. Choosing an image only selects
it. Noctalia saves the choice per monitor in its state, which loads last, so later
applies keep it. The managed fragment sets only the folder and the default image,
which is also the initial selection.

The lock screen shows `desktop.lock_screen.image`. As in Omarchy, the session
locks after five minutes without input: the screen dims for three seconds
first, and any input cancels the lock. The screens turn off 30 seconds after
any lock. Applications that inhibit idle, such as a playing video, keep the
session awake, and **Super+Ctrl+I** switches caffeine on or off, which pauses
the idle lock (also in the main menu under Style, and in the control centre).
**Super+Alt+L** or **Super+Ctrl+L** locks at once, and Noctalia locks before
suspend. Idle never suspends the machine.

The timings are `[idle.behavior.lock]` and `[idle.behavior.screen-off]` in
`90-omarchy.toml`. A supplement changes them in its Noctalia fragment
(`overrides.noctalia`):

```toml
[idle.behavior.lock]
timeout = 600        # or enabled = false to lock only by hand
```

Omarchy's screensaver is not reproduced. The lock-screen widgets and their
layout are Noctalia's own.

## Everyday shortcuts

Super is the Windows key. **Super+K** opens Niri's overlay of the main bindings
and those given a title; `home/dot_config/niri/cfg/keybinds.kdl.tmpl` is the
authoritative list, and
[Omarchy parity](OMARCHY-IMPLEMENTATION.md#shortcuts) covers the Omarchy
additions (clipboard, capture, panels, notifications, timer, toggles).

| Shortcut | Action |
| --- | --- |
| Super+Space / Super+Ctrl+Return | Noctalia launcher: type to search applications, windows and desktop controls |
| Super+Alt+Space | Main menu: applications, capture, style, controls, tools, system |
| Super+Return | Alacritty |
| Super+Alt+Return | Alacritty attached to the tmux session `Work` |
| Super+B / Super+Shift+B / Super+Shift+Return | Zen Browser |
| Super+Shift+Alt+B | Private Zen Browser window |
| Super+E | File manager (Nautilus) |
| Super+Shift+E / Super+Shift+N | Neovim (LazyVim) in Alacritty |
| Super+Shift+M | Launch or focus Spotify |
| Super+Q / Super+W | Close window |
| Super+arrows, Super+H/J/L | Focus column or window |
| Super+Shift+arrows, Super+Ctrl+arrows, Super+Ctrl+H/J/K | Move column or window |
| Super+Home / Super+End | First or last column |
| Super+Ctrl+Alt+arrows | Focus another monitor |
| Super+Shift+Ctrl+arrows | Move column to another monitor |
| Super+1–9 / Super+0 | Workspace 1–10 when it exists; the current number returns to the previous workspace |
| Super+Shift+1–9 / Super+Shift+0 | Move window to that workspace |
| Super+Tab / Super+Shift+Tab | Next or previous workspace |
| Super+Ctrl+Tab | Previously focused workspace |
| Super+R / Super+Shift+R | Cycle preset column widths |
| Super+Minus / Super+Equal | Column width −10% / +10% |
| Super+Shift+Minus / Super+Shift+Equal | Window height −10% / +10% |
| Super+F / Super+M / Super+Shift+F | Maximize column / maximize window to the edges / fullscreen |
| Super+T | Floating or tiled |
| Super+Shift+V | Switch focus between floating and tiled windows |
| Super+[ / Super+] | Move the window into or out of the neighbouring column |
| Super+Alt+L / Super+Ctrl+L | Lock |
| Super+Ctrl+I | Stay awake: pause or resume the idle lock |
| Super+Shift+Q / Super+Escape / Ctrl+Alt+Delete | Session menu |
| Super+Shift+S | Noctalia settings |
| Super+Ctrl+V | Clipboard history |
| Super+Ctrl+Space | Wallpaper and theme picker |
| Super+Shift+Space | Show or hide the bar |
| Super+Alt+K | Next keyboard layout |
| Print / Ctrl+Print | Screenshot of a region with annotation / of the monitor |
| Ctrl+Shift+1 / 2 / 3 | Screenshot of a region / the monitor / the window |
| Super+Shift+P | Turn the monitors off |
| Super+K / Super+Shift+Escape | Shortcut overlay |
| Alt+volume or brightness keys | Change in 1% steps |

Workspaces are Niri's dynamic, per-monitor workspaces: the number keys reach
existing workspaces and do not create ten permanent global ones.

## Validation and recovery

`./bin/shaffy check` renders the whole home for a synthetic user in a sandbox,
with the supplement's fragments, and validates Niri, Noctalia, Fish, tmux and
every TOML, YAML and JSON file; it also lists the Noctalia keys that runtime files
override. `./bin/shaffy apply --home` takes a snapper pre snapshot of `/home`
(configuration `home`), copies every file it will change into
`~/.local/state/shaffy/runs/<op>/before/`, applies, validates the real home's
Niri, Noctalia and Alacritty configuration, takes the post snapshot, and then, in
a running Niri session, has Noctalia reload its configuration and apply its
templates. A failed validation leaves the operation unfinished;
`./bin/shaffy status` names its snapshot and copies. The protocol is described in
[the architecture](ARCHITECTURE.md).

To undo a change, revert it in the supplement or the source, then run
`./bin/shaffy diff` and `./bin/shaffy apply --home`. To restore individual files
from before an apply, compare the operation's pair and undo only those paths, or
copy them back from the run's `before/` folder:

```sh
snapper -c home status PRE..POST
snapper -c home undochange PRE..POST ~/.config/niri/cfg/keybinds.kdl
noctalia msg config-reload
```

Keep the source in step with any file restored by hand, or the next apply brings
the change back. Never roll back all of `/home`, which would also undo unrelated
work. A Noctalia key that `check` reports is cleared in Noctalia's settings, not
by editing its state file while it runs.

References: [Noctalia configuration layers](https://docs.noctalia.dev/noctalia/configuration/),
[widget settings](https://docs.noctalia.dev/noctalia/bar/widgets/),
[widget actions](https://docs.noctalia.dev/noctalia/bar/actions/),
[launcher configuration](https://docs.noctalia.dev/noctalia/launcher/),
[built-in palettes](https://docs.noctalia.dev/noctalia/theming/),
[Niri configuration](https://niri-wm.github.io/niri/Configuration:-Introduction.html),
[chezmoi's file attributes](https://www.chezmoi.io/reference/source-state-attributes/).
The Matte Black palette is Omarchy's, under its MIT licence; `THIRD-PARTY.md`
lists it with the other adapted files.
