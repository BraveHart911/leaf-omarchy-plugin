#!/usr/bin/env python3
"""Leaf Control: a small native GTK4 desktop control center."""

from __future__ import annotations

import ast
import csv
from collections import deque
import cairo
import ctypes
import ctypes.util
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango, Graphene


class DockIcon(Gtk.Widget):
    """Draw magnification inside a fixed slot without changing GTK layout."""
    def __init__(self, gicon, base, peak):
        super().__init__()
        self.base = base
        self.peak = peak
        self.draw_size = float(base)
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        if gicon is None:
            gicon = Gio.ThemedIcon.new("application-x-executable")
        self.paintable = theme.lookup_by_gicon(
            gicon, peak, self.get_scale_factor(), Gtk.TextDirection.NONE,
            Gtk.IconLookupFlags.PRELOAD)
        self.set_halign(Gtk.Align.CENTER)

    def do_measure(self, orientation, for_size):
        return self.peak, self.peak, -1, -1

    def do_snapshot(self, snapshot):
        size = self.draw_size
        point = Graphene.Point()
        point.init((self.get_width() - size) / 2, self.get_height() - size)
        snapshot.save()
        snapshot.translate(point)
        self.paintable.snapshot(snapshot, size, size)
        snapshot.restore()

    def set_draw_size(self, size):
        self.draw_size = float(size)
        self.queue_draw()

from app_layout import ApplicationLayout, app_identifier
from file_search import LocalFileSearch, user_search_roots

APP_ID = "org.leafcontrol.LeafControl"
APP_NAME = "Leaf"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "leaf-control"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "leaf-control"
OMARCHY_STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "omarchy/current"
OMARCHY_THEME_COLORS = OMARCHY_STATE_DIR / "theme/colors.toml"
OMARCHY_THEME_SHELL = OMARCHY_STATE_DIR / "theme/shell.toml"
DEFAULT_NOTE_FOLDER = "Inbox"
EXPOSE_PLUGIN_ID = "expose.window-overview"
EXPOSE_REPOSITORY = "https://github.com/kristofferR/omarchy-expose.git"


def clean_note_folder(value) -> str | None:
    if not isinstance(value, str):
        return None
    name = re.sub(r"\s+", " ", value).strip()[:60]
    return name if name and not any(ord(character) < 32 for character in name) else None

DEFAULT_THEME_COLORS = {
    "mode": "dark", "accent": "#A8D77B", "selection": "#303B30", "muted": "#9CA79A",
    "background": "#171A17", "dark_background": "#171D18", "darker_background": "#111511",
    "lighter_background": "#252D25", "foreground": "#E8ECE6", "dark_foreground": "#9CA79A",
    "light_foreground": "#B4BFB1", "bright_foreground": "#F0F3ED", "red": "#D97777",
    "orange": "#E8AC78", "yellow": "#D5B75C", "green": "#A8D77B", "cyan": "#91CBEA",
    "blue": "#91CBEA", "magenta": "#C59ACB",
}

OMARCHY_TOOL_GROUPS = {
    "System controls": (
        ("omarchy.audio", "Audio", "Adjust volume and choose audio devices", "audio-volume-high-symbolic"),
        ("omarchy.bluetooth", "Bluetooth", "Connect or manage nearby devices", "bluetooth-symbolic"),
        ("omarchy.monitor", "Display", "Manage brightness and display options", "video-display-symbolic"),
        ("omarchy.network", "Network", "Connect to Wi-Fi and manage network links", "network-wireless-symbolic"),
        ("omarchy.power", "Power", "View battery and power options", "battery-good-symbolic"),
    ),
    "Everyday utilities": (
        ("omarchy.clock", "Clock & calendar", "Open Omarchy’s clock and calendar panel", "x-office-calendar-symbolic"),
        ("omarchy.clipboard", "Clipboard", "Browse recent clipboard items", "edit-paste-symbolic"),
        ("omarchy.emojis", "Emoji picker", "Find and insert an emoji", "face-smile-symbolic"),
        ("omarchy.image-picker", "Image picker", "Browse images for Omarchy actions", "image-x-generic-symbolic"),
        ("omarchy.reminders", "Reminders", "Create and review reminders", "alarm-symbolic"),
        ("omarchy.weather", "Weather", "Check the current forecast", "weather-clear-symbolic"),
    ),
    "Network & storage checks": (
        ("omarchy.speedtest", "Internet speed", "Measure your network connection", "network-transmit-receive-symbolic"),
        ("omarchy.wifiqr", "Wi-Fi QR code", "Share the current Wi-Fi network", "qr-code-symbolic"),
        ("omarchy.disk-speedtest", "Disk speed", "Measure storage read and write speed", "drive-harddisk-symbolic"),
    ),
}


def parse_omarchy_plugin_catalog(output: str) -> set[str]:
    """Return enabled first-party panels from Omarchy's plugin registry."""
    plugins = json.loads(output)
    if not isinstance(plugins, list):
        raise ValueError("unexpected plugin catalog")
    return {
        item["id"] for item in plugins
        if isinstance(item, dict)
        and item.get("firstParty") is True
        and item.get("enabled") is True
        and isinstance(item.get("id"), str)
        and isinstance(item.get("kinds"), (list, tuple, set))
        and set(item["kinds"]) & {"panel", "overlay", "bar-widget"}
    }


def expose_plugin_enabled(output: str) -> bool:
    """Check the installed Omarchy registry before invoking Exposé's IPC."""
    plugins = json.loads(output)
    if not isinstance(plugins, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("id") == EXPOSE_PLUGIN_ID
        and item.get("enabled") is True
        and isinstance(item.get("kinds"), list)
        and "overlay" in item["kinds"]
        for item in plugins
    )


def request_expose_open() -> str | None:
    """Return an error when the optional Exposé plugin cannot be opened."""
    try:
        listing = subprocess.run(["omarchy", "plugin", "list", "--json"],
                                 capture_output=True, text=True, timeout=4, check=True)
        if not expose_plugin_enabled(listing.stdout):
            return ("Exposé is not enabled. Install it with: "
                    f"omarchy plugin add {EXPOSE_REPOSITORY} --enable")
        result = subprocess.run(["omarchy-shell", "expose", "open"],
                                capture_output=True, text=True, timeout=5)
        return None if result.returncode == 0 else (
            result.stderr.strip() or "The Exposé plugin did not open")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return str(exc)

def _blend_hex(first: str, second: str, amount: float) -> str:
    a = tuple(int(first[index:index + 2], 16) for index in (1, 3, 5))
    b = tuple(int(second[index:index + 2], 16) for index in (1, 3, 5))
    return "#" + "".join(f"{round(left * (1 - amount) + right * amount):02X}" for left, right in zip(a, b))


def _color_luminance(color: str) -> float:
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(first: str, second: str) -> float:
    luminance_first, luminance_second = _color_luminance(first), _color_luminance(second)
    return (max(luminance_first, luminance_second) + 0.05) / (min(luminance_first, luminance_second) + 0.05)


def _ensure_text_contrast(foreground: str, backgrounds: tuple[str, ...], minimum: float = 4.5) -> str:
    if all(_contrast_ratio(foreground, background) >= minimum for background in backgrounds):
        return foreground
    candidates = ("#000000", "#FFFFFF")
    target = max(candidates, key=lambda candidate: min(
        _contrast_ratio(candidate, background) for background in backgrounds
    ))
    if min(_contrast_ratio(target, background) for background in backgrounds) < minimum:
        return target
    low, high = 0.0, 1.0
    for _ in range(20):
        amount = (low + high) / 2
        candidate = _blend_hex(foreground, target, amount)
        if all(_contrast_ratio(candidate, background) >= minimum for background in backgrounds):
            high = amount
        else:
            low = amount
    return _blend_hex(foreground, target, high)


def load_omarchy_palette(path: Path | None = None) -> dict[str, str]:
    """Read Omarchy's generated palette and derive accessible UI surface colors."""
    path = path or OMARCHY_THEME_COLORS
    source = dict(DEFAULT_THEME_COLORS)
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        parsed = {}
    aliases = {
        "background": ("background", "bg"),
        "dark_background": ("dark_background", "dark_bg"),
        "darker_background": ("darker_background", "darker_bg"),
        "lighter_background": ("lighter_background", "lighter_bg"),
        "foreground": ("foreground", "fg"),
        "dark_foreground": ("dark_foreground", "dark_fg"),
        "light_foreground": ("light_foreground", "light_fg"),
        "bright_foreground": ("bright_foreground", "bright_fg"),
    }
    for key in source:
        value = next((parsed.get(name) for name in aliases.get(key, (key,)) if name in parsed), None)
        if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            source[key] = value.upper()
    if parsed.get("mode") in ("dark", "light"):
        source["mode"] = parsed["mode"]

    light = source["mode"] == "light"
    background, foreground = source["background"], source["foreground"]
    accent = source["accent"]
    accent_ink = max(("#000000", "#FFFFFF"), key=lambda ink: _contrast_ratio(ink, accent))
    surface = source["lighter_background"]
    if light:
        surface_deep = _blend_hex(background, foreground, 0.045)
        surface_deeper = _blend_hex(background, foreground, 0.085)
        surface_hover = _blend_hex(surface, foreground, 0.06)
    else:
        surface_deep = source["dark_background"]
        surface_deeper = source["darker_background"]
        surface_hover = _blend_hex(surface, foreground, 0.10)
    surface_alt = _blend_hex(background, surface, 0.56)
    accent_soft = _blend_hex(background, accent, 0.20 if light else 0.16)
    text_backgrounds = (background, surface_deep, surface_deeper, surface,
                        surface_alt, surface_hover, accent_soft)
    ui_text = _ensure_text_contrast(foreground, text_backgrounds)
    ui_text_soft = _ensure_text_contrast(source["light_foreground"], text_backgrounds)
    muted_text = _ensure_text_contrast(
        _blend_hex(source["muted"], foreground, 0.40), text_backgrounds
    )
    accent_text = _ensure_text_contrast(accent, text_backgrounds)
    palette = {
        **source,
        "ui_bg": background,
        "ui_bg_deep": surface_deep,
        "ui_bg_deeper": surface_deeper,
        "ui_surface": surface,
        "ui_surface_alt": surface_alt,
        "ui_surface_hover": surface_hover,
        "ui_border": _blend_hex(source["muted"], background, 0.44),
        "ui_border_strong": _blend_hex(source["muted"], foreground, 0.28),
        "ui_text": ui_text,
        "ui_text_soft": ui_text_soft,
        "ui_text_muted": muted_text,
        "ui_accent": accent,
        "ui_accent_text": accent_text,
        "ui_accent_hover": _blend_hex(accent, foreground if light else "#FFFFFF", 0.14),
        "ui_accent_soft": accent_soft,
        "ui_accent_ink": accent_ink,
        "ui_hover_tint": "#000000" if light else "#FFFFFF",
        "ui_selection": source["selection"],
    }
    return palette


def load_omarchy_shell_metrics(path: Path | None = None) -> dict[str, int]:
    """Read the shell's base type size and panel spacing when Omarchy provides them."""
    path = path or OMARCHY_THEME_SHELL
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        parsed = {}
    font = parsed.get("font", {})
    spacing = parsed.get("spacing", {})
    try:
        base_size = round(float(font.get("base-size", 12)))
        scale = float(spacing.get("scale", 1.0))
        panel_padding = round(float(spacing.get("panel-padding", 18)) * scale)
        panel_gap = round(float(spacing.get("panel-gap", 14)) * scale)
    except (AttributeError, TypeError, ValueError):
        base_size, panel_padding, panel_gap = 12, 18, 14
    return {
        "base_size": max(12, min(16, base_size)),
        "panel_padding": max(12, min(32, panel_padding)),
        "panel_gap": max(8, min(24, panel_gap)),
    }


def cairo_color(color: str) -> tuple[float, float, float]:
    return tuple(int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))


def gauge_color(fraction: float) -> tuple[float, float, float]:
    """Map low, normal, elevated, and high readings to clear gauge colors."""
    stops = (
        (0.0, (0.24, 0.64, 1.0)),
        (0.25, (0.22, 0.94, 0.50)),
        (0.70, (1.0, 0.72, 0.18)),
        (1.0, (1.0, 0.25, 0.22)),
    )
    level = min(1.0, max(0.0, float(fraction)))
    lower, upper = next(
        (left, right)
        for left, right in zip(stops, stops[1:])
        if left[0] <= level <= right[0]
    )
    amount = (level - lower[0]) / (upper[0] - lower[0])
    return tuple(start + (end - start) * amount for start, end in zip(lower[1], upper[1]))


def temperature_gauge_color(celsius: float) -> tuple[float, float, float]:
    """Keep normal temperatures green; reserve amber and red for heat."""
    stops = (
        (0, (0.24, 0.64, 1.0)),
        (25, (0.18, 0.84, 1.0)),
        (40, (0.22, 0.94, 0.50)),
        (60, (0.22, 0.94, 0.50)),
        (75, (1.0, 0.72, 0.18)),
        (90, (1.0, 0.25, 0.22)),
    )
    value = min(90.0, max(0.0, float(celsius)))
    lower, upper = next(
        (left, right)
        for left, right in zip(stops, stops[1:])
        if left[0] <= value <= right[0]
    )
    amount = (value - lower[0]) / (upper[0] - lower[0])
    return tuple(start + (end - start) * amount for start, end in zip(lower[1], upper[1]))


from system_metrics import (
    system_install_date,
    load_system_health_history,
    save_system_health_history,
    uptime_text,
    human_bytes,
    history_window_seconds,
    history_window_label,
    recent_history_samples,
    read_memory,
    cpu_snapshot,
    cpu_identity,
    cpu_core_snapshot,
    cpu_usage_between,
    hardware_sensor_readings,
    _read_nvidia_smi_metrics,
    read_gpu_metrics,
    network_snapshot,
    process_snapshot,
    network_interface_snapshot,
    disk_io_snapshot,
    mounted_filesystems,
    group_storage_mounts,
    storage_device_inventory,
    human_rate
)


def format_duration(seconds: float, hundredths: bool = False) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole_seconds = int(seconds % 60)
    value = f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}"
    return f"{value}.{int((seconds % 1) * 100):02d}" if hundredths else value


def safe_calculate(expression: str, angle_unit: str = "degrees") -> str:
    """Evaluate arithmetic and whitelisted math functions without eval or shell access."""
    if len(expression) > 500 or expression.count("(") > 80:
        raise ValueError("Expression is too long")
    tree = ast.parse(expression, mode="eval")
    binary_operators = {
        ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
        ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
        ast.Pow: lambda a, b: a ** b,
    }
    unary_operators = {ast.UAdd: lambda x: x, ast.USub: lambda x: -x}

    def checked(value):
        if isinstance(value, complex) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 10**100:
            raise ValueError("Result is out of range")
        return value

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return checked(node.value)
        if isinstance(node, ast.Name) and node.id in ("pi", "e"):
            return math.pi if node.id == "pi" else math.e
        if isinstance(node, ast.BinOp) and type(node.op) in binary_operators:
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 12:
                raise ValueError("Exponent is too large")
            return checked(binary_operators[type(node.op)](left, right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in unary_operators:
            return checked(unary_operators[type(node.op)](evaluate(node.operand)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            arguments = [evaluate(argument) for argument in node.args]
            one_arg = {"sin", "cos", "tan", "asin", "acos", "atan", "sqrt", "ln", "log10", "exp", "abs", "factorial", "floor", "ceil"}
            if name in one_arg and len(arguments) != 1:
                raise ValueError(f"{name} expects one value")
            if name == "log" and len(arguments) not in (1, 2):
                raise ValueError("log expects one value and an optional base")
            if name in ("sin", "cos", "tan"):
                value = math.radians(arguments[0]) if angle_unit == "degrees" else arguments[0]
                return checked(getattr(math, name)(value))
            if name in ("asin", "acos", "atan"):
                value = getattr(math, name)(arguments[0])
                return checked(math.degrees(value) if angle_unit == "degrees" else value)
            if name == "sqrt":
                return checked(math.sqrt(arguments[0]))
            if name in ("ln", "log"):
                return checked(math.log(*arguments))
            if name == "log10":
                return checked(math.log10(arguments[0]))
            if name == "exp":
                return checked(math.exp(arguments[0]))
            if name == "abs":
                return checked(abs(arguments[0]))
            if name == "factorial":
                value = arguments[0]
                if isinstance(value, float) and not value.is_integer():
                    raise ValueError("factorial requires a whole number")
                if value < 0 or value > 170:
                    raise ValueError("factorial accepts values from 0 to 170")
                return math.factorial(int(value))
            if name == "floor":
                return checked(math.floor(arguments[0]))
            if name == "ceil":
                return checked(math.ceil(arguments[0]))
            raise ValueError("Use only supported math functions")
        raise ValueError("Use numbers, basic operators, and supported math functions")

    try:
        return str(checked(evaluate(tree)))
    except (OverflowError, ZeroDivisionError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError("Result is out of range") from exc


def load_calculation_history(path: Path) -> list[dict[str, str]]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    history = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("expression"), str) and isinstance(item.get("result"), str):
            history.append({"expression": item["expression"][:500], "result": item["result"][:500]})
    return history[-20:]


def save_calculation_history(path: Path, history: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(history[-20:], ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def _clean_notes(items) -> list[dict]:
    if not isinstance(items, list):
        return []
    cleaned = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        note = {
            "id": item["id"][:80],
            "title": str(item.get("title", "Untitled"))[:200],
            "body": str(item.get("body", ""))[:200_000],
            "pinned": bool(item.get("pinned", False)),
            "updated": str(item.get("updated", ""))[:40],
        }
        folder = clean_note_folder(item.get("folder"))
        if folder:
            note["folder"] = folder
        cleaned.append(note)
    return cleaned


def load_notes(path: Path, legacy_path: Path | None = None) -> tuple[list[dict], list[dict]]:
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return _clean_notes(data.get("notes")), _clean_notes(data.get("trash"))
    except (OSError, ValueError):
        pass
    if legacy_path:
        try:
            body = legacy_path.read_text()
            if body.strip():
                return ([{"id": "legacy-note", "title": "My note", "body": body,
                          "pinned": False, "updated": ""}], [])
        except OSError:
            pass
    return [], []


def load_note_folders(path: Path, notes: list[dict] | None = None) -> list[str]:
    folders = [DEFAULT_NOTE_FOLDER]
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict) and isinstance(data.get("folders"), list):
        for value in data["folders"]:
            name = clean_note_folder(value)
            if name and name.casefold() not in {folder.casefold() for folder in folders}:
                folders.append(name)
    for note in notes or []:
        value = note.get("folder", DEFAULT_NOTE_FOLDER)
        name = clean_note_folder(value)
        if name and name.casefold() not in {folder.casefold() for folder in folders}:
            folders.append(name)
    return folders


def save_notes(path: Path, notes: list[dict], trash: list[dict], folders: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    folder_names = [DEFAULT_NOTE_FOLDER]
    for value in folders if folders is not None else load_note_folders(path, notes):
        name = clean_note_folder(value)
        if name and name.casefold() not in {folder.casefold() for folder in folder_names}:
            folder_names.append(name)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"notes": notes, "trash": trash, "folders": folder_names}, stream,
                      ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def secure_notes_storage(path: Path) -> None:
    if path.parent.is_dir():
        path.parent.chmod(0o700)
    if path.is_file():
        path.chmod(0o600)


def export_note_markdown(note: dict, path: Path) -> None:
    path.write_text(f"# {note['title'].strip() or 'Untitled'}\n\n{note['body']}", encoding="utf-8")


from preferences import DEFAULT_SETTINGS, APP_PAGE_INDICATOR_STYLES, load_settings, save_settings
from settings_ui import SettingsMixin
from window_layout import WindowLayoutMixin
from layout_rules import page_capacity, dock_hover_sizes
from workspace_model import workspace_sets

APP_CATEGORY_ORDER = (
    "Audio & Video", "Development", "Games", "Graphics & Design",
    "Internet & Communication", "Office & Documents", "System & Settings", "Utilities", "Other",
)


def format_temperature_text(text: str, unit: str) -> str:
    if unit != "F":
        return text
    def convert(match):
        fahrenheit = float(match.group(1)) * 9 / 5 + 32
        return f"{fahrenheit:.1f} °F"
    return re.sub(r"(-?\d+(?:\.\d+)?)\s*°C", convert, text)


def populate_markdown_preview(text_view: Gtk.TextView, markdown: str, palette: dict[str, str] | None = None) -> None:
    """Render a safe, small Markdown subset using GTK text tags, never HTML."""
    palette = palette or load_omarchy_palette()
    buffer = text_view.get_buffer()
    buffer.set_text("")
    tag_table = buffer.get_tag_table()
    tags = {
        "heading-1": {"weight": 700, "size-points": 20, "foreground": palette["bright_foreground"]},
        "heading-2": {"weight": 700, "size-points": 16, "foreground": palette["light_foreground"]},
        "heading-3": {"weight": 700, "size-points": 14, "foreground": palette["ui_text_soft"]},
        "bold": {"weight": 700}, "italic": {"style": 2},
        "inline-code": {"family": "monospace", "foreground": palette["ui_accent_text"], "background": palette["ui_surface_alt"]},
        "code-block": {"family": "monospace", "foreground": palette["ui_text_soft"], "background": palette["ui_bg_deep"], "pixels-above-lines": 4, "pixels-below-lines": 4},
        "list-marker": {"foreground": palette["ui_accent_text"], "weight": 700},
    }
    for name, properties in tags.items():
        existing = tag_table.lookup(name)
        if existing:
            tag_table.remove(existing)
        buffer.create_tag(name, **properties)

    def insert_inline(value: str, tag: str | None = None):
        pattern = re.compile(r"(`[^`]+`|\*\*[^*]+\*\*|(?<!\*)\*[^*]+\*)")
        cursor = 0
        for match in pattern.finditer(value):
            if match.start() > cursor:
                segment = value[cursor:match.start()]
                buffer.insert_with_tags_by_name(buffer.get_end_iter(), segment, tag) if tag else buffer.insert(buffer.get_end_iter(), segment)
            token = match.group(0)
            if token.startswith("`"):
                rendered, style = token[1:-1], "inline-code"
            elif token.startswith("**"):
                rendered, style = token[2:-2], "bold"
            else:
                rendered, style = token[1:-1], "italic"
            styles = [style] + ([tag] if tag else [])
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), rendered, *styles)
            cursor = match.end()
        if cursor < len(value):
            segment = value[cursor:]
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), segment, tag) if tag else buffer.insert(buffer.get_end_iter(), segment)

    in_code = False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), line + "\n", "code-block")
            continue
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        bullet = re.match(r"^\s*[-*+]\s+(.*)$", line)
        numbered = re.match(r"^\s*(\d+)\.\s+(.*)$", line)
        if heading:
            insert_inline(heading.group(2), f"heading-{len(heading.group(1))}")
        elif bullet:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), "• ", "list-marker")
            insert_inline(bullet.group(1))
        elif numbered:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), numbered.group(1) + ". ", "list-marker")
            insert_inline(numbered.group(2))
        elif re.fullmatch(r"\s*([-*_])\1{2,}\s*", line):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), "────────────────────\n", "list-marker")
            continue
        else:
            insert_inline(line)
        buffer.insert(buffer.get_end_iter(), "\n")


