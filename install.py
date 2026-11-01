#!/usr/bin/env python3
"""Install one relocatable Leaf plugin, preserving preferences and bar placement."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

PLUGIN_ID = "io.github.bravehart911.leaf-control"
LEGACY_IDS = ("local.leaf-control", "local.leaf-workspaces")
RUNTIME_FILES = (
    "manifest.json", "BarWidget.qml", "Menu.qml", "launch-leaf", "leaf-control",
    "leaf_control.py", "preferences.py", "settings_ui.py", "system_metrics.py",
    "window_layout.py", "layout_rules.py", "app_layout.py", "file_search.py", "gestures.py", "leaf-gestures.lua",
    "workspace_model.py", "select_workspace.py", "move_windows.py", "owner_probe.py",
    "leaf.css", "README.md", "preview.png", "apps-preview.png", "system-preview.png",
    "settings-preview.png", "LICENSE", "ARCHITECTURE.md", "TESTING.md",
    "install.py", "install-user.sh", "uninstall-user.sh",
)


def migrate_shell(data, enable=True):
    """Keep the old bar placement while replacing Leaf's former plugin IDs."""
    data = json.loads(json.dumps(data))
    for key in ("plugins", "disabledPlugins", "cloneSourceRestores"):
        if key in data:
            data[key] = [entry for entry in data[key]
                         if (entry.get("id") if isinstance(entry, dict) else entry) not in LEGACY_IDS]
    layout = data.setdefault("bar", {}).setdefault("layout", {})
    found = False
    for section, entries in layout.items():
        kept = []
        for entry in entries:
            entry_id = entry.get("id") if isinstance(entry, dict) else entry
            if entry_id == "local.leaf-workspaces":
                continue
            if entry_id in (PLUGIN_ID, "local.leaf-control"):
                if found:
                    continue
                found = True
                if entry_id != PLUGIN_ID:
                    entry = {**entry, "id": PLUGIN_ID} if isinstance(entry, dict) else {"id": PLUGIN_ID}
            kept.append(entry)
        layout[section] = kept
    if enable and not found:
        layout.setdefault("right", []).append({"id": PLUGIN_ID})
    if enable:
        data["disabledPlugins"] = [item for item in data.get("disabledPlugins", []) if item != PLUGIN_ID]
    return data


def read_shell_config(shell_path):
    if shell_path.exists():
        return json.loads(shell_path.read_text())
    default = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "config/omarchy/shell.json"
    if not default.is_file():
        raise RuntimeError(f"Omarchy default shell configuration not found: {default}")
    return json.loads(default.read_text())


def atomic_text(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as stream:
            temporary = Path(stream.name)
            stream.write(text)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def paths():
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    binaries = Path(os.environ.get("XDG_BIN_HOME", Path.home() / ".local/bin"))
    return config, data, state, binaries


def launcher_text(target):
    return "#!/bin/sh\nexec sh " + shlex.quote(str(target / "launch-leaf")) + ' "$@"\n'


def desktop_text(launcher):
    executable = str(launcher).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'''[Desktop Entry]
Name=Leaf
Comment=Applications, workspaces and system monitor
Exec="{executable}"
Icon=applications-system
Terminal=false
Type=Application
Categories=Utility;
StartupWMClass=org.leafcontrol.LeafControl
Actions=Settings;System;Window;

[Desktop Action Settings]
Name=Settings
Exec="{executable}" --settings

[Desktop Action System]
Name=System monitor
Exec="{executable}" --system

[Desktop Action Window]
Name=Open in a window
Exec="{executable}" --apps --window
'''


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_integrations(config, data, state, binaries, backup):
    target = config / "omarchy/plugins" / PLUGIN_ID
    if not (target / "manifest.json").is_file():
        raise RuntimeError(f"Leaf plugin is not installed: {target}")
    launcher = binaries / "leaf-control"
    desktop = data / "applications/org.leafcontrol.LeafControl.desktop"
    state_file = state / "leaf-control/integration.json"
    record = json.loads(state_file.read_text()) if state_file.exists() else {}
    generated = (("launcher", launcher, launcher_text(target), 0o755),
                 ("desktop", desktop, desktop_text(launcher), 0o644))
    for key, destination, contents, mode in generated:
        previous = record.get(key, {}).get("previous")
        if key not in record and destination.exists():
            old_leaf = key == "launcher" and any(
                destination.read_text() == launcher_text(target.parent / legacy)
                for legacy in LEGACY_IDS)
            if destination.read_text() != contents and not old_leaf:
                backup.mkdir(parents=True, exist_ok=True)
                saved = backup / destination.name
                shutil.copy2(destination, saved)
                previous = str(saved)
        elif key in record and destination.exists() and file_digest(destination) != record[key]["installed_sha256"]:
            backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup / destination.name)
        atomic_text(destination, contents, mode)
        record[key] = {"installed_sha256": file_digest(destination), "previous": previous}
    atomic_text(state_file, json.dumps(record, indent=2) + "\n", 0o600)


