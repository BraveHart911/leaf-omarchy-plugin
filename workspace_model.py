"""Resolve desktop slots from connected monitors and configured workspaces.

Persistent workspace rules take precedence. A standard Hyprland installation
uses shared numbered desktops; no connector names or personal Lua helpers are
assumed. IDs stay distinct from the slot numbers displayed by Leaf.
"""


def workspace_sets(monitors, workspaces):
    result = {}
    for monitor in monitors:
        name = monitor.get("name")
        if not name or monitor.get("disabled"):
            continue
        configured = sorted({w["id"] for w in workspaces
                             if w.get("monitor") == name and w.get("ispersistent")
                             and type(w.get("id")) is int and w["id"] > 0})
        if configured:
            result[name] = configured
        else:
            active = monitor.get("activeWorkspace", {}).get("id", 1)
            base = max(0, (active - 1) // 10) * 10 if isinstance(active, int) else 0
            result[name] = list(range(base + 1, base + 11))
    return result


def workspace_target(number, monitor, monitors, workspaces):
    targets = workspace_sets(monitors, workspaces).get(monitor)
    if not targets:
        raise ValueError("Selected monitor is no longer connected")
    if not 1 <= number <= len(targets):
        raise ValueError("This desktop is no longer available")
    return targets[number - 1]
