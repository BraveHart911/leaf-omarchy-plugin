"""Leaf's settings page. Controls are generated from the validated schema."""
from copy import deepcopy
from gi.repository import Gtk, GLib, Pango
from app_layout import app_identifier
from preferences import PREFERENCES, SECTIONS, DEFAULT_SETTINGS, save_settings


class SettingsMixin:
    def _build_settings_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.add_css_class("settings-page")
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        heading.append(self._heading("Settings"))
        heading.append(self._muted_label("Make Leaf feel at home. Changes save automatically."))
        page.append(heading)
        refresh = Gtk.Button(label="Refresh Leaf", halign=Gtk.Align.START)
        refresh.set_tooltip_text("Restart Leaf to load code, styling and settings changes")
        refresh.connect("clicked", self._refresh_leaf)
        heading.append(refresh)
        self.settings_search = Gtk.SearchEntry(placeholder_text="Find a setting…")
        self._set_accessible_info(self.settings_search, "Find a setting")
        page.append(self.settings_search)

        self.settings_columns = Gtk.Box(spacing=24, vexpand=True)
        page.append(self.settings_columns)
        self.settings_sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.settings_sidebar.set_size_request(180, -1)
        self.settings_category_buttons = {}
        self.settings_category = "general"
        first = None
        for key, title, _description in SECTIONS:
            button = Gtk.ToggleButton(label=title)
            button.add_css_class("settings-category")
            if first:
                button.set_group(first)
            else:
                first = button
            button.set_active(key == "general")
            button.connect("toggled", self._select_settings_category, key)
            self.settings_category_buttons[key] = button
            self.settings_sidebar.append(button)
        self.settings_columns.append(self.settings_sidebar)
        self.settings_category_dropdown = Gtk.DropDown.new_from_strings([s[1] for s in SECTIONS])
        self.settings_category_dropdown.set_visible(False)
        self._set_accessible_info(self.settings_category_dropdown, "Settings section")
        self.settings_category_dropdown.connect("notify::selected", self._select_compact_category)
        page.insert_child_after(self.settings_category_dropdown, self.settings_search)

        self.settings_scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.settings_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.settings_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, hexpand=True)
        self.settings_scroll.set_child(self.settings_content)
        self.settings_columns.append(self.settings_scroll)
        self.settings_sections = {}
        self.settings_rows = []
        self.setting_controls = {}
        self.setting_value_labels = {}
        for key, title, description in SECTIONS:
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            section.add_css_class("settings-section")
            section_header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            section_header.add_css_class("settings-section-header")
            label = Gtk.Label(label=title, xalign=0)
            label.add_css_class("settings-section-title")
            section_header.append(label)
            section_header.append(self._muted_label(description))
            section.append(section_header)
            self.settings_sections[key] = section
            self.settings_content.append(section)
            for pref in (p for p in PREFERENCES if p.section == key):
                row = self._setting_row(pref.title, pref.description, key)
                value = self.settings[pref.key]
                if pref.choices:
                    control = Gtk.DropDown.new_from_strings([item[1] for item in pref.choices])
                    control.set_selected([item[0] for item in pref.choices].index(value))
                    signal = "notify::selected"
                elif pref.bounds:
                    low, high, step = pref.bounds
                    control = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, step)
                    control.set_draw_value(False)
                    control.set_round_digits(0)
                    control.set_size_request(140, -1)
                    control.set_value(value)
                    signal = "value-changed"
                else:
                    control = Gtk.Switch()
                    control.set_active(value)
                    signal = "notify::active"
                control.set_valign(Gtk.Align.CENTER)
                control.set_halign(Gtk.Align.END)
                self._set_accessible_info(control, pref.title, pref.description)
                self.setting_controls[pref.key] = control
                if pref.bounds:
                    controls = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
                    controls.append(control)
                    label = Gtk.Label(label=f"{value} {pref.unit}")
                    label.set_width_chars(6)
                    label.add_css_class("setting-value")
                    controls.append(label)
                    self.setting_value_labels[pref.key] = label
                    row.append(controls)
                else:
                    row.append(control)
                control.connect(signal, self._setting_changed, pref)
                section.append(row)

        pin_section = self.settings_sections["dock"]
        self.pinned_editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.pinned_editor.add_css_class("pinned-editor")
        pin_header = Gtk.Box(spacing=8)
        pin_label = Gtk.Label(label="Pinned applications", xalign=0, hexpand=True)
        pin_label.add_css_class("settings-section-title")
        pin_header.append(pin_label)
        add_pin = Gtk.Button(label="Add app…")
        add_pin.connect("clicked", self._show_pin_picker)
        pin_header.append(add_pin)
        self.pinned_editor.append(pin_header)
        self.pin_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.pinned_editor.append(self.pin_list)
        pin_section.insert_child_after(self.pinned_editor, pin_section.get_first_child())
        self._render_pinned_editor()

        theme = self._setting_row("Desktop theme", "Leaf follows your active Omarchy colors. Change themes from Omarchy's theme menu.", "appearance")
        theme.append(Gtk.Image.new_from_icon_name("preferences-desktop-theme-symbolic"))
        self.settings_sections["appearance"].append(theme)
        gestures = self._setting_row("Trackpad gestures", "Three fingers: swipe up to open Leaf, down to dismiss, and left or right to change app pages. Desktop bindings are configured in Hyprland.", "general")
        gestures.append(Gtk.Image.new_from_icon_name("input-touchpad-symbolic"))
        self.settings_sections["general"].append(gestures)
        search_row = self._setting_row("Search locations", "Only file and folder names are indexed. File contents stay private.", "search")
        edit = Gtk.Button(label="Choose folders…", valign=Gtk.Align.CENTER)
        edit.connect("clicked", self._show_search_preferences)
        search_row.append(edit)
        self.settings_sections["search"].append(search_row)
        self.search_locations_summary = self._muted_label(self._search_locations_summary())
        self.search_locations_summary.add_css_class("settings-section-header")
        self.settings_sections["search"].append(self.search_locations_summary)

        self.settings_empty = self._muted_label("No matching settings. Try “icons”, “pinned”, or “workspaces”.")
        self.settings_content.append(self.settings_empty)
        footer = Gtk.Box(spacing=12)
        self.settings_status = Gtk.Label(label="Saved on this device", xalign=0, wrap=True, hexpand=True)
        self.settings_status.add_css_class("muted")
        footer.append(self.settings_status)
        reset = Gtk.Button(label="Restore defaults…")
        reset.connect("clicked", self._confirm_reset_settings)
        footer.append(reset)
        page.append(footer)
        self.settings_search.connect("search-changed", self._filter_settings)
        self._filter_settings()
        self._update_setting_dependencies()
        return page

    def _setting_row(self, title, description, section):
        row = Gtk.Box(spacing=24)
        row.add_css_class("setting-row")
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, hexpand=True)
        name = Gtk.Label(label=title, xalign=0, wrap=True)
        name.add_css_class("setting-title")
        labels.append(name)
        note = self._muted_label(description)
        note.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        note.set_max_width_chars(54)
        labels.append(note)
        row.append(labels)
        self.settings_rows.append((row, section, f"{section} {title} {description}".casefold()))
        return row

    def _select_settings_category(self, button, category):
        if not button.get_active() or not hasattr(self, "settings_empty"):
            return
        self.settings_category = category
        self.settings_category_dropdown.set_selected([s[0] for s in SECTIONS].index(category))
        self.settings_search.set_text("")
        self._filter_settings()
        self.settings_scroll.get_vadjustment().set_value(0)

    def _select_compact_category(self, dropdown, *_args):
        key = SECTIONS[min(dropdown.get_selected(), len(SECTIONS) - 1)][0]
        self.settings_category_buttons[key].set_active(True)

    def _filter_settings(self, *_args):
        query = self.settings_search.get_text().casefold().strip()
        visible_sections = set()
        for row, section, searchable in self.settings_rows:
            visible = all(word in searchable for word in query.split()) if query else section == self.settings_category
            row.set_visible(visible)
            if visible:
                visible_sections.add(section)
        show_pins = ("pin" in query or "dock" in query or "bar" in query) if query else self.settings_category == "dock"
        self.pinned_editor.set_visible(show_pins)
        if show_pins:
            visible_sections.add("dock")
        for key, section in self.settings_sections.items():
            section.set_visible(key in visible_sections)
        self.settings_empty.set_visible(not visible_sections)

    def _responsive_settings(self, width):
        if not hasattr(self, "settings_sidebar"):
            return
        self.settings_sidebar.set_visible(width >= 900)
        self.settings_category_dropdown.set_visible(width < 900)
        for row, _section, _searchable in self.settings_rows:
            row.set_orientation(Gtk.Orientation.VERTICAL if width < 700 else Gtk.Orientation.HORIZONTAL)
            row.set_spacing(10 if width < 700 else 24)
            control = row.get_last_child()
            control.set_halign(Gtk.Align.START if width < 700 else Gtk.Align.END)

    def _setting_changed(self, control, *args):
        if getattr(self, "loading_settings", False):
            return
        pref = args[-1]
        if pref.choices:
            value = pref.choices[min(control.get_selected(), len(pref.choices) - 1)][0]
        elif pref.bounds:
            low, high, step = pref.bounds
            value = max(low, min(high, low + round((control.get_value() - low) / step) * step))
            self.setting_value_labels[pref.key].set_text(f"{value} {pref.unit}")
        else:
            value = control.get_active()
        previous = self.settings[pref.key]
        self.settings[pref.key] = value
        try:
            save_settings(self.settings_path, self.settings)
        except OSError as exc:
            self.settings[pref.key] = previous
            self.settings_status.set_text(f"Could not save: {exc}")
            self._sync_preference_controls()
            return
        self._apply_preferences()
        self.settings_status.set_text("Saved on this device")

    def _update_setting_dependencies(self):
        self.setting_controls["motion_duration"].set_sensitive(self.settings["motion_style"] != "reduced")
        self.setting_controls["dock_running_scope"].set_sensitive(self.settings["dock_show_running"])
        self.setting_controls["app_tile_corners"].set_sensitive(self.settings["app_tile_style"] != "minimal")

    def _apply_preferences(self):
        self._apply_grid_density()
        self._apply_app_appearance()
        self._apply_app_name_visibility()
        self._apply_dock_preferences()
        self._update_window_transparency_style()
        self._apply_motion_settings()
        self._populate_apps(self.search.get_text())
        self._update_app_page_indicator()
        self._update_setting_dependencies()
        self._update_responsive_layout()
        self._sync_system_refresh(refresh_now=self.active_page == "System")

    def _sync_preference_controls(self):
        self.loading_settings = True
        for pref in PREFERENCES:
            widget = self.setting_controls[pref.key]
            value = self.settings[pref.key]
            if pref.choices:
                widget.set_selected([item[0] for item in pref.choices].index(value))
            elif pref.bounds:
                widget.set_value(value)
                self.setting_value_labels[pref.key].set_text(f"{value} {pref.unit}")
            else:
                widget.set_active(value)
        self.loading_settings = False

    def _reset_settings(self, *_args):
        settings = deepcopy(DEFAULT_SETTINGS)
        try:
            save_settings(self.settings_path, settings)
        except OSError as exc:
            self.settings_status.set_text(f"Could not restore defaults: {exc}")
            return
        self.settings = settings
        self._sync_preference_controls()
        self.file_search.reconfigure(None, set())
        self.search_locations_summary.set_text(self._search_locations_summary())
        self._apply_preferences()
        self.settings_status.set_text("Defaults restored. Pinned apps and groups kept.")

    def _render_pinned_editor(self):
        if not hasattr(self, "pin_list"):
            return
        while child := self.pin_list.get_first_child():
            self.pin_list.remove(child)
        apps = {app_identifier(app): app for app in self.apps}
        for index, app_id in enumerate(self.layout.favorites):
            app = apps.get(app_id)
            row = Gtk.Box(spacing=8)
            name = app.get_display_name() if app else app_id.removesuffix(".desktop") + " (unavailable)"
            icon = Gtk.Image.new_from_gicon(app.get_icon()) if app and app.get_icon() else Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
            icon.set_pixel_size(28)
            row.append(icon)
            label = Gtk.Label(label=name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
            label.set_max_width_chars(24)
            row.append(label)
            for action, icon_name, tooltip in ((-1, "go-up-symbolic", "Move earlier"), (1, "go-down-symbolic", "Move later"), (0, "list-remove-symbolic", "Unpin")):
                button = Gtk.Button.new_from_icon_name(icon_name)
                self._set_accessible_info(button, f"{tooltip}: {name}")
                button.set_tooltip_text(tooltip)
                button.set_sensitive(not ((action == -1 and index == 0) or (action == 1 and index == len(self.layout.favorites) - 1)))
                button.connect("clicked", self._edit_pin, app_id, action)
                row.append(button)
            self.pin_list.append(row)
        if not self.layout.favorites:
            self.pin_list.append(self._muted_label("No pinned apps yet. Add an app, or pin one from its launcher menu."))

    def _edit_pin(self, _button, app_id, direction):
        try:
            if direction:
                self.layout.move_favorite(app_id, direction)
            else:
                self.layout.remove_favorite(app_id)
        except OSError as exc:
            self._message(f"Could not save pinned apps: {exc}")
            return
        self._render_pinned_editor()
        self.running_dock_signature = None
        self._update_running_dock()
        self._populate_apps(self.search.get_text())

    def _show_pin_picker(self, *_args):
        dialog = Gtk.Window(title="Pin an application", transient_for=self.window, modal=True)
        dialog.add_css_class("leaf-window")
        dialog.set_default_size(440, 480)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for edge in ("top", "bottom", "start", "end"):
            getattr(content, f"set_margin_{edge}")(16)
        dialog.set_child(content)
        search = Gtk.SearchEntry(placeholder_text="Search applications…")
        content.append(search)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroll.set_child(choices)
        content.append(scroll)
        close = Gtk.Button(label="Done")
        close.connect("clicked", lambda *_: dialog.destroy())
        content.append(close)

        def populate(*_args):
            while child := choices.get_first_child():
                choices.remove(child)
            query = search.get_text().casefold()
            count = 0
            for app in sorted(self.apps, key=lambda a: a.get_display_name().casefold()):
                if query not in app.get_display_name().casefold():
                    continue
                count += 1
                pinned = self.layout.is_favorite(app)
                button = Gtk.Button(label=app.get_display_name() + ("  ✓" if pinned else ""))
                button.set_sensitive(not pinned)
                button.connect("clicked", pin, app)
                choices.append(button)
            if not count:
                choices.append(self._muted_label("No matching applications."))

        def pin(_button, app):
            try:
                if not self.layout.is_favorite(app):
                    self.layout.toggle_favorite(app)
            except OSError as exc:
                self._message(f"Could not pin application: {exc}")
                return
            self._render_pinned_editor()
            self.running_dock_signature = None
            self._update_running_dock()
            populate()

        search.connect("search-changed", populate)
        populate()
        dialog.present()
