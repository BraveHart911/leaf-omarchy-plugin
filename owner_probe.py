#!/usr/bin/env python3
"""Report whether Leaf's owning drawer still exists in Hyprland."""

import json
import subprocess
import sys


def records(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from records(child)
    elif isinstance(value, list):
        for child in value:
            yield from records(child)


def main():
    try:
        owner_pid = int(sys.argv[1])
    except (IndexError, ValueError):
        print("unknown")
        return

    successful_queries = 0
    for topic in ("clients", "layers"):
        try:
            result = subprocess.run(
                ["hyprctl", topic, "-j"],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            data = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            continue
        successful_queries += 1
        if any(record.get("pid") == owner_pid for record in records(data)):
            print("alive")
            return

    # A single successful empty query is insufficient: a Leaf window may be
    # represented in the other Hyprland list, or one IPC request may time out.
    print("dead" if successful_queries == 2 else "unknown")


if __name__ == "__main__":
    main()
