# KDE Connect

KDE Connect links an Android phone to the desktop. The `kde_connect` role
installs `kdeconnect`, `sshfs` for browsing the phone's files and `kdialog` for
its file chooser (`defaults/packages/kde_connect.yml`). The daemon starts at
login through the package's own autostart entry. Pair the phone from its KDE
Connect app; the pairing and the device identity in `~/.config/kdeconnect` stay
private and outside both repositories.

## Noctalia panel

Phone Connect (`icefish/phone-connect`) comes from Noctalia's
[community plugin source](https://github.com/noctalia-dev/community-plugins),
which Noctalia installs and updates. `~/.config/noctalia/95-desktop-apps.toml`
enables it and sets its options: a refresh every 30 seconds, the battery as a
percentage, the clipboard action and the attached panel placement. It is not on
the bar; search **Phone Connect** in the launcher, or open the panel with:

```sh
noctalia msg panel-toggle icefish/phone-connect:details
```

The panel shows the phone's battery and connection, and offers ring, ping,
clipboard transfer, sharing, SMS, media control and browsing the phone's files.
Android grants each permission in the phone's KDE Connect app; file browsing
needs a folder chosen there.

To leave the panel out, set `enabled` under `[plugins]` in the supplement's
Noctalia fragment (`overrides.noctalia`) to the list without
`icefish/phone-connect`; the supplement's fragment loads after the public one.

## Reapply

```sh
./bin/shaffy apply --system --tags kde_connect   # packages and the firewall rule
./bin/shaffy apply --home                        # the Noctalia fragment and launcher entry
```

The home apply reloads a running Noctalia.

## Firewall

KDE Connect discovers devices and connects on TCP and UDP ports 1714 to 1764,
the range in the package's UFW application profile
(`/etc/ufw/applications.d/kdeconnect`) and in
[KDE's documentation](https://userbase.kde.org/KDEConnect#Troubleshooting).
UFW's default incoming policy drops that traffic. The role opens it only for the
network the supplement names:

```yaml
kde_connect:
  interface: wlan0
  subnet: 192.0.2.0/24
```

With these settings the role requires an active UFW, then adds two rules, one
for TCP and one for UDP, that allow ports 1714 to 1764 arriving on that
interface from that subnet, with the comment `shaffy KDE Connect`. An identical
rule with another comment is updated in place. UFW's default policy and every
other rule stay as they are. Without `kde_connect` settings the role installs
the packages and adds no rule.

The rule matches an interface and a subnet, not a Wi-Fi network name (SSID):
another network that uses the same subnet on that interface matches it too.

## Recovery

Removing the `kde_connect` settings stops later applies from adding the rules,
but does not delete them. Delete the two rules by number, highest first, since
the numbers shift after each deletion:

```sh
sudo ufw status numbered    # the two rules with the comment "shaffy KDE Connect"
sudo ufw delete NUMBER
```

Do not reset the firewall; that also removes every other rule. To stop using the
panel, leave it out as described above and apply the home.
