"""Validated preferences shared by the settings UI and runtime.

Keep defaults, labels and allowed values together so a control cannot save a
value the next launch rejects. Unknown keys from old versions are ignored.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile


@dataclass(frozen=True)
class Preference:
    key: str
    section: str
    title: str
    description: str
    default: object
    choices: tuple = ()
    bounds: tuple = ()
    unit: str = ""

    def valid(self, value):
        if self.choices:
            return type(value) is type(self.default) and value in [item[0] for item in self.choices]
        if self.bounds:
            low, high, step = self.bounds
            return type(value) is int and low <= value <= high and (value - low) % step == 0
        return type(value) is bool


SECTIONS = (
    ("general", "General", "Choose how Leaf opens."),
    ("apps", "Applications", "Arrange your launcher and make app labels easy to read."),
    ("dock", "App dock", "Keep pinned and running apps together at the bottom of Leaf."),
    ("workspaces", "Workspaces", "Navigate desktops and see what is open."),
    ("system", "System monitor", "Control the frequency and units of hardware readings."),
    ("appearance", "Appearance", "Follow your desktop theme and adjust motion."),
    ("search", "File search", "Search local file names in folders you choose."),
)

PREFERENCES = (
    Preference("start_page", "general", "Opening page", "Used when you launch Leaf without a page shortcut.", "Apps", (("Apps", "Applications"), ("System", "System monitor"))),
    Preference("window_mode", "general", "Open Leaf in", "Settings always opens full screen. Press F11 at any time to return to a window.", "fullscreen", (("fullscreen", "Full screen"), ("window", "Window / tiled"))),
    Preference("grid_density", "apps", "Grid density", "Sets the preferred number of columns. Narrow windows always reduce columns to fit.", "comfortable", (("compact", "Compact"), ("comfortable", "Comfortable"), ("spacious", "Spacious"))),
    Preference("grid_columns", "apps", "Maximum columns", "An upper limit; Leaf uses fewer columns when space is tight.", 0, ((0, "Automatic"),) + tuple((n, str(n)) for n in range(5, 13))),
    Preference("app_icon_size", "apps", "Icon size", "Size of application and group icons.", "standard", (("small", "Small"), ("standard", "Medium"), ("large", "Large"))),
    Preference("show_app_names", "apps", "Show app names", "Display a label below each application icon.", True),
    Preference("grid_spacing", "apps", "Space between tiles", "The horizontal and vertical gap between applications.", 14, bounds=(4, 24, 2), unit="px"),
    Preference("app_tile_padding", "apps", "Space inside tiles", "Extra room around each icon and label.", 4, bounds=(0, 16, 2), unit="px"),
    Preference("app_tile_style", "apps", "Tile style", "Choose how application tiles stand out from the background.", "minimal", (("minimal", "Icons only"), ("outline", "Outline"), ("card", "Filled"))),
    Preference("app_tile_corners", "apps", "Tile corners", "Used for outlines, filled tiles and hover highlights.", "soft", (("square", "Square"), ("soft", "Soft"), ("rounded", "Rounded"))),
    Preference("app_page_indicator_style", "apps", "Page indicator", "The arrows stay available even when the page label is hidden.", "modern", (("modern", "Page numbers"), ("dots", "Dots"), ("none", "Hidden"))),
    Preference("sort_apps_by_usage", "apps", "Most used apps first", "Sort by launches recorded by Leaf. Turn off to use your saved app order.", False),
    Preference("show_expose_button", "apps", "Open windows button", "Show an Exposé button on Applications. Requires the separate Exposé plugin to be installed and enabled.", False),
    Preference("dock_visible", "dock", "Show app dock", "Display pinned and running apps on the Applications page.", True),
    Preference("dock_show_running", "dock", "Include running apps", "Show open applications after your pinned apps.", True),
    Preference("dock_running_scope", "dock", "Running app scope", "Choose which open windows appear in the bar.", "all", (("all", "All workspaces"), ("monitor", "This monitor"), ("workspace", "Active workspace"))),
    Preference("dock_position", "dock", "Position", "Place the bar above or below the application grid.", "bottom", (("bottom", "Bottom"), ("top", "Above apps"))),
    Preference("dock_alignment", "dock", "Alignment", "Align the bar within the available width.", "center", (("start", "Left"), ("center", "Center"), ("end", "Right"))),
    Preference("dock_icon_size", "dock", "Bar icon size", "Independent of the application grid.", 40, bounds=(24, 72, 4), unit="px"),
    Preference("dock_spacing", "dock", "Space between apps", "The gap between buttons in the dock.", 4, bounds=(0, 24, 2), unit="px"),
    Preference("dock_padding", "dock", "Bar padding", "Space around the outside of the bar's buttons.", 8, bounds=(0, 20, 2), unit="px"),
    Preference("dock_show_names", "dock", "Show names in bar", "Display app names below the icons.", False),
    Preference("dock_background", "dock", "Glass dock surface", "Add a translucent surface around the apps in Leaf.", True),
    Preference("dock_glass_opacity", "dock", "Glass opacity", "How much of the dock surface remains visible.", 55, bounds=(20, 90, 5), unit="%"),
    Preference("dock_magnify", "dock", "Magnify on hover", "Grow the app under the pointer and its neighbors.", True),
    Preference("dock_magnify_scale", "dock", "Magnification peak", "Largest icon size while hovering.", 135, bounds=(110, 180, 5), unit="%"),
    Preference("dock_magnify_reach", "dock", "Magnification reach", "Number of neighboring apps affected on each side.", 2, bounds=(1, 4, 1), unit="apps"),
    Preference("dock_indicator_style", "dock", "Running indicators", "Show how many windows each app has open.", "dots", (("dots", "Window dots"), ("single", "Single dot"), ("bar", "Bar"), ("none", "Off"))),
    Preference("workspace_visible", "workspaces", "Show workspaces", "Show live previews in full screen and compact desktop buttons in a window.", True),
    Preference("workspace_focus_on_open", "workspaces", "Focus workspaces on open", "Start keyboard navigation on desktops instead of the application search.", False),
    Preference("workspace_wrap", "workspaces", "Wrap keyboard navigation", "Continue from the last desktop to the first with the arrow keys.", False),
    Preference("workspace_switch_both_monitors", "workspaces", "Switch all monitors together", "Switch the matching desktop on each monitor when separate desktop sets are configured.", False),
    Preference("workspace_live_previews", "workspaces", "Live window previews", "Show open windows over desktop wallpapers in full screen.", True),
    Preference("workspace_other_monitor_apps", "workspaces", "Apps on other monitors", "Include other monitors' app icons below full-screen previews.", True),
    Preference("temperature_unit", "system", "Temperature units", "Used for available CPU and GPU temperature readings.", "C", (("C", "Celsius (°C)"), ("F", "Fahrenheit (°F)"))),
    Preference("refresh_seconds", "system", "Refresh interval", "Longer intervals use less CPU. Sampling slows while the monitor page is hidden.", 2, tuple((n, f"{n} second" + ("s" if n != 1 else "")) for n in (1, 2, 5, 10))),
    Preference("window_transparency", "appearance", "Background transparency", "Let the desktop show through Applications. Windowed mode limits transparency to 20%; Settings and System stay opaque.", 0, bounds=(0, 60, 1), unit="%"),
    Preference("motion_style", "appearance", "Motion", "Choose page and opening transitions, or turn animations off.", "glide", (("glide", "Smooth glide"), ("blend", "Crossfade"), ("reduced", "Reduced motion"))),
    Preference("motion_duration", "appearance", "Animation duration", "Shorter durations feel faster. Disabled when reduced motion is selected.", 280, bounds=(140, 420, 20), unit="ms"),
)
BY_KEY = {pref.key: pref for pref in PREFERENCES}
DEFAULT_SETTINGS = {pref.key: pref.default for pref in PREFERENCES}
# Preserve dormant utility preferences and data while the current UI is Apps/System.
DEFAULT_SETTINGS.update(clock_24h=False, angle_unit="degrees", stopwatch_hundredths=True,
                        timer_notifications=True, search_roots=None, search_excludes=[])
APP_PAGE_INDICATOR_STYLES = BY_KEY["app_page_indicator_style"].choices


def load_settings(path: Path) -> dict:
    settings = deepcopy(DEFAULT_SETTINGS)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return settings
    if not isinstance(data, dict):
        return settings
    for pref in PREFERENCES:
        if pref.valid(data.get(pref.key)):
            settings[pref.key] = data[pref.key]
    for key in ("clock_24h", "stopwatch_hundredths", "timer_notifications"):
        if type(data.get(key)) is bool:
            settings[key] = data[key]
    if data.get("angle_unit") in ("degrees", "radians"):
        settings["angle_unit"] = data["angle_unit"]
    roots = data.get("search_roots")
    if isinstance(roots, list) and all(isinstance(value, str) for value in roots):
        settings["search_roots"] = list(dict.fromkeys(roots))
    excludes = data.get("search_excludes")
    if isinstance(excludes, list):
        settings["search_excludes"] = sorted({v.strip() for v in excludes if isinstance(v, str)
                                              and v.strip() and v.strip() not in (".", "..")
                                              and "/" not in v and "\\" not in v})
    return settings


def save_settings(path: Path, settings: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(settings, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
