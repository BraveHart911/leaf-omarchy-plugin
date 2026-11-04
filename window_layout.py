"""Responsive native window layout and app dock appearance."""
from gi.repository import Gdk, Gtk, GLib
from layout_rules import grid_columns


class WindowLayoutMixin:
    def _window_monitor_geometry(self):
        display = self.window.get_display()
        surface = self.window.get_surface()
        monitor = display.get_monitor_at_surface(surface) if surface else None
        if not monitor:
            monitors = display.get_monitors()
            monitor = monitors.get_item(0) if monitors.get_n_items() else None
        return monitor.get_geometry() if monitor else None

    def _layout_tick(self, _widget, _clock):
        signature = (self.window.get_width(), self.window.get_height(), self.window.is_fullscreen(),
                     self.app_scroller.get_width(), self.app_scroller.get_height(), self.active_page)
        if signature != getattr(self, "_layout_signature", None):
            self._layout_signature = signature
            self._update_responsive_layout()
        return GLib.SOURCE_CONTINUE

    def _update_responsive_layout(self, *_args):
        if not getattr(self, "window", None) or not getattr(self, "app_scroller", None):
            return GLib.SOURCE_REMOVE
        width, height = self.window.get_width(), self.window.get_height()
        if width < 1:
            return GLib.SOURCE_REMOVE
        compact = width < 700 or height < 600
        if compact:
            self.app_root.add_css_class("compact")
        else:
            self.app_root.remove_css_class("compact")
        self.search.set_size_request(min(520, max(160, width - 130)), -1)
        self.fullscreen_button.set_icon_name("view-restore-symbolic" if self.window.is_fullscreen() else "view-fullscreen-symbolic")
        self._responsive_settings(width)
        if getattr(self, "settings_page", None):
            margin = max(0, (width - 1320) // 2)
            self.settings_page.set_margin_start(margin)
            self.settings_page.set_margin_end(margin)
        if getattr(self, "system_content", None):
            margin = max(0, (width - 1120) // 2)
            self.system_content.set_margin_start(margin)
            self.system_content.set_margin_end(margin)
        if getattr(self, "system_details", None):
            self.system_details.set_orientation(
                Gtk.Orientation.HORIZONTAL if width >= 1040 else Gtk.Orientation.VERTICAL)
        self._fit_app_columns()
        gauge_columns = 1 if width < 640 else 2 if width < 1040 else 3
        if getattr(self, "metrics_grid", None) and self.metrics_grid.get_max_children_per_line() != gauge_columns:
            self.metrics_grid.set_max_children_per_line(gauge_columns)
        show_workspaces = self.settings["workspace_visible"] and bool(self.monitor_items)
        live = show_workspaces and self.window.is_fullscreen() and getattr(self, "workspace_plugin_available", False)
        strip_height = min(200, max(104, round(height * 0.19))) if live else -1
        self.window_section.set_size_request(-1, strip_height)
        self.window_section.set_visible(show_workspaces)
        workspace_columns = min(5, max(1, (width - 56) // 48))
        if self.compact_workspaces.get_max_children_per_line() != workspace_columns:
            self.compact_workspaces.set_max_children_per_line(workspace_columns)
        self.compact_workspaces.set_visible(show_workspaces and not live)
        self._app_viewport_resized()
        if self.active_page == "Apps":
            GLib.idle_add(self._sync_workspace_strip)
        return GLib.SOURCE_REMOVE

    def _fit_app_columns(self):
        width = min(self.app_scroller.get_width(), max(240, self.window.get_width() - 80))
        preferred = {"compact": 108, "comfortable": 124, "spacious": 144}[self.settings["grid_density"]]
        tile_width = max(preferred, self._app_icon_pixels() + 2 * self.settings["app_tile_padding"] + 16)
        maximum = self.settings["grid_columns"] or {"compact": 12, "comfortable": 9, "spacious": 7}[self.settings["grid_density"]]
        columns = grid_columns(width - 8, tile_width, self.settings["grid_spacing"], maximum)
        self.effective_tile_width = tile_width
        for grid in (self.app_grid, self.favorites_grid, self.files_grid, self.folders_grid):
            if grid.get_min_children_per_line() != columns or grid.get_max_children_per_line() != columns:
                grid.set_min_children_per_line(1)
                grid.set_max_children_per_line(columns)
        self._apply_app_tile_padding()

    def _apply_dock_preferences(self):
        if not hasattr(self, "running_dock"):
            return
        provider = getattr(self, "dock_style_provider", None)
        if provider is None:
            provider = Gtk.CssProvider()
            self.dock_style_provider = provider
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                     Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
        padding = self.settings["dock_padding"]
        if self.settings["dock_background"]:
            opacity = self.settings["dock_glass_opacity"] / 100
            surface = (f"background-color: alpha(@leaf-ui-bg-deep, {opacity:.2f}); "
                       "background-image: linear-gradient(to bottom, alpha(@leaf-ui-text, 0.09), transparent); "
                       "border: 1px solid alpha(@leaf-ui-text, 0.18); "
                       "box-shadow: 0 12px 28px alpha(@leaf-ui-bg-deep, 0.42), inset 0 1px alpha(@leaf-ui-text, 0.13);")
        else:
            surface = "background: transparent; border: 1px solid transparent; box-shadow: none;"
        provider.load_from_data(f".running-dock {{ padding: {padding}px; {surface} }}".encode())
        self.running_dock.set_spacing(self.settings["dock_spacing"])
        self.running_dock.set_halign({"start": Gtk.Align.START, "center": Gtk.Align.CENTER,
                                    "end": Gtk.Align.END}[self.settings["dock_alignment"]])
        peak = (self.settings["dock_magnify_scale"] / 100
                if self.settings["dock_magnify"] else 1)
        self.running_dock_scroller.set_min_content_height(
            round(self.settings["dock_icon_size"] * peak) + 34 + padding * 2
            + (20 if self.settings["dock_show_names"] else 0))
        after = self.window_section if self.settings["dock_position"] == "top" else self.app_footer
        self.apps_page.reorder_child_after(self.running_dock_scroller, after)
        self.running_dock_signature = None
        self._update_running_dock()
