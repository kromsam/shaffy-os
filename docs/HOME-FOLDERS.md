# Home folders

Two supplement settings shape the folders in the home. `folders` names the
XDG user directories, the folders that file managers, file choosers and
applications use for documents, pictures, downloads and the like. `folder_links`
adds compatibility links under English names that point at those folders, so
paths from an earlier layout keep working. Both are applied by the home apply.
No apply creates, moves or renames a folder; the folders themselves are the
user's data.

## Folder names

`folders` has one key per XDG role. Each value is a folder name relative to the
home. The public defaults, in `defaults/config.yaml`, are the English names:

| Key | XDG variable | Public default | Also used for |
| --- | --- | --- | --- |
| `desktop` | `XDG_DESKTOP_DIR` | `Desktop` | |
| `documents` | `XDG_DOCUMENTS_DIR` | `Documents` | Sticky Notes, in `<documents>/sticky-notes` |
| `downloads` | `XDG_DOWNLOAD_DIR` | `Downloads` | Files received over the Tailnet |
| `music` | `XDG_MUSIC_DIR` | `Music` | |
| `pictures` | `XDG_PICTURES_DIR` | `Pictures` | Screenshots, in `<pictures>/Screenshots` |
| `projects` | `XDG_PROJECTS_DIR` | `Projects` | |
| `public` | `XDG_PUBLICSHARE_DIR` | `Public` | |
| `templates` | `XDG_TEMPLATES_DIR` | `Templates` | |
| `videos` | `XDG_VIDEOS_DIR` | `Videos` | Screen recordings, in `<videos>/Recordings` |

A supplement sets only the roles it changes; the others keep their defaults. A
name is a single folder directly in the home: it may contain spaces and
apostrophes, but not `/`, `"`, `\`, `$`, a backquote or a line break.

The home apply renders `~/.config/user-dirs.dirs` (mode 0600) from
`home/dot_config/private_user-dirs.dirs.tmpl`, with all nine variables. It is a
managed file: `xdg-user-dir PICTURES` and the applications that follow the XDG
convention read it, and the next apply replaces any change made to it in place.

Create each configured folder before the apply that names it. At the start of
every session `xdg-user-dirs-update` resets an entry whose folder does not
exist to the home itself; `./bin/shaffy status` then reports the file as drift,
and the next apply writes the configured name back.

## Compatibility links

`folder_links` maps an English link name to a folder role. The possible names
are `Desktop`, `Documents`, `Downloads`, `Music`, `Pictures`, `Projects`,
`Public`, `Templates`, `Videos` and `Work`, one template each in `home/`
(`home/symlink_Desktop.tmpl` to `home/symlink_Work.tmpl`, ten in all). Each
renders a relative link in the home, such as `~/Pictures` pointing at the
configured pictures folder. `Work` is an extra name for tools that expect
`~/Work`; like the others it may point at any role.

- Only the names listed are managed. An unlisted name is ignored: a real folder
  or an older link of that name is left exactly as it is, never removed.
- While any link is configured, the home apply also renders `~/.hidden` (mode
  0600) from `home/private_dot_hidden.tmpl`, listing the link names, so that
  file managers and file choosers built on GIO, such as Nautilus, leave the
  links out of their normal listing; showing hidden files reveals them. The
  file then holds exactly the link names. Without links, an existing
  `~/.hidden` is left alone.
- A link is a second path to the same folder, not a copy. Hiding it does not
  mean that it can be removed: remove it only after nothing refers to the old
  path any more.

`./bin/shaffy check` and the preflight of every home apply refuse, before
anything is written:

- a link whose name is a real folder or file in the home, so a link never
  replaces a folder with its contents: move the contents into the configured
  folder and remove the old one, or drop the link from the settings;
- a link whose folder does not exist: create or rename the folder first, or
  drop the link.

A link that already exists with another target is replaced.

## Example: a supplement with Dutch folder names

A supplement that names three folders in Dutch and keeps the English paths
working as links:

```yaml
folders:
  documents: "Documenten"
  music: "Muziek"
  pictures: "Afbeeldingen"
folder_links:
  Documents: documents
  Music: music
  Pictures: pictures
```

`~/.config/user-dirs.dirs` then names `$HOME/Documenten`, `$HOME/Muziek` and
`$HOME/Afbeeldingen`, and keeps the English defaults for the other six roles;
`~/Documents`, `~/Music` and `~/Pictures` become links to the Dutch folders.

## Renaming a folder

An apply never renames a folder, so a new name for an existing folder is a
step of its own, taken before the apply. For the pictures folder in the example
above:

1. Close the applications that have files open in the folder.
2. Rename it, only if the new name is still free. Take a snapshot first if you
   want a restore point, since no apply's snapper pair covers this step:

   ```sh
   snapper -c home create --description "before renaming Pictures"
   test ! -e ~/Afbeeldingen && mv ~/Pictures ~/Afbeeldingen
   ```

3. Set `folders.pictures: "Afbeeldingen"` in the supplement, and
   `folder_links: {Pictures: pictures}` if the old path should keep working.
4. Review and apply:

   ```sh
   ./bin/shaffy check
   ./bin/shaffy diff
   ./bin/shaffy apply --home
   ```

A rename within the home keeps the folder's contents, permissions, hard links
and relative links. Absolute paths that name the old folder, in application
settings, bookmarks or scripts, work again only through the link. With the link
configured, an apply before the rename is refused, since the link would replace
the real `~/Pictures` and point at a folder that does not exist yet.

## Recovery

- A refused apply has changed nothing; do what its message names.
- To undo the rename above, remove the link, rename the folder back, restore
  the previous settings and apply again:

  ```sh
  rm ~/Pictures                  # the link only; no trailing slash
  test ! -e ~/Pictures && mv ~/Afbeeldingen ~/Pictures
  ```

- A name dropped from `folder_links` is no longer managed, and its link stays
  in place until you remove it the same way.
- `~/.config/user-dirs.dirs`, `~/.hidden` and the links are ordinary managed
  files. Each home apply keeps copies of the files it changes and a snapper
  pair of `/home`; restore a single file from the pair with
  `snapper -c home undochange PRE..POST ~/.config/user-dirs.dirs`, and keep the
  supplement in step, or the next apply brings the change back.

References: `user-dirs.dirs(5)` and `xdg-user-dirs-update(1)`, from the
xdg-user-dirs package.
