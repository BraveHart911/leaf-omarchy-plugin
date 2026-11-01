"""Size calculations in GTK logical pixels (already adjusted for DPI)."""
import math


def grid_columns(width, tile_width, spacing, maximum):
    return max(1, min(maximum, int((max(1, width) + spacing) // (tile_width + spacing))))


def page_capacity(columns, height, tile_height, spacing, heading_height=0):
    rows = max(1, math.floor((height - heading_height + spacing) / max(1, tile_height + spacing)))
    return max(1, columns) * rows


def dock_hover_sizes(count, hovered, base_size, peak_percent, reach):
    """Give the hovered dock icon a peak and ease its neighbors back to base."""
    if hovered is None or not 0 <= hovered < count:
        return [base_size] * count
    extra = base_size * (peak_percent / 100 - 1)
    sizes = []
    for index in range(count):
        distance = abs(index - hovered)
        if distance >= reach + 1:
            sizes.append(base_size)
        else:
            falloff = (1 + math.cos(math.pi * distance / (reach + 1))) / 2
            sizes.append(base_size + extra * falloff)
    return sizes
