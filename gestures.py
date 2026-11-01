#!/usr/bin/env python3
"""Hyprland gesture bridge: launch Leaf or send an action to its active window."""
import json
from pathlib import Path
import subprocess
import sys


def dispatch(direction):
    root = Path(__file__).resolve().parent
    if direction == "up":
        subprocess.Popen(["sh", str(root / "launch-leaf"), "--apps", "--fullscreen"], start_new_session=True)
        return
    action = {"left": "next-page", "right": "previous-page", "down": "dismiss"}.get(direction)
    if not action:
        return
    try:
        active = json.loads(subprocess.check_output(["hyprctl", "activewindow", "-j"], timeout=2))
        if active.get("class") != "org.leafcontrol.LeafControl":
            return
        subprocess.run(["gdbus", "call", "--session", "--dest", "org.leafcontrol.LeafControl",
                        "--object-path", "/org/leafcontrol/LeafControl", "--method", "org.gtk.Actions.Activate",
                        action, "[]", "{}"], check=True, timeout=3, stdout=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError):
        return


if __name__ == "__main__":
    dispatch(sys.argv[1] if len(sys.argv) == 2 else "")
