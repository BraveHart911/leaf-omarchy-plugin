import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app_layout import ApplicationLayout
from install import migrate_shell, PLUGIN_ID, RUNTIME_FILES
from layout_rules import grid_columns, page_capacity
from preferences import DEFAULT_SETTINGS, PREFERENCES, load_settings, save_settings
from workspace_model import workspace_sets, workspace_target


ROOT = Path(__file__).resolve().parents[1]


class PreferenceTests(unittest.TestCase):
    def test_every_control_value_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for pref in PREFERENCES:
                values = [v for v, _label in pref.choices] if pref.choices else list(range(pref.bounds[0], pref.bounds[1] + 1, pref.bounds[2])) if pref.bounds else [True, False]
                for value in values:
                    with self.subTest(key=pref.key, value=value):
                        save_settings(path, {pref.key: value})
                        self.assertEqual(load_settings(path)[pref.key], value)

    def test_bool_cannot_be_mistaken_for_integer_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(path, {"grid_columns": False, "refresh_seconds": True, "dock_padding": True})
            self.assertEqual(load_settings(path), DEFAULT_SETTINGS)

    def test_old_indicator_and_removed_page_migrate_without_losing_other_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(path, {"start_page": "Tools", "app_page_indicator_style": "funny", "window_transparency": 47, "workspace_wrap": True})
            loaded = load_settings(path)
            self.assertEqual(loaded["start_page"], "Apps")
            self.assertEqual(loaded["app_page_indicator_style"], "modern")
            self.assertEqual(loaded["window_transparency"], 47)
            self.assertTrue(loaded["workspace_wrap"])

    def test_defaults_do_not_share_mutable_search_lists(self):
        with tempfile.TemporaryDirectory() as directory:
            first = load_settings(Path(directory) / "missing")
            first["search_excludes"].append("changed")
            self.assertEqual(load_settings(Path(directory) / "missing")["search_excludes"], [])


