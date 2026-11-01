import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "leaf_control.py"
spec = importlib.util.spec_from_file_location("leaf_control", MODULE_PATH)
leaf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leaf)


class CalculatorTests(unittest.TestCase):
    def test_arithmetic_precedence(self):
        self.assertEqual(leaf.safe_calculate("2 + 3 * 4"), "14")

    def test_parentheses_and_negative_numbers(self):
        self.assertEqual(leaf.safe_calculate("-(8 - 10) / 2"), "1.0")

    def test_rejects_names_and_calls(self):
        for expression in ("__import__('os')", "open('/etc/passwd')", "x + 2", "(1, 2)"):
            with self.subTest(expression=expression), self.assertRaises((ValueError, SyntaxError)):
                leaf.safe_calculate(expression)

    def test_duration_formatting_for_timer_and_stopwatch(self):
        self.assertEqual(leaf.format_duration(0), "00:00:00")
        self.assertEqual(leaf.format_duration(3661.259, hundredths=True), "01:01:01.25")
        self.assertEqual(leaf.format_duration(-5), "00:00:00")

    def test_rejects_large_exponents(self):
        with self.assertRaises(ValueError):
            leaf.safe_calculate("2 ** 1000")

    def test_scientific_functions_and_constants(self):
        self.assertEqual(leaf.safe_calculate("sqrt(81)"), "9.0")
        self.assertAlmostEqual(float(leaf.safe_calculate("sin(90)")), 1.0)
        self.assertEqual(leaf.safe_calculate("factorial(5)"), "120")
        self.assertAlmostEqual(float(leaf.safe_calculate("log(100, 10)")), 2.0)
        self.assertAlmostEqual(float(leaf.safe_calculate("sin(pi / 2)", "radians")), 1.0)
        self.assertAlmostEqual(float(leaf.safe_calculate("asin(1)")), 90.0)

    def test_scientific_functions_reject_unsafe_or_invalid_inputs(self):
        for expression in ("__import__('os')", "sqrt(-1)", "factorial(2.5)", "factorial(171)", "math.sin(1)"):
            with self.subTest(expression=expression), self.assertRaises((ValueError, SyntaxError)):
                leaf.safe_calculate(expression)

    def test_calculation_history_is_bounded_and_recovers_from_invalid_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "history.json"
            history = [{"expression": f"{value} + 1", "result": str(value + 1)} for value in range(25)]
            leaf.save_calculation_history(path, history)
            loaded = leaf.load_calculation_history(path)
            self.assertEqual(len(loaded), 20)
            self.assertEqual(loaded[0]["expression"], "5 + 1")
            path.write_text("invalid json")
            self.assertEqual(leaf.load_calculation_history(path), [])


class AppLaunchTests(unittest.TestCase):
    def setUp(self):
        self.recorded, self.messages, self.presented = [], [], []
        self.launcher = SimpleNamespace(
            _launching_app_ids={"test.desktop"},
            layout=SimpleNamespace(record_use=lambda app: self.recorded.append(app)),
            _message=self.messages.append,
            _place_drawer_on_focused_monitor=lambda: None,
            _show_drawer=lambda: self.presented.append(True),
            window=SimpleNamespace(present=lambda: None),
        )

    def test_failed_async_launch_reopens_leaf_and_does_not_record_usage(self):
        app = SimpleNamespace(get_display_name=lambda: "Broken App")
        leaf.LeafControl._finish_app_launch(self.launcher, app, "test.desktop", False, "unavailable")
        self.assertEqual(self.messages, ["Could not launch Broken App: unavailable."])
        self.assertEqual(self.recorded, [])
        self.assertEqual(self.presented, [True])
        self.assertEqual(self.launcher._launching_app_ids, set())

    def test_successful_async_launch_records_usage_and_clears_pending(self):
        app = SimpleNamespace(get_display_name=lambda: "Working App")
        leaf.LeafControl._finish_app_launch(self.launcher, app, "test.desktop", True, None)
        self.assertEqual(self.recorded, [app])
        self.assertEqual(self.presented, [])
        self.assertEqual(self.messages, [])
        self.assertEqual(self.launcher._launching_app_ids, set())


