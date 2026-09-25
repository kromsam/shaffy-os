# Unlock the keyring with the startup password

With `login.autologin: true` in the supplement, the `login` role lets SDDM start
Niri for the target user right after the disk is unlocked, and the encrypted
GNOME keyring unlocks with that same password, so the password is typed once per
boot. This needs:

- `/` on an encrypted device. Automatic login skips the password, so only the
  disk encryption keeps the desktop closed; the role refuses without it.
  Shaffy OS does not encrypt disks.
- A disk that systemd-cryptsetup unlocks with a passphrase. During boot it
  stores the passphrase in the kernel keyring as `cryptsetup`, where the
  handoff below finds it. Without it, Niri still starts, but the keyring stays
  locked and asks for its password when an application first needs it.
- The account's keyring password equal to the disk password.

Without the setting, or with `login.autologin: false`, SDDM keeps its normal
password login.

## What the login role does

`./bin/shaffy apply --system --tags login`, part of every system apply. With
`login.autologin: true`:

- SDDM logs the target user into `niri.desktop` automatically
  (`/etc/sddm.conf.d/90-shaffy-autologin.conf`, rendered for the verified target
  user, with no credentials in it).
- Automatic login supplies no password, so the optional `pam_systemd_loadkey.so`
  line in `/etc/pam.d/sddm-autologin` fetches the short-lived `cryptsetup`
  password cache and hands it to the packaged, optional GNOME Keyring hook.
- SDDM gets `KeyringMode=inherit` (`sddm.service.d/20-shaffy-keyring.conf`) so it
  can read that cache.

The role first checks, without writing, that Niri's session exists, the target
account exists, both PAM modules are installed, the packaged automatic-login
stack has exactly the expected keyring hooks, `/` is encrypted, and the user's
default keyring is `login` (or its migration is prepared for the next boot): with
another default, the first automatic login would create an empty `login` keyring
beside it. The standard password-login stack stays as packaged. The system
operation's snapper pair is the rollback for its edits. Installation reloads
systemd's configuration only; never restart SDDM from the desktop, since that ends
the session.

Without `login.autologin: true` the role removes the automatic-login file, the
SDDM override and the `pam_systemd_loadkey.so` line when they are present, and
reloads systemd's configuration after removing a file.

GNOME Keyring's PAM module unlocks only the collection whose file is
`login.keyring` (GNOME Keyring 50 derives the identifier from the basename). On a
fresh account the first login creates it, and setup is complete.

## Migrating an existing keyring (the `login_keyring_migration` role)

A keyring created under another name, such as the default keyring a localized
desktop named in its own language, stays locked after automatic login. Promoting
it is a separate step that runs only when named:

```sh
./bin/shaffy apply --system --tags login_keyring_migration
```

The keyring daemon must not run while its file is renamed, so the role installs
`shaffy-login-keyring.service`, which runs `prepare-login-keyring.py` as the user
at the next boot, before SDDM. The helper refuses a running daemon, an existing
`login.keyring`, linked paths, unexpected ownership or permissions, a default
alias naming no file, and a plaintext keyring. It copies the directory to
`~/.local/state/shaffy/login-keyring/<time>/before/`, verifies the copy by hash,
renames the encrypted file to `login.keyring` without changing its bytes, points
`default` at it and writes `result.json`. The collection's label and password stay
the same. If it fails, SDDM still starts; read the unit's log before trying again.

After the reboot, run the same tag again: once `login` is the default it removes
the unit, its SDDM ordering and the helper, including copies an earlier version
installed under other names. With `login.keyring` present while `default` still
names another keyring, the migration is half-finished; the role refuses and the
keyring directory needs a review with the daemon stopped.

Check it without changing anything with
`python3 roles/login_keyring_migration/files/prepare-login-keyring.py`, which
prints whether a migration is needed.

## Checking the result

Reboot, enter the disk password, and check that the desktop arrives without a
keyring prompt, that the Secret Service reports `login` and `default` as the same
unlocked collection with the expected number of items, and that an application's
saved credentials still work:

```sh
busctl --user call org.freedesktop.secrets /org/freedesktop/secrets org.freedesktop.Secret.Service ReadAlias s default
busctl --user get-property org.freedesktop.secrets /org/freedesktop/secrets/collection/login org.freedesktop.Secret.Collection Locked
busctl --user get-property org.freedesktop.secrets /org/freedesktop/secrets/collection/login org.freedesktop.Secret.Collection Items
```

The first should name `/org/freedesktop/secrets/collection/login` and the
second `false`; the third prints the number of items after its type, `ao`.
Never print secret values or the cached disk password. A later logout and login uses the normal
password login, which needs the account and keyring passwords to match.

## Recovery

Set `login.autologin` to false (or remove it) and apply the `login` role; it
removes the automatic login file, the SDDM override and the
`pam_systemd_loadkey.so` line:

```sh
./bin/shaffy apply --system --tags login
```

To undo a single edit by hand, compare with the operation's snapper pair of `/`
(`snapper -c root status PRE..POST`). `/etc/pam.d/sddm-autologin` is a file
pacman keeps when it was changed, so reinstalling `sddm` does not restore it;
the role's only change to it is the `pam_systemd_loadkey.so` line.

The promoted, encrypted `login.keyring` can stay and keeps newer credentials. A
migration's copy is under `~/.local/state/shaffy/login-keyring/<time>/before/`
with its receipt. Never copy an older keyring over newer credentials or move
keyring files while the daemon runs; a file-level rollback needs an offline
review of everything saved since.

Run the focused checks with:

```sh
/usr/bin/python3 -m unittest tests.test_login_keyring tests.test_login_preflight -v
```

Sources: [systemd's PAM handoff manual](https://man.archlinux.org/man/pam_systemd_loadkey.8),
[GNOME's login-keyring lookup](https://github.com/GNOME/gnome-keyring/blob/50.0/daemon/login/gkd-login.c),
and [GNOME's filename-derived collection identity](https://github.com/GNOME/gnome-keyring/blob/50.0/pkcs11/secret-store/gkm-secret-module.c).