class LeafControl(SettingsMixin, WindowLayoutMixin, Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        dismiss = Gio.SimpleAction.new("dismiss", None)
        dismiss.connect("activate", lambda *_: self._dismiss_drawer() if getattr(self, "window", None) else None)
        self.add_action(dismiss)
        for name, direction in (("next-page", 1), ("previous-page", -1)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, step=direction: self._gesture_page(step))
            self.add_action(action)
        refresh = Gio.SimpleAction.new("refresh", None)
        refresh.connect("activate", self._refresh_leaf)
        self.add_action(refresh)
        self.apps: list[Gio.AppInfo] = []
        self.cpu_model, self.logical_cpu_count = cpu_identity()
        self.app_tiles_by_id = {}
        self.folder_tiles_by_id = {}
        self.folder_member_tiles_by_id = {}
        self.open_folder_window = None
        self.open_folder_id = None
        self.create_group_panel = None
        self.create_group_source_id = None
        self.command_panel = None
        self.app_page_index = 0
        self.running_windows = []
        self.workspace_strip_open = False
        self.workspace_strip_monitor = ""
        self.workspace_strip_payload = ""
        self.drawer_monitor_name = ""
        self.running_marks_by_id = {}
        self.workspace_items = []
        self.monitor_items = []
        self.selected_workspace_id = None
        self.window_state_fingerprint = None
        self.window_refresh_id = None
        self.group_dialog_backdrop = None
        self.group_dialog_layer = None
        self.group_rename_action = None
        self.layout = ApplicationLayout(CONFIG_DIR / "layout.json")
        self.cpu_prev = cpu_snapshot()
        self.cpu_cores_prev = cpu_core_snapshot()
        self.histories = {"cpu": deque(maxlen=90), "memory": deque(maxlen=90), "network": deque(maxlen=90)}
        self.chart_areas = {}
        self.gauge_values = {"cpu": None, "memory": None, "gpu": None}
        self.gauge_temperatures = {"cpu": None, "memory": None, "gpu": None}
        self.net_prev = network_snapshot()
        self.net_interfaces_prev = network_interface_snapshot()
        self.disk_prev = disk_io_snapshot()
        self.io_prev_time = time.monotonic()
        self.process_cpu_prev = {}
        self.active_page = "Apps"
        self.drawer_revealer = None
        self.drawer_dismiss_pending = False
        self._built_pages = set()
        self._launching_app_ids = set()
        self.settings_path = CONFIG_DIR / "settings.json"
        self.settings = load_settings(self.settings_path)
        self.system_health_path = STATE_DIR / "system-health.json"
        self.system_health_days = load_system_health_history(self.system_health_path)
        self.system_health_saved_at = time.monotonic()
        self.system_health_day = datetime.now().date().isoformat()
        self.system_install_date = system_install_date()
        search_roots = self.settings["search_roots"]
        self.file_search = LocalFileSearch(
            [Path(value).expanduser() for value in search_roots] if search_roots is not None else None,
            set(self.settings["search_excludes"]),
        )
        self.system_refresh_id = None
        self.system_refresh_seconds = None
        self.system_baseline_reset_needed = True
        self.hardware_sample_in_flight = False
        self.hardware_sample_error = False
        self.sensor_readings = []
        self.gpu_readings = []
        self.storage_sample_in_flight = False
        self.storage_sample_error = False
        self.storage_volumes = []
        self.theme_monitors = []
        self.folder_manager_return_focus = None
        self.note_export_return_focus = None
        self.layer_shell_active = False
        self.workspace_plugin_available = (CONFIG_DIR.parent / "omarchy/plugins/io.github.bravehart911.leaf-control/Menu.qml").is_file()
        self.theme_reload_id = None
        self.theme_poll_id = None
        self.theme_fingerprint = self._read_theme_fingerprint()
        self.connect("shutdown", self._shutdown_workspace_strip)
        self.connect("shutdown", self._shutdown_theme_watch)
        self.connect("shutdown", lambda *_args: self._save_system_health_history())
        self.notes_path = STATE_DIR / "notes.txt"
        self.notes_store_path = STATE_DIR / "notes.json"
        secure_notes_storage(self.notes_store_path)
        self.notes, self.notes_trash = load_notes(self.notes_store_path, self.notes_path)
        self.note_folders = load_note_folders(self.notes_store_path, self.notes + self.notes_trash)
        self.active_note_folder_filter = "All notes"
        self.updating_note_folder_controls = False
        self.folder_manager_window = None
        self.active_note_id = None
        self.loading_note = False
        if self.notes and not self.notes_store_path.exists():
            try:
                save_notes(self.notes_store_path, self.notes, self.notes_trash, self.note_folders)
            except OSError:
                pass
        self.calc_history_path = STATE_DIR / "calculator-history.json"
        self.calc_history = load_calculation_history(self.calc_history_path)
        self.calc_memory = 0.0
        self.stopwatch_elapsed = 0.0
        self.stopwatch_started = None
        self.stopwatch_tick_id = None
        self.timer_deadline = None
        self.timer_remaining = None
        self.timer_total = None
        self.timer_tick_id = None

    def _gesture_page(self, direction):
        if (getattr(self, "window", None) and self.window.get_visible()
                and self.active_page == "Apps" and not self.search.get_text().strip()
                and not self.open_folder_window and not self.active_context_popover
                and not self.drawer_dismiss_pending):
            self._change_app_page(direction)

    def _refresh_leaf(self, *_args):
        """Replace this process so Python, CSS and preferences reload together."""
        if not getattr(self, "refresh_pending", False):
            self.refresh_pending = True
            # Let the button release and D-Bus response finish before restart.
            GLib.timeout_add(100, self._restart_leaf)

    def _restart_leaf(self):
        try:
            self._save_system_health_history()
            self._shutdown_workspace_strip()
            os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), "--settings"])
        except OSError as error:
            self.refresh_pending = False
            self._message(f"Could not refresh Leaf: {error}")
        return GLib.SOURCE_REMOVE

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        allowed = {"--apps", "--system", "--settings", "--window", "--fullscreen", "--help"}
        if any(arg not in allowed for arg in args):
            command_line.printerr_literal("Unknown option. Use --help.\n")
            return 2
        if "--help" in args:
            command_line.print_literal("Leaf: --apps | --system | --settings, --window | --fullscreen\n")
            return 0
        self.open_settings_on_start = "--settings" in args
        self.open_system_on_start = "--system" in args
        self.open_apps_on_start = "--apps" in args
        self.force_window = "--window" in args
        self.requested_page = next((page for flag, page in (("--settings", "Settings"), ("--system", "System"), ("--apps", "Apps")) if flag in args), None)
        self.explicit_show = bool(args)
        self.activate()
        if "--window" in args and self.requested_page != "Settings":
            self._set_fullscreen(False)
        elif "--fullscreen" in args:
            self._set_fullscreen(True)
        return 0

    def do_activate(self):
        if getattr(self, "window", None):
            if getattr(self, "requested_page", None):
                self._navigate(None, self.requested_page)
                self.requested_page = None
            if not getattr(self, "explicit_show", False) and self.window.get_visible() and self.drawer_revealer.get_reveal_child() and not self.drawer_dismiss_pending:
                self._dismiss_drawer()
                return
            self._place_drawer_on_focused_monitor()
            self.window.present()
            GLib.timeout_add(40, self._show_drawer)
            return
        self._load_style()
        self.window = Gtk.ApplicationWindow(application=self, title=APP_NAME)
        self.window.add_css_class("leaf-stage")
        self.window.set_default_size(1120, 760)
        self.window.set_size_request(360, 320)
        self._place_drawer_on_focused_monitor()
        self.window.connect("close-request", self._close_requested)
        self.window.connect("notify::fullscreened", lambda *_: self._update_responsive_layout())
        self.keyboard_controller = Gtk.EventControllerKey.new()
        self.keyboard_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.keyboard_controller.connect("key-pressed", self._handle_shortcuts)
        self.window.add_controller(self.keyboard_controller)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class("leaf-window")
        root.add_css_class("leaf-drawer-surface")
        self.app_root = root
        root.set_margin_top(0)
        root.set_margin_bottom(0)
        root.set_margin_start(0)
        root.set_margin_end(0)
        self.drawer_revealer = Gtk.Revealer()
        self.drawer_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.drawer_revealer.set_transition_duration(260)
        self.drawer_revealer.set_reveal_child(False)
        self.drawer_revealer.set_hexpand(True)
        self.drawer_revealer.set_vexpand(True)
        self.drawer_revealer.set_child(root)
        self.drawer_revealer.connect("notify::child-revealed", self._drawer_reveal_changed)
        self.window.set_child(self.drawer_revealer)

        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        layout.set_vexpand(True)
        self.drawer_overlay = Gtk.Overlay()
        self.drawer_overlay.set_child(layout)
        root.append(self.drawer_overlay)
        self.group_dialog_backdrop = Gtk.Button()
        self.group_dialog_backdrop.set_has_frame(False)
        self.group_dialog_backdrop.add_css_class("group-dialog-backdrop")
        self.group_dialog_backdrop.set_halign(Gtk.Align.FILL)
        self.group_dialog_backdrop.set_valign(Gtk.Align.FILL)
        self.group_dialog_backdrop.set_hexpand(True)
        self.group_dialog_backdrop.set_vexpand(True)
        self.group_dialog_backdrop.set_visible(False)
        self.group_dialog_backdrop.connect("clicked", self._close_group_overlay)
        self.drawer_overlay.add_overlay(self.group_dialog_backdrop)
        self.group_dialog_layer = Gtk.CenterBox()
        self.group_dialog_layer.set_halign(Gtk.Align.CENTER)
        self.group_dialog_layer.set_valign(Gtk.Align.CENTER)
        self.group_dialog_layer.set_visible(False)
        self.drawer_overlay.add_overlay(self.group_dialog_layer)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.add_css_class("drawer-content")
        content.set_hexpand(True)
        content.set_vexpand(True)
        layout.append(content)

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.app_header = header
        header.set_margin_bottom(self.omarchy_metrics["panel_gap"])
        content.append(header)
        topbar = Gtk.CenterBox()
        topbar.add_css_class("topbar")
        header.append(topbar)
        close_button = Gtk.Button()
        close_button.set_has_frame(False)
        close_button.set_child(Gtk.Image.new_from_icon_name("window-close-symbolic"))
        close_button.add_css_class("settings-nav-button")
        close_button.set_tooltip_text("Close Leaf Control (Esc)")
        self._set_accessible_info(close_button, "Close Leaf Control")
        close_button.connect("clicked", lambda *_args: self._dismiss_drawer())
        topbar.set_start_widget(close_button)
        brand = Gtk.Label(label=APP_NAME)
        brand.add_css_class("sidebar-brand")
        brand.set_halign(Gtk.Align.CENTER)
        center_header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        center_header.add_css_class("center-header")
        center_header.set_spacing(3)
        center_header.append(brand)
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.page_nav = nav
        nav.add_css_class("nav")
        nav.add_css_class("top-nav")
        nav.set_halign(Gtk.Align.CENTER)
        center_header.append(nav)
        topbar.set_center_widget(center_header)
        header_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.fullscreen_button = Gtk.Button.new_from_icon_name("view-restore-symbolic")
        self.fullscreen_button.add_css_class("settings-nav-button")
        self.fullscreen_button.set_tooltip_text("Full screen / window (F11)")
        self._set_accessible_info(self.fullscreen_button, "Toggle full screen")
        self.fullscreen_button.connect("clicked", self._toggle_fullscreen)
        header_actions.append(self.fullscreen_button)
        settings_button = Gtk.Button()
        settings_button.set_child(Gtk.Image.new_from_icon_name("emblem-system-symbolic"))
        settings_button.add_css_class("settings-nav-button")
        settings_button.set_tooltip_text("Settings")
        self._set_accessible_info(settings_button, "Settings", "Open Leaf Control settings")
        settings_button.connect("clicked", self._show_settings)
        header_actions.append(settings_button)
        topbar.set_end_widget(header_actions)
        self.settings_nav_button = settings_button

        search_row = Gtk.CenterBox()
        search_row.add_css_class("search-row")
        self.search_row = search_row
        header.append(search_row)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search applications")
        self.search.set_size_request(200, -1)
        self.search.set_hexpand(False)
        self.search.connect("search-changed", self._search_changed)
        search_row.set_center_widget(self.search)
        expose_button = Gtk.Button()
        expose_button.set_child(Gtk.Image.new_from_icon_name("view-paged-symbolic"))
        expose_button.add_css_class("expose-button")
        expose_button.set_tooltip_text("Show open windows with the Exposé plugin")
        self._set_accessible_info(expose_button, "Open windows with Exposé")
        expose_button.connect("clicked", self._open_expose)
        search_row.set_start_widget(expose_button)
        self.expose_button = expose_button
        expose_button.set_visible(self.settings["show_expose_button"])
        command_button = Gtk.Button()
        command_button.set_has_frame(False)
        command_button.set_child(Gtk.Image.new_from_icon_name("utilities-terminal-symbolic"))
        command_button.set_tooltip_text("Run a local command (Alt+F2)")
        command_button.connect("clicked", self._open_command_panel)
        search_row.set_end_widget(command_button)
        self.nav_buttons = {}
        nav_icons = {
            "Apps": "view-grid-symbolic",
            "System": "computer-symbolic",
        }
        for page in ("Apps", "System"):
            button = Gtk.ToggleButton()
            contents = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            icon = Gtk.Image.new_from_icon_name(nav_icons[page])
            icon.add_css_class("top-nav-icon")
            label = Gtk.Label(label=page)
            label.set_hexpand(True)
            contents.append(icon)
            contents.append(label)
            button.set_child(contents)
            button.add_css_class("top-nav-button")
            self._set_accessible_info(button, page, f"Open the {page} page")
            button.connect("clicked", self._navigate, page)
            nav.append(button)
            self.nav_buttons[page] = button
        self.nav_buttons["Apps"].set_active(True)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150)
        self.stack.set_hhomogeneous(False)
        self.stack.set_vhomogeneous(False)
        self.stack.set_vexpand(True)
        content.append(self.stack)
        self.apps_page = self._build_apps_page()
        self.system_page = None
        self.settings_page = None
        self.stack.add_named(self.apps_page, "Apps")
        self._built_pages.add("Apps")
        self._apply_motion_settings()
        self._apply_grid_density()
        self._apply_app_appearance()
        self._apply_dock_preferences()
        if getattr(self, "open_settings_on_start", False):
            initial_page = "Settings"
        elif getattr(self, "open_system_on_start", False):
            initial_page = "System"
        elif getattr(self, "open_apps_on_start", False):
            initial_page = "Apps"
        else:
            initial_page = self.settings["start_page"]
        self._navigate(None, initial_page)
        self.file_search.start(self._file_index_ready)
        self._set_fullscreen(initial_page == "Settings" or (not getattr(self, "force_window", False) and self.settings["window_mode"] == "fullscreen"))
        self.window.present()
        GLib.timeout_add(40, self._show_drawer)
        self.app_root.add_tick_callback(self._layout_tick)
        # Let GTK paint the shell before resolving desktop entries and creating
        # the app tiles. Gio can spend noticeable time reading desktop files.
        GLib.idle_add(self._discover_apps)
        self.window_refresh_id = GLib.timeout_add_seconds(3, self._poll_window_state)
        GLib.idle_add(self._refresh_window_state)
        self._watch_omarchy_theme()

    def _show_drawer(self):
        self.drawer_dismiss_pending = False
        if self.drawer_revealer:
            self.drawer_revealer.set_reveal_child(True)
        motion_wait = (
            0 if self.settings.get("motion_style") == "reduced"
            else self.settings.get("motion_duration", 280)
        )
        focus_delay = max(40, motion_wait)
        focus_target = getattr(self, "search", None) if self.active_page == "Apps" else None
        if focus_target:
            if self.settings["workspace_focus_on_open"]:
                GLib.timeout_add(focus_delay, self._focus_workspace_strip)
            else:
                GLib.timeout_add(focus_delay, self._restore_widget_focus, focus_target)
        if getattr(self, "window_section", None):
            GLib.idle_add(self._app_viewport_resized)
            GLib.idle_add(self._refresh_window_state)
            GLib.timeout_add(focus_delay, self._sync_workspace_strip)
        return GLib.SOURCE_REMOVE

    def _dismiss_drawer(self):
        self.drawer_dismiss_pending = True
        self._hide_workspace_strip()
        if not self.drawer_revealer:
            self.window.set_visible(False)
            return
        self.drawer_dismiss_pending = True
        self.drawer_revealer.set_reveal_child(False)
        if not self.drawer_revealer.get_child_revealed():
            self._drawer_reveal_changed(self.drawer_revealer, None)

    def _drawer_reveal_changed(self, revealer, _property):
        if self.drawer_dismiss_pending and not revealer.get_child_revealed():
            self.drawer_dismiss_pending = False
            self.window.set_visible(False)

    def _place_drawer_on_focused_monitor(self):
        monitors = self._hypr_json("monitors")
        self.drawer_monitor_name = next((item.get("name") for item in monitors if item.get("focused")), "")

    def _set_fullscreen(self, enabled):
        if enabled:
            display = self.window.get_display()
            monitors = display.get_monitors()
            target = next((monitors.get_item(i) for i in range(monitors.get_n_items())
                           if monitors.get_item(i).get_connector() == self.drawer_monitor_name), None)
            if target:
                self.window.fullscreen_on_monitor(target)
            else:
                self.window.fullscreen()
        else:
            self._hide_workspace_strip()
            self.window.unfullscreen()
            self.window.unmaximize()
        GLib.idle_add(self._update_responsive_layout)

    def _toggle_fullscreen(self, *_args):
        self._set_fullscreen(not self.window.is_fullscreen())

    def _close_requested(self, *_args):
        self._dismiss_drawer()
        return True

    def _read_theme_fingerprint(self) -> str:
        digest = hashlib.sha256()
        for path in (OMARCHY_THEME_COLORS, OMARCHY_THEME_SHELL, OMARCHY_STATE_DIR / "theme.name"):
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(b"missing")
        return digest.hexdigest()

    def _watch_omarchy_theme(self):
        directories = (OMARCHY_STATE_DIR, OMARCHY_THEME_COLORS.parent)
        for path in dict.fromkeys(directories):
            if not path.is_dir():
                continue
            try:
                monitor = Gio.File.new_for_path(str(path)).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
                monitor.connect("changed", self._theme_file_changed)
                self.theme_monitors.append(monitor)
            except GLib.Error:
                continue
        self.theme_poll_id = GLib.timeout_add_seconds(3, self._check_theme_fingerprint)

    def _theme_file_changed(self, *_args):
        if self.theme_reload_id:
            GLib.source_remove(self.theme_reload_id)
        self.theme_reload_id = GLib.timeout_add(250, self._reload_omarchy_theme)

    def _check_theme_fingerprint(self):
        if self._read_theme_fingerprint() != self.theme_fingerprint:
            self._theme_file_changed()
        return GLib.SOURCE_CONTINUE

    def _reload_omarchy_theme(self):
        self.theme_reload_id = None
        fingerprint = self._read_theme_fingerprint()
        if fingerprint == self.theme_fingerprint:
            return GLib.SOURCE_REMOVE
        self.theme_fingerprint = fingerprint
        updated_palette = load_omarchy_palette()
        if getattr(self, "theme_mode_label", None):
            self.theme_mode_label.set_text(f"{updated_palette['mode'].title()} palette · managed by Omarchy")
        palette_changed = updated_palette != self.theme_palette
        self._load_style()
        if not palette_changed:
            return GLib.SOURCE_REMOVE
        for chart in self.chart_areas.values():
            chart.queue_draw()
        if getattr(self, "clock_face", None):
            self.clock_face.queue_draw()
        if getattr(self, "note_preview", None):
            start, end = self.notes_view.get_buffer().get_bounds()
            markdown = self.notes_view.get_buffer().get_text(start, end, True)
            populate_markdown_preview(self.note_preview, markdown, self.theme_palette)
        return GLib.SOURCE_REMOVE

    def _shutdown_theme_watch(self, *_args):
        for monitor in self.theme_monitors:
            monitor.cancel()
        for source_id in (self.theme_reload_id, self.theme_poll_id):
            if source_id:
                GLib.source_remove(source_id)

    def _load_style(self):
        self.theme_palette = load_omarchy_palette()
        self.omarchy_metrics = load_omarchy_shell_metrics()
        if not getattr(self, "style_provider", None):
            self.style_provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), self.style_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            self.transparency_provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), self.transparency_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
            )
        style = (Path(__file__).resolve().parent / "leaf.css").read_text(encoding="utf-8")
        style = style.replace("$base-size", f"{self.omarchy_metrics['base_size']}px")
        definitions = "\n".join(
            f"@define-color leaf-{name.replace('_', '-')} {color};"
            for name, color in self.theme_palette.items() if name.startswith("ui_") or name in {
                "bright_foreground", "cyan", "orange"
            }
        )
        self.style_provider.load_from_data((definitions + "\n" + style).encode("utf-8"))
        self._update_window_transparency_style()
        if getattr(self, "app_root", None):
            self.app_root.set_margin_top(0)
            self.app_root.set_margin_bottom(0)
            self.app_root.set_margin_start(0)
            self.app_root.set_margin_end(0)
        if getattr(self, "app_header", None):
            gap = self.omarchy_metrics["panel_gap"]
            self.app_header.set_spacing(gap)
            self.app_header.set_margin_bottom(gap)
        if getattr(self, "page_nav", None):
            self.page_nav.set_spacing(max(4, self.omarchy_metrics["panel_gap"] // 2))

    def _update_window_transparency_style(self):
        provider = getattr(self, "transparency_provider", None)
        if not provider:
            return
        transparency = max(0, min(60, int(self.settings.get("window_transparency", 0)))) if self.active_page == "Apps" else 0
        if getattr(self, "window", None) and not self.window.is_fullscreen():
            transparency = min(transparency, 20)
        opacity = 1 - transparency / 100
        background = self.theme_palette.get("ui_bg", "#111111")
        css = (
            f"@define-color leaf-window-opacity-base {background};\n"
            f".leaf-drawer-surface {{ background: alpha(@leaf-window-opacity-base, {opacity:.2f}); }}"
        )
        provider.load_from_data(css.encode("utf-8"))

    def _build_apps_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.app_focus_scroll_delta = 0.0
        self.app_focus_scroll_queue = []
        self.app_page_focus_pending = False
        self.active_context_popover = None
        outer.set_hexpand(True)
        outer.set_vexpand(True)
        outer.set_valign(Gtk.Align.FILL)
        self._add_app_focus_scroll(outer)
        self.window_section = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.window_section.add_css_class("window-section")
        self.window_section.set_halign(Gtk.Align.FILL)
        self.window_section.set_focusable(True)
        self._set_accessible_info(self.window_section, "Workspaces", "Left and Right select a workspace; Enter switches; Down returns to apps")
        self.window_section.connect("notify::has-focus", lambda *_args: self._sync_workspace_strip())
        self.window_section.set_size_request(-1, 140)
        self.compact_workspaces = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                             homogeneous=True, row_spacing=4, column_spacing=4)
        self.compact_workspaces.set_hexpand(True)
        self.compact_workspaces.set_halign(Gtk.Align.FILL)
        self.compact_workspaces.set_valign(Gtk.Align.CENTER)
        self.compact_workspaces.set_min_children_per_line(1)
        self.compact_workspaces.set_max_children_per_line(5)
        self.window_section.append(self.compact_workspaces)
        outer.append(self.window_section)
        self.apps_summary = Gtk.Label(xalign=0)
        self.apps_summary.set_xalign(0.5)
        self.apps_summary.add_css_class("muted")
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # The app grid yields vertical space to the fixed page controls and dock.
        # A large minimum here can force those bottom rows below short screens.
        scroller.set_min_content_height(0)
        scroller.set_max_content_height(1200)
        scroller.set_vexpand(True)
        scroller.connect("notify::height", self._app_viewport_resized)
        scroller.connect("notify::width", self._app_viewport_resized)
        self.app_scroller = scroller
        self.app_page_capacity = 0
        self.app_reflow_pending = False
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        scroller.set_child(content)
        self.favorites_heading = Gtk.Label(label="Favorites", xalign=0)
        self.favorites_heading.set_xalign(0.5)
        self.favorites_heading.add_css_class("section-title")
        self.favorites_heading.add_css_class("favorites_heading")
        self.favorites_heading.set_tooltip_text("Drop an app here to add it to Favorites")
        self.favorites_grid = self._new_app_grid()
        self.favorites_heading.set_visible(False)
        self.favorites_grid.set_visible(False)
        for widget in (self.favorites_heading, self.favorites_grid):
            target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.COPY)
            target.connect("enter", self._favorites_drop_enter)
            target.connect("motion", self._favorites_drop_enter)
            target.connect("leave", self._favorites_drop_leave)
            target.connect("drop", self._pin_app_drop)
            if widget is self.favorites_grid:
                widget.add_controller(target)
        self.all_apps_heading = Gtk.Label(label="All applications", xalign=0)
        self.all_apps_heading.set_xalign(0.5)
        self.all_apps_heading.add_css_class("section-title")
        content.append(self.all_apps_heading)
        self.app_grid = self._new_app_grid()
        content.append(self.app_grid)
        self.files_heading = Gtk.Label(label="Files", xalign=0)
        self.files_heading.set_xalign(0.5)
        self.files_heading.add_css_class("section-title")
        content.append(self.files_heading)
        self.files_summary = Gtk.Label(xalign=0)
        self.files_summary.set_xalign(0.5)
        self.files_summary.add_css_class("muted")
        content.append(self.files_summary)
        self.files_grid = self._new_app_grid()
        content.append(self.files_grid)
        self.folders_heading = Gtk.Label(label="Folders", xalign=0)
        self.folders_heading.set_xalign(0.5)
        self.folders_heading.add_css_class("section-title")
        content.append(self.folders_heading)
        self.folders_summary = Gtk.Label(xalign=0)
        self.folders_summary.set_xalign(0.5)
        self.folders_summary.add_css_class("muted")
        content.append(self.folders_summary)
        self.folders_grid = self._new_app_grid()
        content.append(self.folders_grid)
        self.files_heading.set_visible(False)
        self.files_summary.set_visible(False)
        self.files_grid.set_visible(False)
        self.folders_heading.set_visible(False)
        self.folders_summary.set_visible(False)
        self.folders_grid.set_visible(False)
        self.app_page_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.app_page_controls.add_css_class("app-page-controls")
        self.app_page_controls.set_halign(Gtk.Align.CENTER)
        self.app_page_controls.set_valign(Gtk.Align.CENTER)
        self.app_page_controls.set_size_request(-1, 40)
        self.app_page_controls.set_visible(False)
        self.app_page_previous = Gtk.Button()
        self.app_page_previous.set_has_frame(False)
        self.app_page_previous.set_child(Gtk.Image.new_from_icon_name("go-previous-symbolic"))
        self._set_accessible_info(self.app_page_previous, "Previous app page")
        self.app_page_previous.connect("clicked", lambda *_args: self._change_app_page(-1))
        self.app_page_controls.append(self.app_page_previous)
        self.app_page_status = Gtk.Label()
        self.app_page_status.add_css_class("muted")
        self.app_page_status.set_halign(Gtk.Align.CENTER)
        self.app_page_status.set_valign(Gtk.Align.CENTER)
        self.app_page_status.set_max_width_chars(28)
        self.app_page_status.set_ellipsize(Pango.EllipsizeMode.END)
        self.app_page_status.set_single_line_mode(True)
        self.app_page_controls.append(self.app_page_status)
        self.app_page_next = Gtk.Button()
        self.app_page_next.set_has_frame(False)
        self.app_page_next.set_child(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        self._set_accessible_info(self.app_page_next, "Next app page")
        self.app_page_next.connect("clicked", lambda *_args: self._change_app_page(1))
        self.app_page_controls.append(self.app_page_next)
        for arrow, direction in ((self.app_page_previous, -1), (self.app_page_next, 1)):
            arrow.set_tooltip_text("Change page · drop an app or group here to move it to that page")
            target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
            target.connect("enter", self._folder_drop_target_enter)
            target.connect("leave", self._clear_drop_target)
            target.connect("drop", self._drop_on_page_arrow, direction)
            arrow.add_controller(target)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        panel.add_css_class("app-browser-panel")
        panel.set_hexpand(True)
        panel.set_vexpand(True)
        panel.set_margin_start(4)
        panel.set_margin_end(4)
        panel.append(self.apps_summary)
        panel.append(scroller)
        panel.append(self.app_page_controls)
        self.app_footer = panel
        self.running_dock = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        self.running_dock.add_css_class("running-dock")
        self.running_dock.set_valign(Gtk.Align.END)
        self.running_dock_buttons = []
        self.running_dock_signature = None
        self.dock_popover = None
        self.dock_hover_key = None
        self.dock_animation_source = None
        self.dragged_dock_pin_id = None
        dock_motion = Gtk.EventControllerMotion.new()
        dock_motion.connect("leave", lambda *_args: self._set_dock_hover(None))
        dock_motion.connect("motion", self._dock_pointer_motion)
        self.running_dock.add_controller(dock_motion)
        dock_scroll = Gtk.ScrolledWindow(hexpand=True)
        self.running_dock_scroller = dock_scroll
        dock_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        dock_scroll.set_min_content_height(72)
        dock_scroll.set_vexpand(False)
        dock_scroll.set_child(self.running_dock)
        dock_scroll.set_margin_bottom(4)
        dock_scroll.add_css_class("app-dock-scroll")
        dock_scroll.set_tooltip_text("Pinned apps and currently open windows")
        self._set_accessible_info(dock_scroll, "App dock", "Pinned apps launch here; open apps switch to their windows. Drag an app here or pin it from its app menu.")
        pin_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.COPY)
        pin_target.connect("enter", self._favorites_drop_enter)
        pin_target.connect("motion", self._favorites_drop_enter)
        pin_target.connect("leave", self._favorites_drop_leave)
        pin_target.connect("drop", self._pin_app_drop)
        dock_scroll.add_controller(pin_target)
        outer.append(panel)
        # Keep the dock outside the grid's expanding panel so it stays anchored
        # to the bottom edge while the app grid takes the remaining height.
        outer.append(dock_scroll)
        self.all_apps_heading.set_label("Loading applications…")
        self.all_apps_heading.set_visible(True)
        return outer

    @staticmethod
    def _new_app_grid():
        grid = Gtk.FlowBox()
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_max_children_per_line(9)
        grid.set_min_children_per_line(1)
        grid.set_row_spacing(14)
        grid.set_column_spacing(14)
        grid.set_homogeneous(True)
        grid.set_halign(Gtk.Align.CENTER)
        grid.set_valign(Gtk.Align.START)
        return grid

    def _apply_grid_density(self):
        if not getattr(self, "app_grid", None):
            return
        density = self.settings["grid_density"]
        spacing = self.settings["grid_spacing"]
        for grid in (self.favorites_grid, self.app_grid, self.files_grid, self.folders_grid):
            grid.set_row_spacing(spacing)
            grid.set_column_spacing(spacing)
        self._fit_app_columns()
        self._apply_app_tile_padding()
        if getattr(self, "app_page_controls", None):
            self._app_viewport_resized()
        for css_class in ("apps-density-compact", "apps-density-spacious"):
            self.apps_page.remove_css_class(css_class)
        if density != "comfortable":
            self.apps_page.add_css_class(f"apps-density-{density}")

    def _app_icon_pixels(self):
        return {"small": 40, "standard": 56, "large": 68}[self.settings["app_icon_size"]]

    def _apply_app_tile_padding(self):
        display = Gdk.Display.get_default()
        if not display:
            return
        provider = getattr(self, "app_tile_padding_provider", None)
        if provider is None:
            provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2
            )
            self.app_tile_padding_provider = provider
        padding = max(0, min(16, int(self.settings.get("app_tile_padding", 4))))
        width = getattr(self, "effective_tile_width", 124) - 2 * padding - 2
        css = f".app-tile {{ padding: {padding}px; min-width: {width}px; }}"
        if css != getattr(self, "_tile_css", None):
            provider.load_from_data(css.encode("utf-8"))
            self._tile_css = css
        if getattr(self, "apps_page", None):
            self.apps_page.queue_resize()

    def _apply_app_appearance(self):
        style = self.settings["app_tile_style"]
        corners = self.settings["app_tile_corners"]
        for widget in (getattr(self, "apps_page", None),):
            if widget is None:
                continue
            for value in ("minimal", "outline", "card"):
                widget.remove_css_class(f"app-tiles-{value}")
            for value in ("square", "soft", "rounded"):
                widget.remove_css_class(f"app-corners-{value}")
            widget.add_css_class(f"app-tiles-{style}")
            widget.add_css_class(f"app-corners-{corners}")

        def update_icons(widget, pixels):
            if widget.has_css_class("app-tile"):
                box = widget.get_child()
                icon = box.get_first_child() if box else None
                if isinstance(icon, Gtk.Image):
                    icon.set_pixel_size(pixels)
            child = widget.get_first_child()
            while child:
                next_child = child.get_next_sibling()
                update_icons(child, pixels)
                child = next_child

        if getattr(self, "apps_page", None):
            update_icons(self.apps_page, self._app_icon_pixels())

    def _apply_app_name_visibility(self):
        show_names = self.settings["show_app_names"]
        tiles = (item[1] for item in self.app_tiles_by_id.values())
        group_tiles = (item[2] for item in self.folder_member_tiles_by_id.values())
        for tile in (*tiles, *group_tiles):
            box = tile.get_child()
            icon = box.get_first_child() if box else None
            label = icon.get_next_sibling() if icon else None
            if isinstance(label, Gtk.Label):
                label.set_visible(show_names)

    def _discover_apps(self):
        def discover():
            try:
                apps = [app for app in Gio.AppInfo.get_all() if app.should_show()]
                error = None
            except Exception as exc:
                apps, error = [], str(exc)
            GLib.idle_add(self._finish_app_discovery, apps, error)

        threading.Thread(target=discover, name="leaf-app-discovery", daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _finish_app_discovery(self, apps, error):
        self.apps = self.layout.ordered(apps)
        self._populate_apps()
        if error:
            self.apps_summary.set_text(f"Could not load applications: {error}")
            self.apps_summary.set_visible(True)
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _hypr_json(topic):
        try:
            result = subprocess.run(["hyprctl", topic, "-j"], capture_output=True, text=True, timeout=1)
            return json.loads(result.stdout) if result.returncode == 0 else []
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return []

    @staticmethod
    def _hypr_dispatch(script):
        try:
            return subprocess.run(["hyprctl", "dispatch", script], capture_output=True, timeout=1).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _poll_window_state(self):
        if self.window and self.window.get_visible() and self.active_page == "Apps":
            self._refresh_window_state()
            self._sync_workspace_strip()
        return GLib.SOURCE_CONTINUE

    def _refresh_window_state(self):
        if not getattr(self, "window_section", None):
            return GLib.SOURCE_REMOVE
        clients = self._hypr_json("clients")
        workspaces = self._hypr_json("workspaces")
        monitors = self._hypr_json("monitors")
        if not all(isinstance(items, list) for items in (clients, workspaces, monitors)):
            return GLib.SOURCE_REMOVE
        clients = [client for client in clients if client.get("mapped") and client.get("pid") != os.getpid()
                   and client.get("workspace", {}).get("id", 0) > 0]
        workspaces = sorted((item for item in workspaces if item.get("id", 0) > 0), key=lambda item: item["id"])
        monitors = sorted((item for item in monitors if not item.get("disabled")), key=lambda item: (item.get("x", 0), item.get("y", 0)))
        fingerprint = json.dumps(([(c.get("address"), c.get("title"), c.get("workspace", {}).get("id"), c.get("at"), c.get("size")) for c in clients],
                                  [(w.get("id"), w.get("windows"), w.get("monitor")) for w in workspaces],
                                  [(m.get("name"), m.get("x"), m.get("y"), m.get("width"), m.get("height"), m.get("activeWorkspace")) for m in monitors]), sort_keys=True)
        if fingerprint == self.window_state_fingerprint:
            self._update_running_dock()
            return GLib.SOURCE_REMOVE
        self.window_state_fingerprint = fingerprint
        self.running_windows = clients
        self.workspace_items = workspaces
        self.monitor_items = monitors
        self._refresh_compact_workspaces()
        self._update_responsive_layout()
        self._update_running_marks()
        self._update_running_dock()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _app_window_keys(app):
        keys = set()
        for value in (app.get_id(), app.get_executable(), getattr(app, "get_startup_wm_class", lambda: None)()):
            if value:
                base = Path(str(value).split()[0]).name.casefold().removesuffix(".desktop")
                keys.add(base)
                keys.add(base.split(".")[-1])
        return keys - {"", "sh", "env", "flatpak", "electron"}

    def _windows_for_app(self, app):
        keys = self._app_window_keys(app)
        return [client for client in self.running_windows if
                str(client.get("class", "")).casefold() in keys or
                str(client.get("initialClass", "")).casefold() in keys]

    def _app_for_window(self, client):
        return next((app for app in self.apps if client in self._windows_for_app(app)), None)

    def _update_running_marks(self):
        for app_id, marker in self.running_marks_by_id.items():
            app = next((item for item in self.apps if app_identifier(item) == app_id), None)
            marker.set_visible(bool(app and self._windows_for_app(app)))

    @staticmethod
    def _workspace_wallpapers():
        state_home = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        omarchy_state = state_home / "omarchy"
        try:
            theme = (omarchy_state / "current/theme.name").read_text().strip()
        except OSError:
            theme = ""
        roots = [Path.home() / ".config/omarchy/backgrounds" / theme,
                 omarchy_state / "current/theme/backgrounds"]
        extensions = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
        backgrounds = []
        for root in roots:
            try:
                backgrounds.extend(str(path) for path in root.iterdir()
                                   if path.is_file() and path.suffix.lower() in extensions)
            except OSError:
                continue
        backgrounds.sort()
        try:
            pins = json.loads((omarchy_state / "per-workspace-backgrounds.json").read_text()).get("pins", {})
            if not isinstance(pins, dict):
                pins = {}
        except (OSError, ValueError, AttributeError):
            pins = {}
        fallback = omarchy_state / "current/background"
        wallpapers = {}
        for workspace_id in range(1, 21):
            path = pins.get(str(workspace_id)) or (backgrounds[(workspace_id - 1) % len(backgrounds)]
                                                   if backgrounds else str(fallback))
            if Path(path).is_file():
                wallpapers[str(workspace_id)] = path
        return wallpapers

    def _sync_workspace_strip(self):
        if getattr(self, "workspace_strip_process", None) is not None:
            return GLib.SOURCE_REMOVE
        shown = (self.window and self.window.get_visible() and self.active_page == "Apps"
                 and self.drawer_revealer and self.drawer_revealer.get_reveal_child()
                 and not self.drawer_dismiss_pending and self.window.is_fullscreen()
                 and self.settings["workspace_visible"] and self.workspace_plugin_available)
        if not shown:
            self._hide_workspace_strip()
            return GLib.SOURCE_REMOVE
        display = self.window.get_display()
        output = display.get_monitor_at_surface(self.window.get_surface())
        if output and output.get_connector():
            self.drawer_monitor_name = output.get_connector()
        monitor = self.drawer_monitor_name
        position = self.window_section.translate_coordinates(self.app_root, 0, 0)
        top = round(position[1]) if position else 140
        payload = json.dumps({"monitor": monitor, "wallpapers": self._workspace_wallpapers(),
                              "pid": os.getpid(), "top": top,
                              "height": max(100, self.window_section.get_height()),
                              "workspaceSets": workspace_sets(self.monitor_items, self.workspace_items),
                              "livePreviews": self.settings["workspace_live_previews"],
                              "otherMonitorApps": self.settings["workspace_other_monitor_apps"],
                              "switchBothMonitors": self.settings["workspace_switch_both_monitors"],
                              "selected": self.selected_workspace_id if self.window.get_focus() is self.window_section else None}, sort_keys=True)
        if self.workspace_strip_open and payload == self.workspace_strip_payload:
            return GLib.SOURCE_REMOVE
        try:
            self.workspace_strip_process = subprocess.Popen(["omarchy-shell", "shell", "summon", "io.github.bravehart911.leaf-control",
                              payload], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.workspace_strip_open = True
            self.workspace_strip_monitor = monitor
            self.workspace_strip_payload = payload
            GLib.timeout_add(20, self._workspace_strip_update_finished)
        except OSError:
            pass
        return GLib.SOURCE_REMOVE

    def _workspace_strip_update_finished(self):
        process = self.workspace_strip_process
        if process.poll() is None:
            return GLib.SOURCE_CONTINUE
        self.workspace_strip_process = None
        if process.returncode:
            self.workspace_strip_payload = ""
            if self.drawer_dismiss_pending:
                self._drawer_reveal_changed(self.drawer_revealer, None)
            return GLib.SOURCE_REMOVE
        # Coalesce key repeats and deliver the latest focus only after the
        # previous IPC completes; older selections must never arrive last.
        self._sync_workspace_strip()
        if self.drawer_dismiss_pending:
            self._drawer_reveal_changed(self.drawer_revealer, None)
        return GLib.SOURCE_REMOVE

    def _hide_workspace_strip(self):
        if getattr(self, "workspace_strip_process", None) is not None:
            return
        if not getattr(self, "workspace_strip_open", False):
            return
        self.workspace_strip_open = False
        self.workspace_strip_monitor = ""
        self.workspace_strip_payload = ""
        try:
            self.workspace_strip_process = subprocess.Popen(["omarchy-shell", "shell", "hide", "io.github.bravehart911.leaf-control"],
                             start_new_session=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            GLib.timeout_add(20, self._workspace_strip_update_finished)
        except OSError:
            pass

    def _shutdown_workspace_strip(self, *_args):
        # The event loop is ending, so queued callbacks cannot finish cleanup.
        process = getattr(self, "workspace_strip_process", None)
        try:
            if process is not None:
                process.wait(timeout=3)
            subprocess.run(["omarchy-shell", "shell", "hide", "io.github.bravehart911.leaf-control"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _ensure_running_dock_visible(self):
        dock = getattr(self, "running_dock", None)
        if dock:
            dock.set_visible(self.settings.get("dock_visible", True))
        dock_scroller = getattr(self, "running_dock_scroller", None)
        if dock_scroller:
            dock_scroller.set_visible(self.settings.get("dock_visible", True))

    def _update_running_dock(self):
        if not hasattr(self, "running_dock"):
            return
        # Keep visibility independent of dock contents and refresh signatures.
        # Otherwise an early return can leave the dock hidden after navigation.
        self._ensure_running_dock_visible()
        groups = {}
        scope = self.settings["dock_running_scope"]
        monitor = next((m for m in self.monitor_items if m.get("name") == self.drawer_monitor_name), {})
        for window in self.running_windows:
            if scope == "monitor" and window.get("monitor") != monitor.get("id"):
                continue
            if scope == "workspace" and window.get("workspace", {}).get("id") != monitor.get("activeWorkspace", {}).get("id"):
                continue
            app = self._app_for_window(window)
            key = app_identifier(app) if app else str(window.get("initialClass") or window.get("class") or window["address"])
            if key not in groups:
                groups[key] = {"app": app, "windows": [], "name": app.get_display_name() if app else key}
            groups[key]["windows"].append(window)
        active = {m.get("activeWorkspace", {}).get("id") for m in self.monitor_items}
        apps_by_id = {app_identifier(app): app for app in self.apps}
        pinned = [app_id for app_id in self.layout.favorites if app_id in apps_by_id]
        signature = [(k, [(w["address"], w.get("title"), w.get("workspace", {}).get("id")) for w in g["windows"]]) for k, g in groups.items()]
        signature = repr((signature, pinned, sorted(active)))
        if signature == self.running_dock_signature or self.dock_popover or getattr(self, "dock_drag_active", False):
            return
        self.running_dock_signature = signature
        focused = self.window.get_focus()
        focused_key = next((b.dock_key for b in self.running_dock_buttons if b is focused), None)
        child = self.running_dock.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.running_dock.remove(child)
            child = next_child
        self.running_dock_buttons = []
        dock_keys = list(dict.fromkeys([*pinned, *(groups.keys() if self.settings["dock_show_running"] else [])]))
        running_only = [key for key in dock_keys if key not in pinned]
        if not dock_keys:
            hint = Gtk.Label(label="Pin apps here · right-click an app or drag it to this bar")
            hint.add_css_class("dock-empty-hint")
            self.running_dock.append(hint)
        for key in dock_keys:
            if pinned and running_only and key == running_only[0]:
                divider = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
                divider.add_css_class("dock-divider")
                self.running_dock.append(divider)
            group = groups.get(key, {"app": apps_by_id.get(key), "windows": [], "name": ""})
            button = Gtk.Button()
            button.dock_key = key
            button.add_css_class("dock-app")
            app = group["app"] or apps_by_id.get(key)
            windows = group["windows"]
            is_pinned = key in pinned
            if any(w.get("workspace", {}).get("id") in active for w in windows):
                button.add_css_class("dock-current")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            base = self.settings["dock_icon_size"]
            peak = round(base * self.settings["dock_magnify_scale"] / 100) if self.settings["dock_magnify"] else base
            icon = DockIcon(app.get_icon() if app else None, base, peak)
            button.dock_icon = icon
            box.append(icon)
            indicators = Gtk.Box(spacing=3, halign=Gtk.Align.CENTER)
            indicators.add_css_class("dock-indicators")
            style = self.settings["dock_indicator_style"]
            if windows and style in ("dots", "single"):
                for _ in range(min(len(windows), 3) if style == "dots" else 1):
                    marker = Gtk.Box()
                    marker.add_css_class("dock-dot")
                    indicators.append(marker)
                if style == "dots" and len(windows) > 3:
                    count_label = Gtk.Label(label=str(len(windows)))
                    count_label.add_css_class("dock-count")
                    indicators.append(count_label)
            elif windows and style == "bar":
                marker = Gtk.Box()
                marker.add_css_class("dock-bar")
                indicators.append(marker)
            box.append(indicators)
            button.set_child(box)
            name = app.get_display_name() if app else group["name"]
            if self.settings["dock_show_names"]:
                label = Gtk.Label(label=name, max_width_chars=10, ellipsize=Pango.EllipsizeMode.END)
                label.add_css_class("dock-name")
                box.append(label)
            count = len(windows)
            details = f"Pinned app. {count} running window{'s' if count != 1 else ''}." if is_pinned else f"{count} running window{'s' if count != 1 else ''}."
            button.set_tooltip_text(f"{name} · {'Pinned · ' if is_pinned else ''}{count} running window{'s' if count != 1 else ''}")
            self._set_accessible_info(button, name, f"{details} Activate to {'switch to it' if windows else 'launch it'}. Right click for app actions.")
            button.connect("clicked", self._activate_dock_entry, app, windows)
            if is_pinned:
                self._add_dock_pin_drag(button, key)
            elif windows:
                self._add_window_drag(button, windows)
            if app:
                context = Gtk.GestureClick.new()
                context.set_button(3)
                context.connect("pressed", self._show_app_menu, app, button)
                button.add_controller(context)
            self.running_dock.append(button)
            self.running_dock_buttons.append(button)
            if key == focused_key:
                button.grab_focus()
        self._set_dock_hover(self.dock_hover_key, immediate=True)

    def _dock_pointer_motion(self, _controller, x, _y):
        if getattr(self, "dock_drag_active", False):
            return
        centers = [b.get_allocation().x + b.get_width() / 2
                   for b in self.running_dock_buttons]
        if not centers:
            return
        # Interpolate across stable slot centers, including the group divider.
        position = 0.0
        if x >= centers[-1]:
            position = len(centers) - 1
        elif x > centers[0]:
            for i in range(len(centers) - 1):
                if centers[i] <= x <= centers[i + 1]:
                    position = i + (x - centers[i]) / max(1, centers[i + 1] - centers[i])
                    break
        self._set_dock_hover(position)

    def _set_dock_hover(self, position, immediate=False):
        self.dock_hover_key = position
        if not self.settings["dock_magnify"]:
            position = None
        self.dock_targets = dock_hover_sizes(
            len(self.running_dock_buttons), position, self.settings["dock_icon_size"],
            self.settings["dock_magnify_scale"], self.settings["dock_magnify_reach"])
        if immediate or self.settings["motion_style"] == "reduced":
            for button, target in zip(self.running_dock_buttons, self.dock_targets):
                button.dock_icon.set_draw_size(target)
            return
        if self.dock_animation_source is not None:
            return
        self.dock_frame_time = None

        def animate(_widget, clock):
            now = clock.get_frame_time()
            elapsed = (now - self.dock_frame_time) / 1_000_000 if self.dock_frame_time else 1 / 60
            self.dock_frame_time = now
            blend = 1 - math.exp(-min(elapsed, 0.05) / 0.065)
            changing = False
            for button, target in zip(self.running_dock_buttons, self.dock_targets):
                icon = button.dock_icon
                delta = target - icon.draw_size
                changing |= abs(delta) > 0.05
                icon.set_draw_size(target if abs(delta) <= 0.05 else icon.draw_size + delta * blend)
            if not changing:
                self.dock_animation_source = None
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        self.dock_animation_source = self.running_dock.add_tick_callback(animate)

    def _add_dock_pin_drag(self, button, app_id):
        source = Gtk.DragSource.new()
        source.set_actions(Gdk.DragAction.MOVE)
        source.connect("prepare", self._prepare_dock_pin_drag, app_id)
        source.connect("drag-begin", lambda *_args: self._begin_dock_pin_drag())
        source.connect("drag-end", self._finish_dock_pin_drag)
        button.add_controller(source)
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        target.connect("enter", self._dock_pin_drop_enter)
        target.connect("motion", self._dock_pin_drop_enter)
        target.connect("leave", self._clear_drop_target)
        target.connect("drop", self._drop_dock_pin, app_id)
        button.add_controller(target)

    def _prepare_dock_pin_drag(self, source, x, y, app_id):
        self.dragged_dock_pin_id = app_id
        source.set_icon(Gtk.WidgetPaintable.new(source.get_widget()), int(x), int(y))
        return Gdk.ContentProvider.new_for_value(app_id)

    def _begin_dock_pin_drag(self):
        self.dock_drag_active = True
        self._set_dock_hover(None)

    def _finish_dock_pin_drag(self, *_args):
        self.dock_drag_active = False
        self.dragged_dock_pin_id = None
        GLib.idle_add(self._refresh_window_state)

    def _dock_pin_drop_enter(self, target, *_args):
        target.get_widget().add_css_class("drop-target-add")
        return Gdk.DragAction.MOVE if self.dragged_dock_pin_id else Gdk.DragAction.COPY

    def _drop_dock_pin(self, target, source_id, _x, _y, target_id):
        self._clear_drop_target(target)
        if source_id not in self.layout.favorites:
            pinned = self._pin_app_drop(target, source_id, _x, _y)
            if pinned:
                self._render_pinned_editor()
            return pinned
        if target_id not in self.layout.favorites:
            return False
        try:
            moved = self.layout.move_favorite_before(source_id, target_id)
        except OSError as exc:
            self._message(f"Could not reorder pinned apps: {exc}")
            return False
        if moved:
            self.running_dock_signature = None
            self._update_running_dock()
            self._render_pinned_editor()
        return moved

    def _activate_dock_entry(self, button, app, windows):
        if windows:
            self._activate_dock_app(button, windows)
        elif app:
            self._launch_app(app)

    def _add_window_drag(self, widget, windows):
        source = Gtk.DragSource.new()
        source.set_actions(Gdk.DragAction.MOVE)
        payload = json.dumps({"windows": [w["address"] for w in windows]}).encode()
        source.connect("prepare", lambda *_args: Gdk.ContentProvider.new_for_bytes(
            "application/x-leaf-windows", GLib.Bytes.new(payload)))
        def begin(_source, _drag):
            self.dock_drag_active = True
            _source.set_icon(Gtk.WidgetPaintable.new(widget), 24, 24)
        def end(*_args):
            self.dock_drag_active = False
            if self.dock_popover:
                self.dock_popover.popdown()
            GLib.idle_add(self._refresh_window_state)
        source.connect("drag-begin", begin)
        source.connect("drag-end", end)
        widget.add_controller(source)
        widget.set_tooltip_text((widget.get_tooltip_text() or "Window") + " · Drag to a workspace")

    def _activate_dock_app(self, button, windows):
        if len(windows) == 1:
            self._focus_window(windows[0]["address"])
            return
        popover = Gtk.Popover(position=Gtk.PositionType.TOP)
        popover.set_parent(button)
        self.dock_popover = popover
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for window in windows:
            workspace = window.get("workspace", {}).get("id", 1)
            row = Gtk.Button()
            label = Gtk.Label(label=f"{window.get('title') or window.get('class')}  ·  {(workspace - 1) % 10 + 1}", xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(48)
            row.set_child(label)
            row.connect("clicked", lambda _b, address=window["address"]: (popover.popdown(), self._focus_window(address)))
            self._add_window_drag(row, [window])
            box.append(row)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_max_content_height(320)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(box)
        popover.set_child(scroll)
        def closed(*_args):
            self.dock_popover = None
            popover.unparent()
            button.grab_focus()
        popover.connect("closed", closed)
        popover.popup()

    def _focus_window(self, address):
        if address:
            helper = Path(__file__).resolve().parent / "select_workspace.py"
            subprocess.Popen([sys.executable, str(helper), "window", address, self.drawer_monitor_name],
                             start_new_session=True)

    def _populate_apps(self, query=""):
        self._ensure_running_dock_visible()
        self._clear_grid(self.app_grid)
        self._clear_grid(self.favorites_grid)
        self.app_tiles_by_id = {}
        self.folder_tiles_by_id = {}
        self.folder_member_tiles_by_id = {}
        self.running_marks_by_id = {}
        query = query.casefold().strip()
        ordered_apps = (
            self.layout.most_used(self.apps)
            if self.settings["sort_apps_by_usage"] else self.layout.ordered(self.apps)
        )
        visible = [app for app in ordered_apps if query in app.get_display_name().casefold() or query in (app.get_description() or "").casefold()]
        folder_members = self.layout.folder_app_ids() if not query else set()
        others = [app for app in visible if app_identifier(app) not in folder_members]
        category_positions = {category: index for index, category in enumerate(APP_CATEGORY_ORDER)}
        saved_order = {app_identifier(app): index for index, app in enumerate(others)}
        if not self.settings["sort_apps_by_usage"]:
            others.sort(key=lambda app: (category_positions[self._app_category(app)], saved_order[app_identifier(app)]))
        self.favorites_heading.set_visible(False)
        self.favorites_grid.set_visible(False)
        self.all_apps_heading.set_label("Applications" if query else "All applications")
        self.all_apps_heading.set_visible(bool(query))
        self.app_grid.set_visible(bool(others) or not query)
        self.files_heading.set_visible(bool(query))
        self.files_summary.set_visible(bool(query))
        self.files_grid.set_visible(bool(query))
        self.folders_heading.set_visible(bool(query))
        self.folders_summary.set_visible(bool(query))
        self.folders_grid.set_visible(bool(query))
        if query:
            self._clear_grid(self.files_grid)
            self._clear_grid(self.folders_grid)
            self.files_summary.set_text("Searching file names…" if self.file_search.ready else "Indexing names in your personal folders…")
            self.folders_summary.set_text("Searching folder names…" if self.file_search.ready else "Indexing names in your personal folders…")
        self.apps_summary.set_text(
            f"{len(visible)} matching applications · Select an app to launch" if query and visible
            else "No matching applications." if query
            else f"{len(visible)} applications · Drag to arrange · Ctrl+↑/↓ to reorder · Shift+F10 for actions"
        )
        self.apps_summary.set_visible(bool(query))
        items = (
            [("folder", folder_id, folder) for folder_id, folder in self.layout.folders.items()]
            if not query else []
        ) + [("app", app, None) for app in others]
        if not query and not self.settings["sort_apps_by_usage"]:
            items = self.layout.ordered_entries(items)
        if not query:
            self.app_grid_entry_ids = [f"folder:{item}" if kind == "folder" else app_identifier(item)
                                       for kind, item, _extra in items]
        if query:
            self.app_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            visible_items = items
            self.app_page_controls.set_visible(False)
        else:
            self.app_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            per_page = self.app_page_capacity or self._app_page_capacity()
            self.app_page_capacity = per_page
            page_count = max(1, math.ceil(len(items) / per_page))
            self.app_page_count = page_count
            self.app_page_index %= page_count
            start = self.app_page_index * per_page
            visible_items = items[start:start + per_page]
            self.app_page_controls.set_visible(True)
            self._update_app_page_indicator()
            self.app_page_previous.set_sensitive(page_count > 1)
            self.app_page_next.set_sensitive(page_count > 1)
        for kind, item, extra in visible_items:
            if kind == "folder":
                self._append_folder_tile(item, extra)
            else:
                self._append_app_tile(self.app_grid, item)

    def _update_app_page_indicator(self):
        page = self.app_page_index + 1
        total = max(1, getattr(self, "app_page_count", 1))
        style = self.settings.get("app_page_indicator_style", "modern")
        if style not in {name for name, _label in APP_PAGE_INDICATOR_STYLES}:
            style = "modern"
        for name, _label in APP_PAGE_INDICATOR_STYLES:
            css_class = f"app-page-indicator-{name}"
            if self.app_page_status.has_css_class(css_class):
                self.app_page_status.remove_css_class(css_class)
        self.app_page_status.set_visible(style != "none")
        self._set_accessible_info(self.app_page_status, f"Page {page} of {total}")
        if style == "none":
            text = ""
        elif style == "dots" and total <= 12:
            text = " ".join("●" if index == page else "○" for index in range(1, total + 1))
        else:
            text = f"{page} / {total}"
        self.app_page_status.set_text(text)
        if style != "none":
            self.app_page_status.add_css_class(f"app-page-indicator-{style}")

    def _app_viewport_resized(self, *_args):
        if self.app_reflow_pending:
            return
        self.app_reflow_pending = True
        GLib.idle_add(self._reflow_app_pages)

    def _reflow_app_pages(self):
        self.app_reflow_pending = False
        if not getattr(self, "app_scroller", None) or not self.app_scroller.get_height():
            return GLib.SOURCE_REMOVE
        capacity = self._app_page_capacity()
        if capacity and capacity != self.app_page_capacity:
            self.app_page_capacity = capacity
            self._populate_apps(self.search.get_text())
        return GLib.SOURCE_REMOVE

    def _app_page_capacity(self):
        columns = max(1, self.app_grid.get_max_children_per_line())
        viewport = self.app_scroller.get_height() or 320
        fixed = self.all_apps_heading.measure(Gtk.Orientation.VERTICAL, -1)[1] + 10 if self.all_apps_heading.get_visible() else 0
        tile_height = self._app_icon_pixels() + 2 * self.settings["app_tile_padding"] + (44 if self.settings["show_app_names"] else 16)
        for button in self._grid_buttons(self.app_grid):
            tile_height = max(tile_height, button.measure(Gtk.Orientation.VERTICAL, getattr(self, "effective_tile_width", 124))[1])
        return page_capacity(columns, viewport - 8, tile_height, self.app_grid.get_row_spacing(), fixed)

    def _change_app_page(self, direction):
        if self.app_page_focus_pending:
            return
        self.app_page_index = (self.app_page_index + direction) % getattr(self, "app_page_count", 1)
        self._populate_apps()
        # The workspace strip is a separate shell surface; refresh it after
        # every pagination change so it stays present across all app pages.
        GLib.idle_add(self._sync_workspace_strip)
        self.app_page_focus_pending = True
        def focus_page_edge():
            buttons = self._grid_buttons(self.app_grid)
            if buttons:
                buttons[0 if direction > 0 else -1].grab_focus()
            self.app_page_focus_pending = False
            queued = self.app_focus_scroll_queue
            self.app_focus_scroll_queue = []
            for queued_direction in queued:
                self._step_app_focus(queued_direction)
            return GLib.SOURCE_REMOVE

        # FlowBox rebuilds its children during _populate_apps. Wait for GTK to
        # finish that layout before focusing a tile on the newly selected page.
        GLib.idle_add(focus_page_edge)

    def _add_app_focus_scroll(self, widget):
        flags = Gtk.EventControllerScrollFlags.BOTH_AXES | Gtk.EventControllerScrollFlags.DISCRETE
        controller = Gtk.EventControllerScroll.new(flags)
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("scroll", self._on_app_focus_scroll)
        widget.add_controller(controller)

    def _on_app_focus_scroll(self, _controller, _delta_x, delta_y):
        """Move the visible keyboard highlight through app and action buttons."""
        if self.active_page != "Apps":
            return False
        if delta_y == 0:
            return False
        if (self.command_panel or self.create_group_panel or self.open_folder_window
                or getattr(self, "dock_popover", None) or getattr(self, "active_context_popover", None)):
            return False
        targets = [
            button
            for grid in (self.favorites_grid, self.app_grid, self.files_grid, self.folders_grid)
            for button in self._grid_buttons(grid)
        ]
        if not targets:
            return False
        self.app_focus_scroll_delta += delta_y
        if abs(self.app_focus_scroll_delta) < 0.5:
            return True
        direction = 1 if self.app_focus_scroll_delta > 0 else -1
        self.app_focus_scroll_delta = 0.0
        if self.app_page_focus_pending:
            self.app_focus_scroll_queue.append(direction)
            return True
        return self._step_app_focus(direction)

    def _step_app_focus(self, direction):
        if self.app_page_focus_pending:
            self.app_focus_scroll_queue.append(direction)
            return True
        focus = self.window.get_focus()
        targets = [
            button
            for grid in (self.favorites_grid, self.app_grid, self.files_grid, self.folders_grid)
            for button in self._grid_buttons(grid)
        ]
        if not targets:
            return False
        app_buttons = self._grid_buttons(self.app_grid)
        focused_button = focus
        while focused_button and focused_button not in targets:
            focused_button = focused_button.get_parent()
        if (
            not self.search.get_text().strip()
            and getattr(self, "app_page_count", 1) > 1
            and focused_button in app_buttons
        ):
            app_index = app_buttons.index(focused_button)
            if direction > 0 and app_index == len(app_buttons) - 1:
                self._change_app_page(1)
                return True
            if direction < 0 and app_index == 0:
                self._change_app_page(-1)
                return True
        try:
            current = targets.index(focused_button)
        except ValueError:
            current = -1 if direction > 0 else 0
        next_index = (current + direction) % len(targets)
        targets[next_index].grab_focus()
        return True

    def _favorites_drop_enter(self, target, *_args):
        widget = target.get_widget()
        if widget:
            widget.add_css_class("drop-target-add")
        return Gdk.DragAction.COPY

    def _favorites_drop_leave(self, target, *_args):
        widget = target.get_widget()
        if widget:
            widget.remove_css_class("drop-target-add")

    def _pin_app_drop(self, target, app_id, _x, _y):
        self._favorites_drop_leave(target)
        app = next((item for item in self.apps if app_identifier(item) == app_id), None)
        if app is None:
            return False
        if not self.layout.is_favorite(app):
            self.layout.toggle_favorite(app)
            self._populate_apps(self.search.get_text())
            self._update_running_dock()
        return True

    @staticmethod
    def _clear_grid(grid):
        child = grid.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            grid.remove(child)
            child = next_child

    @staticmethod
    def _app_category(app):
        try:
            categories = set((app.get_categories() or "").split(";"))
        except (AttributeError, TypeError):
            categories = set()
        groups = (
            ("Audio & Video", {"AudioVideo", "Audio", "Video", "Music", "Player", "Recorder", "Mixer", "Sequencer", "TV", "AudioVideoEditing"}),
            ("Development", {"Development", "IDE", "Building", "Debugger", "RevisionControl", "WebDevelopment", "ArtificialIntelligence"}),
            ("Games", {"Game", "Amusement"}),
            ("Graphics & Design", {"Graphics", "Photography", "Engineering", "VectorGraphics", "RasterGraphics", "2DGraphics", "Design", "Publishing", "FlowChart"}),
            ("Internet & Communication", {"Network", "WebBrowser", "Email", "Chat", "InstantMessaging", "FileTransfer", "P2P", "RemoteAccess", "Telephony"}),
            ("Office & Documents", {"Office", "WordProcessor", "Spreadsheet", "Presentation", "Database", "Calendar", "ContactManagement", "Viewer", "Dictionary", "Finance"}),
            ("System & Settings", {"System", "Settings", "HardwareSettings", "PackageManager", "Monitor", "TerminalEmulator", "Virtualization", "Printing"}),
            ("Utilities", {"Utility", "Calculator", "FileManager", "Archiving", "Compression", "Core", "TextEditor"}),
        )
        for group, tags in groups:
            if categories.intersection(tags):
                return group

        display_name = app.get_display_name().casefold()
        name = f"{display_name} {app.get_description() or ''}".casefold()
        name_groups = (
            ("Games", ("game", "minecraft", "skyrim", "witcher", "cyberpunk", "hades", "beamng", "cities: skylines", "arena breakout")),
            ("Development", ("code editor", "development", "programming", "ide", "codex", "docker", "cmake", "studio")),
            ("Graphics & Design", ("drawing", "design", "image editor", "photo", "paint", "cad", "illustration", "vector")),
            ("Audio & Video", ("video", "music", "audio", "media", "streaming", "podcast", "obs", "youtube", "jellyfin", "plex")),
            ("Internet & Communication", ("browser", "web", "mail", "email", "message", "chat", "social", "meeting", "maps", "amazon", "basecamp", "discord", "whatsapp", "pinterest", "zoom", "twitter", "hey")),
            ("Office & Documents", ("office", "document", "spreadsheet", "presentation", "writer", "calc", "notes", "pdf", "word", "contacts")),
            ("System & Settings", ("system", "settings", "disk", "hardware", "task manager", "terminal", "package manager")),
            ("Utilities", ("utility", "calculator", "file manager", "archive", "password", "clipboard", "wolframalpha")),
        )
        for group, keywords in name_groups:
            if any(keyword in name for keyword in keywords) or (group == "Internet & Communication" and display_name == "x"):
                return group
        return "Other"

    def _append_app_tile(self, grid, app):
        app_id = app_identifier(app)
        tile = Gtk.Button()
        tile.add_css_class("app-tile")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        image = Gtk.Image.new_from_gicon(app.get_icon()) if app.get_icon() else Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
        image.set_pixel_size(self._app_icon_pixels())
        box.set_spacing(4)
        box.append(image)
        label = Gtk.Label(label=app.get_display_name(), wrap=True, max_width_chars=12, lines=2, ellipsize=Pango.EllipsizeMode.END, justify=Gtk.Justification.CENTER)
        label.add_css_class("app-name")
        label.set_visible(self.settings["show_app_names"])
        box.append(label)
        if self.layout.is_favorite(app):
            favorite_mark = Gtk.Label(label="★")
            favorite_mark.add_css_class("muted")
            box.append(favorite_mark)
        running_mark = Gtk.Box()
        running_mark.add_css_class("running-indicator")
        running_mark.set_halign(Gtk.Align.CENTER)
        running_mark.set_visible(bool(self._windows_for_app(app)))
        box.append(running_mark)
        self.running_marks_by_id[app_id] = running_mark
        tile.set_child(box)
        details = app.get_description() or app.get_display_name()
        tile.set_tooltip_text(f"{app.get_display_name()}\n{details}\n\nDrop on another app to stack them into a group · Drop on a group to add it · Drag to either side to reorder · Shift+F10 for actions")
        self._set_accessible_info(
            tile,
            app.get_display_name(),
            f"{details}. Press Shift+F10 or the Menu key for actions. Use Ctrl+Up or Ctrl+Down to reorder.",
        )
        self.app_tiles_by_id[app_id] = (app, tile)
        tile.connect("clicked", lambda _button, item=app: self._activate_app(item))
        self._add_drag_drop(tile, app_id)
        context = Gtk.GestureClick.new()
        context.set_button(3)
        context.connect("pressed", self._show_app_menu, app, tile)
        tile.add_controller(context)
        grid.append(tile)

    def _append_folder_tile(self, folder_id, folder):
        tile = Gtk.Button()
        tile.add_css_class("app-tile")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        preview = Gtk.Grid(column_spacing=2, row_spacing=2, column_homogeneous=True, row_homogeneous=True)
        preview.set_halign(Gtk.Align.CENTER)
        preview.set_valign(Gtk.Align.CENTER)
        apps_by_id = {app_identifier(app): app for app in self.apps}
        for index, app_id in enumerate(folder["apps"][:6]):
            app = apps_by_id.get(app_id)
            if not app:
                continue
            icon = Gtk.Image.new_from_gicon(app.get_icon()) if app.get_icon() else Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
            icon.set_pixel_size(26)
            self._set_accessible_info(icon, app.get_display_name())
            preview.attach(icon, index % 3, index // 3, 1, 1)
        preview.set_size_request(84, 54)
        box.append(preview)
        label = Gtk.Label(label=folder["name"], wrap=True, max_width_chars=18, justify=Gtk.Justification.CENTER)
        label.add_css_class("app-name")
        box.append(label)
        count = Gtk.Label(label=f"{len(folder['apps'])} apps")
        count.add_css_class("muted")
        box.append(count)
        tile.set_child(box)
        self._set_accessible_info(tile, folder["name"], f"Group with {len(folder['apps'])} apps. Activate to open the app grid. Drag to place this group; drop an app here to add it. Use Ctrl+Up or Ctrl+Down to move it; Shift+F10 for actions.")
        tile.set_tooltip_text(f"{folder['name']} · {len(folder['apps'])} apps\nDrag to place this group · Drop an app here to add it · Ctrl+↑/↓ to move")
        self.folder_tiles_by_id[folder_id] = tile
        tile.connect("clicked", self._open_folder, folder_id, tile)
        self._add_folder_drop_target(tile, folder_id)
        context = Gtk.GestureClick.new()
        context.set_button(3)
        context.connect("pressed", self._show_folder_menu, folder_id, tile)
        tile.add_controller(context)
        self.app_grid.append(tile)

    def _append_file_tile(self, path):
        tile = Gtk.Button()
        tile.add_css_class("app-tile")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        is_folder = path.is_dir()
        image = Gtk.Image.new_from_icon_name("folder-symbolic" if is_folder else "text-x-generic-symbolic")
        image.set_pixel_size(self._app_icon_pixels())
        box.append(image)
        label = Gtk.Label(label=path.name, wrap=True, max_width_chars=24, justify=Gtk.Justification.CENTER)
        label.add_css_class("app-name")
        box.append(label)
        parent = Gtk.Label(label=str(path.parent), wrap=True, max_width_chars=34, justify=Gtk.Justification.CENTER)
        parent.add_css_class("muted")
        box.append(parent)
        tile.set_child(box)
        tile.set_tooltip_text(str(path))
        tile.connect("clicked", self._open_file_result, path)
        (self.folders_grid if is_folder else self.files_grid).append(tile)

    def _open_file_result(self, _button, path):
        try:
            Gio.AppInfo.launch_default_for_uri(path.as_uri(), None)
            self._dismiss_drawer()
        except (GLib.Error, OSError, ValueError) as exc:
            self._message(f"Could not open {path.name}: {exc}")

    def _show_file_results(self, query, results, ready, error):
        if self.search.get_text().strip() != query:
            return
        self._clear_grid(self.files_grid)
        self._clear_grid(self.folders_grid)
        files = [path for path in results if not path.is_dir()]
        folders = [path for path in results if path.is_dir()]
        if error:
            self.files_summary.set_text(f"File search unavailable: {error}")
            self.folders_summary.set_text(f"Folder search unavailable: {error}")
        elif not self.file_search.roots:
            self.files_summary.set_text("No personal folders were found to search.")
            self.folders_summary.set_text("No personal folders were found to search.")
        elif not ready:
            self.files_summary.set_text("Indexing file names in your personal folders…")
            self.folders_summary.set_text("Indexing folder names in your personal folders…")
        else:
            self.files_summary.set_text(
                f"{len(files)} matching file{'s' if len(files) != 1 else ''} · names only"
                if files else "No matching files."
            )
            self.folders_summary.set_text(
                f"{len(folders)} matching folder{'s' if len(folders) != 1 else ''} · names only"
                if folders else "No matching folders."
            )
            for path in files:
                self._append_file_tile(path)
            for path in folders:
                self._append_file_tile(path)

    def _file_index_ready(self):
        if getattr(self, "settings_page", None) and getattr(self, "settings_status", None):
            self.settings_status.set_text("Local filename index is ready")
        query = self.search.get_text().strip()
        if query:
            self.file_search.search(query, self._show_file_results)
        return GLib.SOURCE_REMOVE

    def _prepare_tile_drag(self, source, x, y, entry_id):
        self.dragged_entry_id = entry_id
        source.set_icon(Gtk.WidgetPaintable.new(source.get_widget()), int(x), int(y))
        return Gdk.ContentProvider.new_for_value(entry_id)

    def _place_grid_entry(self, source_id, target_id, after=False):
        ids = list(getattr(self, "app_grid_entry_ids", []))
        if source_id not in ids and any(app_identifier(app) == source_id for app in self.apps):
            ids.append(source_id)
        try:
            changed = self.layout.place_entry(source_id, target_id, ids, after)
            if changed and self.settings["sort_apps_by_usage"]:
                self.settings["sort_apps_by_usage"] = False
                save_settings(self.settings_path, self.settings)
        except OSError as error:
            self._message(f"Could not save the arrangement: {error}")
            return False
        if changed:
            ordered = [entry for entry in self.layout.order if entry in ids]
            self.app_page_index = ordered.index(source_id) // max(1, self.app_page_capacity)
            GLib.idle_add(self._finish_grid_placement, source_id)
        return changed

    def _finish_grid_placement(self, source_id):
        self._populate_apps(self.search.get_text())
        if source_id.startswith("folder:"):
            GLib.idle_add(self._focus_rebuilt_folder_tile, source_id.removeprefix("folder:"))
        else:
            GLib.idle_add(self._focus_rebuilt_app_tile, source_id)
        return GLib.SOURCE_REMOVE

    def _place_on_adjacent_page(self, source_id, direction):
        ids = list(getattr(self, "app_grid_entry_ids", []))
        if source_id not in ids or self.search.get_text().strip():
            return False
        page = self.app_page_index + direction
        if not 0 <= page < getattr(self, "app_page_count", 1):
            return False
        remaining = [entry for entry in ids if entry != source_id]
        if not remaining:
            return False
        slot = page * max(1, self.app_page_capacity)
        target = remaining[min(slot, len(remaining) - 1)]
        return self._place_grid_entry(source_id, target, after=slot >= len(remaining))

    def _drop_on_page_arrow(self, target, source_id, _x, _y, direction):
        self._clear_drop_target(target)
        return self._place_on_adjacent_page(source_id, direction)

    def _add_drag_drop(self, tile, app_id, add_source=True):
        if add_source:
            source = Gtk.DragSource.new()
            source.set_actions(Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
            source.connect("prepare", self._prepare_tile_drag, app_id)
            source.connect("drag-end", lambda *_: setattr(self, "dragged_entry_id", None))
            tile.add_controller(source)

        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("enter", self._app_drop_target_motion)
        target.connect("motion", self._app_drop_target_motion)
        target.connect("leave", self._clear_drop_target)
        target.connect("drop", self._drop_app, app_id)
        tile.add_controller(target)

    def _add_folder_drop_target(self, tile, folder_id, add_source=True):
        if add_source:
            source = Gtk.DragSource.new()
            source.set_actions(Gdk.DragAction.MOVE)
            source.connect("prepare", self._prepare_tile_drag, f"folder:{folder_id}")
            source.connect("drag-end", lambda *_: setattr(self, "dragged_entry_id", None))
            tile.add_controller(source)
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("enter", self._folder_drop_target_enter)
        target.connect("motion", self._folder_drop_target_enter)
        target.connect("leave", self._clear_drop_target)
        target.connect("drop", self._drop_app, f"folder:{folder_id}")
        tile.add_controller(target)

    @staticmethod
    def _clear_drop_target(target, *_args):
        tile = target.get_widget()
        if tile:
            for state in ("group", "add", "before", "after"):
                tile.remove_css_class(f"drop-target-{state}")

    def _app_drop_target_motion(self, target, x, y):
        tile = target.get_widget()
        if not tile:
            return Gdk.DragAction.MOVE
        self._clear_drop_target(target)
        width, height = tile.get_width(), tile.get_height()
        if (not str(getattr(self, "dragged_entry_id", "")).startswith("folder:")
                and width and height and width * 0.30 <= x <= width * 0.70 and height * 0.16 <= y <= height * 0.84):
            state = "group"
        else:
            state = "before" if x < width / 2 else "after"
        tile.add_css_class(f"drop-target-{state}")
        return Gdk.DragAction.MOVE

    def _folder_drop_target_enter(self, target, x=0, y=0):
        if str(getattr(self, "dragged_entry_id", "")).startswith("folder:"):
            return self._app_drop_target_motion(target, x, y)
        tile = target.get_widget()
        if tile:
            self._clear_drop_target(target)
            tile.add_css_class("drop-target-add")
        return Gdk.DragAction.MOVE

    def _drop_app(self, _target, source_id, x, y, target_id):
        self._clear_drop_target(_target)
        if source_id == target_id:
            return False

        entry_ids = [app_identifier(app) for app in self.apps]
        entry_ids.extend(f"folder:{folder_id}" for folder_id in self.layout.folders)
        if source_id not in entry_ids or target_id not in entry_ids:
            return False
        if source_id.startswith("folder:"):
            tile = _target.get_widget()
            width = tile.get_width() if tile else 0
            return self._place_grid_entry(source_id, target_id, after=x >= width / 2)

        if target_id.startswith("folder:"):
            folder_id = target_id.removeprefix("folder:")
            changed = self.layout.add_to_folder(folder_id, source_id)
            if changed:
                GLib.idle_add(self._populate_apps, self.search.get_text())
            return changed

        tile = _target.get_widget()
        width = tile.get_width() if tile else 0
        height = tile.get_height() if tile else 0
        existing_folder = next(
            (folder_id for folder_id, folder in self.layout.folders.items() if source_id in folder["apps"]),
            None,
        )
        is_center_drop = (
            width and height and width * 0.30 <= x <= width * 0.70
            and height * 0.16 <= y <= height * 0.84
        )
        if is_center_drop:
            name = "New group"
            suffix = 2
            while not self.layout.is_folder_name_available(name):
                name = f"New group {suffix}"
                suffix += 1
            folder_id = self.layout.create_folder(name, [source_id, target_id])
            GLib.idle_add(self._populate_apps, self.search.get_text())
            return bool(folder_id)

        installed_ids = [app_identifier(app) for app in self.apps]
        if existing_folder:
            self.layout.remove_from_folder(existing_folder, source_id)
        return self._place_grid_entry(source_id, target_id, after=x >= width / 2)

    def _app_reorder_target(self, app_id, tile, direction):
        if self.search.get_text().strip():
            return None
        flow_child = tile.get_parent()
        grid = flow_child.get_parent() if flow_child else None
        if grid not in (self.favorites_grid, self.app_grid):
            return None
        buttons = self._grid_buttons(grid)
        try:
            index = buttons.index(tile) + direction
        except ValueError:
            return None
        if not 0 <= index < len(buttons):
            return None
        target_tile = buttons[index]
        return next(
            (other_id for other_id, (_app, other_tile) in self.app_tiles_by_id.items()
             if other_id != app_id and other_tile is target_tile),
            None,
        )

    def _move_app_by_visible_position(self, app_id, tile, direction, refresh=True):
        target_id = self._app_reorder_target(app_id, tile, direction)
        if target_id is None:
            return False
        installed_ids = [app_identifier(app) for app in self.apps]
        moved = (
            self.layout.move_before(app_id, target_id, installed_ids)
            if direction < 0 else self.layout.move_after(app_id, target_id, installed_ids)
        )
        if not moved:
            return False
        if refresh:
            self._populate_apps(self.search.get_text())
            GLib.idle_add(self._focus_rebuilt_app_tile, app_id)
        return True

    def _move_folder_by_visible_position(self, folder_id, tile, direction):
        if self.search.get_text().strip():
            return False
        ids = list(getattr(self, "app_grid_entry_ids", []))
        source = f"folder:{folder_id}"
        if source not in ids:
            return False
        index = ids.index(source) + direction
        if not 0 <= index < len(ids):
            return False
        return self._place_grid_entry(source, ids[index], after=direction > 0)

    def _focus_rebuilt_app_tile(self, app_id):
        entry = self.app_tiles_by_id.get(app_id)
        if entry:
            entry[1].grab_focus()
        return GLib.SOURCE_REMOVE

    def _focus_rebuilt_folder_tile(self, folder_id):
        tile = self.folder_tiles_by_id.get(folder_id)
        if tile:
            tile.grab_focus()
        else:
            self.search.grab_focus()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _restore_widget_focus(widget):
        if widget and widget.get_mapped() and widget.get_sensitive():
            widget.grab_focus()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _focus_and_select_entry(entry):
        if entry.get_mapped():
            entry.grab_focus()
            entry.select_region(0, -1)
        return GLib.SOURCE_REMOVE

    def _popover_closed(self, popover, anchor):
        if getattr(self, "active_context_popover", None) is popover:
            self.active_context_popover = None
        popover.unparent()
        if (not getattr(self, "active_context_popover", None)
                and not self.create_group_panel and not self.open_folder_window):
            GLib.idle_add(self._restore_widget_focus, anchor)

    @staticmethod
    def _focus_first_popover_action(popover):
        def first_action(widget):
            if isinstance(widget, Gtk.Button):
                return widget if widget.get_sensitive() else None
            if isinstance(widget, Gtk.ScrolledWindow):
                return first_action(widget.get_child())
            child = widget.get_first_child() if widget else None
            while child:
                action = first_action(child)
                if action:
                    return action
                child = child.get_next_sibling()
            return None

        action = first_action(popover.get_child())
        if action:
            action.grab_focus()
        return GLib.SOURCE_REMOVE

    def _set_scrollable_context_menu(self, popover, actions):
        popover.add_css_class("leaf-context-popover")
        popover.set_has_arrow(False)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        window_height = self.window.get_height() if self.window else 0
        scroll.set_max_content_height(max(180, min(480, window_height - 80 if window_height else 420)))
        scroll.set_child(actions)
        popover.set_child(scroll)
        self.active_context_popover = popover

    def _show_app_menu(self, _gesture, _presses, x, y, app, tile):
        popover = Gtk.Popover()
        popover.set_parent(tile)
        anchor_rect = Gdk.Rectangle()
        anchor_rect.x, anchor_rect.y = int(x), int(y)
        anchor_rect.width = anchor_rect.height = 1
        popover.set_pointing_to(anchor_rect)
        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        actions.add_css_class("group-context-menu")
        actions.set_margin_top(6)
        actions.set_margin_bottom(6)
        actions.set_margin_start(6)
        actions.set_margin_end(6)
        self._set_scrollable_context_menu(popover, actions)
        self._append_app_context_header(actions, app)
        actions.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        for title, icon_name, action, destructive in self._app_context_entries(app, tile):
            button = self._context_menu_action(title, icon_name, destructive)
            button.connect("clicked", self._run_app_action, action, popover)
            actions.append(button)
        popover.connect("closed", self._popover_closed, tile)
        popover.popup()
        GLib.idle_add(self._focus_first_popover_action, popover)

    def _app_context_entries(self, app, tile=None):
        entries = [
            (("New window" if self._windows_for_app(app) else "Open app"), "window-new-symbolic", lambda: self._launch_app(app), False),
            (("Unpin from dock" if self.layout.is_favorite(app) else "Pin to dock"), "starred-symbolic", lambda: self.layout.toggle_favorite(app), False),
            ("Create group…", "folder-new-symbolic", lambda: self._create_folder_dialog(app), False),
        ]
        current_folder = self.layout.folder_for(app)
        if current_folder:
            entries.append(("Remove from group", "list-remove-symbolic", lambda: self.layout.remove_from_folder(current_folder, app_identifier(app)), True))
        other_groups = any(folder_id != current_folder for folder_id in self.layout.folders)
        if other_groups:
            entries.append((
                "Move to group…", "folder-move-symbolic",
                lambda: self._show_group_picker(app, tile), False,
            ))
        return entries

    @staticmethod
    def _append_app_context_header(container, app):
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.add_css_class("context-menu-header")
        icon = Gtk.Image.new_from_gicon(app.get_icon()) if app.get_icon() else Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
        icon.set_pixel_size(28)
        header.append(icon)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name = Gtk.Label(label=app.get_display_name(), xalign=0)
        name.add_css_class("context-menu-title")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(28)
        labels.append(name)
        header.append(labels)
        container.append(header)

    def _group_window_layout(self, app_count):
        geometry = self._window_monitor_geometry()
        screen_width = self.window.get_width() or (geometry.width if geometry else 1120)
        screen_height = self.window.get_height() or (geometry.height if geometry else 760)
        max_width = max(320, screen_width - 96)
        max_height = max(320, screen_height - 96)
        tile_width = 126 if self.settings["show_app_names"] else 110
        tile_height = 108 if self.settings["show_app_names"] else 78
        gap = 12
        panel_padding = 36
        header_space = 112
        max_columns = max(1, (max_width - panel_padding + gap) // (tile_width + gap))
        max_rows = max(1, (max_height - header_space + gap) // (tile_height + gap))
        columns = min(max_columns, max(4, math.ceil(max(app_count, 1) / max_rows)))
        rows = math.ceil(app_count / columns)
        width = max(620, columns * tile_width + (columns - 1) * gap + panel_padding)
        height = max(420, rows * tile_height + max(0, rows - 1) * gap + header_space)
        return columns, min(width, max_width), min(height, max_height)

    def _open_folder(self, _button, folder_id, tile):
        folder = self.layout.folders.get(folder_id)
        if not folder:
            return
        if self.create_group_panel:
            self._close_group_create(restore_focus=False)
        if self.open_folder_window and self.open_folder_id == folder_id:
            self.group_dialog_layer.set_visible(True)
            return self.open_folder_window
        if self.open_folder_window:
            self._close_folder_window()

        by_id = {app_identifier(app): app for app in self.apps}
        members = [by_id[app_id] for app_id in folder["apps"] if app_id in by_id]
        columns, panel_width, panel_height = self._group_window_layout(len(members))
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        panel.add_css_class("group-dialog-panel")
        panel.set_halign(Gtk.Align.CENTER)
        panel.set_valign(Gtk.Align.CENTER)
        panel.set_size_request(panel_width, panel_height)
        header = Gtk.CenterBox()
        title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        heading = Gtk.Label(label=folder["name"])
        heading.add_css_class("group-dialog-title")
        title.append(heading)
        count = len(members)
        subtitle = Gtk.Label(label=f"{count} app{'s' if count != 1 else ''}")
        subtitle.add_css_class("muted")
        title.append(subtitle)
        entry = Gtk.Entry(text=folder["name"], placeholder_text="Group name")
        entry.set_max_length(60)
        entry.set_width_chars(22)
        self._set_accessible_info(entry, "Group name", "Type a name, then press Enter to save or Escape to cancel")
        hint = Gtk.Label(xalign=0.5)
        hint.add_css_class("muted")
        edit_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        edit_view.append(entry)
        edit_view.append(hint)
        title_stack = Gtk.Stack()
        title_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        title_stack.set_transition_duration(120)
        title_stack.add_named(title, "label")
        title_stack.add_named(edit_view, "entry")
        header.set_center_widget(title_stack)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        rename = Gtk.ToggleButton()
        rename.set_has_frame(False)
        rename.add_css_class("group-icon-action")
        rename.set_child(Gtk.Image.new_from_icon_name("document-edit-symbolic"))
        rename.set_tooltip_text("Rename group")
        self._set_accessible_info(rename, "Rename group")
        close = Gtk.Button()
        close.set_has_frame(False)
        close.set_child(Gtk.Image.new_from_icon_name("window-close-symbolic"))
        close.add_css_class("group-icon-action")
        close.set_tooltip_text("Close group")
        self._set_accessible_info(close, "Close group")
        close.connect("clicked", self._close_folder_window)
        actions.append(rename)
        actions.append(close)
        header.set_end_widget(actions)
        rename_state = {"cancel": False, "editing": False}

        def rename_toggled(button):
            if button.get_active():
                rename_state["cancel"] = False
                if not rename_state["editing"]:
                    entry.set_text(self.layout.folders.get(folder_id, {}).get("name", heading.get_text()))
                    entry.remove_css_class("error")
                    hint.set_text("")
                rename_state["editing"] = True
                button.set_child(Gtk.Image.new_from_icon_name("emblem-ok-symbolic"))
                button.set_tooltip_text("Save group name")
                self._set_accessible_info(button, "Save group name")
                title_stack.set_visible_child_name("entry")
                GLib.idle_add(self._focus_and_select_entry, entry)
            elif rename_state["cancel"]:
                rename_state["cancel"] = False
                rename_state["editing"] = False
                title_stack.set_visible_child_name("label")
                button.set_child(Gtk.Image.new_from_icon_name("document-edit-symbolic"))
                button.set_tooltip_text("Rename group")
                self._set_accessible_info(button, "Rename group")
            else:
                new_name = entry.get_text().strip()
                if not new_name:
                    entry.add_css_class("error")
                    hint.set_text("Enter a group name")
                    button.set_active(True)
                    entry.grab_focus()
                    return
                if not self._save_group_name(folder_id, new_name, tile, heading):
                    entry.add_css_class("error")
                    hint.set_text("That group name is already in use")
                    button.set_active(True)
                    entry.grab_focus()
                    return
                rename_state["editing"] = False
                title_stack.set_visible_child_name("label")
                button.set_child(Gtk.Image.new_from_icon_name("document-edit-symbolic"))
                button.set_tooltip_text("Rename group")
                self._set_accessible_info(button, "Rename group")

        rename.connect("toggled", rename_toggled)
        entry.connect("activate", lambda *_args: rename.set_active(False))
        entry_keys = Gtk.EventControllerKey.new()
        def cancel_on_escape(_controller, keyval, _keycode, _state):
            if keyval != Gdk.KEY_Escape:
                return False
            rename_state["cancel"] = True
            entry.set_text(heading.get_text())
            rename.set_active(False)
            return True

        entry_keys.connect("key-pressed", cancel_on_escape)
        entry.add_controller(entry_keys)
        self.group_rename_action = lambda: rename.set_active(True)
        panel.append(header)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        grid.set_halign(Gtk.Align.CENTER)
        grid.set_valign(Gtk.Align.CENTER)
        scroller.set_child(grid)
        panel.append(scroller)
        for index, app in enumerate(members):
            app_id = app_identifier(app)
            launch = Gtk.Button()
            launch.set_has_frame(False)
            launch.add_css_class("app-tile")
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            icon = Gtk.Image.new_from_gicon(app.get_icon()) if app.get_icon() else Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
            icon.set_pixel_size(min(self._app_icon_pixels(), 48))
            content.append(icon)
            label = Gtk.Label(label=app.get_display_name(), wrap=True, max_width_chars=14, justify=Gtk.Justification.CENTER)
            label.set_lines(2)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.add_css_class("app-name")
            label.set_visible(self.settings["show_app_names"])
            content.append(label)
            running_mark = Gtk.Box()
            running_mark.add_css_class("running-indicator")
            running_mark.set_halign(Gtk.Align.CENTER)
            running_mark.set_visible(bool(self._windows_for_app(app)))
            content.append(running_mark)
            self.running_marks_by_id[app_id] = running_mark
            launch.set_child(content)
            launch.set_tooltip_text(f"{app.get_display_name()}\nClick to launch · Shift+F10 for actions")
            self._set_accessible_info(
                launch,
                app.get_display_name(),
                "Launch this application. Press Shift+F10 or the Menu key for group actions.",
            )
            self.folder_member_tiles_by_id[app_id] = (app, folder_id, launch)
            launch.connect("clicked", lambda _button, item=app: (self._close_folder_window(), self._activate_app(item)))
            self._add_drag_drop(launch, app_id)
            context = Gtk.GestureClick.new()
            context.set_button(3)
            context.connect("pressed", self._show_folder_app_menu, app, folder_id, launch)
            launch.add_controller(context)
            grid.attach(launch, index % columns, index // columns, 1, 1)
        self.open_folder_window = panel
        self.open_folder_id = folder_id
        self.group_dialog_layer.set_center_widget(panel)
        self.group_dialog_backdrop.set_visible(True)
        self.group_dialog_layer.set_visible(True)
        self.group_dialog_layer.set_accessible_role(Gtk.AccessibleRole.DIALOG)
        self._set_accessible_info(self.group_dialog_layer, folder["name"], f"Application group with {count} apps")
        first_app = grid.get_first_child()
        if first_app:
            GLib.idle_add(self._restore_widget_focus, first_app)
        return panel

    def _close_folder_window(self, *_args):
        folder_id = self.open_folder_id
        tile = self.folder_tiles_by_id.get(folder_id) if folder_id else None
        if self.group_dialog_backdrop:
            self.group_dialog_backdrop.set_visible(False)
        if self.group_dialog_layer:
            self.group_dialog_layer.set_visible(False)
            self.group_dialog_layer.set_center_widget(None)
        self.open_folder_window = None
        self.open_folder_id = None
        self.group_rename_action = None
        self.folder_member_tiles_by_id = {
            app_id: item for app_id, item in self.folder_member_tiles_by_id.items()
            if not folder_id or item[1] != folder_id
        }
        if tile:
            GLib.idle_add(self._restore_widget_focus, tile)
        return True

    def _close_group_overlay(self, *_args):
        if self.command_panel:
            return self._close_command_panel()
        if self.create_group_panel:
            return self._close_group_create()
        return self._close_folder_window()

    def _close_group_create(self, *_args, restore_focus=True):
        if not self.create_group_panel:
            return False
        source_id = self.create_group_source_id
        self.group_dialog_backdrop.set_visible(False)
        self.group_dialog_layer.set_visible(False)
        self.group_dialog_layer.set_center_widget(None)
        self.create_group_panel = None
        self.create_group_source_id = None
        if restore_focus:
            GLib.idle_add(self._focus_rebuilt_app_tile, source_id)
        return True

    def _open_group_rename(self, folder_id, tile):
        panel = self._open_folder(None, folder_id, tile)
        if panel:
            GLib.idle_add(self._activate_group_rename)
        return panel

    def _activate_group_rename(self):
        if self.group_rename_action:
            self.group_rename_action()
        return GLib.SOURCE_REMOVE

    def _remove_app_from_folder(self, folder_id, app_id):
        changed = self.layout.remove_from_folder(folder_id, app_id)
        if changed:
            self._populate_apps(self.search.get_text())
            if self.open_folder_id == folder_id and self.open_folder_window:
                self._close_folder_window()
        return changed

    def _move_app_to_folder(self, folder_id, app_id):
        changed = self.layout.add_to_folder(folder_id, app_id)
        if changed:
            self._populate_apps(self.search.get_text())
            if self.open_folder_window:
                self._close_folder_window()
        return changed

    def _show_group_picker(self, app, anchor):
        current_folder = self.layout.folder_for(app)
        groups = [
            (folder_id, folder)
            for folder_id, folder in sorted(
                self.layout.folders.items(), key=lambda item: item[1]["name"].casefold()
            )
            if folder_id != current_folder
        ]
        if not groups or not anchor:
            return None
        popover = Gtk.Popover()
        popover.add_css_class("leaf-context-popover")
        popover.set_has_arrow(False)
        popover.set_parent(anchor)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_margin_top(8)
        content.set_margin_bottom(8)
        content.set_margin_start(8)
        content.set_margin_end(8)
        title = Gtk.Label(label="Move to group", xalign=0)
        title.add_css_class("context-menu-title")
        content.append(title)
        search = Gtk.SearchEntry(placeholder_text="Find a group")
        self._set_accessible_info(search, "Find a group", "Filter available destination groups by name")
        content.append(search)
        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        window_height = self.window.get_height() if self.window else 0
        scroll.set_max_content_height(max(180, min(360, window_height - 100 if window_height else 300)))
        scroll.set_child(list_box)
        content.append(scroll)
        rows = []
        app_id = app_identifier(app)
        for folder_id, folder in groups:
            row = self._context_menu_action(
                f"{folder['name']} · {len(folder['apps'])} apps", "folder-symbolic"
            )
            row.set_tooltip_text(folder["name"])
            self._set_accessible_info(row, f"Move to {folder['name']}", f"Add this app to the {folder['name']} group")
            row.connect("clicked", self._choose_app_group, folder_id, app_id, popover, anchor)
            list_box.append(row)
            rows.append((row, folder))

        def filter_groups(*_args):
            query = search.get_text().casefold().strip()
            for row, folder in rows:
                row.set_visible(not query or query in folder["name"].casefold())

        search.connect("search-changed", filter_groups)
        popover.set_child(content)
        self.active_context_popover = popover
        popover.connect("closed", self._popover_closed, anchor)
        popover.popup()
        GLib.idle_add(self._focus_first_popover_action, popover)
        return popover

    def _choose_app_group(self, _button, folder_id, app_id, popover, anchor):
        moved = self._move_app_to_folder(folder_id, app_id)
        popover.popdown()
        if moved:
            GLib.timeout_add(50, self._focus_rebuilt_folder_tile, folder_id)

    def _show_folder_app_menu(self, _gesture, _presses, x, y, app, folder_id, tile):
        popover = Gtk.Popover()
        popover.set_parent(tile)
        anchor_rect = Gdk.Rectangle()
        anchor_rect.x, anchor_rect.y = int(x), int(y)
        anchor_rect.width = anchor_rect.height = 1
        popover.set_pointing_to(anchor_rect)
        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        actions.add_css_class("group-context-menu")
        actions.set_margin_top(6)
        actions.set_margin_bottom(6)
        actions.set_margin_start(6)
        actions.set_margin_end(6)
        self._set_scrollable_context_menu(popover, actions)
        for title, icon_name, action, destructive in self._folder_app_context_entries(app, folder_id, tile):
            button = self._context_menu_action(title, icon_name, destructive)
            button.connect("clicked", self._run_app_action, action, popover)
            actions.append(button)
        popover.connect("closed", self._popover_closed, tile)
        popover.popup()
        GLib.idle_add(self._focus_first_popover_action, popover)

    def _folder_app_context_entries(self, app, folder_id, tile):
        app_id = app_identifier(app)
        entries = [
            ("Open app", "system-run-symbolic", lambda: self._activate_app(app), False),
            (("Unpin from dock" if self.layout.is_favorite(app) else "Pin to dock"), "starred-symbolic", lambda: self.layout.toggle_favorite(app), False),
            ("Remove from group", "list-remove-symbolic", lambda: self._remove_app_from_folder(folder_id, app_id), True),
        ]
        if any(target_id != folder_id for target_id in self.layout.folders):
            entries.append((
                "Move to group…", "folder-move-symbolic",
                lambda: self._show_group_picker(app, tile), False,
            ))
        return entries

    def _show_folder_menu(self, _gesture, _presses, x, y, folder_id, tile):
        popover = Gtk.Popover()
        popover.set_parent(tile)
        anchor_rect = Gdk.Rectangle()
        anchor_rect.x, anchor_rect.y = int(x), int(y)
        anchor_rect.width = anchor_rect.height = 1
        popover.set_pointing_to(anchor_rect)
        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        actions.add_css_class("group-context-menu")
        actions.set_margin_top(6)
        actions.set_margin_bottom(6)
        actions.set_margin_start(6)
        actions.set_margin_end(6)
        self._set_scrollable_context_menu(popover, actions)
        folder = self.layout.folders.get(folder_id)
        if folder:
            header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            header.set_margin_start(8)
            header.set_margin_end(8)
            name = Gtk.Label(label=folder["name"], xalign=0, ellipsize=Pango.EllipsizeMode.END)
            name.set_max_width_chars(28)
            name.add_css_class("context-menu-title")
            header.append(name)
            count = len(folder["apps"])
            detail = Gtk.Label(label=f"{count} apps", xalign=0)
            detail.add_css_class("muted")
            header.append(detail)
            actions.append(header)
            actions.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        entries = [
            ("Open group", "view-grid-symbolic", lambda: self._open_folder(None, folder_id, tile), False),
            ("Move earlier", "go-up-symbolic", lambda: self._move_folder_by_visible_position(folder_id, tile, -1), False),
            ("Move later", "go-down-symbolic", lambda: self._move_folder_by_visible_position(folder_id, tile, 1), False),
            ("Move to previous page", "go-previous-symbolic", lambda: self._place_on_adjacent_page(f"folder:{folder_id}", -1), False),
            ("Move to next page", "go-next-symbolic", lambda: self._place_on_adjacent_page(f"folder:{folder_id}", 1), False),
            ("Rename group…", "document-edit-symbolic", lambda: self._open_group_rename(folder_id, tile), False),
            ("Ungroup apps", "folder-open-symbolic", lambda: self.layout.delete_folder(folder_id), True),
        ]
        for title, icon_name, action, destructive in entries:
            button = self._context_menu_action(title, icon_name, destructive)
            button.connect("clicked", self._run_app_action, action, popover)
            actions.append(button)
        popover.connect("closed", self._popover_closed, tile)
        popover.popup()
        GLib.idle_add(self._focus_first_popover_action, popover)

    @staticmethod
    def _context_menu_action(label, icon_name, destructive=False):
        button = Gtk.Button()
        button.set_has_frame(False)
        button.add_css_class("context-menu-action")
        if destructive:
            button.add_css_class("destructive-action")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_margin_start(6)
        row.set_margin_end(8)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(16)
        row.append(icon)
        text = Gtk.Label(label=label, xalign=0)
        text.set_hexpand(True)
        text.set_max_width_chars(28)
        text.set_ellipsize(Pango.EllipsizeMode.END)
        row.append(text)
        button.set_child(row)
        button.set_halign(Gtk.Align.FILL)
        button.set_hexpand(True)
        return button

    def _create_folder_dialog(self, app, initial_apps=None):
        if self.open_folder_window:
            self._close_folder_window()
        if self.create_group_panel:
            self._close_group_create(restore_focus=False)
        candidates = self.layout.ordered(self.apps)
        if len(candidates) < 2:
            self._message("Add another application before creating a group.")
            return None

        selected_ids = {app_identifier(item) for item in (initial_apps or [app])}
        source_id = app_identifier(app)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        panel.add_css_class("group-dialog-panel")
        monitor = self._window_monitor_geometry()
        available_width = self.window.get_width() or 720
        available_height = self.window.get_height() or 520
        panel.set_size_request(
            max(320, min(680, available_width - 64)),
            max(300, min(540, available_height - 80)),
        )
        header = Gtk.CenterBox()
        heading = Gtk.Label(label="Create group")
        heading.add_css_class("group-dialog-title")
        header.set_center_widget(heading)
        close = Gtk.Button()
        close.set_has_frame(False)
        close.set_child(Gtk.Image.new_from_icon_name("window-close-symbolic"))
        close.add_css_class("group-icon-action")
        close.set_tooltip_text("Cancel group creation")
        self._set_accessible_info(close, "Cancel group creation")
        close.connect("clicked", self._close_group_create)
        header.set_end_widget(close)
        panel.append(header)

        name = Gtk.Entry(placeholder_text="Group name")
        name.set_max_length(60)
        default_name = "New group"
        suffix = 2
        while not self.layout.is_folder_name_available(default_name):
            default_name = f"New group {suffix}"
            suffix += 1
        name.set_text(default_name)
        self._set_accessible_info(name, "Group name", "Name the new application group")
        panel.append(name)

        search = Gtk.SearchEntry(placeholder_text="Find apps to add")
        search.add_css_class("group-create-search")
        self._set_accessible_info(search, "Find apps to add", "Filter the applications available for this group")
        panel.append(search)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        choices = Gtk.FlowBox()
        choices.set_selection_mode(Gtk.SelectionMode.NONE)
        choices.set_min_children_per_line(2)
        choices.set_max_children_per_line(5)
        choices.set_column_spacing(12)
        choices.set_row_spacing(12)
        choices.set_halign(Gtk.Align.CENTER)
        choices.set_valign(Gtk.Align.START)
        scroller.set_child(choices)
        panel.append(scroller)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        footer.add_css_class("group-create-footer")
        status = Gtk.Label(xalign=0)
        status.add_css_class("muted")
        status.set_hexpand(True)
        footer.append(status)
        cancel = Gtk.Button(label="Cancel")
        cancel.set_has_frame(False)
        cancel.add_css_class("group-create-cancel")
        cancel.connect("clicked", self._close_group_create)
        footer.append(cancel)
        create = Gtk.Button(label="Create group")
        create.add_css_class("group-create-primary")
        footer.append(create)
        panel.append(footer)

        buttons = []
        for item in candidates:
            app_id = app_identifier(item)
            button = Gtk.ToggleButton()
            button.set_has_frame(False)
            button.add_css_class("group-create-choice")
            button.set_active(app_id in selected_ids)
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            icon = Gtk.Image.new_from_gicon(item.get_icon()) if item.get_icon() else Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
            icon.set_pixel_size(min(self._app_icon_pixels(), 48))
            icon_preview = Gtk.Overlay()
            icon_preview.set_child(icon)
            selected_marker = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            selected_marker.set_pixel_size(18)
            selected_marker.set_halign(Gtk.Align.END)
            selected_marker.set_valign(Gtk.Align.START)
            selected_marker.set_visible(button.get_active())
            selected_marker.add_css_class("group-create-check")
            icon_preview.add_overlay(selected_marker)
            content.append(icon_preview)
            label = Gtk.Label(label=item.get_display_name(), wrap=True, max_width_chars=14, justify=Gtk.Justification.CENTER)
            label.add_css_class("app-name")
            content.append(label)
            button.set_child(content)
            existing_group_id = self.layout.folder_for(item)
            existing_group = self.layout.folders.get(existing_group_id) if existing_group_id else None
            tooltip = item.get_display_name()
            if existing_group:
                tooltip += f"\nCurrently in {existing_group['name']} · selecting it will move it here"
            button.set_tooltip_text(tooltip)
            self._set_accessible_info(button, item.get_display_name(), "Select this application for the new group")
            button.connect("toggled", lambda control, marker=selected_marker: marker.set_visible(control.get_active()))
            choices.append(button)
            buttons.append((button, item))

        def update_filter(*_args):
            query = search.get_text().casefold().strip()
            for button, item in buttons:
                text = f"{item.get_display_name()} {item.get_description() or ''}".casefold()
                button.get_parent().set_visible(not query or query in text)

        def update_validation(*_args):
            count = sum(button.get_active() for button, _item in buttons)
            group_name = name.get_text().strip()
            available = self.layout.is_folder_name_available(group_name)
            create.set_sensitive(count >= 2 and available)
            name.remove_css_class("error")
            if not group_name:
                status.set_text("Enter a group name")
                name.add_css_class("error")
            elif not available:
                status.set_text("That group name is already in use")
                name.add_css_class("error")
            elif count < 2:
                status.set_text(f"{count} selected · Choose at least 2 apps")
            else:
                status.set_text(f"{count} apps selected")

        def create_group(*_args):
            app_ids = [app_identifier(item) for button, item in buttons if button.get_active()]
            try:
                folder_id = self.layout.create_folder(name.get_text(), app_ids)
            except ValueError as exc:
                status.set_text(str(exc))
                return
            self._close_group_create(restore_focus=False)
            if self.search.get_text():
                self.search.set_text("")
            else:
                self._populate_apps()
            GLib.idle_add(self._focus_rebuilt_folder_tile, folder_id)

        search.connect("search-changed", update_filter)
        name.connect("changed", update_validation)
        name.connect("activate", lambda *_args: create_group() if create.get_sensitive() else None)
        for button, _item in buttons:
            button.connect("toggled", update_validation)
        create.connect("clicked", create_group)
        update_filter()
        update_validation()

        self.create_group_panel = panel
        self.create_group_source_id = source_id
        self.group_dialog_layer.set_center_widget(panel)
        self.group_dialog_backdrop.set_visible(True)
        self.group_dialog_layer.set_visible(True)
        self.group_dialog_layer.set_accessible_role(Gtk.AccessibleRole.DIALOG)
        self._set_accessible_info(self.group_dialog_layer, "Create group", "Name a group and choose two or more applications")
        GLib.idle_add(self._focus_and_select_entry, name)
        return panel

    def _save_group_name(self, folder_id, new_name, tile, heading):
        if not self.layout.rename_folder(folder_id, new_name):
            return False
        heading.set_text(new_name)
        tile_box = tile.get_child() if tile else None
        preview = tile_box.get_first_child() if tile_box else None
        tile_name = preview.get_next_sibling() if preview else None
        if tile_name:
            tile_name.set_text(new_name)
        folder = self.layout.folders.get(folder_id)
        if folder:
            count = len(folder["apps"])
            if tile:
                tile.set_tooltip_text(f"{new_name} · {count} apps\nDrop an app here to add it")
                self._set_accessible_info(
                    tile, new_name,
                    f"Group with {count} apps. Activate to open the centered app grid. Drop an app here to add it; Shift+F10 for actions.",
                )
            self._set_accessible_info(self.group_dialog_layer, new_name, f"Application group with {count} apps")
        return True

    def _run_app_action(self, _button, action, popover):
        anchor = popover.get_parent()
        app_id = next(
            (ident for ident, (_app, tile) in self.app_tiles_by_id.items() if tile is anchor),
            None,
        )
        dock_key = next(
            (button.dock_key for button in self.running_dock_buttons if button is anchor),
            None,
        )
        folder_id = next(
            (ident for ident, tile in self.folder_tiles_by_id.items() if tile is anchor),
            None,
        )
        try:
            result = action()
        except (OSError, ValueError) as exc:
            popover.popdown()
            self._message(f"Could not save the change: {exc}")
            return
        popover.popdown()
        if isinstance(result, (Gtk.Window, Gtk.Popover)) or self.open_folder_window or self.create_group_panel:
            return
        if self.window.get_visible():
            GLib.idle_add(self._refresh_apps_and_restore_focus, app_id, folder_id, dock_key)

    def _refresh_apps_and_restore_focus(self, app_id=None, folder_id=None, dock_key=None):
        self._populate_apps(self.search.get_text())
        self._update_running_dock()
        if app_id in self.app_tiles_by_id:
            self.app_tiles_by_id[app_id][1].grab_focus()
        elif folder_id in self.folder_tiles_by_id:
            self.folder_tiles_by_id[folder_id].grab_focus()
        elif dock_key:
            next((button for button in self.running_dock_buttons if button.dock_key == dock_key), self.search).grab_focus()
        else:
            self.search.grab_focus()
        return GLib.SOURCE_REMOVE

    def _launch_app(self, app):
        app_id = app_identifier(app)
        if app_id in self._launching_app_ids:
            return True
        self._launching_app_ids.add(app_id)

        def launch():
            try:
                started = app.launch([], None)
                error = None if started else "the desktop entry did not start the application"
            except Exception as exc:
                started = False
                error = getattr(exc, "message", None) or str(exc)
            GLib.idle_add(self._finish_app_launch, app, app_id, started, error)

        threading.Thread(target=launch, name="leaf-app-launch", daemon=True).start()
        # Start the close animation immediately; desktop launch services can
        # take a moment to answer, and that wait should not hold the drawer open.
        self._dismiss_drawer()
        return True

    def _finish_app_launch(self, app, app_id, started, error):
        self._launching_app_ids.discard(app_id)
        if not started:
            self._place_drawer_on_focused_monitor()
            self.window.present()
            self._show_drawer()
            self._message(f"Could not launch {app.get_display_name()}: {error or 'unknown launch error'}.")
            return GLib.SOURCE_REMOVE
        try:
            self.layout.record_use(app)
        except OSError as exc:
            self._message(f"Application opened, but Leaf could not save its launch count: {exc}")
        return GLib.SOURCE_REMOVE

    def _activate_app(self, app):
        windows = self._windows_for_app(app)
        if windows:
            self.layout.record_use(app)
            monitor_id = next((item.get("id") for item in self._hypr_json("monitors") if item.get("focused")), None)
            local_window = next((window for window in windows if window.get("monitor") == monitor_id), None)
            self._focus_window((local_window or windows[0]).get("address"))
        else:
            self._launch_app(app)

    def _open_command_panel(self, *_args):
        if self.open_folder_window:
            self._close_folder_window()
        if self.create_group_panel:
            self._close_group_create(restore_focus=False)
        if self.command_panel:
            return
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        panel.add_css_class("group-dialog-panel")
        panel.add_css_class("command-panel")
        heading = Gtk.Label(label="Run a command", xalign=0)
        heading.add_css_class("group-dialog-title")
        panel.append(heading)
        entry = Gtk.Entry(placeholder_text="Command on this computer")
        entry.connect("activate", self._run_command_entry)
        panel.append(entry)
        hint = Gtk.Label(label="Enter to run · Esc to close", xalign=0)
        hint.add_css_class("muted")
        panel.append(hint)
        self.command_panel = panel
        self.group_dialog_layer.set_center_widget(panel)
        self.group_dialog_backdrop.set_visible(True)
        self.group_dialog_layer.set_visible(True)
        GLib.idle_add(self._restore_widget_focus, entry)

    def _close_command_panel(self):
        if not self.command_panel:
            return False
        self.group_dialog_backdrop.set_visible(False)
        self.group_dialog_layer.set_visible(False)
        self.group_dialog_layer.set_center_widget(None)
        self.command_panel = None
        return True

    def _run_command_entry(self, entry):
        try:
            arguments = shlex.split(entry.get_text().strip())
            if not arguments:
                return
            subprocess.Popen(arguments, start_new_session=True)
        except (OSError, ValueError) as exc:
            self._message(f"Could not run command: {exc}")
            return
        self._close_command_panel()
        self._dismiss_drawer()

    def _open_expose(self, *_args):
        """Open the optional Exposé overlay without blocking GTK."""
        if not self.settings["show_expose_button"]:
            return

        def launch():
            GLib.idle_add(self._finish_expose_open, request_expose_open())

        threading.Thread(target=launch, name="leaf-expose-launch", daemon=True).start()

    def _finish_expose_open(self, error):
        if error:
            self._message(error)
        else:
            self._dismiss_drawer()
        return GLib.SOURCE_REMOVE

    def _build_system_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content_width = -1
        self.system_content = page
        self.metric_labels = {}
        self.metric_bars = {}
        self.chart_captions = {}
        self.chart_areas = {}
        self.gauge_areas = {}
        self.storage_row_widgets = []
        self.health_stat_labels = {}

        self.metrics_grid = Gtk.FlowBox(column_spacing=16, row_spacing=12, homogeneous=True,
                                        selection_mode=Gtk.SelectionMode.NONE)
        self.metrics_grid.set_min_children_per_line(1)
        self.metrics_grid.set_max_children_per_line(3)
        self.metrics_grid.set_halign(Gtk.Align.FILL)
        page.append(self.metrics_grid)

        gauge_specs = (("cpu", "CPU", "cpu"), ("memory", "Memory", "ram"), ("gpu", "GPU", "gpu"))
        for column, (key, name, icon_kind) in enumerate(gauge_specs):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            card.add_css_class("system-gauge")
            card.set_hexpand(True)
            card.set_size_request(240, -1)
            header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            header.set_halign(Gtk.Align.CENTER)
            icon = Gtk.DrawingArea()
            icon.set_content_width(58)
            icon.set_content_height(38)
            icon.set_draw_func(self._draw_hardware_icon, icon_kind)
            icon.set_accessible_role(Gtk.AccessibleRole.IMG)
            self._set_accessible_info(icon, f"{name} hardware icon")
            header.append(icon)
            heading = Gtk.Label(label=name, xalign=0.5)
            heading.add_css_class("metric-heading")
            header.append(heading)
            card.append(header)

            value = Gtk.Label(label="—", xalign=0.5)
            value.add_css_class("gauge-value")
            card.append(value)
            gauge = Gtk.DrawingArea()
            gauge.set_content_height(148)
            gauge.set_hexpand(True)
            gauge.set_accessible_role(Gtk.AccessibleRole.IMG)
            self._set_accessible_info(gauge, f"{name} resource gauge", f"Current {name.lower()} usage as a percentage")
            gauge.set_draw_func(self._draw_system_gauge, key)
            card.append(gauge)
            detail = Gtk.Label(xalign=0.5, wrap=True)
            detail.set_max_width_chars(38)
            detail.add_css_class("system-detail")
            if key == "cpu":
                detail.set_visible(False)
            card.append(detail)
            self.metric_labels[key] = (value, detail, card)
            self.gauge_areas[key] = gauge

            bar = Gtk.ProgressBar()
            bar.set_visible(False)
            self._set_accessible_info(bar, f"{name} usage", f"Current {name.lower()} usage")
            self.metric_bars[key] = bar

            self.metrics_grid.append(card)

        storage_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        storage_section.set_halign(Gtk.Align.FILL)
        storage_section.set_size_request(content_width, -1)
        storage_value = Gtk.Label(label="Checking storage…", xalign=0)
        storage_value.add_css_class("system-detail")
        storage_value.set_visible(True)
        storage_section.append(storage_value)
        self.storage_rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        storage_section.append(self.storage_rows_box)
        storage_detail = Gtk.Label(xalign=0)
        storage_detail.set_visible(False)
        storage_section.append(storage_detail)
        self.metric_labels["storage"] = (storage_value, storage_detail, storage_section)
        page.append(storage_section)
        self.network_status = self._muted_label("Network · waiting for a sample…")
        self.disk_status = self._muted_label("Disk activity · waiting for a sample…")
        page.append(self.network_status)
        page.append(self.disk_status)

        health_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        health_section.set_halign(Gtk.Align.FILL)
        health_section.set_size_request(content_width, -1)
        health_title_row = Gtk.Box(spacing=8)
        health_title_row.set_halign(Gtk.Align.CENTER)
        health_icon = Gtk.DrawingArea()
        health_icon.set_content_width(34)
        health_icon.set_content_height(28)
        health_icon.set_draw_func(self._draw_hardware_icon, "health")
        health_icon.set_accessible_role(Gtk.AccessibleRole.IMG)
        self._set_accessible_info(health_icon, "PC health icon")
        health_title_row.append(health_icon)
        health_title = Gtk.Label(label="PC health", xalign=0)
        health_title.add_css_class("health-heading")
        health_title_row.append(health_title)
        health_section.append(health_title_row)
        self.health_meta_label = Gtk.Label(xalign=0.5)
        self.health_meta_label.add_css_class("health-meta")
        health_section.append(self.health_meta_label)

        health_rows = Gtk.Grid(column_spacing=18, row_spacing=4)
        health_rows.set_hexpand(True)
        health_rows.set_column_homogeneous(False)
        row_heading = Gtk.Label(label="TODAY AVG", xalign=0.5)
        row_heading.add_css_class("health-column-label")
        trend_heading = Gtk.Label(label="7-DAY TREND", xalign=0)
        trend_heading.add_css_class("health-column-label")
        health_rows.attach(row_heading, 1, 0, 1, 1)
        health_rows.attach(trend_heading, 2, 0, 1, 1)
        self.health_stat_labels = {}
        self.health_sparkline_areas = {}
        for row, (key, name, icon_kind) in enumerate((
            ("cpu", "CPU", "cpu"), ("memory", "RAM", "ram"), ("gpu", "GPU", "gpu"),
        ), start=1):
            identity = Gtk.Box(spacing=7)
            identity.set_size_request(110, -1)
            identity.set_halign(Gtk.Align.START)
            icon = Gtk.DrawingArea()
            icon.set_content_width(30)
            icon.set_content_height(24)
            icon.set_draw_func(self._draw_hardware_icon, icon_kind)
            icon.set_accessible_role(Gtk.AccessibleRole.IMG)
            self._set_accessible_info(icon, f"{name} hardware icon")
            identity.append(icon)
            name_label = Gtk.Label(label=name, xalign=0)
            name_label.add_css_class("health-metric-name")
            identity.append(name_label)
            health_rows.attach(identity, 0, row, 1, 1)

            average = Gtk.Label(label="—", xalign=0.5)
            average.set_size_request(92, -1)
            average.add_css_class("health-average")
            health_rows.attach(average, 1, row, 1, 1)
            self.health_stat_labels[key] = average

            sparkline = Gtk.DrawingArea()
            sparkline.set_content_height(62)
            sparkline.set_hexpand(True)
            sparkline.set_draw_func(self._draw_daily_health_bars, key)
            sparkline.set_accessible_role(Gtk.AccessibleRole.IMG)
            self._set_accessible_info(
                sparkline,
                f"{name} daily averages",
                f"Daily average {name.lower()} usage for the last seven days. Readings are collected while Leaf Control is running.",
            )
            health_rows.attach(sparkline, 2, row, 1, 1)
            self.health_sparkline_areas[key] = sparkline

        date_range = Gtk.CenterBox()
        first_day = Gtk.Label(label="7 days ago", xalign=0)
        first_day.add_css_class("health-date-label")
        last_day = Gtk.Label(label="Today", xalign=1)
        last_day.add_css_class("health-date-label")
        date_range.set_start_widget(first_day)
        date_range.set_end_widget(last_day)
        health_rows.attach(date_range, 2, 4, 1, 1)
        health_section.append(health_rows)
        page.append(health_section)
        self._render_daily_health()
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(0)
        scroller.set_max_content_height(760)
        scroller.set_child(page)
        return scroller

    def _render_daily_health(self):
        if not self.health_stat_labels:
            return
        if self.system_install_date:
            age_days = max(0, (datetime.now() - self.system_install_date).days)
            age = f"~{age_days}d"
        else:
            age = "—"
        self.health_meta_label.set_text(f"OS age {age}  ·  Uptime {uptime_text()}")
        self.health_meta_label.set_tooltip_text("OS age is estimated from the oldest available package history")
        today = datetime.now().date().isoformat()
        metrics = self.system_health_days.get(today, {})
        for key in ("cpu", "memory", "gpu"):
            data = metrics.get(key)
            average = data["total"] / data["count"] if data and data["count"] else None
            self.health_stat_labels[key].set_text(f"{round(average)}%" if average is not None else "—")
            days = []
            for offset in range(6, -1, -1):
                day = datetime.now().date() - timedelta(days=offset)
                daily = self.system_health_days.get(day.isoformat(), {}).get(key)
                value = daily["total"] / daily["count"] if daily and daily["count"] else None
                days.append(f"{day.strftime('%A')}: {value:.1f}%" if value is not None else f"{day.strftime('%A')}: no readings")
            self.health_sparkline_areas[key].set_tooltip_text("Daily average usage\n" + "\n".join(days))
        for sparkline in self.health_sparkline_areas.values():
            sparkline.queue_draw()

    def _record_daily_health(self, cpu, memory, gpu):
        today = datetime.now().date().isoformat()
        if today != self.system_health_day:
            self._save_system_health_history()
            self.system_health_day = today
        metrics = self.system_health_days.setdefault(today, {})
        for key, value in (("cpu", cpu), ("memory", memory), ("gpu", gpu)):
            if value is None:
                continue
            try:
                sample = min(100.0, max(0.0, float(value)))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(sample):
                continue
            aggregate = metrics.setdefault(key, {"total": 0.0, "count": 0})
            aggregate["total"] += sample
            aggregate["count"] += 1
        self.system_health_days = dict(sorted(self.system_health_days.items())[-30:])
        self._render_daily_health()
        if time.monotonic() - self.system_health_saved_at >= 30:
            self._save_system_health_history()

    def _save_system_health_history(self):
        try:
            save_system_health_history(self.system_health_path, self.system_health_days)
            self.system_health_saved_at = time.monotonic()
        except OSError:
            pass

    def _draw_daily_health_bars(self, _area, cr, width, height, key):
        color_key = {"cpu": "blue", "memory": "green", "gpu": "orange"}[key]
        color = cairo_color(self.theme_palette[color_key])
        left, right = 34, max(35, width - 4)
        top, bottom = 5, max(6, height - 15)
        today = datetime.now().date()
        daily_values = []
        for index in range(7):
            day = today - timedelta(days=6 - index)
            aggregate = self.system_health_days.get(day.isoformat(), {}).get(key)
            average = aggregate["total"] / aggregate["count"] if aggregate and aggregate["count"] else None
            daily_values.append((day, average))
        peak = max((value for _day, value in daily_values if value is not None), default=0)
        scale = next((limit for limit in (5, 10, 20, 40, 60, 80, 100) if peak <= limit), 100)

        cr.set_line_width(1)
        cr.set_source_rgba(*cairo_color(self.theme_palette["ui_border"]), 0.48)
        for fraction in (0.5, 1.0):
            y = bottom - (bottom - top) * fraction
            cr.move_to(left, y)
            cr.line_to(right, y)
            cr.stroke()
        cr.move_to(left, bottom)
        cr.line_to(right, bottom)
        cr.stroke()
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(8)
        scale_label = f"0–{scale}%"
        cr.set_source_rgba(*cairo_color(self.theme_palette["ui_text_muted"]), 0.9)
        cr.move_to(0, top + 7)
        cr.show_text(scale_label)

        group_width = (right - left) / 7
        bar_width = min(18, group_width * 0.44)
        for index, (day, average) in enumerate(daily_values):
            center = left + group_width * (index + 0.5)
            # A faint track keeps the seven-day rhythm legible even before a
            # day has enough readings to produce an average.
            cr.rectangle(center - bar_width / 2, top, bar_width, bottom - top)
            cr.set_source_rgba(*cairo_color(self.theme_palette["ui_border"]), 0.12)
            cr.fill()
            if average is not None:
                bar_height = (bottom - top) * min(scale, max(0.0, average)) / scale
                cr.rectangle(center - bar_width / 2, bottom - bar_height, bar_width, max(2, bar_height))
                cr.set_source_rgba(*color, 0.98 if index == 6 else 0.70)
                cr.fill()
            else:
                cr.arc(center, bottom - 2, 1.2, 0, math.tau)
                cr.set_source_rgba(*cairo_color(self.theme_palette["ui_text_muted"]), 0.55)
                cr.fill()
            day_label = "Today" if index == 6 else day.strftime("%a")
            extents = cr.text_extents(day_label)
            cr.set_source_rgba(*cairo_color(self.theme_palette["ui_text_muted"]), 0.86)
            cr.move_to(center - extents.width / 2, height - 1)
            cr.show_text(day_label)

    def _draw_system_gauge(self, _area, cr, width, height, key):
        value = self.gauge_values.get(key)
        fraction = min(1.0, max(0.0, float(value) / 100)) if value is not None else 0
        liquid = gauge_color(fraction)

        cx = width / 2
        radius = min(width * 0.35, max(22, height - 18))
        center_y = height - 9
        tube_width = 11
        start_angle = math.pi

        def arc_path(end_angle, arc_start=start_angle):
            cr.new_path()
            cr.arc(cx, center_y, radius, arc_start, end_angle)

        # Thin glass raceway: nested strokes create a clean lens edge and dark channel.
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        arc_path(0)
        cr.set_source_rgba(1, 1, 1, 0.10)
        cr.set_line_width(tube_width + 5)
        cr.stroke()
        arc_path(0)
        cr.set_source_rgba(1, 1, 1, 0.34)
        cr.set_line_width(tube_width + 1.5)
        cr.stroke()
        arc_path(0)
        cr.set_source_rgba(*cairo_color(self.theme_palette["ui_bg_deep"]), 0.96)
        cr.set_line_width(tube_width - 1.5)
        cr.stroke()

        # A few clean radial marks follow the semicircle like a restrained gauge cluster.
        cr.set_line_cap(cairo.LINE_CAP_BUTT)
        for tick in range(5):
            angle = math.pi + math.pi * tick / 4
            inner = radius + tube_width * 0.95
            major = tick in (0, 2, 4)
            outer = inner + (5 if major else 3)
            cr.move_to(cx + math.cos(angle) * inner, center_y + math.sin(angle) * inner)
            cr.line_to(cx + math.cos(angle) * outer, center_y + math.sin(angle) * outer)
            cr.set_source_rgba(1, 1, 1, 0.48 if major else 0.26)
            cr.set_line_width(1.2)
            cr.stroke()

        # One continuous fluid column advances from the lower-left end around the arc.
        if value is not None and fraction > 0:
            fluid_ends = ((start_angle, start_angle + math.pi * fraction),)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            for fluid_start, fluid_end in fluid_ends:
                arc_path(fluid_end, fluid_start)
                cr.set_source_rgba(*liquid, 0.24)
                cr.set_line_width(tube_width + 5)
                cr.stroke()
                cr.new_path()
                cr.arc(cx, center_y, radius, fluid_start, fluid_end)
                cr.set_source_rgba(*liquid, 0.98)
                cr.set_line_width(tube_width - 2)
                cr.stroke()

                # A small glassy glint marks the advancing liquid surface.
                end_x = cx + math.cos(fluid_end) * radius
                end_y = center_y + math.sin(fluid_end) * radius
                cr.arc(end_x, end_y, 1.6, 0, math.tau)
                cr.set_source_rgba(1, 1, 1, 0.68)
                cr.fill()

        # A smaller nested arc shows thermal load without competing with usage.
        temperature = self.gauge_temperatures.get(key)
        inner_radius = radius * 0.72
        inner_width = 6
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.new_path()
        cr.arc(cx, center_y, inner_radius, math.pi, math.tau)
        cr.set_source_rgba(1, 1, 1, 0.08)
        cr.set_line_width(inner_width + 3.5)
        cr.stroke()
        cr.new_path()
        cr.arc(cx, center_y, inner_radius, math.pi, math.tau)
        cr.set_source_rgba(1, 1, 1, 0.26)
        cr.set_line_width(inner_width + 1)
        cr.stroke()
        cr.new_path()
        cr.arc(cx, center_y, inner_radius, math.pi, math.tau)
        cr.set_source_rgba(*cairo_color(self.theme_palette["ui_bg_deep"]), 0.98)
        cr.set_line_width(inner_width - 1)
        cr.stroke()

        if temperature is not None:
            temp_fraction = min(1.0, max(0.0, temperature / 100))
            temp_color = temperature_gauge_color(temperature)
            if temp_fraction > 0:
                temp_end = math.pi + math.pi * temp_fraction
                cr.new_path()
                cr.arc(cx, center_y, inner_radius, math.pi, temp_end)
                cr.set_source_rgba(*temp_color, 0.22)
                cr.set_line_width(inner_width + 3.5)
                cr.stroke()
                cr.new_path()
                cr.arc(cx, center_y, inner_radius, math.pi, temp_end)
                cr.set_source_rgba(*temp_color, 0.98)
                cr.set_line_width(inner_width - 1)
                cr.stroke()
        else:
            temp_color = cairo_color(self.theme_palette["ui_text_muted"])

        display_temperature = temperature
        if display_temperature is not None and self.settings["temperature_unit"] == "F":
            display_temperature = display_temperature * 9 / 5 + 32
        temp_text = f"{round(display_temperature)}°" if display_temperature is not None else "—"
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(17)
        text_extents = cr.text_extents(temp_text)
        cr.set_source_rgba(*temp_color, 0.96 if temperature is not None else 0.58)
        cr.move_to(cx - text_extents.width / 2 - text_extents.x_bearing,
                   center_y - inner_radius * 0.36)
        cr.show_text(temp_text)

    def _draw_drive_capacity_gauge(self, _area, cr, width, height, gauge_data):
        fraction = gauge_data.get("fraction")
        if fraction is None:
            liquid = cairo_color(self.theme_palette["ui_text_muted"])
        else:
            liquid = gauge_color(fraction)

        cx = width / 2
        radius = min(width * 0.36, height - 26)
        center_y = height - 17
        tube_width = 7
        cr.set_line_cap(cairo.LINE_CAP_ROUND)

        def arc(start, end):
            cr.new_path()
            cr.arc(cx, center_y, radius, start, end)

        # Match the main gauges with a fine glass rim and a dark channel.
        arc(math.pi, math.tau)
        cr.set_source_rgba(1, 1, 1, 0.10)
        cr.set_line_width(tube_width + 4)
        cr.stroke()
        arc(math.pi, math.tau)
        cr.set_source_rgba(1, 1, 1, 0.32)
        cr.set_line_width(tube_width + 1)
        cr.stroke()
        arc(math.pi, math.tau)
        cr.set_source_rgba(*cairo_color(self.theme_palette["ui_bg_deep"]), 0.96)
        cr.set_line_width(tube_width - 1)
        cr.stroke()

        # Three restrained index marks keep the small gauge legible.
        cr.set_line_cap(cairo.LINE_CAP_BUTT)
        for tick, angle in enumerate((math.pi, math.pi * 1.5, math.tau)):
            inner = radius + tube_width * 0.9
            outer = inner + 3.5
            cr.move_to(cx + math.cos(angle) * inner, center_y + math.sin(angle) * inner)
            cr.line_to(cx + math.cos(angle) * outer, center_y + math.sin(angle) * outer)
            cr.set_source_rgba(1, 1, 1, 0.40 if tick == 1 else 0.28)
            cr.set_line_width(1)
            cr.stroke()

        if fraction is not None and fraction > 0:
            end_angle = math.pi + math.pi * fraction
            arc(math.pi, end_angle)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_source_rgba(*liquid, 0.24)
            cr.set_line_width(tube_width + 4)
            cr.stroke()
            arc(math.pi, end_angle)
            cr.set_source_rgba(*liquid, 0.98)
            cr.set_line_width(tube_width - 2)
            cr.stroke()
            end_x = cx + math.cos(end_angle) * radius
            end_y = center_y + math.sin(end_angle) * radius
            cr.arc(end_x, end_y, 1.3, 0, math.tau)
            cr.set_source_rgba(1, 1, 1, 0.72)
            cr.fill()

        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(9)
        text = f"{round(fraction * 100)}%" if fraction is not None else "—"
        extents = cr.text_extents(text)
        cr.set_source_rgba(*(liquid if fraction is not None else cairo_color(self.theme_palette["ui_text_muted"])), 0.94)
        cr.move_to(cx - extents.width / 2 - extents.x_bearing, height - 1)
        cr.show_text(text)

    def _draw_hardware_icon(self, _area, cr, width, height, kind):
        ink = cairo_color(self.theme_palette["ui_accent"])
        soft = cairo_color(self.theme_palette["ui_text_soft"])
        cr.set_line_width(1.8)
        cr.set_line_join(1)
        cx, cy = width / 2, height / 2

        def outline(x, y, w, h, radius=2):
            cr.rectangle(x + radius, y, w - radius * 2, h)
            cr.stroke()

        cr.set_source_rgba(*ink, 0.9)
        if kind == "cpu":
            size = min(22, height * 0.58)
            x, y = cx - size / 2, cy - size / 2
            cr.rectangle(x, y, size, size)
            cr.stroke()
            cr.rectangle(x + 5, y + 5, size - 10, size - 10)
            cr.stroke()
            for step in range(4):
                offset = x + 4 + step * (size - 8) / 3
                cr.move_to(offset, y - 4); cr.line_to(offset, y)
                cr.move_to(offset, y + size); cr.line_to(offset, y + size + 4)
                offset_y = y + 4 + step * (size - 8) / 3
                cr.move_to(x - 4, offset_y); cr.line_to(x, offset_y)
                cr.move_to(x + size, offset_y); cr.line_to(x + size + 4, offset_y)
            cr.stroke()
        elif kind == "ram":
            x, y, w, h = cx - width * 0.38, cy - 8, width * 0.76, 16
            cr.rectangle(x, y, w, h); cr.stroke()
            cr.set_source_rgba(*soft, 0.9)
            for chip in range(4):
                cr.rectangle(x + 6 + chip * (w - 20) / 4, y + 3, 8, 8); cr.stroke()
            cr.set_source_rgba(*ink, 0.95)
            for contact in range(7):
                px = x + 5 + contact * (w - 10) / 6
                cr.move_to(px, y + h); cr.line_to(px, y + h + 4)
            cr.stroke()
        elif kind == "gpu":
            x, y, w, h = cx - width * 0.4, cy - 10, width * 0.8, 20
            cr.rectangle(x, y, w, h); cr.stroke()
            for fan_x in (x + w * 0.34, x + w * 0.67):
                cr.arc(fan_x, cy, 6, 0, math.tau); cr.stroke()
                cr.arc(fan_x, cy, 1.4, 0, math.tau); cr.fill()
            cr.move_to(x + w - 1, y + 4); cr.line_to(x + w + 4, y + 4)
            cr.move_to(x + w - 1, y + 9); cr.line_to(x + w + 4, y + 9)
            cr.move_to(x + w - 1, y + 14); cr.line_to(x + w + 4, y + 14)
            cr.stroke()
        elif kind in ("hdd", "ssd", "nvme", "drive"):
            if kind == "nvme":
                x, y, w, h = cx - width * 0.38, cy - 7, width * 0.76, 14
                cr.rectangle(x, y, w, h); cr.stroke()
                cr.set_source_rgba(*soft, 0.9)
                for chip in range(3):
                    cr.rectangle(x + 6 + chip * (w - 32) / 3, y + 2, 10, 8); cr.stroke()
                cr.set_source_rgba(*ink, 0.95)
                for contact in range(5):
                    px = x + w - 7 + contact * 1.5
                    cr.move_to(px, y + 3); cr.line_to(px, y + h - 3)
                cr.stroke()
            else:
                x, y, w, h = cx - width * 0.33, cy - 12, width * 0.66, 24
                cr.rectangle(x, y, w, h); cr.stroke()
                if kind == "hdd":
                    cr.arc(cx - 3, cy, 7, 0, math.tau); cr.stroke()
                    cr.arc(cx - 3, cy, 2, 0, math.tau); cr.fill()
                    cr.move_to(cx - 3, cy); cr.line_to(x + w - 5, y + 5); cr.stroke()
                else:
                    cr.set_source_rgba(*soft, 0.9)
                    for chip in range(3):
                        cr.rectangle(x + 4 + chip * 10, y + 4, 7, 7); cr.stroke()
                cr.set_source_rgba(*ink, 0.95)
                cr.rectangle(x + w - 4, y + h - 5, 2, 2); cr.fill()
        elif kind == "network":
            cr.arc(cx, cy, 10, 0, math.tau); cr.stroke()
            cr.arc(cx, cy, 4, 0, math.tau); cr.stroke()
            cr.move_to(cx - 12, cy); cr.line_to(cx + 12, cy)
            cr.move_to(cx, cy - 12); cr.line_to(cx, cy + 12); cr.stroke()
        elif kind == "sensor":
            cr.rectangle(cx - 5, cy - 12, 10, 19); cr.stroke()
            cr.arc(cx, cy + 8, 5, 0, math.tau); cr.fill()
            cr.move_to(cx, cy - 5); cr.line_to(cx, cy + 8); cr.stroke()
        elif kind == "clock":
            cr.arc(cx, cy, 11, 0, math.tau); cr.stroke()
            cr.move_to(cx, cy); cr.line_to(cx, cy - 6)
            cr.line_to(cx + 5, cy - 2); cr.stroke()
        elif kind == "health":
            x, y = cx - width * 0.36, cy - height * 0.36
            w, h = width * 0.72, height * 0.56
            cr.rectangle(x, y, w, h); cr.stroke()
            cr.move_to(cx, y + h)
            cr.line_to(cx, y + h + 4)
            cr.move_to(cx - 5, y + h + 4)
            cr.line_to(cx + 5, y + h + 4)
            cr.stroke()
            cr.set_source_rgba(*soft, 0.98)
            cr.move_to(x + 3, y + h * 0.56)
            cr.line_to(x + 7, y + h * 0.56)
            cr.line_to(x + 9, y + h * 0.34)
            cr.line_to(x + 12, y + h * 0.78)
            cr.line_to(x + 15, y + h * 0.48)
            cr.line_to(x + 18, y + h * 0.56)
            cr.line_to(x + w - 3, y + h * 0.56)
            cr.stroke()
        else:
            cr.rectangle(cx - 10, cy - 10, 20, 20); cr.stroke()
            for offset in (-6, 0, 6):
                cr.move_to(cx + offset, cy - 7); cr.line_to(cx + offset, cy + 7)
            cr.stroke()

    def _draw_history(self, _area, cr, width, height, key):
        left, right, top, bottom = 3, max(5, width - 3), 5, max(7, height - 7)
        cr.set_line_width(1)
        cr.set_source_rgba(*cairo_color(self.theme_palette["ui_text_muted"]), 0.18)
        for fraction in (0.25, 0.5, 0.75):
            y = top + (bottom - top) * fraction
            cr.move_to(left, y)
            cr.line_to(right, y)
        cr.stroke()

        window_seconds = history_window_seconds(self.settings["refresh_seconds"])
        now = time.monotonic()
        cutoff = now - window_seconds
        samples = recent_history_samples(self.histories[key], window_seconds, now)
        if key == "network":
            maximum = max((max(value) for _, value in samples), default=0) or 1
            series = (([(timestamp, value[0]) for timestamp, value in samples], cairo_color(self.theme_palette["cyan"])),
                      ([(timestamp, value[1]) for timestamp, value in samples], cairo_color(self.theme_palette["orange"])))
        else:
            maximum = 100
            color = self.theme_palette["ui_accent"] if key == "cpu" else self.theme_palette["magenta"]
            series = ((samples, cairo_color(color)),)
        for values, color in series:
            if not values:
                continue
            segments = [[]]
            previous_time = None
            gap_limit = max(1.0, self.settings["refresh_seconds"] * 2.5)
            for timestamp, value in values:
                if previous_time is not None and timestamp - previous_time > gap_limit:
                    segments.append([])
                x_fraction = min(1.0, max(0.0, (timestamp - cutoff) / window_seconds))
                normalized = min(1.0, max(0.0, value / maximum))
                point = (left + (right - left) * x_fraction,
                         bottom - (bottom - top) * normalized)
                segments[-1].append(point)
                previous_time = timestamp
            for points in segments:
                if not points:
                    continue
                if len(points) > 1:
                    cr.move_to(points[0][0], bottom)
                    cr.line_to(*points[0])
                    for point in points[1:]:
                        cr.line_to(*point)
                    cr.line_to(points[-1][0], bottom)
                    cr.close_path()
                    cr.set_source_rgba(*color, 0.10)
                    cr.fill()
                cr.set_source_rgba(*color, 0.96)
                cr.set_line_width(2)
                cr.move_to(*points[0])
                for point in points[1:]:
                    cr.line_to(*point)
                cr.stroke()
                cr.arc(points[-1][0], points[-1][1], 2.7, 0, math.tau)
                cr.set_source_rgba(*color, 1.0)
                cr.fill()

    def _update_history_accessibility(self):
        """Keep each chart's screen-reader description in sync with its data."""
        chart_names = {"cpu": "Processor", "memory": "Memory", "network": "Network transfer"}
        window_seconds = history_window_seconds(self.settings["refresh_seconds"])
        now = time.monotonic()
        for key, chart in self.chart_areas.items():
            samples = recent_history_samples(self.histories[key], window_seconds, now)
            if not samples:
                summary = "No readings have been collected yet."
            elif key == "network":
                _sample_time, (received, sent) = samples[-1]
                summary = (
                    f"Most recent sample: download {human_rate(received)}, "
                    f"upload {human_rate(sent)}. {len(samples)} samples are plotted; "
                    "the chart scale adjusts to the highest recent rate."
                )
            else:
                _sample_time, latest_value = samples[-1]
                latest = round(latest_value)
                summary = (
                    f"Most recent sample: {latest} percent. {len(samples)} samples "
                    "are plotted on a fixed scale from 0 to 100 percent."
                )
            self._set_accessible_info(
                chart,
                f"{chart_names[key]} history chart",
                summary,
            )

    def _refresh_system(self):
        if not getattr(self, "window", None):
            return GLib.SOURCE_REMOVE
        snap = cpu_snapshot()
        cpu = cpu_usage_between(self.cpu_prev, snap)
        self.cpu_prev = snap
        if cpu is not None:
            self.histories["cpu"].append((time.monotonic(), cpu))
        core_snap = cpu_core_snapshot()
        self.cpu_cores_prev = core_snap
        self.logical_cpu_count = len(core_snap) or self.logical_cpu_count
        val, detail, _ = self.metric_labels["cpu"]
        val.set_text(f"{cpu}%" if cpu is not None else "Unavailable")
        self.gauge_values["cpu"] = cpu
        self.gauge_areas["cpu"].queue_draw()
        detail.set_text("")
        self.metric_bars["cpu"].set_fraction((cpu or 0) / 100)
        self.metric_bars["cpu"].set_visible(cpu is not None)
        self._set_accessible_progress(self.metric_bars["cpu"], cpu or 0)
        mem = read_memory()
        total = mem.get("MemTotal", 0)
        available = mem.get("MemAvailable", 0)
        used = total - available
        if total:
            memory_percent = used / total * 100
            self.histories["memory"].append((time.monotonic(), memory_percent))
        else:
            memory_percent = None
        self.gauge_values["memory"] = memory_percent
        self.gauge_areas["memory"].queue_draw()
        val, detail, _ = self.metric_labels["memory"]
        val.set_text(f"{round(memory_percent)}%" if memory_percent is not None else "Unavailable")
        detail.set_text(f"{human_bytes(used)} / {human_bytes(total)}" if total else "")
        self.metric_bars["memory"].set_fraction(min(1.0, max(0.0, used / total)) if total else 0)
        self.metric_bars["memory"].set_visible(bool(total))
        self._set_accessible_progress(self.metric_bars["memory"], used / total * 100 if total else 0)
        now = time.monotonic()
        elapsed = max(now - self.io_prev_time, 0.001)
        net_now = network_snapshot()
        net_interfaces_now = network_interface_snapshot()
        disk_now = disk_io_snapshot()
        net_rate = None
        interface_rates = []
        disk_rate = None
        if net_now and self.net_prev:
            net_rate = (max(0, net_now[0] - self.net_prev[0]) / elapsed, max(0, net_now[1] - self.net_prev[1]) / elapsed)
        if net_interfaces_now and self.net_interfaces_prev:
            for name, counters in net_interfaces_now.items():
                previous = self.net_interfaces_prev.get(name)
                if previous:
                    received = max(0, counters[0] - previous[0]) / elapsed
                    sent = max(0, counters[1] - previous[1]) / elapsed
                    interface_rates.append((name, received, sent))
        interface_rates.sort(key=lambda item: item[1] + item[2], reverse=True)
        if disk_now and self.disk_prev:
            disk_rate = (max(0, disk_now[0] - self.disk_prev[0]) / elapsed, max(0, disk_now[1] - self.disk_prev[1]) / elapsed)
        self.net_prev, self.net_interfaces_prev, self.disk_prev, self.io_prev_time = net_now, net_interfaces_now, disk_now, now
        if net_rate is not None:
            self.histories["network"].append((now, net_rate))

        if hasattr(self, "network_status"):
            self.network_status.set_text(f"Network  ↓ {human_rate(net_rate[0])}   ↑ {human_rate(net_rate[1])}" if net_rate else "Network · unavailable")
            self.disk_status.set_text(f"Disk  Read {human_rate(disk_rate[0])}   Write {human_rate(disk_rate[1])}" if disk_rate else "Disk activity · unavailable")
        self._request_storage_sample()
        self._render_storage_metrics(disk_rate)

        self._request_hardware_sample()
        self._render_hardware_metrics()
        self._record_daily_health(cpu, memory_percent, self.gauge_values.get("gpu"))
        self._update_history_accessibility()
        for chart in self.chart_areas.values():
            chart.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _update_system_refresh_label(self):
        label = getattr(self, "system_refresh_label", None)
        if not label:
            return
        interval = self.settings["refresh_seconds"]
        unit = "second" if interval == 1 else "seconds"
        label.set_text(f"A live view of your computer · refreshes every {interval} {unit}")

    def _request_storage_sample(self):
        if self.storage_sample_in_flight:
            return
        self.storage_sample_in_flight = True

        def collect():
            try:
                volumes = mounted_filesystems()
                error = False
            except Exception:
                volumes, error = [], True
            GLib.idle_add(self._finish_storage_sample, volumes, error)

        threading.Thread(target=collect, name="leaf-storage-sample", daemon=True).start()

    def _finish_storage_sample(self, volumes, error):
        self.storage_volumes = volumes
        self.storage_sample_error = error
        self.storage_sample_in_flight = False
        if getattr(self, "window", None):
            self._render_storage_metrics()
        return GLib.SOURCE_REMOVE

    def _render_storage_metrics(self, disk_rate=None):
        volumes = self.storage_volumes
        summary, detail, _ = self.metric_labels["storage"]
        devices = storage_device_inventory(volumes)
        if len(devices) != len(self.storage_row_widgets):
            child = self.storage_rows_box.get_first_child()
            while child:
                next_child = child.get_next_sibling()
                self.storage_rows_box.remove(child)
                child = next_child
            self.storage_row_widgets = []
            for device in devices:
                row = Gtk.Box(spacing=8)
                row.add_css_class("storage-row")
                row.set_hexpand(True)
                icon = Gtk.DrawingArea()
                icon.set_content_width(48)
                icon.set_content_height(30)
                icon.set_draw_func(self._draw_hardware_icon, device["kind"])
                icon.set_accessible_role(Gtk.AccessibleRole.IMG)
                self._set_accessible_info(icon, f"{device['model']} hardware icon")
                row.append(icon)
                identity = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                identity.set_hexpand(True)
                row.append(identity)
                device_label = Gtk.Label(xalign=0)
                device_label.add_css_class("storage-device-name")
                device_label.set_max_width_chars(22)
                device_label.set_ellipsize(Pango.EllipsizeMode.END)
                identity.append(device_label)
                mount_label = Gtk.Label(xalign=0)
                mount_label.add_css_class("storage-status")
                mount_label.set_hexpand(True)
                mount_label.set_max_width_chars(38)
                mount_label.set_ellipsize(Pango.EllipsizeMode.END)
                identity.append(mount_label)
                capacity = Gtk.Label(xalign=1)
                capacity.add_css_class("storage-capacity")
                capacity.set_max_width_chars(18)
                capacity.set_ellipsize(Pango.EllipsizeMode.END)
                capacity.set_tooltip_text("Used / total capacity")
                row.append(capacity)
                gauge_data = {"fraction": None}
                gauge = Gtk.DrawingArea()
                gauge.set_content_width(72)
                gauge.set_content_height(58)
                gauge.set_draw_func(self._draw_drive_capacity_gauge, gauge_data)
                gauge.set_accessible_role(Gtk.AccessibleRole.IMG)
                self._set_accessible_info(gauge, f"{device['model']} capacity gauge")
                row.append(gauge)
                self.storage_rows_box.append(row)
                self.storage_row_widgets.append({
                    "name": device_label, "mount": mount_label,
                    "capacity": capacity, "gauge": gauge, "gauge_data": gauge_data,
                })
        for device, widgets in zip(devices, self.storage_row_widgets):
            kind_name = {"nvme": "NVMe SSD", "ssd": "SSD", "hdd": "HDD", "drive": "Drive"}.get(device["kind"], "Drive")
            model_name = str(device["model"]).strip()
            if len(model_name) > 18:
                model_name = model_name[:17].rstrip() + "…"
            widgets["name"].set_text(f"{kind_name} · {model_name}")
            mounts = device["mounts"]
            mount_summary = ", ".join(mounts)
            if len(mounts) > 1:
                mount_summary = f"{mounts[0]} · +{len(mounts) - 1}"
            widgets["mount"].set_text(f"Mounted · {mount_summary}" if device["mounted"] else "Not mounted")
            if device["mounted"]:
                widgets["mount"].add_css_class("storage-mounted")
                widgets["mount"].remove_css_class("storage-unmounted")
            else:
                widgets["mount"].add_css_class("storage-unmounted")
                widgets["mount"].remove_css_class("storage-mounted")
            total = max(int(device["total"]), 0)
            used = min(max(int(device["used"]), 0), total)
            short = lambda amount: human_bytes(amount).replace(".0 ", " ")
            capacity_text = f"{short(used)} / {short(total)}" if device["mounted"] else short(total)
            widgets["capacity"].set_text(capacity_text)
            fraction = min(1.0, used / total) if device["mounted"] and total else None
            widgets["gauge_data"]["fraction"] = fraction
            widgets["gauge"].set_tooltip_text(
                f"{round(fraction * 100)}% used" if fraction is not None else "Usage unavailable while unmounted"
            )
            widgets["gauge"].queue_draw()
        if devices:
            mounted_count = sum(bool(device["mounted"]) for device in devices)
            summary.set_text(f"{len(devices)} drives · {mounted_count} mounted")
        elif self.storage_sample_in_flight:
            summary.set_text("Scanning…")
        elif self.storage_sample_error:
            summary.set_text("Unavailable")
        else:
            summary.set_text("No drives")
        detail.set_text("")

    def _request_hardware_sample(self):
        if self.hardware_sample_in_flight:
            return
        self.hardware_sample_in_flight = True

        def collect():
            try:
                sensors = hardware_sensor_readings()
                gpus = read_gpu_metrics()
                error = False
            except Exception:
                sensors, gpus, error = [], [], True
            GLib.idle_add(self._finish_hardware_sample, sensors, gpus, error)

        threading.Thread(target=collect, name="leaf-hardware-sample", daemon=True).start()

    def _finish_hardware_sample(self, sensors, gpus, error):
        self.sensor_readings = sensors
        self.gpu_readings = gpus
        self.hardware_sample_error = error
        self.hardware_sample_in_flight = False
        if getattr(self, "window", None):
            self._render_hardware_metrics()
        return GLib.SOURCE_REMOVE

    def _render_hardware_metrics(self):
        sensors = self.sensor_readings
        def temperature_values(readings):
            values = []
            for name, reading in readings:
                if not str(reading).endswith("°C"):
                    continue
                match = re.search(r"-?\d+(?:\.\d+)?", str(reading))
                if match:
                    values.append((float(match.group()), str(name).casefold()))
            return values

        system_temperatures = temperature_values(sensors)
        cpu_primary = [
            value for value, name in system_temperatures
            if any(marker in name for marker in ("tctl", "tdie", "package", "coretemp", "x86_pkg_temp"))
        ]
        cpu_fallback = [
            value for value, name in system_temperatures
            if any(marker in name for marker in ("cpu", "processor", "core"))
        ]
        memory_temperatures = [
            value for value, name in system_temperatures
            if any(marker in name for marker in ("spd", "jc42", "dimm", "memory", "ddr"))
        ]
        gpus = self.gpu_readings
        gpu_temperatures = temperature_values([
            sensor
            for gpu in gpus
            for sensor in gpu.get("sensors", [])
        ])
        self.gauge_temperatures["cpu"] = max(cpu_primary or cpu_fallback, default=None)
        self.gauge_temperatures["memory"] = max(memory_temperatures, default=None)
        self.gauge_temperatures["gpu"] = max(
            (value for value, _name in gpu_temperatures), default=None
        )
        for key, gauge in self.gauge_areas.items():
            gauge.queue_draw()

        val, detail, _ = self.metric_labels["gpu"]
        if gpus:
            gpu_loads = [gpu["utilization"] for gpu in gpus if isinstance(gpu["utilization"], int) and 0 <= gpu["utilization"] <= 100]
            average_load = sum(gpu_loads) / len(gpu_loads) if gpu_loads else None
            self.gauge_values["gpu"] = average_load
            self.gauge_areas["gpu"].queue_draw()
            val.set_text(f"{round(average_load)}%" if average_load is not None else "Load unavailable")
            self.metric_bars["gpu"].set_visible(bool(gpu_loads))
            if gpu_loads:
                self.metric_bars["gpu"].set_fraction(average_load / 100)
                self._set_accessible_progress(self.metric_bars["gpu"], average_load)
            vram = [gpu for gpu in gpus if gpu["vram_total"] and gpu["vram_used"] is not None]
            if vram:
                used_vram = sum(gpu["vram_used"] for gpu in vram)
                total_vram = sum(gpu["vram_total"] for gpu in vram)
                detail.set_text(f"{len(gpus)} GPUs · {human_bytes(used_vram)} / {human_bytes(total_vram)} VRAM")
            else:
                detail.set_text(f"{len(gpus)} GPUs")
        else:
            val.set_text("—")
            detail.set_text("")
            self.gauge_values["gpu"] = None
            self.gauge_areas["gpu"].queue_draw()
            self.metric_bars["gpu"].set_visible(False)
        self._render_daily_health()

    def _refresh_clock(self):
        if getattr(self, "clock_label", None):
            clock_format = "%H:%M:%S" if self.settings["clock_24h"] else "%I:%M:%S %p"
            formatted_time = time.strftime(clock_format)
            formatted_date = time.strftime("%A, %B %-d")
            self.clock_label.set_text(formatted_time)
            self._set_accessible_info(self.clock_label, f"Current time: {formatted_time}")
            self.clock_date.set_text(formatted_date)
            self._set_accessible_info(self.clock_date, f"Current date: {formatted_date}")
            local_time = time.localtime()
            zone = time.tzname[1 if local_time.tm_isdst > 0 else 0]
            self.clock_timezone.set_text(f"LOCAL TIME  ·  {zone}")
            self._set_accessible_info(self.clock_timezone, f"Local time zone: {zone}")
            self.clock_face.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _draw_clock_face(self, _area, cr, width, height):
        cx, cy = width / 2, height / 2
        radius = max(18, min(width, height) / 2 - 9)
        cr.arc(cx, cy, radius, 0, math.tau)
        cr.set_source_rgba(*cairo_color(self.theme_palette["ui_bg_deeper"]), 0.72)
        cr.fill_preserve()
        cr.set_line_width(1.2)
        cr.set_source_rgba(*cairo_color(self.theme_palette["ui_accent"]), 0.48)
        cr.stroke()
        for tick in range(60):
            angle = tick * math.tau / 60 - math.pi / 2
            outer = radius - 7
            inner = outer - (9 if tick % 5 == 0 else 3)
            cr.move_to(cx + math.cos(angle) * inner, cy + math.sin(angle) * inner)
            cr.line_to(cx + math.cos(angle) * outer, cy + math.sin(angle) * outer)
            cr.set_line_width(1.8 if tick % 5 == 0 else 0.8)
            cr.set_source_rgba(*cairo_color(self.theme_palette["ui_text_soft"]), 0.78 if tick % 5 == 0 else 0.34)
            cr.stroke()
        now = time.localtime()
        second = now.tm_sec + (time.time() % 1)
        minute = now.tm_min + second / 60
        hour = (now.tm_hour % 12) + minute / 60
        hands = ((hour / 12 * math.tau, radius * 0.48, 4.0, cairo_color(self.theme_palette["bright_foreground"])),
                 (minute / 60 * math.tau, radius * 0.70, 2.7, cairo_color(self.theme_palette["ui_accent"])),
                 (second / 60 * math.tau, radius * 0.78, 1.1, cairo_color(self.theme_palette["cyan"])))
        for phase, length, line_width, color in hands:
            angle = phase - math.pi / 2
            cr.move_to(cx - math.cos(angle) * radius * 0.10, cy - math.sin(angle) * radius * 0.10)
            cr.line_to(cx + math.cos(angle) * length, cy + math.sin(angle) * length)
            cr.set_line_width(line_width)
            cr.set_line_cap(1)
            cr.set_source_rgba(*color, 0.96)
            cr.stroke()
        cr.arc(cx, cy, 4.2, 0, math.tau)
        cr.set_source_rgba(*cairo_color(self.theme_palette["ui_accent"]), 1)
        cr.fill()

    def _toggle_stopwatch(self, *_args):
        if self.stopwatch_started is not None:
            self.stopwatch_elapsed += time.monotonic() - self.stopwatch_started
            self.stopwatch_started = None
            self._stop_stopwatch_tick()
            self.stopwatch_button.set_label("Resume")
        else:
            self.stopwatch_started = time.monotonic()
            if self.stopwatch_tick_id is None:
                interval = 100 if self.settings["stopwatch_hundredths"] else 1000
                self.stopwatch_tick_id = GLib.timeout_add(interval, self._tick_stopwatch)
            self.stopwatch_button.set_label("Pause")
        self._update_stopwatch_label()

    def _calculate(self, *_args):
        expression = self.calc_entry.get_text().strip()
        try:
            normalized = expression.translate(str.maketrans({"×": "*", "÷": "/", "−": "-"}))
            result = safe_calculate(normalized, self.settings["angle_unit"])
        except (SyntaxError, ValueError, ZeroDivisionError, OverflowError):
            self.calc_result.set_text("Check the expression")
            self.calc_history_list.unselect_all()
            return
        self.calc_result.set_text(f"= {result}")
        self.calc_result.remove_css_class("muted")
        self.calc_history = (self.calc_history + [{"expression": expression, "result": result}])[-20:]
        save_calculation_history(self.calc_history_path, self.calc_history)
        self._render_calculation_history()

    def _calculator_key(self, _button, token):
        current = self.calc_entry.get_text()
        if token == "C":
            self.calc_entry.set_text("")
            self.calc_result.set_text("Ready")
        elif token == "⌫":
            self.calc_entry.set_text(current[:-1])
        else:
            mapped = {"√": "sqrt(", "π": "pi", "^": "**", "!": "factorial(", "×": "*", "÷": "/", "−": "-"}
            self.calc_entry.set_text(current + mapped.get(token, f"{token}(" if token in {"sin", "cos", "tan", "asin", "acos", "atan", "ln", "log", "log10", "abs", "floor", "ceil"} else token))
        self.calc_entry.grab_focus()

    def _toggle_scientific_keys(self, button):
        self.scientific_keys.set_reveal_child(button.get_active())

    def _calculator_memory(self, _button, action):
        if action == "clear":
            self.calc_memory = 0.0
        elif action == "recall":
            value = format(self.calc_memory, ".15g")
            current = self.calc_entry.get_text()
            self.calc_entry.set_text(current + value)
            self.calc_entry.grab_focus()
        else:
            try:
                expression = self.calc_entry.get_text().strip().translate(str.maketrans({"×": "*", "÷": "/", "−": "-"}))
                value = float(safe_calculate(expression, self.settings["angle_unit"]))
            except (SyntaxError, ValueError, ZeroDivisionError, OverflowError):
                self.calc_result.set_text("Calculate a valid value first")
                return
            if action == "store":
                self.calc_memory = value
            elif action == "add":
                self.calc_memory += value
            elif action == "subtract":
                self.calc_memory -= value
        self.calc_memory_label.set_text(f"M  {self.calc_memory:.8g}")

    def _render_calculation_history(self):
        child = self.calc_history_list.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.calc_history_list.remove(child)
            child = next_child
        for item in reversed(self.calc_history):
            row = Gtk.ListBoxRow()
            button = Gtk.Button(label=f"{item['expression']}  =  {item['result']}")
            button.set_halign(Gtk.Align.FILL)
            button.set_hexpand(True)
            self._set_accessible_info(button, f"Reuse calculation {item['expression']} equals {item['result']}", "Load this expression and calculate it again")
            button.connect("clicked", self._reuse_calculation, item["expression"])
            row.set_child(button)
            self.calc_history_list.append(row)

    def _reuse_calculation(self, _button, expression):
        self.calc_entry.set_text(expression)
        self._calculate()

    def _copy_calculation_result(self, *_args):
        if self.calc_history:
            display = Gdk.Display.get_default()
            if display:
                display.get_clipboard().set(self.calc_history[-1]["result"])

    def _clear_calculation_history(self, *_args):
        self.calc_history.clear()
        save_calculation_history(self.calc_history_path, self.calc_history)
        self._render_calculation_history()

    def _reset_stopwatch(self, *_args):
        self.stopwatch_elapsed = 0.0
        self.stopwatch_started = None
        self._stop_stopwatch_tick()
        self.stopwatch_button.set_label("Start")
        self._update_stopwatch_label()

    def _update_stopwatch_label(self):
        elapsed = self.stopwatch_elapsed
        if self.stopwatch_started is not None:
            elapsed += time.monotonic() - self.stopwatch_started
        formatted = format_duration(elapsed, hundredths=self.settings["stopwatch_hundredths"])
        self.stopwatch_label.set_text(formatted)
        self._set_accessible_info(
            self.stopwatch_label, f"Stopwatch elapsed time: {format_duration(elapsed)}",
            "Elapsed time since the stopwatch started",
        )

    def _tick_stopwatch(self):
        if self.stopwatch_started is None:
            self.stopwatch_tick_id = None
            return GLib.SOURCE_REMOVE
        self._update_stopwatch_label()
        return GLib.SOURCE_CONTINUE

    def _stop_stopwatch_tick(self):
        if self.stopwatch_tick_id is not None:
            GLib.source_remove(self.stopwatch_tick_id)
            self.stopwatch_tick_id = None

    def _toggle_timer(self, *_args):
        if self.timer_deadline is not None:
            self.timer_remaining = max(0.0, self.timer_deadline - time.monotonic())
            self.timer_deadline = None
            self._stop_timer_tick()
            self.timer_button.set_label("Resume")
            formatted = format_duration(self.timer_remaining)
            self.timer_label.set_text(formatted)
            self._set_accessible_info(
                self.timer_label, f"Timer paused: {formatted}",
                "Remaining countdown time. Activate Resume to continue.",
            )
            return
        if self.timer_remaining is None or self.timer_remaining <= 0:
            try:
                minutes = float(self.timer_entry.get_text())
                if not math.isfinite(minutes) or minutes <= 0 or minutes > 1440:
                    raise ValueError
            except ValueError:
                self.timer_label.set_text("Enter a duration from 0 to 1440 minutes")
                self._set_accessible_info(
                    self.timer_label, "Invalid timer duration",
                    "Enter a duration greater than zero and no more than 1440 minutes.",
                )
                return
            self.timer_remaining = minutes * 60
            self.timer_total = self.timer_remaining
        self.timer_deadline = time.monotonic() + self.timer_remaining
        self.timer_remaining = None
        self.timer_button.set_label("Pause")
        self._tick_timer()
        if self.timer_deadline is not None and self.timer_tick_id is None:
            self.timer_tick_id = GLib.timeout_add(250, self._tick_timer)

    def _cancel_timer(self, *_args):
        self.timer_deadline = None
        self.timer_remaining = None
        self.timer_total = None
        self._stop_timer_tick()
        self.timer_button.set_label("Start")
        self.timer_label.set_text("Timer idle")
        self._set_accessible_info(self.timer_label, "Timer idle", "Set a duration in minutes, then activate Start.")
        self.timer_progress.set_fraction(0)
        self._set_accessible_progress(self.timer_progress, 0)

    def _tick_timer(self):
        if self.timer_deadline is None:
            self.timer_tick_id = None
            return GLib.SOURCE_REMOVE
        remaining = max(0.0, self.timer_deadline - time.monotonic())
        formatted = format_duration(math.ceil(remaining))
        self.timer_label.set_text(formatted)
        self._set_accessible_info(
            self.timer_label, f"Time remaining: {formatted}",
            "Remaining time in the active countdown.",
        )
        if self.timer_total:
            progress = min(1.0, max(0.0, 1 - remaining / self.timer_total))
            self.timer_progress.set_fraction(progress)
            self._set_accessible_progress(self.timer_progress, progress * 100)
        if remaining <= 0:
            self.timer_deadline = None
            self.timer_remaining = 0.0
            self.timer_tick_id = None
            self.timer_button.set_label("Start")
            self.timer_label.set_text("Finished")
            self._set_accessible_info(self.timer_label, "Timer finished", "The countdown has completed.")
            self.timer_progress.set_fraction(1.0)
            self._set_accessible_progress(self.timer_progress, 100)
            notification = Gio.Notification.new("Timer finished")
            notification.set_body("Your Leaf Control countdown has completed.")
            if self.settings["timer_notifications"]:
                self.send_notification("leaf-control-timer", notification)
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _stop_timer_tick(self):
        if self.timer_tick_id is not None:
            GLib.source_remove(self.timer_tick_id)
            self.timer_tick_id = None

    def _persist_notes(self):
        try:
            save_notes(self.notes_store_path, self.notes, self.notes_trash, self.note_folders)
            if getattr(self, "notes_status", None):
                self.notes_status.set_text(f"Saved locally · {len(self.notes)} notes")
            return True
        except OSError as exc:
            if getattr(self, "notes_status", None):
                self.notes_status.set_text(f"Could not save notes: {exc}")
            return False

    def _set_note_editor_enabled(self, enabled):
        for widget in (self.note_title, self.note_folder_selector, self.note_pin_button, self.notes_view, self.note_mode_button, self.export_note_button):
            widget.set_sensitive(enabled)

    def _refresh_note_folder_controls(self):
        if not getattr(self, "note_folder_filter", None):
            return
        self.updating_note_folder_controls = True
        filters = ["All notes", *self.note_folders]
        if self.active_note_folder_filter not in filters:
            self.active_note_folder_filter = "All notes"
        self.note_folder_filter.set_model(Gtk.StringList.new(filters))
        self.note_folder_filter.set_selected(filters.index(self.active_note_folder_filter))
        self.note_folder_selector.set_model(Gtk.StringList.new(self.note_folders))
        note = next((item for item in self.notes if item["id"] == self.active_note_id), None)
        folder = note.get("folder", DEFAULT_NOTE_FOLDER) if note else DEFAULT_NOTE_FOLDER
        if folder not in self.note_folders:
            folder = DEFAULT_NOTE_FOLDER
        self.note_folder_selector.set_selected(self.note_folders.index(folder))
        self.updating_note_folder_controls = False

    def _dropdown_string(self, dropdown):
        item = dropdown.get_selected_item()
        return item.get_string() if item else ""

    def _note_folder_filter_changed(self, dropdown, *_args):
        if self.updating_note_folder_controls:
            return
        selected = self._dropdown_string(dropdown)
        if selected:
            self.active_note_folder_filter = selected
        self._render_notes_list()

    def _note_folder_selected(self, dropdown, *_args):
        if self.loading_note or self.updating_note_folder_controls or not self.active_note_id:
            return
        folder = clean_note_folder(self._dropdown_string(dropdown))
        note = next((item for item in self.notes if item["id"] == self.active_note_id), None)
        if note is None or folder is None or note.get("folder", DEFAULT_NOTE_FOLDER) == folder:
            return
        note["folder"] = folder
        note["updated"] = str(time.time())
        self._persist_notes()
        self._render_notes_list()

    def _show_folder_manager(self, *_args):
        if self.folder_manager_window:
            self.folder_manager_window.present()
            self.new_folder_entry.grab_focus()
            return
        window = Gtk.Window(title="Organize note folders", transient_for=self.window, modal=True)
        window.set_default_size(430, 420)
        self.folder_manager_window = window
        self.folder_manager_return_focus = self.manage_folders_button
        window.connect("close-request", self._folder_manager_closed)
        key_controller = Gtk.EventControllerKey.new()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect("key-pressed", self._folder_manager_key_pressed)
        window.add_controller(key_controller)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        panel.set_margin_top(18)
        panel.set_margin_bottom(18)
        panel.set_margin_start(18)
        panel.set_margin_end(18)
        window.set_child(panel)
        panel.append(self._heading("Note folders"))
        panel.append(self._muted_label("Create folders, rename them, or move their notes back to Inbox."))
        create_row = Gtk.Box(spacing=8)
        self.new_folder_entry = Gtk.Entry(placeholder_text="New folder name")
        self.new_folder_entry.set_hexpand(True)
        self._set_accessible_info(self.new_folder_entry, "New folder name", "Enter a name for the note folder to create")
        self.new_folder_entry.connect("activate", self._create_note_folder)
        create_row.append(self.new_folder_entry)
        create_button = Gtk.Button(label="Create")
        create_button.add_css_class("tool-action")
        self._set_accessible_info(create_button, "Create note folder", "Create a folder with the entered name")
        create_button.connect("clicked", self._create_note_folder)
        create_row.append(create_button)
        panel.append(create_row)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        self.folder_manager_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroller.set_child(self.folder_manager_list)
        panel.append(scroller)
        self.folder_manager_status = Gtk.Label(label="", xalign=0)
        self.folder_manager_status.add_css_class("muted")
        panel.append(self.folder_manager_status)
        done = Gtk.Button(label="Done", halign=Gtk.Align.END)
        done.add_css_class("tool-action")
        self._set_accessible_info(done, "Close note folder manager")
        done.connect("clicked", lambda *_: window.close())
        panel.append(done)
        self._render_folder_manager()
        window.present()
        self.new_folder_entry.grab_focus()

    def _folder_manager_key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape and self.folder_manager_window:
            self.folder_manager_window.close()
            return True
        return False

    def _folder_manager_closed(self, *_args):
        self.folder_manager_window = None
        return_focus = self.folder_manager_return_focus
        self.folder_manager_return_focus = None
        if return_focus:
            GLib.idle_add(self._restore_widget_focus, return_focus)
        return False

    def _render_folder_manager(self):
        if not getattr(self, "folder_manager_list", None):
            return
        child = self.folder_manager_list.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.folder_manager_list.remove(child)
            child = next_child
        for folder in self.note_folders:
            row = Gtk.Box(spacing=8)
            entry = Gtk.Entry(text=folder)
            entry.set_hexpand(True)
            self._set_accessible_info(entry, f"{folder} folder name", "Edit this notes folder's name")
            if folder.casefold() == DEFAULT_NOTE_FOLDER.casefold():
                entry.set_editable(False)
                entry.add_css_class("muted")
            row.append(entry)
            if folder.casefold() != DEFAULT_NOTE_FOLDER.casefold():
                rename = Gtk.Button(label="Save")
                rename.set_tooltip_text("Rename this folder")
                self._set_accessible_info(rename, f"Save {folder} folder name", "Rename this notes folder")
                rename.connect("clicked", self._rename_note_folder, folder, entry)
                row.append(rename)
                delete = Gtk.Button(label="Delete")
                delete.set_tooltip_text("Move folder notes to Inbox")
                self._set_accessible_info(delete, f"Delete {folder} folder", "Move this folder's notes to Inbox")
                delete.connect("clicked", self._delete_note_folder, folder)
                row.append(delete)
            self.folder_manager_list.append(row)

    def _create_note_folder(self, *_args):
        name = clean_note_folder(self.new_folder_entry.get_text())
        if not name:
            self.folder_manager_status.set_text("Enter a folder name.")
            self.new_folder_entry.grab_focus()
            return
        existing = next((folder for folder in self.note_folders if folder.casefold() == name.casefold()), None)
        if existing:
            self.folder_manager_status.set_text(f"{existing} already exists.")
            self.new_folder_entry.grab_focus()
            return
        self.note_folders.append(name)
        self.new_folder_entry.set_text("")
        self._persist_notes()
        self._refresh_note_folder_controls()
        self._render_notes_list()
        self._render_folder_manager()
        self.folder_manager_status.set_text(f"Created {name}.")
        self.new_folder_entry.grab_focus()

    def _rename_note_folder(self, _button, old_name, entry):
        name = clean_note_folder(entry.get_text())
        if not name:
            self.folder_manager_status.set_text("Enter a folder name.")
            entry.grab_focus()
            return
        existing = next((folder for folder in self.note_folders if folder.casefold() == name.casefold() and folder != old_name), None)
        if existing:
            self.folder_manager_status.set_text(f"{existing} already exists.")
            entry.grab_focus()
            return
        index = self.note_folders.index(old_name)
        self.note_folders[index] = name
        for note in self.notes + self.notes_trash:
            if note.get("folder", DEFAULT_NOTE_FOLDER).casefold() == old_name.casefold():
                note["folder"] = name
        if self.active_note_folder_filter.casefold() == old_name.casefold():
            self.active_note_folder_filter = name
        self._persist_notes()
        self._refresh_note_folder_controls()
        self._render_notes_list()
        self._render_folder_manager()
        self.folder_manager_status.set_text(f"Renamed folder to {name}.")
        self.new_folder_entry.grab_focus()

    def _delete_note_folder(self, _button, name):
        if name.casefold() == DEFAULT_NOTE_FOLDER.casefold():
            return
        self.note_folders = [folder for folder in self.note_folders if folder.casefold() != name.casefold()]
        for note in self.notes + self.notes_trash:
            if note.get("folder", DEFAULT_NOTE_FOLDER).casefold() == name.casefold():
                note["folder"] = DEFAULT_NOTE_FOLDER
        self._persist_notes()
        self._refresh_note_folder_controls()
        self._render_notes_list()
        self._render_folder_manager()
        self.folder_manager_status.set_text(f"Moved notes from {name} to Inbox.")
        self.new_folder_entry.grab_focus()

    def _render_notes_list(self, *_args):
        child = self.notes_list.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.notes_list.remove(child)
            child = next_child
        query = self.note_search.get_text().casefold().strip()
        ordered = sorted(self.notes, key=lambda note: note["updated"], reverse=True)
        ordered.sort(key=lambda note: not note["pinned"])
        visible = [note for note in ordered
                   if (self.active_note_folder_filter == "All notes" or
                       note.get("folder", DEFAULT_NOTE_FOLDER) == self.active_note_folder_filter)
                   and (not query or query in (note["title"] + "\n" + note["body"] + "\n" + note.get("folder", DEFAULT_NOTE_FOLDER)).casefold())]
        for note in visible:
            marker = "★  " if note["pinned"] else ""
            button = Gtk.Button(label=marker + (note["title"].strip() or "Untitled"))
            button.set_tooltip_text(note.get("folder", DEFAULT_NOTE_FOLDER))
            button.set_halign(Gtk.Align.FILL)
            button.set_hexpand(True)
            self._set_accessible_info(button, note["title"].strip() or "Untitled", f"Open note in {note.get('folder', DEFAULT_NOTE_FOLDER)}{' · pinned' if note['pinned'] else ''}")
            button.connect("clicked", lambda _button, note_id=note["id"]: self._select_note(note_id))
            self.notes_list.append(button)
        if not visible:
            empty = Gtk.Label(label="No notes in this view", xalign=0)
            empty.add_css_class("muted")
            empty.set_margin_top(8)
            self.notes_list.append(empty)

    def _select_note(self, note_id):
        note = next((item for item in self.notes if item["id"] == note_id), None)
        if note is None:
            return
        self.active_note_id = note_id
        self.loading_note = True
        self._refresh_note_folder_controls()
        self.note_title.set_text(note["title"])
        self.note_pin_button.set_active(note["pinned"])
        self.notes_view.get_buffer().set_text(note["body"])
        self.note_mode_button.set_active(False)
        self.note_mode_button.set_label("Preview")
        populate_markdown_preview(self.note_preview, note["body"], self.theme_palette)
        self.note_content_stack.set_visible_child_name("edit")
        self.loading_note = False
        self._set_note_editor_enabled(True)
        self.notes_status.set_text(f"Saved locally · {len(self.notes)} notes")

    def _new_note(self, *_args):
        folder = self.active_note_folder_filter if self.active_note_folder_filter != "All notes" else DEFAULT_NOTE_FOLDER
        note = {"id": uuid.uuid4().hex, "title": "Untitled", "body": "", "pinned": False,
                "folder": folder, "updated": str(time.time())}
        self.notes.append(note)
        self._persist_notes()
        self._render_notes_list()
        self._select_note(note["id"])
        self.note_title.grab_focus()
        self.note_title.select_region(0, -1)

    def _save_active_note(self, *_args):
        if self.loading_note or not self.active_note_id:
            return
        note = next((item for item in self.notes if item["id"] == self.active_note_id), None)
        if note is None:
            return
        start, end = self.notes_view.get_buffer().get_bounds()
        note["title"] = self.note_title.get_text()[:200]
        note["body"] = self.notes_view.get_buffer().get_text(start, end, True)[:200_000]
        note.setdefault("folder", DEFAULT_NOTE_FOLDER)
        note["updated"] = str(time.time())
        self._persist_notes()
        self._render_notes_list()

    def _toggle_note_mode(self, button):
        if self.loading_note:
            return
        if not self.active_note_id:
            button.set_active(False)
            return
        if button.get_active():
            start, end = self.notes_view.get_buffer().get_bounds()
            markdown = self.notes_view.get_buffer().get_text(start, end, True)
            populate_markdown_preview(self.note_preview, markdown, self.theme_palette)
            self.note_content_stack.set_visible_child_name("preview")
            button.set_label("Edit")
        else:
            self.note_content_stack.set_visible_child_name("edit")
            button.set_label("Preview")

    def _refresh_compact_workspaces(self):
        sets = workspace_sets(self.monitor_items, self.workspace_items)
        ids = sets.get(self.drawer_monitor_name, [])
        monitor = next((m for m in self.monitor_items if m.get("name") == self.drawer_monitor_name), {})
        active = monitor.get("activeWorkspace", {}).get("id")
        signature = (ids, active)
        if signature == getattr(self, "_compact_workspace_signature", None):
            return
        self._compact_workspace_signature = signature
        while child := self.compact_workspaces.get_first_child():
            self.compact_workspaces.remove(child)
        for number, workspace_id in enumerate(ids, 1):
            button = Gtk.ToggleButton(label=str(number))
            button.set_active(workspace_id == active)
            button.set_tooltip_text(f"Workspace {workspace_id}")
            button.connect("clicked", lambda _b, slot=number: self._select_workspace_slot(slot))
            self.compact_workspaces.append(button)

    def _select_workspace_slot(self, number):
        helper = Path(__file__).resolve().parent / "select_workspace.py"
        mode = "both" if self.settings["workspace_switch_both_monitors"] else "one"
        try:
            subprocess.Popen([sys.executable, str(helper), "workspace", str(number), self.drawer_monitor_name, mode], start_new_session=True)
        except OSError as exc:
            self._message(f"Could not switch workspace: {exc}")

    def _focus_workspace_strip(self):
        if not self.settings["workspace_visible"] or not self.monitor_items:
            self.search.grab_focus()
            return False
        monitor = next((m for m in self.monitor_items if m.get("name") == self.drawer_monitor_name), {})
        active = monitor.get("activeWorkspace", {}).get("id", 1)
        ids = workspace_sets(self.monitor_items, self.workspace_items).get(self.drawer_monitor_name, [])
        self.selected_workspace_id = ids.index(active) + 1 if active in ids else 1
        self.window_section.grab_focus()
        self._sync_workspace_strip()
        return False

    def _handle_workspace_keys(self, keyval, state):
        if self.active_page != "Apps" or self.command_panel or self.create_group_panel or self.open_folder_window:
            return False
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        focus = self.window.get_focus()
        search_focus = focus is self.search or (focus is not None and focus.is_ancestor(self.search))
        if search_focus:
            if keyval == Gdk.KEY_Up:
                self.nav_buttons["Apps"].grab_focus()
                return True
            if keyval == Gdk.KEY_Down and not self.search.get_text().strip():
                self._focus_workspace_strip()
                return True
            return False
        if focus is not self.window_section:
            return False
        selected = self.selected_workspace_id or 1
        count = len(workspace_sets(self.monitor_items, self.workspace_items).get(self.drawer_monitor_name, [])) or 10
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Home, Gdk.KEY_End):
            if keyval == Gdk.KEY_Home:
                selected = 1
            elif keyval == Gdk.KEY_End:
                selected = count
            else:
                selected += 1 if keyval == Gdk.KEY_Right else -1
                selected = (selected - 1) % count + 1 if self.settings["workspace_wrap"] else max(1, min(count, selected))
            self.selected_workspace_id = selected
            self._sync_workspace_strip()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self._select_workspace_slot(selected)
            return True
        if keyval == Gdk.KEY_Down:
            for grid in (self.favorites_grid, self.app_grid):
                buttons = self._grid_buttons(grid)
                if buttons:
                    buttons[0].grab_focus()
                    return True
            self.search.grab_focus()
            return True
        if keyval == Gdk.KEY_Up:
            self.search.grab_focus()
            return True
        return False

    def _handle_header_keys(self, keyval, state):
        if self.command_panel or self.create_group_panel or self.open_folder_window or getattr(self, "dock_popover", None):
            return False
        buttons = [
            self.nav_buttons["Apps"], self.nav_buttons["System"],
            self.settings_nav_button,
        ]
        active = self.settings_nav_button if self.active_page == "Settings" else self.nav_buttons[self.active_page]
        if keyval == Gdk.KEY_F6:
            active.grab_focus()
            return True
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        focus = self.window.get_focus()
        if self.active_page == "Settings" and keyval == Gdk.KEY_Up and (focus is self.settings_search or (focus and focus.is_ancestor(self.settings_search))):
            active.grab_focus()
            return True
        if focus not in buttons:
            return False
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Home, Gdk.KEY_End):
            index = buttons.index(focus)
            index = 0 if keyval == Gdk.KEY_Home else len(buttons) - 1 if keyval == Gdk.KEY_End else (index + (1 if keyval == Gdk.KEY_Right else -1)) % len(buttons)
            buttons[index].grab_focus()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            focus.emit("clicked")
            focus.grab_focus()
            return True
        if keyval == Gdk.KEY_Down:
            if self.active_page == "Apps":
                self.search.grab_focus()
            elif self.active_page == "Settings":
                self.settings_search.grab_focus()
            else:
                page = self.stack.get_visible_child()
                if page:
                    page.child_focus(Gtk.DirectionType.TAB_FORWARD)
            return True
        # Let the page-specific handler route Up from app tiles. Consuming it
        # here used to jump straight to the last tile, which made vertical
        # grid movement (and crossing to the previous app page) impossible.
        return False

    def _handle_shortcuts(self, _controller, keyval, _keycode, state):
        if keyval == Gdk.KEY_F11:
            self._toggle_fullscreen()
            return True
        if self._handle_header_keys(keyval, state):
            return True
        if self._handle_workspace_keys(keyval, state):
            return True
        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(state & Gdk.ModifierType.ALT_MASK)
        super_key = bool(state & Gdk.ModifierType.SUPER_MASK)
        if (
            self.active_page == "Apps"
            and super_key
            and not control
            and not alt
            and keyval in (Gdk.KEY_Left, Gdk.KEY_Right)
            and not self.search.get_text().strip()
        ):
            self._change_app_page(1 if keyval == Gdk.KEY_Right else -1)
            return True
        if alt and keyval == Gdk.KEY_F2:
            self._open_command_panel()
            return True
        if control and keyval in (Gdk.KEY_comma, Gdk.KEY_less):
            self._navigate(None, "Settings")
            return True
        if control and keyval in (Gdk.KEY_f, Gdk.KEY_F) and self.active_page == "Apps":
            self.search.grab_focus()
            return True
        if keyval == Gdk.KEY_Escape:
            return self._handle_escape()
        if self.active_page == "Apps":
            if not self.search.get_text().strip() and keyval in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
                self._change_app_page(1 if keyval == Gdk.KEY_Page_Down else -1)
                return True
            focus = self.window.get_focus()
            focused_app = next(
                ((app_id, app, tile) for app_id, (app, tile) in self.app_tiles_by_id.items() if focus is tile),
                None,
            )
            focused_folder = next(
                ((folder_id, tile) for folder_id, tile in self.folder_tiles_by_id.items() if focus is tile),
                None,
            )
            focused_folder_member = next(
                ((app_id, *item) for app_id, item in self.folder_member_tiles_by_id.items() if focus is item[2]),
                None,
            )
            menu_key = keyval == Gdk.KEY_Menu or (keyval == Gdk.KEY_F10 and shift)
            if menu_key and focused_app:
                _app_id, app, tile = focused_app
                self._show_app_menu(None, 1, tile.get_width() / 2, tile.get_height() / 2, app, tile)
                return True
            if menu_key and focused_folder:
                folder_id, tile = focused_folder
                self._show_folder_menu(None, 1, tile.get_width() / 2, tile.get_height() / 2, folder_id, tile)
                return True
            if menu_key and focused_folder_member:
                _app_id, app, folder_id, tile = focused_folder_member
                self._show_folder_app_menu(None, 1, tile.get_width() / 2, tile.get_height() / 2, app, folder_id, tile)
                return True
            if control and keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and focused_app:
                app_id, _app, tile = focused_app
                self._move_app_by_visible_position(app_id, tile, -1 if keyval == Gdk.KEY_Up else 1)
                return True
            if control and keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and focused_folder:
                folder_id, tile = focused_folder
                self._move_folder_by_visible_position(folder_id, tile, -1 if keyval == Gdk.KEY_Up else 1)
                return True
            if getattr(self, "dock_popover", None):
                return False
            dock_buttons = getattr(self, "running_dock_buttons", [])
            if focus in dock_buttons:
                index = dock_buttons.index(focus)
                if keyval in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Home, Gdk.KEY_End):
                    target = 0 if keyval == Gdk.KEY_Home else len(dock_buttons) - 1 if keyval == Gdk.KEY_End else (index + (1 if keyval == Gdk.KEY_Right else -1)) % len(dock_buttons)
                    dock_buttons[target].grab_focus()
                    return True
                if keyval == Gdk.KEY_Up:
                    for grid in (self.folders_grid, self.files_grid, self.app_grid, self.favorites_grid):
                        buttons = self._grid_buttons(grid)
                        if buttons:
                            buttons[-1].grab_focus()
                            return True
                    self.search.grab_focus()
                    return True
                if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
                    focus.emit("clicked")
                    return True
                if keyval == Gdk.KEY_Down:
                    self.nav_buttons["Apps"].grab_focus()
                    return True
            grids = (self.favorites_grid, self.app_grid, self.files_grid, self.folders_grid)
            search_has_focus = focus == self.search or (
                focus is not None and focus.is_ancestor(self.search)
            )
            if keyval == Gdk.KEY_Down and search_has_focus:
                for grid in grids:
                    buttons = self._grid_buttons(grid)
                    if buttons:
                        buttons[0].grab_focus()
                        return True
                if dock_buttons:
                    dock_buttons[0].grab_focus()
                else:
                    self.nav_buttons["Apps"].grab_focus()
                return True
            visible_grids = [(grid, self._grid_buttons(grid)) for grid in grids]
            visible_grids = [(grid, buttons) for grid, buttons in visible_grids if buttons]
            if keyval in (Gdk.KEY_Left, Gdk.KEY_Right) and not (control or alt or super_key):
                all_buttons = [button for _grid, buttons in visible_grids for button in buttons]
                if focus in all_buttons:
                    direction = 1 if keyval == Gdk.KEY_Right else -1
                    target = all_buttons.index(focus) + direction
                    if 0 <= target < len(all_buttons):
                        all_buttons[target].grab_focus()
                    elif not self.search.get_text().strip() and getattr(self, "app_page_count", 1) > 1:
                        self._change_app_page(direction)
                    else:
                        all_buttons[target % len(all_buttons)].grab_focus()
                    return True
            for grid_position, (grid, buttons) in enumerate(visible_grids):
                if focus not in buttons:
                    continue
                index = buttons.index(focus)
                first_y = buttons[0].get_parent().get_allocation().y
                columns = max(1, sum(button.get_parent().get_allocation().y == first_y for button in buttons))
                target = None
                if keyval == Gdk.KEY_Left and index % columns > 0:
                    target = index - 1
                elif keyval == Gdk.KEY_Right and index + 1 < len(buttons) and index // columns == (index + 1) // columns:
                    target = index + 1
                elif keyval == Gdk.KEY_Up and index - columns >= 0:
                    target = index - columns
                elif keyval == Gdk.KEY_Down and index + columns < len(buttons):
                    target = index + columns
                if target is not None:
                    buttons[target].grab_focus()
                    return True
                if (
                    grid is self.app_grid
                    and not self.search.get_text().strip()
                    and getattr(self, "app_page_count", 1) > 1
                    and keyval in (Gdk.KEY_Up, Gdk.KEY_Down)
                ):
                    # Keep vertical arrow navigation inside the app grid when
                    # it reaches the top or bottom row: continue on the
                    # adjacent page instead of jumping to the dock or header.
                    self._change_app_page(1 if keyval == Gdk.KEY_Down else -1)
                    return True
                if keyval == Gdk.KEY_Down:
                    for _next_grid, next_buttons in visible_grids[grid_position + 1:]:
                        next_buttons[0].grab_focus()
                        return True
                    if dock_buttons:
                        dock_buttons[0].grab_focus()
                    else:
                        self.nav_buttons["Apps"].grab_focus()
                    return True
                elif keyval == Gdk.KEY_Up:
                    for _previous_grid, previous_buttons in reversed(visible_grids[:grid_position]):
                        previous_buttons[-1].grab_focus()
                        return True
                    if not self.search.get_text().strip():
                        self._focus_workspace_strip()
                        return True
                    self.search.grab_focus()
                    return True
                break
        focus = self.window.get_focus()
        if self.active_page == "Apps" and not (control or alt or super_key) and not self.command_panel and not self.create_group_panel and not self.open_folder_window:
            if not isinstance(focus, (Gtk.Editable, Gtk.TextView)) and not (focus and focus.get_ancestor(Gtk.Popover)):
                character = Gdk.keyval_to_unicode(keyval)
                if character and chr(character).isprintable() and not chr(character).isspace():
                    self.search.set_text(self.search.get_text() + chr(character))
                    self.search.set_position(-1)
                    self.search.grab_focus()
                    return True
        return False

    def _handle_escape(self):
        """Close the topmost transient UI before clearing search or the drawer."""
        for name in ("active_context_popover", "dock_popover"):
            popover = getattr(self, name, None)
            if popover:
                popover.popdown()
                return True
        if self.command_panel:
            self._close_command_panel()
            return True
        if self.create_group_panel:
            self._close_group_create(restore_focus=False)
            return True
        if self.open_folder_window:
            self._close_folder_window()
            return True
        if self.active_page == "Apps" and self.search.get_text().strip():
            self.search.set_text("")
            return True
        self._dismiss_drawer()
        return True

    @staticmethod
    def _grid_buttons(grid):
        if not grid.get_visible():
            return []
        buttons = []
        child = grid.get_first_child()
        while child:
            button = child.get_child()
            if button and button.get_visible() and button.get_sensitive():
                buttons.append(button)
            child = child.get_next_sibling()
        return buttons

    def _toggle_note_pin(self, button):
        if self.loading_note or not self.active_note_id:
            return
        note = next((item for item in self.notes if item["id"] == self.active_note_id), None)
        if note is None:
            return
        note["pinned"] = button.get_active()
        note["updated"] = str(time.time())
        self._persist_notes()
        self._render_notes_list()

    def _delete_active_note(self, *_args):
        note = next((item for item in self.notes if item["id"] == self.active_note_id), None)
        if note is None:
            return
        self.notes.remove(note)
        self.notes_trash.append(note)
        self._persist_notes()
        self._render_notes_list()
        self.active_note_id = None
        query = self.note_search.get_text().casefold().strip()
        ordered_notes = sorted(self.notes, key=lambda note: note["updated"], reverse=True)
        ordered_notes.sort(key=lambda note: not note["pinned"])
        next_note = next((item for item in ordered_notes
                          if (self.active_note_folder_filter == "All notes" or
                              item.get("folder", DEFAULT_NOTE_FOLDER) == self.active_note_folder_filter)
                          and (not query or query in (item["title"] + "\n" + item["body"] + "\n" + item.get("folder", DEFAULT_NOTE_FOLDER)).casefold())), None)
        if next_note:
            self._select_note(next_note["id"])
        else:
            self.loading_note = True
            self.note_title.set_text("")
            self.notes_view.get_buffer().set_text("")
            self.note_pin_button.set_active(False)
            self.loading_note = False
            self._set_note_editor_enabled(False)
            self.notes_status.set_text("Note moved to trash · use Restore last deleted to recover it")

    def _restore_note(self, *_args):
        if not self.notes_trash:
            self.notes_status.set_text("There are no deleted notes to restore")
            return
        note = self.notes_trash.pop()
        self.notes.append(note)
        folder = note.get("folder", DEFAULT_NOTE_FOLDER)
        if folder not in self.note_folders:
            self.note_folders.append(folder)
        self.active_note_folder_filter = folder
        self._refresh_note_folder_controls()
        self._persist_notes()
        self._render_notes_list()
        self._select_note(note["id"])

    def _export_active_note(self, *_args):
        note = next((item for item in self.notes if item["id"] == self.active_note_id), None)
        if note is None:
            return
        self.note_export_return_focus = self.window.get_focus() or self.export_note_button
        chooser = Gtk.FileChooserNative.new("Export note as Markdown", self.window,
                                            Gtk.FileChooserAction.SAVE, "Export", "Cancel")
        filename = re.sub(r"[^A-Za-z0-9._ -]+", "", note["title"]).strip() or "note"
        chooser.set_current_name(filename + ".md")
        chooser.connect("response", self._finish_note_export, note.copy())
        self.note_export_chooser = chooser
        chooser.show()

    def _finish_note_export(self, chooser, response, note):
        if response == Gtk.ResponseType.ACCEPT:
            file = chooser.get_file()
            path = file.get_path() if file else None
            if path:
                try:
                    export_note_markdown(note, Path(path))
                    self.notes_status.set_text(f"Exported Markdown to {path}")
                except OSError as exc:
                    self.notes_status.set_text(f"Could not export note: {exc}")
        chooser.destroy()
        return_focus = self.note_export_return_focus or self.export_note_button
        self.note_export_return_focus = None
        GLib.idle_add(self._restore_widget_focus, return_focus)

    def _search_changed(self, entry):
        self._populate_apps(entry.get_text())
        self.file_search.search(entry.get_text(), self._show_file_results)
        if entry.get_text().strip() and self.active_page != "Apps":
            self._navigate(None, "Apps")

    def _navigate(self, button, page):
        if page not in ("Apps", "System", "Settings"):
            page = "Apps"
        self._ensure_page_built(page)
        self.active_page = page
        self.stack.set_visible_child_name(page)
        if page == "Apps":
            self._ensure_running_dock_visible()
            GLib.idle_add(self._sync_workspace_strip)
        else:
            self._hide_workspace_strip()
        if getattr(self, "app_header", None):
            self.app_header.set_visible(True)
        if getattr(self, "search_row", None):
            self.search_row.set_visible(page == "Apps")
        if getattr(self, "search", None):
            self.search.set_visible(page == "Apps")
        if page == "Settings":
            self._set_fullscreen(True)
            self._render_pinned_editor()
        self._update_window_transparency_style()
        GLib.idle_add(self._update_responsive_layout)
        self._sync_system_refresh(refresh_now=True)
        self.search.set_placeholder_text({
            "Apps": "Search applications",
            "System": "Search apps to launch",
            "Settings": "Search applications",
        }[page])
        for name, nav_button in self.nav_buttons.items():
            nav_button.set_active(name == page)
        if getattr(self, "settings_nav_button", None):
            if page == "Settings":
                self.settings_nav_button.add_css_class("active")
            else:
                self.settings_nav_button.remove_css_class("active")

    def _ensure_page_built(self, page):
        if page in self._built_pages:
            return
        builders = {
            "System": ("system_page", self._build_system_page),
            "Settings": ("settings_page", self._build_settings_page),
        }
        entry = builders.get(page)
        if not entry:
            return
        attribute, builder = entry
        widget = builder()
        setattr(self, attribute, widget)
        self.stack.add_named(widget, page)
        self._built_pages.add(page)
        self._apply_motion_settings()

    def _show_settings(self, *_args):
        self._navigate(None, "Settings")

    def _system_monitor_is_relevant(self):
        # System widgets are created on first use so they do not delay startup.
        return (getattr(self, "window", None) is not None
                and "System" in getattr(self, "_built_pages", set()))

    def _sync_system_refresh(self, refresh_now=False):
        relevant = self._system_monitor_is_relevant()
        page_visible = self.active_page == "System" and self.window.get_visible()
        interval = self.settings["refresh_seconds"] if page_visible else 60
        if not relevant:
            if self.system_refresh_id is not None:
                GLib.source_remove(self.system_refresh_id)
                self.system_refresh_id = None
                self._save_system_health_history()
            self.system_refresh_seconds = None
            return
        if self.system_refresh_id is not None and self.system_refresh_seconds != interval:
            GLib.source_remove(self.system_refresh_id)
            self.system_refresh_id = None
        if self.system_refresh_id is None:
            if refresh_now:
                self._refresh_system()
            self.system_refresh_seconds = interval
            self.system_refresh_id = GLib.timeout_add_seconds(interval, self._refresh_system)
        elif refresh_now:
            self._refresh_system()

    def _search_locations_summary(self):
        count = len(self.file_search.roots)
        if count == 0:
            return "No folders selected · file search is off"
        folder_word = "folder" if count == 1 else "folders"
        excluded = len(self.settings["search_excludes"])
        if excluded:
            return f"{count} {folder_word} · skipping {excluded} folder names"
        return f"{count} {folder_word} · standard hidden and cache folders skipped"

    def _show_search_preferences(self, *_args):
        dialog = Gtk.Dialog(title="Search locations", transient_for=self.window, modal=True)
        dialog.add_css_class("leaf-window")
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.ACCEPT)
        dialog.set_default_size(600, 620)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_start(18)
        content.set_margin_end(18)
        content.set_margin_top(14)
        content.set_margin_bottom(14)
        intro = self._muted_label("Leaf searches folder and file names only. Add one folder path per line; hidden folders and common caches stay skipped automatically.")
        intro.set_wrap(True)
        content.append(intro)

        roots_label = Gtk.Label(label="Folders to search", xalign=0)
        roots_label.add_css_class("metric-heading")
        content.append(roots_label)
        roots_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        roots_view.get_buffer().set_text("\n".join(str(path) for path in self.file_search.roots))
        roots_scroll = Gtk.ScrolledWindow(min_content_height=155)
        roots_scroll.set_child(roots_view)
        content.append(roots_scroll)
        standard_folders = Gtk.CheckButton(label="Use standard personal folders")
        standard_folders.set_active(self.settings["search_roots"] is None)
        roots_view.set_sensitive(not standard_folders.get_active())
        standard_folders.connect("toggled", lambda button: roots_view.set_sensitive(not button.get_active()))
        content.append(standard_folders)

        excludes_label = Gtk.Label(label="Folder names to skip", xalign=0)
        excludes_label.add_css_class("metric-heading")
        content.append(excludes_label)
        excludes_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        excludes_view.get_buffer().set_text("\n".join(self.settings["search_excludes"]))
        excludes_scroll = Gtk.ScrolledWindow(min_content_height=105)
        excludes_scroll.set_child(excludes_view)
        content.append(excludes_scroll)
        content.append(self._muted_label("Enter directory names, not paths. Matching folders are skipped under every search location. For example: build or vendor."))
        status = Gtk.Label(xalign=0, wrap=True)
        status.add_css_class("muted")
        content.append(status)

        dialog.connect("response", self._save_search_preferences, roots_view, excludes_view, standard_folders, status)
        dialog.present()

    @staticmethod
    def _text_view_value(view):
        buffer = view.get_buffer()
        start, end = buffer.get_bounds()
        return buffer.get_text(start, end, True)

    def _save_search_preferences(self, dialog, response, roots_view, excludes_view, standard_folders, status):
        if response != Gtk.ResponseType.ACCEPT:
            dialog.destroy()
            return
        roots = None if standard_folders.get_active() else []
        if roots is not None:
            for raw in self._text_view_value(roots_view).splitlines():
                value = raw.strip()
                if not value:
                    continue
                path = Path(value).expanduser()
                if not path.is_absolute() or not path.is_dir():
                    status.set_text(f"Choose an existing folder path: {value}")
                    return
                path = path.resolve(strict=False)
                if path not in roots:
                    roots.append(path)

        excluded = set()
        for raw in self._text_view_value(excludes_view).splitlines():
            name = raw.strip()
            if not name:
                continue
            if name in (".", "..") or "/" in name or "\\" in name:
                status.set_text(f"Use a folder name without path separators: {name}")
                return
            excluded.add(name)

        previous_roots = self.settings["search_roots"]
        previous_excludes = self.settings["search_excludes"]
        self.settings["search_roots"] = [str(path) for path in roots] if roots is not None else None
        self.settings["search_excludes"] = sorted(excluded)
        try:
            save_settings(self.settings_path, self.settings)
        except OSError as exc:
            self.settings["search_roots"] = previous_roots
            self.settings["search_excludes"] = previous_excludes
            status.set_text(f"Could not save search preferences: {exc}")
            return
        self.file_search.reconfigure(roots, excluded)
        self.search_locations_summary.set_text(self._search_locations_summary())
        self.settings_status.set_text("Search locations saved · indexing folder and file names…")
        dialog.destroy()

    def _confirm_reset_settings(self, *_args):
        dialog = Gtk.Dialog(title="Restore default settings?", transient_for=self.window, modal=True)
        dialog.add_css_class("leaf-window")
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Restore defaults", Gtk.ResponseType.ACCEPT)
        dialog.set_default_size(460, 190)
        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.append(self._muted_label(
            "This restores Leaf Control preferences, grid layout, and search locations to their defaults. "
            "Your notes, app groups, and favorites are kept."
        ))
        dialog.connect("response", self._reset_settings_response)
        dialog.present()

    def _reset_settings_response(self, dialog, response):
        dialog.destroy()
        if response == Gtk.ResponseType.ACCEPT:
            self._reset_settings()

    def _apply_motion_settings(self):
        """Apply the selected motion style to drawer and page transitions."""
        style = self.settings.get("motion_style", "glide")
        duration = self.settings.get("motion_duration", 280)
        if style == "reduced":
            drawer_transition = Gtk.RevealerTransitionType.NONE
            stack_transition = Gtk.StackTransitionType.NONE
            duration = 0
        elif style == "blend":
            drawer_transition = Gtk.RevealerTransitionType.CROSSFADE
            stack_transition = Gtk.StackTransitionType.CROSSFADE
        else:
            drawer_transition = Gtk.RevealerTransitionType.SLIDE_UP
            stack_transition = Gtk.StackTransitionType.SLIDE_UP_DOWN
        if self.drawer_revealer:
            self.drawer_revealer.set_transition_type(drawer_transition)
            self.drawer_revealer.set_transition_duration(duration)
        if getattr(self, "stack", None):
            self.stack.set_transition_type(stack_transition)
            self.stack.set_transition_duration(duration)

    def _message(self, message):
        dialog = Gtk.AlertDialog(message=message)
        dialog.show(self.window)

    @staticmethod
    def _heading(text):
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class("section-title")
        return label

    @staticmethod
    def _muted_label(text):
        label = Gtk.Label(label=text, xalign=0, wrap=True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.add_css_class("muted")
        return label

    @staticmethod
    def _set_accessible_info(widget, label, description=None):
        properties = [Gtk.AccessibleProperty.LABEL]
        values = [label]
        if description:
            properties.append(Gtk.AccessibleProperty.DESCRIPTION)
            values.append(description)
        widget.update_property(properties, values)

    @staticmethod
    def _set_accessible_progress(widget, percent):
        value = min(100.0, max(0.0, float(percent)))
        widget.update_property(
            [Gtk.AccessibleProperty.VALUE_MIN, Gtk.AccessibleProperty.VALUE_MAX,
             Gtk.AccessibleProperty.VALUE_NOW, Gtk.AccessibleProperty.VALUE_TEXT],
            [0.0, 100.0, value, f"{round(value)}%"],
        )


def main():
    return LeafControl().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
