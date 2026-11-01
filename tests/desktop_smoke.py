"""Interactive desktop QA with isolated user data and screenshots.

Run on a Hyprland session: GDK_BACKEND=wayland python3 tests/desktop_smoke.py
This opens a QA window, exercises real GTK controls, resizes it, then closes it.
It never launches applications, changes desktops, or edits user preferences.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import leaf_control as leaf
from preferences import PREFERENCES, load_settings

OUTPUT = ROOT / "artifacts/qa"
OUTPUT.mkdir(parents=True, exist_ok=True)
temporary = tempfile.TemporaryDirectory(prefix="leaf-desktop-qa-")
leaf.CONFIG_DIR = Path(temporary.name) / "config"
leaf.STATE_DIR = Path(temporary.name) / "state"
app = leaf.LeafControl()
app.set_application_id("org.leafcontrol.LeafQA")
app.set_flags(leaf.Gio.ApplicationFlags.NON_UNIQUE)
app.workspace_plugin_available = False
app.force_window = True
app.open_apps_on_start = True
app.settings["motion_style"] = "reduced"
app.file_search.reconfigure([], set())
failures = []
results = []
steps = []


def client():
    clients = json.loads(subprocess.check_output(["hyprctl", "clients", "-j"]))
    return next(c for c in clients if c.get("pid") == os.getpid())


def capture(name):
    c = client()
    x, y = c["at"]
    width, height = c["size"]
    subprocess.run(["grim", "-g", f"{x},{y} {width}x{height}", str(OUTPUT / f"{name}.png")], check=True)
    results.append({"capture": name, "size": [app.window.get_width(), app.window.get_height()], "page": app.active_page})


def resize(width, height):
    c = client()
    address = "address:" + c["address"]
    actions = []
    if not c["floating"]:
        actions.append(f'hl.dispatch(hl.dsp.window.float({{window={json.dumps(address)}, action="toggle"}}))')
    actions.append(f'hl.dispatch(hl.dsp.window.resize({{window={json.dumps(address)}, x={width}, y={height}, relative=false}}))')
    result = subprocess.run(["hyprctl", "repl", "\n".join(actions)], capture_output=True, text=True)
    if result.returncode or "error" in result.stdout.lower():
        raise RuntimeError(result.stderr or result.stdout)
    print(f"resize {width}x{height}: {result.stdout.strip()}", flush=True)
    app.window.set_default_size(width, height)


def assert_layout():
    width = app.window.get_width()
    if app.active_page == "Apps":
        buttons = app._grid_buttons(app.app_grid)
        assert buttons, "Application page has no apps"
        for button in buttons:
            pos = button.translate_coordinates(app.app_scroller, 0, 0)
            assert pos[0] >= -1 and pos[0] + button.get_width() <= app.app_scroller.get_width() + 1, ("App clipped horizontally", pos, width)
        assert app.running_dock_scroller.get_height() > 0
    if app.active_page == "Settings":
        assert app.settings_scroll.get_width() > 200
        assert app.settings_scroll.get_height() > 100
        assert app.settings_sidebar.get_visible() == (width >= 900)
        for row, _category, _search in app.settings_rows:
            if row.get_visible() and row.get_parent().get_visible():
                control = row.get_last_child()
                pos = control.translate_coordinates(row, 0, 0)
                assert pos[0] + control.get_width() <= row.get_width() + 1, ("Setting clipped", width)


def settings_interactions():
    assert app.window.is_fullscreen(), "Settings did not open fullscreen"
    for pref in PREFERENCES:
        control = app.setting_controls[pref.key]
        if pref.choices:
            control.set_selected((control.get_selected() + 1) % len(pref.choices))
        elif pref.bounds:
            control.set_value(pref.bounds[1])
        else:
            control.set_active(not control.get_active())
        assert load_settings(app.settings_path)[pref.key] == app.settings[pref.key], pref.key
    app._reset_settings()
    assert not app.settings["dock_show_names"]
    for item in app.apps[:3]:
        app.layout.toggle_favorite(item)
    original = app.layout.favorites[:]
    app._edit_pin(None, original[-1], -1)
    assert app.layout.favorites == [original[0], original[2], original[1]]
    app._edit_pin(None, original[1], 0)
    assert original[1] not in app.layout.favorites
    app.settings_category_buttons["dock"].set_active(True)
    app.settings_search.set_text("temperature")
    app._filter_settings()
    assert app.settings_sections["system"].get_visible()
    app.settings_search.set_text("no-such-setting-xyz")
    app._filter_settings()
    assert app.settings_empty.get_visible()
    app.settings_search.set_text("")
    app._filter_settings()


def prepare_groups():
    app._navigate(None, "Apps")
    app.qa_first_group = app.layout.create_folder("QA first", [leaf.app_identifier(a) for a in app.apps[:2]])
    app.qa_second_group = app.layout.create_folder("QA second", [leaf.app_identifier(a) for a in app.apps[2:4]])
    app._populate_apps()


def place_group():
    first = "folder:" + app.qa_first_group
    second = "folder:" + app.qa_second_group
    assert app._place_grid_entry(first, second, after=True)


def move_group_to_next_page():
    first = "folder:" + app.qa_first_group
    second = "folder:" + app.qa_second_group
    assert app.app_grid_entry_ids.index(first) == app.app_grid_entry_ids.index(second) + 1
    assert app._place_on_adjacent_page(first, 1)


def verify_group_and_gestures():
    first = "folder:" + app.qa_first_group
    assert app.app_page_index == 1
    assert first in app.app_grid_entry_ids[app.app_page_capacity:app.app_page_capacity * 2]
    restored = leaf.ApplicationLayout(app.layout.path)
    assert restored.order == app.layout.order
    assert len(restored.folders[app.qa_first_group]["apps"]) == 2
    expected = (app.app_page_index + 1) % app.app_page_count
    app._gesture_page(1)
    assert app.app_page_index == expected


def restore_qa_grid():
    app.layout.delete_folder(app.qa_first_group)
    app.layout.delete_folder(app.qa_second_group)
    app.layout.order = []
    app.layout.save()
    app.app_page_index = 0
    app._populate_apps()
    app._navigate(None, "Settings")


steps.extend([
    (lambda: app._navigate(None, "Settings")),
    settings_interactions,
    lambda: capture("qa-settings-pinned-fullscreen"),
    prepare_groups, place_group, move_group_to_next_page, verify_group_and_gestures, restore_qa_grid,
])
for width, height in ((1280, 720), (960, 720), (640, 640), (480, 720)):
    steps.extend([
        lambda: app._set_fullscreen(False),
        lambda w=width, h=height: resize(w, h),
        assert_layout,
        lambda w=width: capture(f"qa-settings-{w}"),
        lambda: app._navigate(None, "Apps"),
        assert_layout,
        lambda w=width: capture(f"qa-apps-{w}"),
        lambda: app._navigate(None, "System"),
        lambda w=width: capture(f"qa-system-{w}"),
        lambda: app._navigate(None, "Settings"),
    ])


def next_step():
    if not steps:
        (OUTPUT / "desktop-smoke.json").write_text(json.dumps({"results": results, "failures": failures}, indent=2))
        app.quit()
        return False
    action = steps.pop(0)
    try:
        action()
    except Exception:
        failures.append(traceback.format_exc())
        print(failures[-1], file=sys.stderr)
    leaf.GLib.timeout_add(700, next_step)
    return False


leaf.GLib.timeout_add(1800, next_step)
app.run([sys.argv[0]])
temporary.cleanup()
raise SystemExit(bool(failures))
