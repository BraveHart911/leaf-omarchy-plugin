import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from file_search import build_file_index, user_search_roots


class LocalFileSearchTests(unittest.TestCase):
    def test_index_is_bounded_to_visible_names_and_skips_hidden_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.txt").write_text("private contents are not indexed")
            (root / "Folder").mkdir()
            (root / "Folder" / "photo.png").write_bytes(b"image bytes")
            (root / ".private").mkdir()
            (root / ".private" / "secret.txt").write_text("hidden")
            (root / ".cache").mkdir()
            (root / ".cache" / "cache.txt").write_text("cache")
            paths = build_file_index([root])
            names = {path.name for path in paths}
            self.assertEqual(names, {"notes.txt", "Folder", "photo.png"})

    def test_index_respects_entry_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number in range(5):
                (root / f"file-{number}.txt").touch()
            self.assertEqual(len(build_file_index([root], max_entries=3)), 3)

    def test_xdg_user_directory_config_is_read_without_shell_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            personal = home / "Archive"
            personal.mkdir()
            config_dir = home / "config"
            config_dir.mkdir()
            (config_dir / "user-dirs.dirs").write_text(f'XDG_DOCUMENTS_DIR="$HOME/Archive"\n')
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_dir)}):
                self.assertEqual(user_search_roots(home), [personal])


if __name__ == "__main__":
    unittest.main()
