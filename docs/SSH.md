# SSH with Proton Pass

The managed `ssh` integration uses the Proton Pass desktop SSH agent. It starts
`/usr/bin/proton-pass` at login through Shaffy OS's own autostart entry and
selects `~/.ssh/proton-pass-ssh-agent.sock` in Niri, environment.d and Fish.
The app has no option to start hidden, so the entry starts it with
`shaffy-desktop start-in-tray`, which closes its first window: Proton Pass
then waits in the tray.
Fish keeps an inherited `SSH_AUTH_SOCK`, including an agent forwarded over SSH.
A supplement that does not request `ssh` leaves it unmanaged.

## Prepare the desktop app

The desktop app needs a paid Proton Pass plan other than Pass Essentials and
an SSH key stored in its vault. In **Settings → General**, enable **Use Proton
Pass as SSH agent**. Follow [Proton's setup guide](https://proton.me/support/ssh-agent)
for account and key setup. Shaffy OS supplies the environment configuration,
so do not paste the app's shell-configuration command into a managed file.
Leave the app's separate start-at-login switch off to use the owned autostart
entry. If `proton-pass` is omitted from packages, install it through the source
before requesting managed SSH.

Proton Pass desktop 1.43.1 reports that passphrase-protected SSH keys are not
supported. Importing such a key can leave the agent without a usable identity.
If needed, decrypt a temporary copy in a private directory, import that copy,
then remove it. Keep the original protected key until agent authentication has
been verified. Private keys and app sign-in state stay outside both source repositories.

The private-key field expects text. A clipboard containing a file or PEM MIME
type can paste as empty. When importing through the clipboard, use
`wl-copy --sensitive --type text/plain < PRIVATE_TEMPORARY_FILE`, paste into
the field, and clear the clipboard with `wl-copy --clear` afterwards. Avoid
printing key material into a terminal or sharing it in troubleshooting output.

Sign in and unlock Proton Pass, then verify its socket explicitly before
changing an existing installation:

```sh
SSH_AUTH_SOCK="$HOME/.ssh/proton-pass-ssh-agent.sock" ssh-add -l
```

The command should list the expected public fingerprint. The desktop socket
is distinct from the [pass-cli agent](WORKSPACE-TOOLS.md#proton-pass-cli);
running the CLI agent does not prepare this integration.

## Enable and verify

Set `integrations.ssh: managed` in the supplement, run `./bin/shaffy check`
and inspect `./bin/shaffy diff`, then approve or run `./bin/shaffy apply --home`.
Log out and back in so Niri and the user manager inherit the new socket.
After login, open Proton Pass from the tray and unlock it. In a new terminal, verify:

```sh
printf '%s\n' "$SSH_AUTH_SOCK"
ssh-add -l
```

Test an SSH connection to a known host and, if configured, a signed Git commit.
Git signing continues to use the public key in `identity.signing.key` from the
supplement. The agent must contain its matching private key.

The shared SSH configuration still has `IdentityFile ~/.ssh/id_ed25519` and
`AddKeysToAgent yes`. A local private key can therefore authenticate when the
vault is unavailable, and OpenSSH can request adding it to the active agent.
This integration changes agent discovery and startup; it does not enforce
vault-only authentication or remove local keys. Per-host policy belongs in the
supplement. If the app is locked or the socket is absent, unlock or launch
Proton Pass and rerun `ssh-add -l` before diagnosing host authentication.

Use `disabled` to remove the owned login entry and managed environment, while
keeping SSH configuration and keys. Use `released` to leave the entry in
place and stop tracking it. To use a different agent, release the integration
and declare its configuration through supplement overrides or assets.

## Migrating the packaged agent

<!-- Remove after 2027-02-06 with test_legacy_ssh_activation_migrates_and_converges
and test_legacy_ssh_activation_is_removed_only_when_owned_and_disabled in tests/test_apply.py
and the old environment retirement in home/.chezmoiremove. -->

Prepare and verify Proton Pass first. An existing managed installation's next
home apply copies and removes its recorded
`~/.config/systemd/user/sockets.target.wants/ssh-agent.socket`, creates the owned
Proton Pass autostart entry, and removes the old
`~/.config/environment.d/30-ssh-agent.conf`. The replacement environment is
`~/.config/environment.d/90-proton-pass-ssh-agent.conf`. SSH keys and the shared
SSH configuration remain in place. The old service may keep running until
logout; log out and back in, unlock Proton Pass and verify again.

A released or unowned old socket link stays untouched. The old environment
fragment is retired because previous releases removed it outside managed SSH
as well. A pre-existing `shaffy-proton-pass.desktop` that the ledger does not
own blocks activation, including during migration. Review the conflicting entry and move it aside before applying again. This
keeps ownership of the old activation until migration completes. Do not delete a user's independently
managed agent as a migration shortcut.
