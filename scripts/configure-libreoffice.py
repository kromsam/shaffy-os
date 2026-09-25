#!/usr/bin/env python3
"""Select LibreOffice's Tabbed interface; default to a read-only check."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
from xml.dom import minidom


OOR = "http://openoffice.org/2001/registry"
ROOT = "/org.openoffice.Office.UI.ToolbarMode"
APPS = ("Writer", "Calc", "Impress", "Draw")
LAYOUT = "notebookbar.ui"
EMPTY = f'<oor:items xmlns:oor="{OOR}"/>'.encode()


def application_paths(app):
    """Return LibreOffice's saved spelling first, then the short equivalent."""
    return (f"{ROOT}/Applications/org.openoffice.Office.UI.ToolbarMode:Application['{app}']",
            f"{ROOT}/Applications/{app}")


def tabbed_config(before):
    """Preserve unrelated settings and XML namespaces in the private profile."""
    document = minidom.parseString(before or EMPTY)
    root = document.documentElement
    if root.namespaceURI != OOR or root.localName != "items":
        raise ValueError("Unexpected LibreOffice registry root")
    changed = False
    # Applications/*/Active enables the mode; Active* chooses its UI file.
    # LibreOffice rewrites application paths into its long form when it saves
    # the profile, so both spellings name the same setting.
    settings = [((ROOT,), f"Active{app}") for app in APPS]
    settings += [(application_paths(app), "Active") for app in APPS]
    for paths, name in settings:
        items = [node for node in root.childNodes
                 if node.nodeType == node.ELEMENT_NODE and node.tagName == "item"
                 and node.getAttributeNS(OOR, "path") in paths]
        if not items:
            item = document.createElement("item")
            item.setAttributeNS(OOR, "oor:path", paths[0])
            root.appendChild(item)
            items = [item]
        props = [node for item in items for node in item.childNodes
                 if node.nodeType == node.ELEMENT_NODE and node.tagName == "prop"
                 and node.getAttributeNS(OOR, "name") == name]
        if not props:
            prop = document.createElement("prop")
            prop.setAttributeNS(OOR, "oor:name", name)
            items[0].appendChild(prop)
            props = [prop]
        for prop in props:
            values = prop.getElementsByTagName("value")
            if (len(values) == 1 and values[0].firstChild is not None
                    and values[0].firstChild.nodeValue == LAYOUT
                    and prop.getAttributeNS(OOR, "op") in ("", "fuse", "replace")):
                continue
            for child in list(prop.childNodes):
                prop.removeChild(child)
            prop.setAttributeNS(OOR, "oor:op", "fuse")
            value = document.createElement("value")
            value.appendChild(document.createTextNode(LAYOUT))
            prop.appendChild(value)
            changed = True
    return document.toxml(encoding="UTF-8") if changed else before, changed


def require_closed(profile):
    result = subprocess.run(
        ["pgrep", "-u", str(os.getuid()), "-x", "soffice.bin"],
        capture_output=True, check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError("Cannot check whether LibreOffice is running")
    if result.returncode == 0 or (profile.parent / ".lock").exists():
        raise RuntimeError("Close LibreOffice before applying its layout preference")


def apply_config(registry, before, after, state):
    require_closed(registry.parent)
    registry.parent.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if before is not None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = state / f"registrymodifications-{stamp}.xcu"
        with backup.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(before)
        print(f"Backup: {backup}")
    # Replace atomically, preserving the original if a write fails.
    with tempfile.NamedTemporaryFile(dir=registry.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
            require_closed(registry.parent)
            current = registry.read_bytes() if registry.exists() else None
            if current != before:
                raise RuntimeError("LibreOffice settings changed during apply; retry when closed")
            temporary.replace(registry)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if (os.getuid() == 0 or Path.home() != Path(pwd.getpwuid(os.getuid()).pw_dir)
            or not Path("/etc/arch-release").exists()):
        raise RuntimeError("Run as the desktop user with their own HOME on Arch, without sudo")
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    registry = config / "libreoffice/4/user/registrymodifications.xcu"
    if registry.is_symlink():
        raise RuntimeError("Refusing to replace a symlinked LibreOffice registry")
    before = registry.read_bytes() if registry.exists() else None
    after, changed = tabbed_config(before)
    if changed and args.apply:
        apply_config(registry, before, after, state / "shaffy/runs/libreoffice")
        if tabbed_config(registry.read_bytes())[1]:
            raise RuntimeError("LibreOffice layout verification failed")
    print(f"configuration_changed: {int(changed and args.apply)}")
    if changed and not args.apply:
        print("LibreOffice layout needs attention; use --apply to select Tabbed.", file=sys.stderr)
        return 1
    print("LibreOffice Tabbed layout verified: " + ", ".join(APPS) + ".")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        sys.exit(str(error))
