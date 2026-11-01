# Testing

Run from the project root:

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile leaf_control.py app_layout.py file_search.py preferences.py settings_ui.py window_layout.py system_metrics.py workspace_model.py
GDK_BACKEND=wayland python3 tests/desktop_smoke.py
```

The unit suite checks preference validation and persistence, favorite order, workspace ID mapping, responsive rules, and installation/migration. The desktop smoke test uses isolated temporary preferences and a non-unique GTK app ID. It exercises settings controls, pinned-app ordering, and Apps/System/Settings at several window widths, saves screenshots and a result summary in `artifacts/qa/`, then exits.

On a running Omarchy session, verify that the plugin catalog lists Leaf once with both `bar-widget` and `menu` kinds. Open Settings from the bar and desktop entry, check that it starts fullscreen, then toggle F11 and tile it at narrow widths. Check app search, groups, favorites, pinned app controls, and unavailable system metrics. Open workspace cards on each monitor, verify that Wayland and XWayland clients appear, then drag an icon or miniature to another workspace and confirm the target window moved. Selecting a workspace should switch only the relevant monitor. Escape should close the workspace overlay when Leaf exits.
