"""XOR Shell GTK application.

Run modes:
  python3 -m xor_shell              panel + dock + launcher
  python3 -m xor_shell --launcher   standalone full-screen launcher
  python3 -m xor_shell --list-apps  headless discovered-app dump
  python3 -m xor_shell --status-json

The shell is an *experience layer* on the XFCE/X11 session, not a
window-manager replacement yet.  XFCE remains a safe fallback while the
Wnck/X11 integration is verified on a booted desktop.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional

from .core import (
    SHELL_VERSION,
    DesktopApp,
    ShellConfig,
    collect_system_status,
    discover_apps,
    group_apps,
    load_config,
    panel_gradient_css,
    resolve_wallpaper,
    search_apps,
)
from .control_center import apply_xfce_wallpaper


def _load_gtk():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

    Wnck = None
    try:
        gi.require_version("Wnck", "3.0")
        from gi.repository import Wnck as wnck_module
        Wnck = wnck_module
    except Exception:
        Wnck = None  # Shell still works; only live window buttons disappear.
    return SimpleNamespace(Gdk=Gdk, GdkPixbuf=GdkPixbuf, Gio=Gio,
                           GLib=GLib, Gtk=Gtk, Wnck=Wnck)


def launch_argv_detached(argv: List[str]) -> bool:
    if not argv or not shutil.which(argv[0]):
        return False
    subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return True


def _command_exists(argv: List[str]) -> bool:
    return bool(argv and shutil.which(argv[0]))


class XorShell:
    def __init__(self, gtk, launcher_only: bool = False):
        self.g = gtk
        self.Gtk, self.Gdk, self.GLib, self.Gio = gtk.Gtk, gtk.Gdk, gtk.GLib, gtk.Gio
        self.launcher_only = launcher_only
        self.config: ShellConfig = load_config()
        self.apps: List[DesktopApp] = discover_apps()
        self.apps_by_id: Dict[str, DesktopApp] = {app.app_id: app for app in self.apps}
        self.groups = group_apps(self.apps)
        self.current_category = "All Apps"
        self.filtered_apps: List[DesktopApp] = list(self.apps)
        self.wnck_screen = None
        self.workspace_buttons = []
        self._dock_recentring = False

        self._install_css()
        self._apply_wallpaper()
        self.launcher = self._build_launcher()
        self.command_centre = self._build_command_centre()
        if not launcher_only:
            self.panel = self._build_panel()
            self.dock = self._build_dock()
            self._init_wnck()
            self._position_shell_windows()
            self.panel.show_all()
            self.dock.show_all()
            self.GLib.timeout_add_seconds(1, self._tick_clock)
            self.GLib.timeout_add_seconds(15, self._refresh_status)

    # ---------------------------------------------------------------- CSS
    def _install_css(self):
        palette = self.config.accent_palette
        css = ("@define-color xor_primary %s;\n"
               "@define-color xor_secondary %s;\n"
               "@define-color xor_highlight %s;\n" %
               (palette["primary"], palette["secondary"], palette["highlight"]))
        asset = Path(__file__).resolve().parent / "assets" / "style.css"
        try:
            css += asset.read_text(encoding="utf-8")
        except OSError:
            pass
        # Apply the saved panel opacity and reduce-motion preference.  An
        # appearance control that saves a value but never uses it is a
        # fake control; keep these declarations next to the base theme.
        css += "\n.shell-panel {\n  " + panel_gradient_css(self.config.panel_opacity) + "\n}\n"
        if self.config.reduce_motion:
            # GTK 3 CSS has no transition property; setting the toolkit
            # animation preference is the supported way to honour this.
            settings = self.Gtk.Settings.get_default()
            if settings is not None:
                settings.set_property("gtk-enable-animations", False)
        provider = self.Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        screen = self.Gdk.Screen.get_default()
        self.Gtk.StyleContext.add_provider_for_screen(
            screen, provider, self.Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _apply_wallpaper(self):
        """Apply the configured backdrop after XFCE has migrated its keys.

        xfdesktop's first-login migration can reset the single-image keys to
        desktop-base defaults even when /etc/skel supplies XOR values.  The
        running shell is the reliable owner of the live-session backdrop.
        """
        wallpaper = resolve_wallpaper(self.config.wallpaper)
        if wallpaper:
            apply_xfce_wallpaper(wallpaper)

    # ------------------------------------------------------------ helpers
    def _shell_window(self, title: str, dock_hint: bool = True):
        win = self.Gtk.Window(title=title)
        win.set_decorated(False)
        win.set_app_paintable(True)
        win.set_skip_taskbar_hint(True)
        win.set_skip_pager_hint(True)
        if dock_hint:
            win.set_type_hint(self.Gdk.WindowTypeHint.DOCK)
            win.set_keep_above(True)
        screen = win.get_screen()
        visual = screen.get_rgba_visual() if screen else None
        if visual:
            win.set_visual(visual)
        win.get_style_context().add_class("shell-window")
        win.connect("destroy", self.Gtk.main_quit)
        return win

    def _monitor_geometry(self):
        display = self.Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        return monitor.get_geometry()

    def _icon(self, icon_name: str, size: int):
        if icon_name and Path(icon_name).is_file():
            try:
                pixbuf = self.g.GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    icon_name, size, size, True)
                return self.Gtk.Image.new_from_pixbuf(pixbuf)
            except Exception:
                pass
        image = self.Gtk.Image.new_from_icon_name(
            icon_name or "application-x-executable", self.Gtk.IconSize.DIALOG)
        image.set_pixel_size(size)
        return image

    def _label(self, text: str, css: str = "", xalign: float = 0.0):
        label = self.Gtk.Label(label=text)
        label.set_xalign(xalign)
        if css:
            label.get_style_context().add_class(css)
        return label

    def _button(self, label: str, css: str, handler: Callable, tooltip: str = ""):
        button = self.Gtk.Button(label=label)
        button.get_style_context().add_class(css)
        if tooltip:
            button.set_tooltip_text(tooltip)
        button.connect("clicked", handler)
        return button

    def _notify_missing(self, command: str):
        dialog = self.Gtk.MessageDialog(
            transient_for=getattr(self, "panel", None), flags=0,
            message_type=self.Gtk.MessageType.WARNING,
            buttons=self.Gtk.ButtonsType.OK,
            text=f'"{command}" is not installed in this XOR image yet.')
        dialog.run()
        dialog.destroy()

    def run_command(self, argv: List[str]):
        if not launch_argv_detached(argv):
            self._notify_missing(argv[0] if argv else "command")

    def launch_app(self, app: DesktopApp):
        launched = False
        try:
            app_info = self.Gio.DesktopAppInfo.new(app.app_id)
            if app_info:
                launched = app_info.launch([], None)
        except Exception:
            launched = False
        if not launched:
            argv = app.launch_argv()
            launched = launch_argv_detached(argv)
        if not launched:
            self._notify_missing(app.name)
        self.launcher.hide()
        if self.launcher_only:
            self.Gtk.main_quit()

    def launch_by_id(self, app_id: str, fallback: Optional[List[str]] = None):
        app = self.apps_by_id.get(app_id)
        if app:
            self.launch_app(app)
        elif fallback:
            self.run_command(fallback)

    # ---------------------------------------------------------------- panel
    def _build_panel(self):
        win = self._shell_window("XOR Panel")
        frame = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL, spacing=10)
        frame.get_style_context().add_class("shell-panel")
        win.add(frame)

        frame.add(self._button("✦  XOR", "brand-button",
                               lambda _b: self.toggle_launcher(),
                               "Open XOR Launcher"))

        self.workspace_box = self.Gtk.Box(
            orientation=self.Gtk.Orientation.HORIZONTAL, spacing=5)
        for i in range(4):
            btn = self._button(str(i + 1), "workspace-button",
                                 lambda _b, n=i: self._activate_workspace(n),
                                 f"Workspace {i + 1}")
            self.workspace_buttons.append(btn)
            self.workspace_box.add(btn)
        frame.add(self.workspace_box)

        centre = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=0)
        self.clock_label = self._label("", "clock-label", 0.5)
        self.active_label = self._label("XOR OS · Aurora", "active-window-label", 0.5)
        centre.add(self.clock_label)
        centre.add(self.active_label)
        frame.pack_start(centre, True, True, 0)

        self.network_pill = self._label("Offline", "status-pill")
        self.battery_pill = self._label("AC power", "status-pill")
        frame.add(self.network_pill)
        frame.add(self.battery_pill)
        frame.add(self._button("✧", "panel-button",
                               lambda _b: self.toggle_command_centre(),
                               "XOR Command Centre"))
        frame.add(self._button("⬢", "panel-button",
                               lambda _b: self.launch_by_id(
                                   "xor-control-center.desktop", ["xor-control-center"]),
                               "XOR Control Centre"))
        frame.add(self._button("⏻", "panel-button", self._show_power_menu,
                               "Session and power"))
        self._tick_clock()
        self._refresh_status()
        return win

    # ------------------------------------------------------------------ dock
    def _build_dock(self):
        win = self._shell_window("XOR Dock")
        orientation = (self.Gtk.Orientation.VERTICAL
                       if self.config.dock_position == "left"
                       else self.Gtk.Orientation.HORIZONTAL)
        frame = self.Gtk.Box(orientation=orientation, spacing=8)
        frame.get_style_context().add_class("floating-dock")
        self.dock_frame = frame
        win.add(frame)

        launcher_btn = self.Gtk.Button()
        launcher_btn.get_style_context().add_class("brand-button")
        launcher_btn.set_tooltip_text("Open XOR Launcher")
        launcher_btn.add(self._label("✦", xalign=0.5))
        launcher_btn.connect("clicked", lambda _b: self.toggle_launcher())
        frame.add(launcher_btn)

        for app_id in self.config.pinned_apps:
            app = self.apps_by_id.get(app_id)
            if app:
                frame.add(self._dock_app_button(app))

        separator = self.Gtk.Separator(
            orientation=(self.Gtk.Orientation.HORIZONTAL
                         if orientation == self.Gtk.Orientation.VERTICAL
                         else self.Gtk.Orientation.VERTICAL))
        frame.add(separator)
        self.running_box = self.Gtk.Box(orientation=orientation, spacing=6)
        frame.add(self.running_box)

        trash = self.Gtk.Button()
        trash.get_style_context().add_class("dock-button")
        trash.set_tooltip_text("Open Trash in Files")
        trash.add(self._icon("user-trash", self.config.dock_icon_size))
        trash.connect("clicked", lambda _b: self.run_command(["Thunar", "trash:///"]))
        frame.add(trash)
        win.connect("size-allocate", self._centre_dock)
        return win

    def _dock_app_button(self, app: DesktopApp):
        button = self.Gtk.Button()
        button.get_style_context().add_class("dock-button")
        button.set_tooltip_text(app.name)
        button.add(self._icon(app.icon, self.config.dock_icon_size))
        button.connect("clicked", lambda _b, a=app: self.launch_app(a))
        return button

    def _centre_dock(self, _widget=None, _allocation=None):
        if self._dock_recentring or not hasattr(self, "dock"):
            return False
        self._dock_recentring = True
        try:
            geo = self._monitor_geometry()
            width = self.dock.get_allocated_width()
            height = self.dock.get_allocated_height()
            if self.config.dock_position == "left":
                x = geo.x + 14
                y = geo.y + max(0, (geo.height - height) // 2)
            else:
                x = geo.x + max(0, (geo.width - width) // 2)
                y = geo.y + max(0, geo.height - height - 14)
            self.dock.move(x, y)
        finally:
            self._dock_recentring = False
        return False

    def _position_shell_windows(self):
        geo = self._monitor_geometry()
        self.panel.set_size_request(geo.width, 58)
        self.panel.move(geo.x, geo.y)
        self.GLib.idle_add(self._centre_dock)

    # ------------------------------------------------------------- launcher
    def _build_launcher(self):
        win = self._shell_window("XOR Launcher", dock_hint=False)
        win.get_style_context().add_class("launcher-window")
        win.set_type_hint(self.Gdk.WindowTypeHint.DIALOG)
        win.connect("key-press-event", self._launcher_keypress)
        win.connect("delete-event", lambda w, _e: self._close_launcher())

        outer = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL)
        outer.set_halign(self.Gtk.Align.CENTER)
        outer.set_valign(self.Gtk.Align.CENTER)
        win.add(outer)

        card = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=18)
        card.get_style_context().add_class("launcher-card")
        card.set_size_request(1040, 700)
        outer.add(card)

        header = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL, spacing=16)
        title_box = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=3)
        title_box.add(self._label("Find everything.", "hero-title"))
        title_box.add(self._label("Applications, trusted XOR tools and your workspace.",
                                "hero-subtitle"))
        header.pack_start(title_box, True, True, 0)
        header.add(self._button("✕", "panel-button",
                                 lambda _b: self._close_launcher(), "Close (Esc)"))
        card.add(header)

        self.search_entry = self.Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search apps…  (Enter opens the first match)")
        self.search_entry.get_style_context().add_class("search-entry")
        self.search_entry.connect("search-changed", lambda _e: self._refresh_launcher())
        self.search_entry.connect("activate", lambda _e: self._launch_first_match())
        card.add(self.search_entry)

        self.category_box = self.Gtk.Box(
            orientation=self.Gtk.Orientation.HORIZONTAL, spacing=7)
        card.add(self.category_box)
        self._rebuild_category_pills()

        scroller = self.Gtk.ScrolledWindow()
        scroller.set_policy(self.Gtk.PolicyType.NEVER, self.Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        self.app_flow = self.Gtk.FlowBox()
        self.app_flow.set_max_children_per_line(5)
        self.app_flow.set_selection_mode(self.Gtk.SelectionMode.NONE)
        self.app_flow.set_homogeneous(True)
        self.app_flow.set_column_spacing(12)
        self.app_flow.set_row_spacing(12)
        scroller.add(self.app_flow)
        card.add(scroller)
        self.launcher_status = self._label("", "muted-label")
        card.add(self.launcher_status)
        self._refresh_launcher()
        return win

    def _rebuild_category_pills(self):
        for child in list(self.category_box.get_children()):
            self.category_box.remove(child)
        for name in self.groups:
            button = self._button(name, "category-pill",
                                  lambda _b, n=name: self._set_category(n))
            if name == self.current_category:
                button.get_style_context().add_class("active")
            self.category_box.add(button)
        self.category_box.show_all()

    def _set_category(self, name: str):
        self.current_category = name
        self._rebuild_category_pills()
        self._refresh_launcher()

    def _app_card(self, app: DesktopApp):
        button = self.Gtk.Button()
        button.get_style_context().add_class("app-card")
        button.set_tooltip_text(app.comment or app.name)
        box = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=7)
        icon = self._icon(app.icon, 48)
        icon.set_halign(self.Gtk.Align.CENTER)
        title = self._label(app.name, "app-card-title", 0.5)
        title.set_line_wrap(True)
        title.set_justify(self.Gtk.Justification.CENTER)
        comment_text = app.comment[:58] + ("…" if len(app.comment) > 58 else "")
        comment = self._label(comment_text, "app-card-comment", 0.5)
        comment.set_line_wrap(True)
        comment.set_justify(self.Gtk.Justification.CENTER)
        box.add(icon); box.add(title); box.add(comment)
        button.add(box)
        button.connect("clicked", lambda _b, a=app: self.launch_app(a))
        return button

    def _refresh_launcher(self):
        query = self.search_entry.get_text() if hasattr(self, "search_entry") else ""
        base = self.groups.get(self.current_category, self.apps)
        self.filtered_apps = search_apps(base, query)
        for child in list(self.app_flow.get_children()):
            self.app_flow.remove(child)
        for app in self.filtered_apps[:80]:
            self.app_flow.add(self._app_card(app))
        self.app_flow.show_all()
        count = len(self.filtered_apps)
        self.launcher_status.set_text(
            f"{count} app{'s' if count != 1 else ''} · category: {self.current_category} · Esc closes")

    def _launch_first_match(self):
        if self.filtered_apps:
            self.launch_app(self.filtered_apps[0])

    def _launcher_keypress(self, _widget, event):
        if event.keyval == self.Gdk.KEY_Escape:
            return self._close_launcher()
        return False

    def _close_launcher(self):
        self.launcher.hide()
        if self.launcher_only:
            self.Gtk.main_quit()
        return True

    def toggle_launcher(self):
        if self.launcher.get_visible():
            self.launcher.hide()
            return
        self.launcher.fullscreen()
        self.launcher.show_all()
        self.search_entry.set_text("")
        self._refresh_launcher()
        self.search_entry.grab_focus()

    # ------------------------------------------------------- command centre
    def _build_command_centre(self):
        win = self._shell_window("XOR Command Centre", dock_hint=False)
        win.set_type_hint(self.Gdk.WindowTypeHint.DIALOG)
        win.connect("delete-event", lambda w, _e: (w.hide(), True)[1])
        card = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=16)
        card.get_style_context().add_class("command-centre")
        card.set_size_request(430, 640)
        win.add(card)

        header = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL)
        title = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=2)
        title.add(self._label("Command Centre", "hero-title"))
        title.add(self._label("Beautiful controls, honest system state.", "hero-subtitle"))
        header.pack_start(title, True, True, 0)
        header.add(self._button("✕", "panel-button", lambda _b: win.hide(), "Close"))
        card.add(header)

        self.cc_status = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=10)
        card.add(self.cc_status)

        actions = [
            ("⌂  Files", ["Thunar"]),
            ("◉  Network settings", ["nm-connection-editor"]),
            ("♪  Sound", ["pavucontrol"]),
            ("▣  Displays", ["xfce4-display-settings"]),
            ("✦  Appearance", ["xor-control-center", "--page", "appearance"]),
            ("⬢  Security & Activity", ["xor-activity"]),
            ("⌁  Screenshot", ["xfce4-screenshooter"]),
            ("⚙  All settings", ["xfce4-settings-manager"]),
        ]
        grid = self.Gtk.Grid(column_spacing=10, row_spacing=10)
        grid.set_column_homogeneous(True)
        for index, (label, argv) in enumerate(actions):
            btn = self._button(label, "app-card",
                               lambda _b, a=argv: (self.run_command(a), win.hide()))
            grid.attach(btn, index % 2, index // 2, 1, 1)
        card.add(grid)
        self._refresh_command_centre()
        return win

    def _refresh_command_centre(self):
        if not hasattr(self, "cc_status"):
            return
        for child in list(self.cc_status.get_children()):
            self.cc_status.remove(child)
        status = collect_system_status()
        cards = [
            (status.network, status.hostname),
            (status.battery.label, "Battery / power"),
            (status.memory_label, "Live memory footprint"),
        ]
        for value, caption in cards:
            card = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=2)
            card.get_style_context().add_class("glass-card")
            card.add(self._label(value, "metric-value"))
            card.add(self._label(caption, "muted-label"))
            self.cc_status.add(card)
        self.cc_status.show_all()

    def toggle_command_centre(self):
        if self.command_centre.get_visible():
            self.command_centre.hide()
            return
        self._refresh_command_centre()
        geo = self._monitor_geometry()
        width = 462
        self.command_centre.move(geo.x + geo.width - width - 18, geo.y + 72)
        self.command_centre.show_all()

    # -------------------------------------------------------------------- Wnck
    def _init_wnck(self):
        if not self.g.Wnck:
            return
        try:
            self.wnck_screen = self.g.Wnck.Screen.get_default()
            self.wnck_screen.force_update()
            self.wnck_screen.connect("active-window-changed",
                                      lambda *_a: self._refresh_windows())
            self.wnck_screen.connect("window-opened",
                                      lambda *_a: self._refresh_windows())
            self.wnck_screen.connect("window-closed",
                                      lambda *_a: self._refresh_windows())
            self.wnck_screen.connect("active-workspace-changed",
                                      lambda *_a: self._refresh_windows())
            self._refresh_windows()
        except Exception:
            self.wnck_screen = None

    def _activate_workspace(self, index: int):
        if not self.wnck_screen:
            return
        workspaces = self.wnck_screen.get_workspaces()
        if index < len(workspaces):
            workspaces[index].activate(self.Gtk.get_current_event_time())

    def _refresh_windows(self):
        if not self.wnck_screen or not hasattr(self, "running_box"):
            return False
        active = self.wnck_screen.get_active_window()
        self.active_label.set_text(active.get_name() if active else "XOR OS · Aurora")
        active_ws = self.wnck_screen.get_active_workspace()
        workspaces = self.wnck_screen.get_workspaces()
        for i, button in enumerate(self.workspace_buttons):
            ctx = button.get_style_context()
            if active_ws and i < len(workspaces) and workspaces[i] == active_ws:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")
        for child in list(self.running_box.get_children()):
            self.running_box.remove(child)
        seen = set()
        for window in self.wnck_screen.get_windows():
            try:
                if window.is_skip_tasklist():
                    continue
                name = window.get_name() or "Window"
                key = window.get_class_group_name() or name
            except Exception:
                continue
            if key in seen:
                continue
            seen.add(key)
            button = self.Gtk.Button()
            button.get_style_context().add_class("dock-button")
            button.get_style_context().add_class("running")
            button.set_tooltip_text(name)
            pixbuf = None
            try:
                pixbuf = window.get_mini_icon()
            except Exception:
                pixbuf = None
            if pixbuf:
                button.add(self.Gtk.Image.new_from_pixbuf(pixbuf))
            else:
                button.add(self._icon("application-x-executable", 32))
            button.connect("clicked",
                           lambda _b, w=window: w.activate(self.Gtk.get_current_event_time()))
            self.running_box.add(button)
        self.running_box.show_all()
        self._centre_dock()
        return False

    # ------------------------------------------------------------------- misc
    def _tick_clock(self):
        fmt = "%a %d %b  ·  %H:%M:%S" if self.config.show_seconds else "%a %d %b  ·  %H:%M"
        if hasattr(self, "clock_label"):
            self.clock_label.set_text(datetime.now().strftime(fmt))
        return True

    def _refresh_status(self):
        status = collect_system_status()
        if hasattr(self, "network_pill"):
            self.network_pill.set_text("● " + status.network)
            self.battery_pill.set_text(status.battery.label)
        return True

    def _show_power_menu(self, widget):
        menu = self.Gtk.Menu()
        items = [
            ("Lock", ["xflock4"]),
            ("Suspend", ["systemctl", "suspend"]),
            ("Log Out…", ["xfce4-session-logout"]),
            ("Restart…", ["systemctl", "reboot"]),
            ("Shut Down…", ["systemctl", "poweroff"]),
        ]
        for label, argv in items:
            item = self.Gtk.MenuItem(label=label)
            item.connect("activate", lambda _i, a=argv: self.run_command(a))
            menu.add(item)
        menu.show_all()
        menu.popup_at_widget(widget, self.Gdk.Gravity.SOUTH_EAST,
                             self.Gdk.Gravity.NORTH_EAST, None)


def print_apps() -> int:
    apps = discover_apps()
    for app in apps:
        print(f"{app.app_id}\t{app.name}\t{app.primary_category}")
    print(f"# {len(apps)} applications", file=sys.stderr)
    return 0


def print_status() -> int:
    cfg = load_config()
    status = collect_system_status()
    payload = {
        "shell_version": SHELL_VERSION,
        "config": cfg.to_dict(),
        "system": {
            "hostname": status.hostname,
            "username": status.username,
            "os": status.os_label,
            "kernel": status.kernel,
            "battery": status.battery.label,
            "network": status.network,
            "memory": status.memory_label,
        },
        "display_available": bool(os.environ.get("DISPLAY")),
    }
    print(json.dumps(payload, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="xor-shell",
                                     description="XOR Shell — Aurora desktop experience")
    parser.add_argument("--launcher", action="store_true", help="open only the launcher")
    parser.add_argument("--list-apps", action="store_true", help="print discovered apps and exit")
    parser.add_argument("--status-json", action="store_true", help="print shell status JSON and exit")
    parser.add_argument("--version", action="version", version=SHELL_VERSION)
    args = parser.parse_args(argv)

    if args.list_apps:
        return print_apps()
    if args.status_json:
        return print_status()
    if not os.environ.get("DISPLAY"):
        print("xor-shell: no DISPLAY available. Try --list-apps or --status-json.",
              file=sys.stderr)
        return 2
    try:
        gtk = _load_gtk()
    except Exception as exc:
        print(f"xor-shell: GTK/PyGObject unavailable: {exc}", file=sys.stderr)
        return 3

    shell = XorShell(gtk, launcher_only=args.launcher)
    if args.launcher:
        shell.toggle_launcher()
    gtk.Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
