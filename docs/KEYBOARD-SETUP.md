# Keyboard layouts and key maps

Two supplement settings shape the keyboard: `desktop.keyboard` chooses Niri's
layouts, and `overrides.keyboard_hwdb` remaps keys of a particular keyboard in
systemd's hardware database. Without either, Niri follows the system's
`localectl` settings and no key is remapped.

The public role owns installation, permissions and reloading of the fragment;
the supplement owns its hardware match and scancodes. General desktop shortcuts
and repeat speed are part of the public desktop.

## Layouts

`desktop.keyboard` sets Niri's XKB `layouts` (required), `variants` and
`options`. `layouts` and `variants` are comma-separated lists that line up, one
variant (possibly empty) per layout; `options` is a comma-separated list of XKB
options:

```yaml
desktop:
  keyboard:
    layouts: "us,de"
    variants: "intl,"
    options: "compose:menu"
```

The home apply renders them into `~/.config/niri/cfg/input.kdl`. **Super+Alt+K**
switches to the next layout (`switch-layout "next"`), and Noctalia shows the
active layout beside the clock. Niri keeps one active layout for all keyboards.
Without the setting the `xkb` block stays empty and Niri reads the layout set
with `localectl set-x11-keymap`.

## Key maps

`overrides.keyboard_hwdb` names a file in the supplement with entries for
systemd's hardware database. It remaps scancodes of the keyboards an entry
matches, in the kernel, without a remapper daemon. This example makes Caps Lock
act as Super on one USB keyboard only, matched by its vendor and product and by
the event capabilities of its keyboard interface:

```yaml
overrides:
  keyboard_hwdb: overrides/keyboard.hwdb
```

```text
evdev:input:b0003v1234p5678*-e0,1,4,11,14,*
 KEYBOARD_KEY_70039=leftmeta
```

Find a keyboard's vendor and product with `udevadm info` on its
`/dev/input/by-id/…-event-kbd` node, and a key's scancode with `evtest`, which
prints the `MSC_SCAN` value of each key pressed while it runs. The installed
`/usr/lib/udev/hwdb.d/60-keyboard.hwdb` documents the match syntax.

The fragment has system scope: it is read as text of at most 65,536 characters
when the settings are loaded, and the system apply takes it from its verified
copy of the settings. `./bin/shaffy data` shows it; `./bin/shaffy diff` covers
the home only. The `keyboard` role, part of every system apply, installs it:

```sh
./bin/shaffy apply --system --tags keyboard
```

The role writes `/etc/udev/hwdb.d/90-shaffy-keyboard.hwdb` (root, mode 0644)
and, when the file changed, rebuilds the database with `systemd-hwdb update`
and has udev re-evaluate the input devices, so a connected keyboard takes the
map at once and one plugged in later takes it too. Without the setting the role
removes the file and rebuilds the database. Check the result with
`udevadm info` on the keyboard's node, which lists the `KEYBOARD_KEY_…`
properties it received, and with `evtest`.

## Recovery

Keep another keyboard, such as a laptop's own, as recovery input while changing
a map. To undo a map, remove `overrides.keyboard_hwdb` from the supplement (or
correct the fragment) and apply the `keyboard` tag again. The role removes or
replaces the file and rebuilds the database, but a keyboard that is already
remapped keeps a key an entry no longer sets until it is unplugged and plugged
in again, because udev applies hardware-database keys to a device and never
resets them. A reboot has the same effect for a built-in keyboard.

References: the installed `/usr/lib/udev/hwdb.d/60-keyboard.hwdb` and
[systemd's keyboard database instructions](https://github.com/systemd/systemd/blob/main/hwdb.d/60-keyboard.hwdb),
plus [Niri's input configuration](https://niri-wm.github.io/niri/Configuration:-Input.html).