class LauncherDragDropTests(unittest.TestCase):
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

    class FakeDropTarget:
        class Widget:
            def get_width(self):
                return 100

            def get_height(self):
                return 100

            def remove_css_class(self, _class_name):
                pass

        def get_widget(self):
            return self.Widget()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.apps = [self.FakeApp("a.desktop", "Alpha"), self.FakeApp("b.desktop", "Beta")]
        self.layout = leaf.ApplicationLayout(Path(self.temp_dir.name) / "layout.json")
        self.launcher = SimpleNamespace(
            apps=self.apps,
            layout=self.layout,
            search=SimpleNamespace(get_text=lambda: ""),
            _populate_apps=lambda _query: None,
            _clear_drop_target=leaf.LeafControl._clear_drop_target,
        )
        self.launcher._create_folder_dialog = lambda *_args, **_kwargs: None
        self.launcher.settings = {"sort_apps_by_usage": False}
        self.launcher.app_page_capacity = 12
        self.launcher._finish_grid_placement = lambda _source: False
        def place(source, target, after=False):
            self.launcher.app_grid_entry_ids = self.layout.order[:] or [app.get_id() for app in self.apps]
            return leaf.LeafControl._place_grid_entry(self.launcher, source, target, after)
        self.launcher._place_grid_entry = place


    def tearDown(self):
        self.temp_dir.cleanup()

    def test_drop_on_left_half_reorders_before(self):
        self.layout.order = ["b.desktop", "a.desktop"]
        with patch.object(leaf.GLib, "idle_add", return_value=1):
            handled = leaf.LeafControl._drop_app(self.launcher, self.FakeDropTarget(), "a.desktop", 25, 20, "b.desktop")
        self.assertTrue(handled)
        self.assertEqual(self.layout.order, ["a.desktop", "b.desktop"])

    def test_drop_on_right_half_reorders_after(self):
        self.layout.order = ["a.desktop", "b.desktop"]
        with patch.object(leaf.GLib, "idle_add", return_value=1):
            handled = leaf.LeafControl._drop_app(self.launcher, self.FakeDropTarget(), "a.desktop", 75, 20, "b.desktop")
        self.assertTrue(handled)
        self.assertEqual(self.layout.order, ["b.desktop", "a.desktop"])

    def test_drop_in_center_creates_a_group(self):
        self.layout.order = ["a.desktop", "b.desktop"]
        called = []
        self.launcher._create_folder_dialog = lambda *_args, **_kwargs: called.append(True)
        with patch.object(leaf.GLib, "idle_add", return_value=1):
            handled = leaf.LeafControl._drop_app(self.launcher, self.FakeDropTarget(), "a.desktop", 50, 20, "b.desktop")
        self.assertTrue(handled)
        self.assertEqual(called, [])
        self.assertEqual(len(self.layout.folders), 1)
        folder_id, folder = next(iter(self.layout.folders.items()))
        self.assertEqual(folder["name"], "New group")
        self.assertEqual(set(folder["apps"]), {"a.desktop", "b.desktop"})
        restored = leaf.ApplicationLayout(Path(self.temp_dir.name) / "layout.json")
        self.assertEqual(set(restored.folders[folder_id]["apps"]), {"a.desktop", "b.desktop"})

    def test_drop_on_group_adds_application(self):
        folder_id = self.layout.create_folder("Work", ["a.desktop", "b.desktop"])
        self.apps.append(self.FakeApp("c.desktop", "Gamma"))
        with patch.object(leaf.GLib, "idle_add", return_value=1):
            handled = leaf.LeafControl._drop_app(self.launcher, self.FakeDropTarget(), "c.desktop", 50, 20, f"folder:{folder_id}")
        self.assertTrue(handled)
        self.assertEqual(set(self.layout.folders[folder_id]["apps"]), {"a.desktop", "b.desktop", "c.desktop"})

    def test_drop_group_member_to_edge_ungroups_and_reorders(self):
        folder_id = self.layout.create_folder("Work", ["a.desktop", "b.desktop"])
        self.layout.order = ["a.desktop", "b.desktop"]
        with patch.object(leaf.GLib, "idle_add", return_value=1):
            handled = leaf.LeafControl._drop_app(self.launcher, self.FakeDropTarget(), "a.desktop", 25, 50, "b.desktop")
        self.assertTrue(handled)
        self.assertEqual(self.layout.folder_app_ids(), {"b.desktop"})
        self.assertEqual(self.layout.order, ["a.desktop", "b.desktop"])