def uninstall_integrations(config, data, state, binaries):
    target = config / "omarchy/plugins" / PLUGIN_ID
    launcher = binaries / "leaf-control"
    desktop = data / "applications/org.leafcontrol.LeafControl.desktop"
    state_file = state / "leaf-control/integration.json"
    record = json.loads(state_file.read_text()) if state_file.exists() else {}
    generated = (("launcher", launcher, launcher_text(target)),
                 ("desktop", desktop, desktop_text(launcher)))
    for key, destination, contents in generated:
        if not destination.exists():
            continue
        entry = record.get(key)
        owned = (file_digest(destination) == entry["installed_sha256"] if entry
                 else destination.read_text() == contents or
                 (key == "launcher" and any(destination.read_text() == launcher_text(target.parent / legacy)
                                            for legacy in LEGACY_IDS)))
        if not owned:
            print(f"Kept modified file: {destination}")
            continue
        previous = entry.get("previous") if entry else None
        if previous:
            saved = Path(previous)
            if not saved.is_file() or not saved.resolve().is_relative_to((state / "leaf-backups").resolve()):
                print(f"Kept {destination}: original backup is unavailable")
                continue
            shutil.copy2(saved, destination)
        else:
            destination.unlink()
    state_file.unlink(missing_ok=True)


def install(update=False):
    root = Path(__file__).resolve().parent
    config, data, state, binaries = paths()
    plugins = config / "omarchy/plugins"
    target = plugins / PLUGIN_ID
    if target.exists() and not update:
        raise RuntimeError("Leaf is already installed. Run install-user.sh --update.")
    for name in RUNTIME_FILES:
        if not (root / name).is_file():
            raise RuntimeError(f"Incomplete package: {name} is missing")
    shell_path = config / "omarchy/shell.json"
    shell = read_shell_config(shell_path)
    migrated = migrate_shell(shell)
    backup = state / "leaf-backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup.mkdir(parents=True)
    if shell_path.exists():
        shutil.copy2(shell_path, backup / "shell.json")
    plugins.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="leaf-stage-", dir=plugins.parent) as temporary:
        staged = Path(temporary) / PLUGIN_ID
        staged.mkdir()
        for name in RUNTIME_FILES:
            shutil.copy2(root / name, staged / name)
        for name in ("launch-leaf", "leaf-control", "install-user.sh", "uninstall-user.sh"):
            (staged / name).chmod(0o755)
        if (target / ".git").exists():
            shutil.copytree(target / ".git", staged / ".git")
        if target.exists():
            shutil.move(str(target), backup / PLUGIN_ID)
        try:
            staged.rename(target)
            atomic_text(shell_path, json.dumps(migrated, indent=2) + "\n")
        except Exception:
            if target.exists():
                shutil.rmtree(target)
            if (backup / PLUGIN_ID).exists():
                shutil.move(str(backup / PLUGIN_ID), target)
            raise
    for legacy_id in LEGACY_IDS:
        legacy = plugins / legacy_id
        if legacy.exists():
            shutil.move(str(legacy), backup / legacy_id)
    install_integrations(config, data, state, binaries, backup)
    old_runtime = data / "leaf-control"
    if old_runtime.exists() and (old_runtime / "leaf_control.py").exists():
        shutil.move(str(old_runtime), backup / "old-runtime")
    print(f"Installed Leaf as one plugin: {target}\nBackup: {backup}\nPreferences and app arrangements kept.")


def uninstall(integration_only=False):
    config, data, state, binaries = paths()
    uninstall_integrations(config, data, state, binaries)
    if integration_only:
        print("Removed Leaf command and desktop integration. Remove the plugin with: omarchy plugin remove " + PLUGIN_ID)
        return
    shell_path = config / "omarchy/shell.json"
    backup = state / "leaf-backups" / datetime.now().strftime("uninstall-%Y%m%d-%H%M%S-%f")
    backup.mkdir(parents=True)
    if shell_path.exists():
        shell = migrate_shell(json.loads(shell_path.read_text()), enable=False)
        for entries in shell.get("bar", {}).get("layout", {}).values():
            entries[:] = [entry for entry in entries if (entry.get("id") if isinstance(entry, dict) else entry) != PLUGIN_ID]
        shell["plugins"] = [entry for entry in shell.get("plugins", []) if (entry.get("id") if isinstance(entry, dict) else entry) != PLUGIN_ID]
        shutil.copy2(shell_path, backup / "shell.json")
        atomic_text(shell_path, json.dumps(shell, indent=2) + "\n")
    target = config / "omarchy/plugins" / PLUGIN_ID
    if target.exists():
        shutil.move(str(target), backup / PLUGIN_ID)
    print("Removed Leaf. Preferences, pinned apps, groups and local data were kept.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--integration-only", action="store_true",
                        help="install or remove command and desktop entry for a plugin added with omarchy plugin add")
    args = parser.parse_args()
    try:
        if args.uninstall:
            uninstall(args.integration_only)
        elif args.integration_only:
            config, data, state, binaries = paths()
            backup = state / "leaf-backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            install_integrations(config, data, state, binaries, backup)
            print("Installed Leaf command and desktop integration.")
        else:
            install(args.update)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Leaf installation failed: {error}\n")
    if not os.environ.get("LEAF_INSTALL_NO_RESCAN") and shutil.which("omarchy-shell"):
        subprocess.run(["omarchy-shell", "shell", "rescanPlugins"], check=False, timeout=10)


if __name__ == "__main__":
    main()
