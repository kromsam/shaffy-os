# Home folders

Shaffy OS follows the XDG directories configured by the installation, in any
language. A Dutch, French or Japanese installation needs no `folders` block
and no English aliases. The public defaults leave `~/.config/user-dirs.dirs`
untouched and create no standard folders or compatibility links.

The templates resolve paths with `xdg-user-dir` when the home is rendered.
Screenshots, recordings, received files and notes therefore follow the actual
XDG paths, including custom locations. The settings loader does not cache
those paths or copy them into the supplement. If the platform changes a
folder setting, the next home apply updates the applications that use it.

| Role | XDG variable | Shaffy OS also uses it for |
| --- | --- | --- |
| `desktop` | `XDG_DESKTOP_DIR` | |
| `documents` | `XDG_DOCUMENTS_DIR` | Sticky Notes, in `<documents>/sticky-notes` |
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
An existing supplement that repeats the installation's directory settings can
drop those entries without changing the current paths. Remove `folders`
entirely when no entries remain, rather than leaving an empty mapping.

`projects` is not a standard XDG role. There is no default project folder and
Shaffy OS no longer writes `XDG_PROJECTS_DIR`. An explicit `folders.projects`
can supply a target for a compatibility link; otherwise it is unnecessary.
An older `XDG_PROJECTS_DIR` entry is left alone as unmanaged content.

## Compatibility links

XDG-aware applications do not need aliases. Use `folder_links` only when an
existing application, bookmark or script still uses an old literal path:

```yaml
folder_links:
  Pictures: pictures
```

Here `~/Pictures` points at the installation's pictures directory, without
needing to repeat its localized name. An explicit `folders.pictures`, if
present, takes precedence. Links within the home are relative; targets
outside it use an absolute path.

Allowed link names are `Desktop`, `Documents`, `Downloads`, `Music`,
`Pictures`, `Projects`, `Public`, `Templates`, `Videos` and `Work`. Each can
point at a standard role or at an explicit `folders.projects`. For example:

```yaml
folders:
  projects: "Code"
folder_links:
  Work: projects
```

Only listed links are managed. Unlisted names, including real directories
and older symlinks, are left alone. While links are configured, `~/.hidden`
lists their names so GIO file managers hide the aliases in normal listings.
Without links, an existing `~/.hidden` is also left alone.

`check` and the home apply refuse links that would replace real files or
folders, point at missing targets, or point to themselves. A matching English
XDG directory does not need a link to itself. Removing a link declaration
stops managing it; remove the actual symlink only after its old path is no
longer needed.

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
