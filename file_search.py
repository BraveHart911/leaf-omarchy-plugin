"""Private, bounded filename indexing over the user's XDG folders."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from gi.repository import GLib

MAX_INDEX_ENTRIES = 100_000
MAX_DEPTH = 8
SKIP_DIRS = {".cache", "node_modules", "venv", ".venv", "__pycache__"}


def user_search_roots(home: Path | None = None) -> list[Path]:
    home = Path(home or Path.home()).expanduser()
    configured: dict[str, str] = {}
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "user-dirs.dirs"
    try:
        for line in config.read_text().splitlines():
            match = re.match(r'^XDG_(?:DESKTOP|DOCUMENTS|DOWNLOAD|MUSIC|PICTURES|PUBLICSHARE|TEMPLATES|VIDEOS)_DIR="(.*)"\s*$', line)
            if match:
                configured[line.split("=", 1)[0]] = match.group(1)
    except OSError:
        pass

    roots: list[Path] = []
    for key in ("XDG_DESKTOP_DIR", "XDG_DOCUMENTS_DIR", "XDG_DOWNLOAD_DIR", "XDG_PICTURES_DIR", "XDG_VIDEOS_DIR", "XDG_MUSIC_DIR", "XDG_PUBLICSHARE_DIR"):
        raw = configured.get(key, "")
        if raw:
            raw = raw.replace("${HOME}", str(home)).replace("$HOME", str(home))
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = home / candidate
        else:
            fallback = {
                "XDG_DESKTOP_DIR": "Desktop", "XDG_DOCUMENTS_DIR": "Documents",
                "XDG_DOWNLOAD_DIR": "Downloads", "XDG_PICTURES_DIR": "Pictures",
                "XDG_VIDEOS_DIR": "Videos", "XDG_MUSIC_DIR": "Music",
                "XDG_PUBLICSHARE_DIR": "Public",
            }[key]
            candidate = home / fallback
        candidate = candidate.resolve(strict=False)
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return roots


def build_file_index(roots: list[Path], max_entries: int = MAX_INDEX_ENTRIES, max_depth: int = MAX_DEPTH,
                     excluded_dirs: set[str] | None = None) -> list[Path]:
    """Collect names only; do not read file content or follow directory symlinks."""
    results: list[Path] = []
    seen: set[Path] = set()
    skipped_dirs = SKIP_DIRS | set(excluded_dirs or ())
    for root in roots:
        root = Path(root).resolve(strict=False)
        if not root.is_dir() or root.name in skipped_dirs:
            continue
        for current, dirs, files in os.walk(root, followlinks=False):
            here = Path(current)
            depth = len(here.relative_to(root).parts)
            dirs[:] = [name for name in dirs if not name.startswith(".") and name not in skipped_dirs and not (here / name).is_symlink()]
            for name in [*dirs, *files]:
                if name.startswith("."):
                    continue
                path = here / name
                if path not in seen:
                    results.append(path)
                    seen.add(path)
                    if len(results) >= max_entries:
                        return results
            if depth >= max_depth:
                dirs.clear()
    return results


class LocalFileSearch:
    def __init__(self, roots: list[Path] | None = None, excluded_dirs: set[str] | None = None):
        self.roots = list(roots if roots is not None else user_search_roots())
        self.excluded_dirs = set(excluded_dirs or ())
        self.paths: list[Path] = []
        self.ready = False
        self.error: str | None = None
        self._lock = threading.Lock()
        self._generation = 0
        self._index_generation = 0
        self._timeout_id = 0
        self._on_ready = None

    def start(self, on_ready=None) -> None:
        if on_ready is not None:
            self._on_ready = on_ready
        self._index_generation += 1
        generation = self._index_generation
        roots = list(self.roots)
        excluded_dirs = set(self.excluded_dirs)
        with self._lock:
            self.paths = []
            self.ready = False
            self.error = None

        def work():
            try:
                paths = build_file_index(roots, excluded_dirs=excluded_dirs)
                error = None
            except OSError as exc:
                paths, error = [], str(exc)
            with self._lock:
                if generation != self._index_generation:
                    return
                self.paths = paths
                self.ready = True
                self.error = error
            if self._on_ready:
                GLib.idle_add(self._on_ready)
        threading.Thread(target=work, name="leaf-file-index", daemon=True).start()

    def reconfigure(self, roots: list[Path] | None = None, excluded_dirs: set[str] | None = None) -> None:
        self.roots = list(roots if roots is not None else user_search_roots())
        self.excluded_dirs = set(excluded_dirs or ())
        self.start(self._on_ready)

    def search(self, query: str, callback) -> None:
        self._generation += 1
        generation = self._generation
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
        self._timeout_id = GLib.timeout_add(140, self._run_search, query.strip(), generation, callback)

    def _run_search(self, query: str, generation: int, callback) -> bool:
        self._timeout_id = 0
        if generation != self._generation:
            return GLib.SOURCE_REMOVE
        if not query:
            callback(query, [], self.ready, self.error)
            return GLib.SOURCE_REMOVE
        needle = query.casefold()
        with self._lock:
            paths = list(self.paths)
            ready, error = self.ready, self.error
        found = [path for path in paths if needle in path.name.casefold()]
        found.sort(key=lambda path: (path.name.casefold() != needle, not path.name.casefold().startswith(needle), len(path.name), path.name.casefold()))
        callback(query, found[:30], ready, error)
        return GLib.SOURCE_REMOVE
