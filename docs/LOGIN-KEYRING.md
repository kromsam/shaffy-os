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
default keyring is `login`: with another default, the first automatic login would
create an empty `login` keyring beside it ([a keyring under another
name](#a-keyring-under-another-name)). The standard password-login stack stays as packaged. The system
operation's snapper pair is the rollback for its edits. Installation reloads
systemd's configuration only; never restart SDDM from the desktop, since that ends
the session.

Without `login.autologin: true` the role removes the automatic-login file, the
SDDM override and the `pam_systemd_loadkey.so` line when they are present, and
reloads systemd's configuration after removing a file.

GNOME Keyring's PAM module unlocks only the collection whose file is
`login.keyring` (GNOME Keyring 50 derives the identifier from the basename). On a
fresh account the first login creates it, and setup is complete.

## A keyring under another name

A keyring made under another name, such as the default keyring a localized
desktop named in its own language, stays locked after automatic login, so the
role refuses automatic login while `default` names it. Rename it once by hand
before setting `login.autologin`. The keyring daemon must not run meanwhile,
since it keeps the collection open and writes it back:

1. Reboot, and at SDDM's login screen switch to a text console (Ctrl+Alt+F3)
   and log in there, so no desktop session starts the daemon.
2. Keep its socket from starting it, and check that none runs; `pgrep` prints
   nothing:

   ```sh
   systemctl --user stop gnome-keyring-daemon.socket gnome-keyring-daemon.service
   pgrep -u "$USER" -l gnome-keyring
   ```

3. Keep a private copy, rename the keyring that `default` names to
   `login.keyring` and point `default` at it:

   ```sh
   cp -a ~/.local/share/keyrings ~/.local/share/keyrings.before
   cd ~/.local/share/keyrings
   test ! -e login.keyring && mv -- "$(cat default).keyring" login.keyring && printf 'login\n' > default
   ```

   If `login.keyring` already exists, the last line changes nothing: a login
   has made one beside the old keyring, and which of the two to keep needs a
   review.

4. Reboot and log in. The renamed keyring keeps its items, label and password,
   and automatic login unlocks it only when that password is the disk password;
   change it in a keyring manager such as Seahorse when it differs. Then set
   `login.autologin: true`, apply the `login` role and check the result as
   below. Remove `~/.local/share/keyrings.before` once it holds.

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

A renamed, encrypted `login.keyring` can stay and keeps newer credentials. Never
copy an older keyring over newer credentials or move keyring files while the
daemon runs; a file-level rollback needs an offline review of everything saved
since.

Run the focused checks with:

```sh
/usr/bin/python3 -m unittest tests.test_login_preflight -v
```

Sources: [systemd's PAM handoff manual](https://man.archlinux.org/man/pam_systemd_loadkey.8),
[GNOME's login-keyring lookup](https://github.com/GNOME/gnome-keyring/blob/50.0/daemon/login/gkd-login.c),
and [GNOME's filename-derived collection identity](https://github.com/GNOME/gnome-keyring/blob/50.0/pkcs11/secret-store/gkm-secret-module.c).
