# Home folders

Shaffy OS follows the XDG directories configured by the installation, in any
language. A Dutch, French or Japanese installation needs no `folders` block.
The public defaults leave `~/.config/user-dirs.dirs` untouched and create no
standard folders.

The templates resolve paths with `xdg-user-dir` when the home is rendered.
Screenshots, recordings and received files therefore follow the actual
XDG paths, including custom locations. The settings loader does not cache
those paths or copy them into the supplement. If the platform changes a
folder setting, the next home apply updates the applications that use it.

| Role | XDG variable | Shaffy OS also uses it for |
| --- | --- | --- |
| `desktop` | `XDG_DESKTOP_DIR` | |
| `documents` | `XDG_DOCUMENTS_DIR` | |
| `downloads` | `XDG_DOWNLOAD_DIR` | Files received over the Tailnet |
| `music` | `XDG_MUSIC_DIR` | |
| `pictures` | `XDG_PICTURES_DIR` | Screenshots, in `<pictures>/Screenshots` |
| `public` | `XDG_PUBLICSHARE_DIR` | |
| `templates` | `XDG_TEMPLATES_DIR` | |
| `videos` | `XDG_VIDEOS_DIR` | Screen recordings, in `<videos>/Recordings` |

To inspect a directory, run `xdg-user-dir PICTURES` or
`xdg-user-dir DOCUMENTS`. Shaffy OS keeps the native tool's fallback for an
unset directory rather than guessing a translation. A directory explicitly
set to `$HOME/` stays set to the home.

## Explicit overrides

Use `folders` only when the installation's existing choice should change:

```yaml
folders:
  pictures: "Photos"
```

The home apply changes only `XDG_PICTURES_DIR` in this example, preserving
all other entries and comments. The screenshot settings use `Photos` on that
same apply. An already-correct entry is left as it is. Explicit overrides
are folder names directly under the home; spaces, accents and apostrophes are
allowed. They cannot contain `/`, `"`, `\`, `$`, a backquote or a line break.

Create or migrate the target folder before applying an override. An apply
never creates, moves or renames these folders: they hold the user's data.
`xdg-user-dirs-update` can reset a missing directory to the home at login.

Removing an override stops managing that entry; it does not undo the last
value. Removing all standard overrides leaves the entire XDG file alone.
Remove `folders` entirely when no entries remain, rather than leaving an empty
mapping.

## Changing a directory deliberately

Close applications using the directory, take a snapshot, and move its data
before naming the new target in the supplement. For example, when the current
pictures directory should become `Photos`:

```sh
old_pictures=$(xdg-user-dir PICTURES)
snapper -c home create --description "before moving the pictures directory"
test "${old_pictures%/}" != "${HOME%/}" && test ! -e "$HOME/Photos" && \
  mv -- "$old_pictures" "$HOME/Photos"
```

Set `folders.pictures: "Photos"`, then review and apply:

```sh
./bin/shaffy check
./bin/shaffy diff
./bin/shaffy apply --home
```

The manual move happens before the apply's snapshots. To undo it, restore the
data and the corresponding settings together. The apply keeps copies and a
Snapper pair for the configuration files it changes; restoring only a managed
file without changing its source would be undone by the next apply.

Reference: [xdg-user-dirs](https://www.freedesktop.org/wiki/Software/xdg-user-dirs/)
and the installed `xdg-user-dir(1)` and `user-dirs.dirs(5)` manuals.
