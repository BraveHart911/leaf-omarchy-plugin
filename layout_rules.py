"""Size calculations in GTK logical pixels (already adjusted for DPI)."""
import math


def grid_columns(width, tile_width, spacing, maximum):
    return max(1, min(maximum, int((max(1, width) + spacing) // (tile_width + spacing))))


def page_capacity(columns, height, tile_height, spacing, heading_height=0):
    rows = max(1, math.floor((height - heading_height + spacing) / max(1, tile_height + spacing)))
    return max(1, columns) * rows
