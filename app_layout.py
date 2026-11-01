"""Persistent application ordering and favorites for Leaf Control."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Iterable


def app_identifier(app) -> str:
    """Return the desktop-entry ID, which remains stable across restarts."""
    return app.get_id() or app.get_executable() or app.get_display_name().casefold()


class ApplicationLayout:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.order: list[str] = []
        self.favorites: list[str] = []
        self.usage_counts: dict[str, int] = {}
        self.folders: dict[str, dict[str, object]] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            if data.get("version") != 1:
                return
            self.order = self._unique_strings(data.get("order", []))
            self.favorites = self._unique_strings(data.get("favorites", []))
            raw_usage = data.get("usage", {})
            if isinstance(raw_usage, dict):
                self.usage_counts = {
                    app_id: count for app_id, count in raw_usage.items()
                    if isinstance(app_id, str) and app_id and isinstance(count, int)
                    and not isinstance(count, bool) and count >= 0
                }
            raw_folders = data.get("folders", {})
            if isinstance(raw_folders, dict):
                for folder_id, folder in raw_folders.items():
                    if not isinstance(folder_id, str) or not isinstance(folder, dict):
                        continue
                    name = folder.get("name")
                    app_ids = self._unique_strings(folder.get("apps", []))
                    if isinstance(name, str) and name.strip() and app_ids:
                        self.folders[folder_id] = {"name": name.strip(), "apps": app_ids}
        except (OSError, ValueError, TypeError, AttributeError):
            # A bad or old layout must not prevent the launcher from opening.
            self.order, self.favorites, self.usage_counts, self.folders = [], [], {}, {}

    @staticmethod
    def _unique_strings(values) -> list[str]:
        if not isinstance(values, list):
            return []
        return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))

    def ordered(self, apps: Iterable) -> list:
        apps = list(apps)
        positions = {app_id: index for index, app_id in enumerate(self.order)}
        return sorted(apps, key=lambda app: (positions.get(app_identifier(app), len(positions)), app.get_display_name().casefold()))

    def most_used(self, apps: Iterable) -> list:
        """Order apps by recorded successful opens, preserving saved order for ties."""
        apps = list(apps)
        positions = {app_id: index for index, app_id in enumerate(self.order)}
        return sorted(apps, key=lambda app: (
            -self.usage_counts.get(app_identifier(app), 0),
            positions.get(app_identifier(app), len(positions)),
            app.get_display_name().casefold(),
        ))

    def record_use(self, app) -> None:
        app_id = app_identifier(app)
        self.usage_counts[app_id] = self.usage_counts.get(app_id, 0) + 1
        self.save()

    def is_favorite(self, app) -> bool:
        return app_identifier(app) in self.favorites

    def folder_for(self, app) -> str | None:
        app_id = app_identifier(app)
        return next((folder_id for folder_id, folder in self.folders.items() if app_id in folder["apps"]), None)

    def folder_app_ids(self) -> set[str]:
        return {app_id for folder in self.folders.values() for app_id in folder["apps"]}

    def create_folder(self, name: str, app_ids: Iterable[str]) -> str:
        name = name.strip()
        ids = list(dict.fromkeys(app_id for app_id in app_ids if isinstance(app_id, str) and app_id))
        if not name or len(ids) < 2:
            raise ValueError("A folder needs a name and at least two applications")
        if not self.is_folder_name_available(name):
            raise ValueError("A folder with that name already exists")
        for folder_id, folder in list(self.folders.items()):
            folder["apps"] = [app_id for app_id in folder["apps"] if app_id not in ids]
            if not folder["apps"]:
                del self.folders[folder_id]
                self.order = [entry for entry in self.order if entry != f"folder:{folder_id}"]
        folder_id = str(uuid.uuid4())
        self.folders[folder_id] = {"name": name, "apps": ids}
        # Groups share the saved grid order with apps. Place a new group at
        # the first member's existing position, leaving the member order intact.
        positions = {entry: index for index, entry in enumerate(self.order)}
        member_positions = [positions[app_id] for app_id in ids if app_id in positions]
        insert_at = min(member_positions) if member_positions else 0
        self.order.insert(insert_at, f"folder:{folder_id}")
        self.save()
        return folder_id

    def add_to_folder(self, folder_id: str, app_id: str) -> bool:
        if folder_id not in self.folders:
            return False
        if app_id in self.folders[folder_id]["apps"]:
            return True
        for existing_id, folder in list(self.folders.items()):
            if app_id in folder["apps"]:
                folder["apps"].remove(app_id)
                if not folder["apps"]:
                    del self.folders[existing_id]
                    self.order = [entry for entry in self.order if entry != f"folder:{existing_id}"]
        self.folders[folder_id]["apps"].append(app_id)
        self.save()
        return True

    def remove_from_folder(self, folder_id: str, app_id: str) -> bool:
        folder = self.folders.get(folder_id)
        if not folder or app_id not in folder["apps"]:
            return False
        folder["apps"].remove(app_id)
        if not folder["apps"]:
            del self.folders[folder_id]
            self.order = [entry for entry in self.order if entry != f"folder:{folder_id}"]
        self.save()
        return True

    def rename_folder(self, folder_id: str, name: str) -> bool:
        name = name.strip()
        if folder_id not in self.folders or not name or not self.is_folder_name_available(name, excluding=folder_id):
            return False
        self.folders[folder_id]["name"] = name
        self.save()
        return True

    def is_folder_name_available(self, name: str, excluding: str | None = None) -> bool:
        normalized = name.strip().casefold()
        return bool(normalized) and all(
            folder_id == excluding or folder["name"].casefold() != normalized
            for folder_id, folder in self.folders.items()
        )

    def delete_folder(self, folder_id: str) -> bool:
        if folder_id not in self.folders:
            return False
        del self.folders[folder_id]
        self.order = [entry for entry in self.order if entry != f"folder:{folder_id}"]
        self.save()
        return True

    def toggle_favorite(self, app) -> bool:
        app_id = app_identifier(app)
        if app_id in self.favorites:
            self.favorites.remove(app_id)
            favorite = False
        else:
            self.favorites.append(app_id)
            favorite = True
        self.save()
        return favorite

    def move_before(self, source_id: str, target_id: str, installed_ids: Iterable[str]) -> bool:
        ids = list(dict.fromkeys(installed_ids))
        if source_id not in ids or target_id not in ids:
            return False
        if source_id == target_id:
            return True
        full_order = list(dict.fromkeys([*self.order, *ids]))
        full_order.remove(source_id)
        full_order.insert(full_order.index(target_id), source_id)
        self.order = full_order
        self.save()
        return True

    def move_favorite(self, app_id: str, direction: int) -> bool:
        """Move a pin one slot without changing the application grid order."""
        if app_id not in self.favorites or direction not in (-1, 1):
            return False
        index = self.favorites.index(app_id)
        target = index + direction
        if not 0 <= target < len(self.favorites):
            return False
        previous = self.favorites[:]
        self.favorites[index], self.favorites[target] = self.favorites[target], self.favorites[index]
        try:
            self.save()
        except OSError:
            self.favorites = previous
            raise
        return True

    def move_favorite_before(self, source_id: str, target_id: str) -> bool:
        """Reorder a pinned app in one save when it is dropped on another pin."""
        if source_id not in self.favorites or target_id not in self.favorites:
            return False
        if source_id == target_id:
            return True
        previous = self.favorites[:]
        self.favorites.remove(source_id)
        self.favorites.insert(self.favorites.index(target_id), source_id)
        try:
            self.save()
        except OSError:
            self.favorites = previous
            raise
        return True

    def remove_favorite(self, app_id: str) -> bool:
        if app_id not in self.favorites:
            return False
        previous = self.favorites[:]
        self.favorites.remove(app_id)
        try:
            self.save()
        except OSError:
            self.favorites = previous
            raise
        return True

    def move_after(self, source_id: str, target_id: str, installed_ids: Iterable[str]) -> bool:
        return self.move_entry_after(source_id, target_id, installed_ids)

    def move_entry_before(self, source_id: str, target_id: str, entry_ids: Iterable[str]) -> bool:
        return self._move_entry(source_id, target_id, entry_ids, after=False)

    def move_entry_after(self, source_id: str, target_id: str, entry_ids: Iterable[str]) -> bool:
        return self._move_entry(source_id, target_id, entry_ids, after=True)

    def _move_entry(self, source_id: str, target_id: str, entry_ids: Iterable[str], after: bool) -> bool:
        ids = list(dict.fromkeys(entry_ids))
        if source_id not in ids or target_id not in ids:
            return False
        if source_id == target_id:
            return True
        full_order = list(dict.fromkeys([*self.order, *ids]))
        full_order.remove(source_id)
        full_order.insert(full_order.index(target_id) + int(after), source_id)
        self.order = full_order
        self.save()
        return True

    def ordered_entries(self, entries: Iterable[tuple[str, object, object]]) -> list[tuple[str, object, object]]:
        """Sort app and group grid entries using their shared persisted order."""
        positions = {entry_id: index for index, entry_id in enumerate(self.order)}
        materialized = list(entries)
        fallback = len(positions)
        return sorted(materialized, key=lambda entry: (
            positions.get(f"folder:{entry[1]}" if entry[0] == "folder" else app_identifier(entry[1]),
                          -1 if entry[0] == "folder" else fallback),
            entry[0],
        ))

    def place_entry(self, source_id: str, target_id: str, visible_ids: Iterable[str], after=False) -> bool:
        """Reorder exactly the displayed grid, including legacy unpositioned groups."""
        ids = list(dict.fromkeys(visible_ids))
        if source_id == target_id or source_id not in ids or target_id not in ids:
            return False
        ids.remove(source_id)
        ids.insert(ids.index(target_id) + int(after), source_id)
        previous = self.order[:]
        self.order = ids + [entry for entry in previous if entry not in ids]
        try:
            self.save()
        except OSError:
            self.order = previous
            raise
        return True

    def move_to_start(self, app, installed_ids: Iterable[str]) -> None:
        app_id = app_identifier(app)
        ids = list(dict.fromkeys(installed_ids))
        if app_id in ids:
            full_order = list(dict.fromkeys([*self.order, *ids]))
            full_order.remove(app_id)
            full_order.insert(0, app_id)
            self.order = full_order
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1, "order": self.order, "favorites": self.favorites,
            "usage": self.usage_counts, "folders": self.folders,
        }
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
                temp_name = stream.name
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)
