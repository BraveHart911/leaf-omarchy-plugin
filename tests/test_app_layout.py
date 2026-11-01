import tempfile
import unittest
from pathlib import Path

from app_layout import ApplicationLayout


class FakeApp:
    def __init__(self, app_id, name):
        self.app_id = app_id
        self.name = name

    def get_id(self):
        return self.app_id

    def get_executable(self):
        return self.name

    def get_display_name(self):
        return self.name


class ApplicationLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "config" / "layout.json"
        self.apps = [FakeApp("a.desktop", "Alpha"), FakeApp("b.desktop", "Beta"), FakeApp("c.desktop", "Gamma")]

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_favorites_persist_between_layout_instances(self):
        layout = ApplicationLayout(self.path)
        self.assertTrue(layout.toggle_favorite(self.apps[1]))
        self.assertFalse(layout.toggle_favorite(self.apps[1]))
        layout.toggle_favorite(self.apps[0])
        reloaded = ApplicationLayout(self.path)
        self.assertTrue(reloaded.is_favorite(self.apps[0]))
        self.assertFalse(reloaded.is_favorite(self.apps[1]))

    def test_reordering_persists_and_new_apps_sort_after_saved_apps(self):
        layout = ApplicationLayout(self.path)
        self.assertTrue(layout.move_before("c.desktop", "a.desktop", ["a.desktop", "b.desktop", "c.desktop"]))
        reloaded = ApplicationLayout(self.path)
        ordered = reloaded.ordered(self.apps)
        self.assertEqual([app.get_id() for app in ordered], ["c.desktop", "a.desktop", "b.desktop"])

    def test_invalid_layout_falls_back_to_defaults(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("not json")
        layout = ApplicationLayout(self.path)
        self.assertEqual([app.get_id() for app in layout.ordered(self.apps)], ["a.desktop", "b.desktop", "c.desktop"])

    def test_reorder_keeps_removed_app_ids_in_saved_layout(self):
        layout = ApplicationLayout(self.path)
        layout.order = ["a.desktop", "b.desktop", "c.desktop"]
        layout.save()
        self.assertTrue(layout.move_before("c.desktop", "a.desktop", ["a.desktop", "c.desktop"]))
        self.assertEqual(layout.order, ["c.desktop", "a.desktop", "b.desktop"])

    def test_move_after_persists_and_keeps_removed_app_ids(self):
        layout = ApplicationLayout(self.path)
        layout.order = ["a.desktop", "b.desktop", "removed.desktop", "c.desktop"]
        self.assertTrue(layout.move_after("a.desktop", "b.desktop", ["a.desktop", "b.desktop", "c.desktop"]))
        self.assertEqual(layout.order, ["b.desktop", "a.desktop", "removed.desktop", "c.desktop"])
        reloaded = ApplicationLayout(self.path)
        self.assertEqual([app.get_id() for app in reloaded.ordered(self.apps)], ["b.desktop", "a.desktop", "c.desktop"])

    def test_folder_membership_and_rename_persist(self):
        layout = ApplicationLayout(self.path)
        folder_id = layout.create_folder("Work", ["a.desktop", "b.desktop"])
        self.assertEqual(layout.folder_app_ids(), {"a.desktop", "b.desktop"})
        self.assertTrue(layout.rename_folder(folder_id, "Projects"))
        reloaded = ApplicationLayout(self.path)
        self.assertEqual(reloaded.folders[folder_id]["name"], "Projects")
        self.assertTrue(reloaded.remove_from_folder(folder_id, "a.desktop"))
        self.assertTrue(reloaded.delete_folder(folder_id))
        self.assertEqual(reloaded.folder_app_ids(), set())

    def test_folder_names_are_case_insensitive_and_rename_rejects_duplicates(self):
        layout = ApplicationLayout(self.path)
        folder_id = layout.create_folder("Work", ["a.desktop", "b.desktop"])
        self.assertFalse(layout.is_folder_name_available(" work "))
        with self.assertRaisesRegex(ValueError, "already exists"):
            layout.create_folder("WORK", ["b.desktop", "c.desktop"])
        other_id = layout.create_folder("Personal", ["b.desktop", "c.desktop"])
        self.assertFalse(layout.rename_folder(other_id, " work "))
        self.assertTrue(layout.rename_folder(folder_id, " work "))
        self.assertEqual(layout.folders[folder_id]["name"], "work")


if __name__ == "__main__":
    unittest.main()
