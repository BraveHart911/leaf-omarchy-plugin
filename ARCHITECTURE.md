# Architecture

The Omarchy package has one manifest (`io.github.bravehart911.leaf-control`) with a bar widget and menu. `BarWidget.qml` launches the source-relative `launch-leaf`; `Menu.qml` renders the monitor-aware workspace overlay and handles drag/drop moves. `workspace_model.py` maps displayed slots to Hyprland workspace IDs using persistent workspace rules. `select_workspace.py` and `move_windows.py` perform compositor actions.

`leaf_control.py` owns the GTK4 application shell and views. `settings_ui.py` builds searchable settings controls; `preferences.py` validates and persists their values. `window_layout.py` adapts the window, app grid, workspace controls, and dock to available width and height. Settings opens fullscreen, while the same GTK window can be toggled or launched in a resizable mode. `app_layout.py` manages application identifiers, favorites, order, and groups. `file_search.py` indexes local filenames asynchronously. `system_metrics.py` contains the system-data readers and formatting helpers. `leaf.css` styles shared components and states.

The application discovers launchable entries through `Gio.AppInfo` and uses `Gio.AppInfo.launch`. Hardware metrics come from local procfs, sysfs, DRM, hwmon/thermal, mount, and OS data. Unsupported metrics remain unavailable. Preferences and application layout live under `$XDG_CONFIG_HOME/leaf-control`; bounded historical metrics live under `$XDG_STATE_HOME/leaf-control`.

The workspace menu uses Quickshell's Hyprland model and window screencopy for Wayland clients. XWayland clients remain in its window model and drag payloads; they use an app icon in the miniature when screencopy cannot capture them. The menu tracks the GTK owner's lifetime, so it closes if the owner exits.

`install.py` stages a complete plugin copy, backs up the previous install and shell configuration, migrates the old workspace-only plugin, and writes the command and desktop entry. It does not alter Hyprland configuration.