class PinTests(unittest.TestCase):
    def test_pin_order_and_removal_persist_without_changing_grid_or_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "layout.json"
            layout = ApplicationLayout(path)
            layout.favorites = ["one", "two", "three"]
            layout.order = ["three", "two", "one"]
            layout.folders = {"group": {"name": "Work", "apps": ["one", "two"]}}
            layout.save()
            self.assertTrue(layout.move_favorite("three", -1))
            self.assertFalse(layout.move_favorite("one", -1))
            layout.remove_favorite("two")
            loaded = ApplicationLayout(path)
            self.assertEqual(loaded.favorites, ["one", "three"])
            self.assertEqual(loaded.order, ["three", "two", "one"])
            self.assertEqual(loaded.folders, layout.folders)

    def test_failed_pin_save_keeps_previous_order(self):
        layout = ApplicationLayout(Path("/nonexistent/leaf-test"))
        layout.favorites = ["one", "two"]
        with patch.object(layout, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                layout.move_favorite("two", -1)
            self.assertEqual(layout.favorites, ["one", "two"])


class WorkspaceTests(unittest.TestCase):
    def test_shared_desktops_work_with_arbitrary_connector_names(self):
        monitors = [{"name": "eDP-1", "activeWorkspace": {"id": 1}}, {"name": "USB-C-7", "activeWorkspace": {"id": 3}}]
        sets = workspace_sets(monitors, [])
        self.assertEqual(sets["eDP-1"], list(range(1, 11)))
        self.assertEqual(sets["USB-C-7"], list(range(1, 11)))

    def test_persistent_rules_preserve_separate_and_nonconsecutive_ids(self):
        monitors = [{"name": "Display-A"}, {"name": "Display-B"}]
        spaces = [{"id": n, "monitor": name, "ispersistent": True} for name, ids in [("Display-A", [1, 3, 5]), ("Display-B", [21, 22, 25])] for n in ids]
        self.assertEqual(workspace_target(3, "Display-B", monitors, spaces), 25)
        with self.assertRaises(ValueError):
            workspace_target(4, "Display-B", monitors, spaces)
        with self.assertRaises(ValueError):
            workspace_target(1, "unplugged", monitors, spaces)


class ResponsiveTests(unittest.TestCase):
    def test_page_capacity_uses_fitted_columns_in_narrow_windows(self):
        narrow = grid_columns(480, 124, 14, 12)
        self.assertEqual(narrow, 3)
        self.assertEqual(page_capacity(narrow, 280, 112, 14), 6)
        self.assertEqual(grid_columns(1920, 124, 14, 12), 12)
        self.assertEqual(grid_columns(200, 124, 14, 12), 1)


class InstallationTests(unittest.TestCase):
    def test_migration_preserves_bar_location_and_unrelated_configuration(self):
        shell = {"bar": {"layout": {"left": [{"id": "clock"}], "right": [{"id": "local.leaf-control", "custom": 4}, {"id": "audio"}]}}, "plugins": [{"id": "local.leaf-workspaces"}, {"id": "weather"}], "idle": {"lock": 300}}
        original = copy.deepcopy(shell)
        migrated = migrate_shell(shell)
        self.assertEqual(shell, original)
        self.assertEqual(migrated["bar"]["layout"]["right"], [{"id": PLUGIN_ID, "custom": 4}, {"id": "audio"}])
        self.assertEqual(migrated["idle"], shell["idle"])
        self.assertEqual(migrated["plugins"], [{"id": "weather"}])
        self.assertEqual(migrate_shell(migrated), migrated)

    def test_clean_install_update_and_uninstall_under_fresh_xdg_directories(self):
        with tempfile.TemporaryDirectory(prefix="leaf clean install ") as directory:
            base = Path(directory)
            environment = os.environ.copy()
            for key, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state"), ("XDG_BIN_HOME", "bin")):
                environment[key] = str(base / folder)
            default = base / "omarchy/config/omarchy/shell.json"
            default.parent.mkdir(parents=True)
            default.write_text(json.dumps({"version": 1, "idle": {"lock": 300},
                "bar": {"layout": {"left": [{"id": "omarchy.menu"}], "right": [{"id": "omarchy.audio"}]}},
                "plugins": []}))
            environment["OMARCHY_PATH"] = str(base / "omarchy")
            environment["LEAF_INSTALL_NO_RESCAN"] = "1"
            def install(*args):
                return subprocess.run([sys.executable, str(ROOT / "install.py"), *args], env=environment, capture_output=True, text=True, check=True)
            install()
            plugin = base / "config/omarchy/plugins" / PLUGIN_ID
            self.assertTrue(all((plugin / name).is_file() for name in RUNTIME_FILES))
            manifest = json.loads((plugin / "manifest.json").read_text())
            self.assertEqual(set(manifest["kinds"]), {"bar-widget", "menu"})
            shell = json.loads((base / "config/omarchy/shell.json").read_text())
            self.assertEqual(shell["idle"]["lock"], 300)
            self.assertEqual(shell["bar"]["layout"]["left"], [{"id": "omarchy.menu"}])
            self.assertEqual(shell["bar"]["layout"]["right"], [{"id": "omarchy.audio"}, {"id": PLUGIN_ID}])
            settings = base / "config/leaf-control/settings.json"
            save_settings(settings, {"dock_icon_size": 48})
            install("--update")
            self.assertEqual(load_settings(settings)["dock_icon_size"], 48)
            install("--uninstall")
            self.assertFalse(plugin.exists())
            self.assertTrue(settings.exists())
            self.assertFalse((base / "bin/leaf-control").exists())

    def test_integrations_restore_previous_files_and_preserve_user_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            environment = os.environ.copy()
            for key, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                                ("XDG_STATE_HOME", "state"), ("XDG_BIN_HOME", "bin")):
                environment[key] = str(base / folder)
            environment["LEAF_INSTALL_NO_RESCAN"] = "1"
            plugin = base / "config/omarchy/plugins" / PLUGIN_ID
            plugin.mkdir(parents=True)
            (plugin / "manifest.json").write_text((ROOT / "manifest.json").read_text())
            launcher = base / "bin/leaf-control"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("original launcher\n")
            desktop = base / "data/applications/org.leafcontrol.LeafControl.desktop"
            desktop.parent.mkdir(parents=True)
            desktop.write_text("original desktop\n")
            shell = base / "config/omarchy/shell.json"
            shell.write_text('{"version":1}\n')
            def run(*args):
                return subprocess.run([sys.executable, str(ROOT / "install.py"), *args],
                                      env=environment, capture_output=True, text=True, check=True)
            run("--integration-only")
            self.assertEqual(shell.read_text(), '{"version":1}\n')
            self.assertTrue(launcher.read_text().startswith("#!/bin/sh"))
            run("--uninstall", "--integration-only")
            self.assertEqual(launcher.read_text(), "original launcher\n")
            self.assertEqual(desktop.read_text(), "original desktop\n")
            self.assertTrue(plugin.exists())
            run("--integration-only")
            launcher.write_text("user changed launcher\n")
            result = run("--uninstall", "--integration-only")
            self.assertIn("Kept modified file", result.stdout)
            self.assertEqual(launcher.read_text(), "user changed launcher\n")
            self.assertEqual(desktop.read_text(), "original desktop\n")

    def test_source_install_migrates_existing_leaf_id_and_bar_position(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            environment = os.environ.copy()
            for key, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                                ("XDG_STATE_HOME", "state"), ("XDG_BIN_HOME", "bin")):
                environment[key] = str(base / folder)
            environment["LEAF_INSTALL_NO_RESCAN"] = "1"
            old = base / "config/omarchy/plugins/local.leaf-control"
            old.mkdir(parents=True)
            (old / "manifest.json").write_text('{"id":"local.leaf-control"}')
            shell_path = base / "config/omarchy/shell.json"
            shell_path.write_text(json.dumps({"version": 1, "idle": {"lock": 600},
                "bar": {"layout": {"left": [{"id": "omarchy.menu"},
                                           {"id": "local.leaf-control", "custom": 7}],
                                   "right": [{"id": "omarchy.audio"}]}}, "plugins": []}))
            subprocess.run([sys.executable, str(ROOT / "install.py")], env=environment,
                           capture_output=True, text=True, check=True)
            shell = json.loads(shell_path.read_text())
            self.assertEqual(shell["bar"]["layout"]["left"][1], {"id": PLUGIN_ID, "custom": 7})
            self.assertEqual(shell["idle"]["lock"], 600)
            self.assertFalse(old.exists())
            self.assertTrue((old.parent / PLUGIN_ID / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
