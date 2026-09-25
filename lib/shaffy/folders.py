"""Compatibility links must never replace a real folder or point at a missing one,
and supplement assets must never replace a folder or go through a link."""
from pathlib import Path


def link_conflicts(settings, home):
    """Problems for configured folder_links: a real folder or file in the way, or a missing target."""
    problems = []
    for name, folder in sorted(settings.get('folder_links', {}).items()):
        path, target = Path(home) / name, Path(home) / settings['folders'][folder]
        if not path.is_symlink() and path.exists():
            problems.append(f'folder_links.{name}: {path} is a real {"folder" if path.is_dir() else "file"}; '
                            f'move its contents into {settings["folders"][folder]} and remove it, or drop '
                            'the link from the settings')
        elif not target.is_dir():
            problems.append(f'folder_links.{name}: its target {target} is not a folder; create or '
                            'migrate it first, or drop the link from the settings')
    return problems


def asset_conflicts(assets, home):
    """Problems for supplement assets: a real folder at the target, or a link or file on its way."""
    problems = []
    for asset in assets:
        label, current = f"assets.~/{asset['target']}", Path(home)
        for part in Path(asset['target']).parts[:-1]:
            current = current / part
            if current.is_symlink():
                problems.append(f'{label}: {current} is a link, which the copy would replace with a folder; '
                                'name the file by its real path instead')
                break
            if current.exists() and not current.is_dir():
                problems.append(f'{label}: {current} is a file, not a folder')
                break
        else:
            target = Path(home) / asset['target']
            if target.is_dir() and not target.is_symlink():
                problems.append(f'{label}: {target} is a real folder, which the copy would replace; '
                                'name a file inside it')
    return problems