class NotesTests(unittest.TestCase):
    def test_existing_note_store_is_secured_without_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            root.mkdir(mode=0o755)
            store = root / "notes.json"
            store.write_text('{"notes": [{"id": "old", "body": "private"}], "trash": []}')
            store.chmod(0o644)
            leaf.secure_notes_storage(store)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o600)
            self.assertEqual(leaf.load_notes(store)[0][0]["body"], "private")

    def test_failed_note_replace_keeps_previous_store(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "notes.json"
            store.write_text("previous")
            with patch.object(Path, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    leaf.save_notes(store, [{"id": "new", "body": "new"}], [])
            self.assertEqual(store.read_text(), "previous")
            self.assertEqual(list(store.parent.iterdir()), [store])

    def test_notes_save_keeps_store_private_under_permissive_umask(self):
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "state"
                root.mkdir(mode=0o755)
                store = root / "notes.json"
                if existing:
                    store.write_text("old")
                    store.chmod(0o644)
                original_umask = os.umask(0o022)
                try:
                    leaf.save_notes(store, [{"id": "note-1", "title": "Private", "body": "secret"}], [])
                finally:
                    os.umask(original_umask)
                self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o600)
                self.assertEqual(leaf.load_notes(store)[0][0]["body"], "secret")
                self.assertEqual(list(root.iterdir()), [store])

    def test_notes_save_restore_and_export_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "notes.json"
            note = {"id": "note-1", "title": "Ideas", "body": "- first\n- second", "pinned": True, "updated": "1"}
            trash = [{"id": "note-2", "title": "Old", "body": "recoverable", "pinned": False, "updated": "0"}]
            leaf.save_notes(store, [note], trash)
            notes, deleted = leaf.load_notes(store)
            self.assertEqual(notes, [note])
            self.assertEqual(deleted, trash)
            exported = root / "ideas.md"
            leaf.export_note_markdown(notes[0], exported)
            self.assertEqual(exported.read_text(), "# Ideas\n\n- first\n- second")

    def test_legacy_note_is_migrated_without_deleting_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "notes.txt"
            legacy.write_text("Existing note content")
            notes, trash = leaf.load_notes(root / "notes.json", legacy)
            self.assertEqual(notes[0]["body"], "Existing note content")
            self.assertEqual(trash, [])
            self.assertTrue(legacy.exists())


class SettingsTests(unittest.TestCase):
    def test_settings_round_trip_and_invalid_values_use_defaults(self):
        self.assertEqual(leaf.DEFAULT_SETTINGS["app_tile_style"], "minimal")
        self.assertEqual(leaf.DEFAULT_SETTINGS["app_tile_corners"], "soft")
        self.assertEqual(leaf.DEFAULT_SETTINGS["app_icon_size"], "standard")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            settings = {
                "clock_24h": True, "temperature_unit": "F", "refresh_seconds": 5, "angle_unit": "radians",
                "start_page": "System", "app_tile_style": "card", "app_tile_corners": "rounded", "app_icon_size": "large",
            }
            leaf.save_settings(path, settings)
            self.assertEqual(leaf.load_settings(path), {**leaf.DEFAULT_SETTINGS, **settings})
            path.write_text('{"clock_24h": "yes", "temperature_unit": "K", "refresh_seconds": 3, "app_tile_style": "fancy", "app_tile_corners": "pill", "app_icon_size": "huge"}')
            self.assertEqual(leaf.load_settings(path), leaf.DEFAULT_SETTINGS)

    def test_temperature_display_conversion(self):
        self.assertEqual(leaf.format_temperature_text("CPU: 45.0 °C, GPU: -10.0 °C", "F"), "CPU: 113.0 °F, GPU: 14.0 °F")
        self.assertEqual(leaf.format_temperature_text("CPU: 45.0 °C", "C"), "CPU: 45.0 °C")


class OmarchyThemeTests(unittest.TestCase):
    def test_palette_reads_current_theme_colors_and_derives_surfaces(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "colors.toml"
            path.write_text('mode = "dark"\naccent = "#E36B2C"\nbackground = "#151718"\nforeground = "#D8D0B8"\n')
            palette = leaf.load_omarchy_palette(path)
        self.assertEqual(palette["ui_accent"], "#E36B2C")
        self.assertEqual(palette["ui_bg"], "#151718")
        self.assertEqual(palette["ui_text"], "#D8D0B8")
        self.assertEqual(palette["mode"], "dark")

    def test_light_palette_uses_light_surfaces_and_contrasting_accent_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "colors.toml"
            path.write_text('mode = "light"\naccent = "#B43E32"\nbackground = "#EEE8DA"\nforeground = "#161715"\n')
            palette = leaf.load_omarchy_palette(path)
        self.assertEqual(palette["ui_bg"], "#EEE8DA")
        self.assertEqual(palette["ui_accent_ink"], "#FFFFFF")
        self.assertEqual(palette["ui_hover_tint"], "#000000")


class OmarchyPluginCatalogTests(unittest.TestCase):
    def test_catalog_includes_only_enabled_first_party_surface_plugins(self):
        payload = [
            {"id": "omarchy.audio", "firstParty": True, "enabled": True, "kinds": ["panel"]},
            {"id": "omarchy.weather", "firstParty": True, "enabled": False, "kinds": ["panel"]},
            {"id": "community.example", "firstParty": False, "enabled": True, "kinds": ["panel"]},
            {"id": "omarchy.bad-kind", "firstParty": True, "enabled": True, "kinds": ["service"]},
        ]
        self.assertEqual(leaf.parse_omarchy_plugin_catalog(json.dumps(payload)), {"omarchy.audio"})

    def test_catalog_rejects_non_list_payloads(self):
        with self.assertRaises(ValueError):
            leaf.parse_omarchy_plugin_catalog('{"plugins": []}')


class ContextMenuTests(unittest.TestCase):
    def test_app_menu_contains_only_short_relevant_actions(self):
        app = SimpleNamespace(get_id=lambda: "app.desktop")
        layout = SimpleNamespace(folders={}, is_favorite=lambda _app: False, folder_for=lambda _app: None)
        owner = SimpleNamespace(
            layout=layout,
            _windows_for_app=lambda _app: [],
            _launch_app=lambda _app: None,
            _create_folder_dialog=lambda _app: None,
        )
        entries = leaf.LeafControl._app_context_entries(owner, app)
        self.assertEqual([entry[0] for entry in entries], ["Open app", "Pin to app bar", "Create group…"])

    def test_app_menu_adds_only_the_current_group_action(self):
        app = SimpleNamespace(get_id=lambda: "app.desktop")
        layout = SimpleNamespace(folders={"work": {"apps": ["app.desktop"]}}, is_favorite=lambda _app: True, folder_for=lambda _app: "work")
        owner = SimpleNamespace(
            layout=layout,
            _windows_for_app=lambda _app: [object()],
            _launch_app=lambda _app: None,
            _create_folder_dialog=lambda _app: None,
        )
        entries = leaf.LeafControl._app_context_entries(owner, app)
        self.assertEqual(
            [entry[0] for entry in entries],
            ["New window", "Unpin from app bar", "Create group…", "Remove from group"],
        )

    def test_app_menu_uses_one_group_picker_action_instead_of_each_destination(self):
        app = SimpleNamespace(get_id=lambda: "app.desktop")
        groups = {
            "work": {"name": "Work", "apps": ["a", "b"]},
            "media": {"name": "Media", "apps": ["c", "d"]},
        }
        layout = SimpleNamespace(
            folders=groups,
            is_favorite=lambda _app: False,
            folder_for=lambda _app: None,
        )
        owner = SimpleNamespace(
            layout=layout,
            _windows_for_app=lambda _app: [],
            _launch_app=lambda _app: None,
            _create_folder_dialog=lambda _app: None,
            _show_group_picker=lambda *_args: None,
        )
        entries = leaf.LeafControl._app_context_entries(owner, app, object())
        labels = [entry[0] for entry in entries]
        self.assertEqual(labels.count("Move to group…"), 1)
        self.assertEqual(len(labels), 4)
        self.assertNotIn("Move to Work", labels)
        self.assertNotIn("Move to Media", labels)

    def test_group_member_menu_uses_one_destination_picker_action(self):
        app = SimpleNamespace(get_id=lambda: "app.desktop")
        layout = SimpleNamespace(
            folders={
                "work": {"name": "Work", "apps": ["app.desktop"]},
                "media": {"name": "Media", "apps": ["other.desktop"]},
            },
            is_favorite=lambda _app: False,
        )
        owner = SimpleNamespace(
            layout=layout,
            _activate_app=lambda _app: None,
            _remove_app_from_folder=lambda *_args: None,
            _show_group_picker=lambda *_args: None,
        )
        entries = leaf.LeafControl._folder_app_context_entries(owner, app, "work", object())
        self.assertEqual(
            [entry[0] for entry in entries],
            ["Open app", "Pin to app bar", "Remove from group", "Move to group…"],
        )

    def test_escape_closes_context_menu_before_dismissing_drawer(self):
        class Popover:
            closed = False

            def popdown(self):
                self.closed = True

        popover = Popover()
        dismissed = []
        owner = SimpleNamespace(
            active_context_popover=popover,
            dock_popover=None,
            command_panel=None,
            create_group_panel=None,
            open_folder_window=None,
            active_page="Apps",
            _dismiss_drawer=lambda: dismissed.append(True),
        )
        self.assertTrue(leaf.LeafControl._handle_escape(owner))
        self.assertTrue(popover.closed)
        self.assertEqual(dismissed, [])

    def test_escape_closes_group_overlay_before_clearing_search_or_drawer(self):
        closed = []
        dismissed = []
        owner = SimpleNamespace(
            active_context_popover=None,
            dock_popover=None,
            command_panel=None,
            create_group_panel=object(),
            open_folder_window=None,
            active_page="Apps",
            _close_group_create=lambda **kwargs: closed.append(kwargs),
            _dismiss_drawer=lambda: dismissed.append(True),
        )
        self.assertTrue(leaf.LeafControl._handle_escape(owner))
        self.assertEqual(closed, [{"restore_focus": False}])
        self.assertEqual(dismissed, [])

    def test_escape_clears_search_before_dismissing_drawer(self):
        class Search:
            value = "mail"

            def get_text(self):
                return self.value

            def set_text(self, value):
                self.value = value

        search = Search()
        dismissed = []
        owner = SimpleNamespace(
            active_context_popover=None,
            dock_popover=None,
            command_panel=None,
            create_group_panel=None,
            open_folder_window=None,
            active_page="Apps",
            search=search,
            _dismiss_drawer=lambda: dismissed.append(True),
        )
        self.assertTrue(leaf.LeafControl._handle_escape(owner))
        self.assertEqual(search.value, "")
        self.assertEqual(dismissed, [])


class DrawerInteractionTests(unittest.TestCase):
    def test_drawer_hides_only_after_slide_down_finishes_and_can_reopen(self):
        class Revealer:
            def __init__(self):
                self.target = True
                self.child_revealed = True

            def set_reveal_child(self, value):
                self.target = value

            def get_child_revealed(self):
                return self.child_revealed

        class Window:
            visible = True

            def set_visible(self, value):
                self.visible = value

        revealer = Revealer()
        window = Window()
        app = SimpleNamespace(
            settings=dict(leaf.DEFAULT_SETTINGS),
            drawer_revealer=revealer,
            drawer_dismiss_pending=False,
            window=window,
            active_page="Apps",
            _hide_workspace_strip=lambda: None,
        )
        leaf.LeafControl._dismiss_drawer(app)
        self.assertFalse(revealer.target)
        self.assertTrue(window.visible)
        self.assertTrue(app.drawer_dismiss_pending)

        revealer.child_revealed = False
        leaf.LeafControl._drawer_reveal_changed(app, revealer, None)
        self.assertFalse(window.visible)
        self.assertFalse(app.drawer_dismiss_pending)

        self.assertEqual(leaf.LeafControl._show_drawer(app), leaf.GLib.SOURCE_REMOVE)
        self.assertTrue(revealer.target)
        self.assertFalse(app.drawer_dismiss_pending)


class AppGridScrollTests(unittest.TestCase):
    class Button:
        def __init__(self, owner, name):
            self.owner = owner
            self.name = name

        def get_visible(self):
            return True

        def get_sensitive(self):
            return True

        def grab_focus(self):
            self.owner.focus = self

    class Grid:
        def __init__(self, buttons=None, visible=True):
            self.buttons = buttons or []
            self.visible = visible

        def get_visible(self):
            return self.visible

    def _launcher(self):
        launcher = SimpleNamespace(
            active_page="Apps",
            app_focus_scroll_delta=0.0,
            app_focus_scroll_queue=[],
            app_page_focus_pending=False,
            app_page_count=3,
            app_page_index=0,
            command_panel=None,
            create_group_panel=None,
            open_folder_window=None,
            dock_popover=None,
            search=SimpleNamespace(get_text=lambda: ""),
        )
        launcher.pages = [
            [self.Button(launcher, f"page-{page}-app-{index}") for index in range(2)]
            for page in range(1, 4)
        ]
        launcher.app_grid = self.Grid(launcher.pages[0])
        launcher.favorites_grid = self.Grid()
        launcher.files_grid = self.Grid(visible=False)
        launcher.folders_grid = self.Grid(visible=False)
        launcher.window = SimpleNamespace(get_focus=lambda: launcher.focus)
        launcher._grid_buttons = lambda grid: grid.buttons if grid.visible else []

        def populate(_query=""):
            launcher.app_grid.buttons = launcher.pages[launcher.app_page_index]

        launcher._sync_workspace_strip = lambda: None
        launcher._populate_apps = populate
        launcher._change_app_page = lambda direction: leaf.LeafControl._change_app_page(launcher, direction)
        launcher._step_app_focus = lambda direction: leaf.LeafControl._step_app_focus(launcher, direction)
        launcher.focus = launcher.app_grid.buttons[-1]
        return launcher

    def test_wheel_crosses_page_edges_and_queues_fast_ticks(self):
        launcher = self._launcher()
        pending_idle = []

        def idle_add(callback, *args):
            pending_idle.append((callback, args))
            return len(pending_idle)

        def flush_idle():
            while pending_idle:
                callback, args = pending_idle.pop(0)
                callback(*args)

        with patch.object(leaf.GLib, "idle_add", side_effect=idle_add):
            handled = leaf.LeafControl._on_app_focus_scroll(launcher, None, 0, 1)
            self.assertTrue(handled)
            self.assertEqual(launcher.app_page_index, 1)
            self.assertTrue(launcher.app_page_focus_pending)

            # Two more wheel ticks arrive before GTK finishes the page rebuild.
            leaf.LeafControl._on_app_focus_scroll(launcher, None, 0, 1)
            leaf.LeafControl._on_app_focus_scroll(launcher, None, 0, 1)
            flush_idle()
            self.assertEqual(launcher.app_page_index, 2)
            self.assertIs(launcher.focus, launcher.pages[2][0])

            # Scrolling upward from the first tile returns to the prior page's last tile.
            leaf.LeafControl._on_app_focus_scroll(launcher, None, 0, -1)
            flush_idle()

        self.assertEqual(launcher.app_page_index, 1)
        self.assertIs(launcher.focus, launcher.pages[1][-1])


class SystemHistoryTests(unittest.TestCase):
    def test_history_window_tracks_refresh_interval(self):
        self.assertEqual(leaf.history_window_seconds(2), 180)
        self.assertEqual(leaf.history_window_label(2), "3 min")
        self.assertEqual(leaf.history_window_label(1), "1.5 min")

    def test_recent_history_filters_old_samples_and_preserves_order(self):
        samples = [(70.0, 10), (90.0, 20), (100.0, 30)]
        self.assertEqual(leaf.recent_history_samples(samples, 20, now=100.0), samples[1:])

    def test_chart_accessibility_summary_counts_only_visible_window(self):
        captured = {}
        summary_target = SimpleNamespace(
            settings={"refresh_seconds": 1},
            histories={"cpu": [(0.0, 3), (40.0, 27), (99.0, 64)]},
            chart_areas={"cpu": object()},
            _set_accessible_info=lambda _chart, name, description: captured.update(
                name=name, description=description
            ),
        )
        with patch.object(leaf.time, "monotonic", return_value=100.0):
            leaf.LeafControl._update_history_accessibility(summary_target)
        self.assertIn("64 percent", captured["description"])
        self.assertIn("2 samples are plotted", captured["description"])


class LocalDataTests(unittest.TestCase):
    def test_memory_reader_returns_numeric_values_when_available(self):
        values = leaf.read_memory()
        if Path("/proc/meminfo").exists():
            self.assertGreater(values.get("MemTotal", 0), 0)
            self.assertGreaterEqual(values.get("MemAvailable", 0), 0)

    def test_storage_and_network_readers_return_structured_local_values(self):
        volumes = leaf.mounted_filesystems()
        for device, mountpoint, total, used, free in volumes:
            self.assertTrue(device.startswith("/dev/"))
            self.assertTrue(mountpoint.startswith("/"))
            self.assertGreater(total, 0)
            self.assertGreaterEqual(used, 0)
            self.assertGreaterEqual(free, 0)
        for snapshot in (leaf.network_snapshot(), leaf.disk_io_snapshot()):
            if snapshot is not None:
                self.assertEqual(len(snapshot), 2)
                self.assertTrue(all(value >= 0 for value in snapshot))

    def test_storage_summary_groups_shared_device_mounts(self):
        volumes = [
            ("/dev/mapper/root", "/", 1000, 400, 600),
            ("/dev/mapper/root", "/home", 1000, 400, 600),
            ("/dev/sdb1", "/mnt/archive", 2000, 1000, 1000),
        ]
        self.assertEqual(leaf.group_storage_mounts(volumes), [
            ("/dev/mapper/root", ["/", "/home"], 1000, 400),
            ("/dev/sdb1", ["/mnt/archive"], 2000, 1000),
        ])

    def test_network_interface_reader_keeps_interface_counters_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dev"
            path.write_text(
                "Inter-| Receive | Transmit\n"
                " face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n"
                "  lo: 99 0 0 0 0 0 0 0 88 0 0 0 0 0 0 0\n"
                "  enp2s0: 1000 1 0 0 0 0 0 0 2000 1 0 0 0 0 0 0\n"
                "  wlan0: 3000 1 0 0 0 0 0 0 4000 1 0 0 0 0 0 0\n"
            )
            self.assertEqual(leaf.network_interface_snapshot(path), {
                "enp2s0": (1000, 2000), "wlan0": (3000, 4000),
            })

    def test_per_core_cpu_parser_and_delta(self):
        with tempfile.TemporaryDirectory() as directory:
            stat = Path(directory) / "stat"
            stat.write_text("cpu 10 0 4 80 2\ncpu0 5 0 2 40 1\ncpu1 5 0 2 40 1\n")
            previous = leaf.cpu_core_snapshot(stat)
            stat.write_text("cpu 20 0 5 85 2\ncpu0 10 0 3 45 1\ncpu1 10 0 2 40 1\n")
            current = leaf.cpu_core_snapshot(stat)
            self.assertEqual(set(current), {"cpu0", "cpu1"})
            self.assertEqual(leaf.cpu_usage_between(previous["cpu0"], current["cpu0"]), 55)

    def test_capability_detected_temperature_and_fan_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hwmon = root / "hwmon" / "hwmon0"
            hwmon.mkdir(parents=True)
            (hwmon / "name").write_text("testchip")
            (hwmon / "temp1_label").write_text("CPU")
            (hwmon / "temp1_input").write_text("45321")
            (hwmon / "fan1_input").write_text("1200")
            thermal = root / "thermal" / "thermal_zone0"
            thermal.mkdir(parents=True)
            (thermal / "type").write_text("acpitz")
            (thermal / "temp").write_text("41000")
            values = leaf.hardware_sensor_readings(root / "hwmon", root / "thermal")
            self.assertIn(("testchip · CPU", "45.3 °C"), values)
            self.assertIn(("testchip · Fan 1", "1200 RPM"), values)
            self.assertIn(("Thermal · acpitz", "41.0 °C"), values)

    def test_gpu_sysfs_metrics_use_only_exposed_values(self):
        with tempfile.TemporaryDirectory() as directory:
            device = Path(directory) / "card0" / "device"
            device.mkdir(parents=True)
            for name, value in {"vendor": "0x1002", "device": "0x7550", "gpu_busy_percent": "23", "mem_info_vram_used": "500000000", "mem_info_vram_total": "2000000000", "pp_dpm_sclk": "0: 400Mhz\n1: 1200Mhz *"}.items():
                (device / name).write_text(value)
            gpu = leaf.read_gpu_metrics(Path(directory))[0]
            self.assertEqual(gpu["vendor"], "AMD")
            self.assertEqual(gpu["utilization"], 23)
            self.assertEqual(gpu["vram_used"], 500000000)
            self.assertEqual(gpu["clock"], 1200)
            self.assertIsNone(gpu["power_watts"])


if __name__ == "__main__":
    unittest.main()
