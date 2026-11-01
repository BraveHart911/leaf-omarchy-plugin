#!/usr/bin/env python3
"""Dismiss Leaf, then focus a validated workspace or window using Hyprland IPC."""
import json
import re
import subprocess
import sys
import time
from workspace_model import workspace_sets, workspace_target


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=4).stdout


def ipc(name):
    return json.loads(run("hyprctl", name, "-j"))


def leaf_visible():
    return any(c.get("class") in ("org.leafcontrol.LeafControl", "leaf_control.py") and c.get("mapped")
               for c in ipc("clients"))


def dismiss_leaf():
    run("gdbus", "call", "--session", "--dest", "org.leafcontrol.LeafControl",
        "--object-path", "/org/leafcontrol/LeafControl", "--method",
        "org.gtk.Actions.Activate", "dismiss", "[]", "{}")
    deadline = time.monotonic() + 3
    while leaf_visible():
        if time.monotonic() >= deadline:
            raise RuntimeError("Leaf did not release its window")
        time.sleep(0.04)


def select(kind, value, monitor, mode="one"):
    monitors, workspaces = ipc("monitors"), ipc("workspaces")
    if kind == "workspace":
        number = int(value)
        if mode not in ("one", "both"):
            raise ValueError("Invalid workspace switching mode")
        target = workspace_target(number, monitor, monitors, workspaces)
        sets = workspace_sets(monitors, workspaces)
        targets = {monitor: target}
        if mode == "both":
            # Shared desktops cannot be on multiple outputs simultaneously.
            # Only switch other monitors when their configured IDs are disjoint.
            for name, ids in sets.items():
                if name != monitor and number <= len(ids) and not set(ids).intersection(sets[monitor]):
                    targets[name] = ids[number - 1]
        ordered = [name for name in targets if name != monitor] + [monitor]
        actions = ["hl.dispatch(hl.dsp.focus({ monitor = " + json.dumps(name) + " }))\n"
                   + "hl.dispatch(hl.dsp.focus({ workspace = " + json.dumps(str(targets[name])) + " }))"
                   for name in ordered]
    elif kind == "window" and re.fullmatch(r"(?:0x)?[a-fA-F0-9]+", value):
        address = "0x" + value.removeprefix("0x")
        if not any(c.get("address") == address for c in ipc("clients")):
            raise ValueError("This window has already closed")
        actions = ["hl.dispatch(hl.dsp.focus({ window = " + json.dumps("address:" + address) + " }))"]
    else:
        raise ValueError("Invalid selection")
    dismiss_leaf()
    for action in actions:
        run("hyprctl", "eval", action)


if __name__ == "__main__":
    try:
        select(*sys.argv[1:])
    except Exception as error:
        print(f"Leaf selection failed: {error}", file=sys.stderr)
        subprocess.run(["notify-send", "Leaf", f"Could not switch: {error}"], check=False)
        sys.exit(1)
