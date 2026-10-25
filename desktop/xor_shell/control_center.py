"""XOR Control Centre.

A first-party settings surface for things XOR actually owns today:
shell appearance, wallpapers, broker/security status and shortcuts to
trusted XFCE settings tools.  It deliberately does not fake toggles for
subsystems XOR has not integrated yet.  ``--text`` exposes the same
status data for terminals, tests and recovery sessions.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from .core import (
    ACCENTS,
    SHELL_VERSION,
    collect_system_status,
    discover_wallpapers,
    load_config,
    resolve_wallpaper,
    save_config,
)

PAGES = ("overview", "appearance", "security", "system", "about")


def apply_xfce_wallpaper(wallpaper_path: Path) -> bool:
    """Apply an XFCE desktop backdrop via xfconf-query if present."""
    if not shutil.which("xfconf-query") or not wallpaper_path.is_file():
        return False
    ok = True
    for workspace in ("workspace0", "workspace1"):
        prop = f"/backdrop/screen0/monitor0/{workspace}/last-image"
        result = subprocess.run(
            ["xfconf-query", "-c", "xfce4-desktop", "-p", prop,
             "-s", str(wallpaper_path), "--create", "-t", "string"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        ok = ok and result.returncode == 0
    return ok


def audit_summary(audit_path: Optional[str] = None) -> dict:
    """Small safe summary; never returns file contents."""
    try:
        # activity_core is a sibling in both repo and installed layouts.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import activity_core  # noqa: PLC0415
        records, ok, checked = activity_core.load(audit_path)
    except Exception as exc:  # viewer unavailable should not crash settings
        return {"available": False, "error": type(exc).__name__}
    return {
        "available": True,
        "records": len(records),
        "executed": len(activity_core.apply_filter(records, "executed")),
        "denied": len(activity_core.apply_filter(records, "denied")),
        "failed": len(activity_core.apply_filter(records, "failed")),
        "chain_valid": ok,
        "chain_checked": checked,
    }


def text_status() -> str:
    cfg = load_config()
    status = collect_system_status()
    audit = audit_summary()
    wallpaper = resolve_wallpaper(cfg.wallpaper)
    lines = [
        "XOR Control Centre",
        f"Shell:      Aurora {SHELL_VERSION}",
        f"Accent:     {cfg.accent_palette['label']} ({cfg.accent})",
        f"Wallpaper:  {wallpaper or cfg.wallpaper}",
        f"Dock:       {cfg.dock_position}, {cfg.dock_icon_size}px icons",
        f"Host:       {status.username}@{status.hostname}",
        f"Kernel:     {status.kernel}",
        f"Network:    {status.network}",
        f"Battery:    {status.battery.label}",
        f"Memory:     {status.memory_label}",
    ]
    if audit.get("available"):
        lines.append(
            "Broker log: {records} records · {executed} executed · "
            "{denied} denied · {failed} failed · chain valid: {chain}".format(
                records=audit["records"], executed=audit["executed"],
                denied=audit["denied"], failed=audit["failed"],
                chain=audit["chain_valid"]))
    else:
        lines.append("Broker log: unavailable in this environment")
    return "\n".join(lines)


def _run(command: List[str]) -> bool:
    if not command or not shutil.which(command[0]):
        return False
    subprocess.Popen(command, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def _load_gtk():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GdkPixbuf, Gtk
    return Gdk, GdkPixbuf, Gtk


class ControlCentre:  # pragma: no cover - GTK requires display
    def __init__(self, Gdk, GdkPixbuf, Gtk, initial_page: str):
        self.Gdk, self.GdkPixbuf, self.Gtk = Gdk, GdkPixbuf, Gtk
        self.config = load_config()
        self.wallpapers = discover_wallpapers()
        self.window = Gtk.Window(title="XOR Control Centre")
        self.window.set_default_size(1180, 760)
        self.window.connect("destroy", Gtk.main_quit)
        self._install_css()

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        outer.get_style_context().add_class("control-window")
        self.window.add(outer)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sidebar.set_size_request(238, -1)
        sidebar.add(self._label("✦ XOR", "hero-title"))
        sidebar.add(self._label("Control Centre", "muted-label"))
        line = Gtk.Box()
        line.get_style_context().add_class("accent-line")
        line.set_size_request(-1, 4)
        sidebar.add(line)
        outer.add(sidebar)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_hexpand(True)
        outer.add(self.stack)

        self.sidebar_buttons = {}
        labels = {
            "overview": "✧  _Overview",
            "appearance": "◐  _Appearance",
            "security": "⬢  _Security",
            "system": "⚙  S_ystem",
            "about": "◍  A_bout XOR",
        }
        for page in PAGES:
            btn = Gtk.Button(label=labels[page], use_underline=True)
            btn.get_style_context().add_class("sidebar-button")
            btn.connect("clicked", lambda _b, p=page: self.show_page(p))
            sidebar.add(btn)
            self.sidebar_buttons[page] = btn

        self.stack.add_named(self._overview_page(), "overview")
        self.stack.add_named(self._appearance_page(), "appearance")
        self.stack.add_named(self._security_page(), "security")
        self.stack.add_named(self._system_page(), "system")
        self.stack.add_named(self._about_page(), "about")
        self.show_page(initial_page if initial_page in PAGES else "overview")
        self.window.show_all()

    # --------------------------------------------------------------- helpers
    def _install_css(self):
        palette = self.config.accent_palette
        css = ("@define-color xor_primary %s;\n"
               "@define-color xor_secondary %s;\n"
               "@define-color xor_highlight %s;\n" %
               (palette["primary"], palette["secondary"], palette["highlight"]))
        style_path = Path(__file__).resolve().parent / "assets" / "style.css"
        try:
            css += style_path.read_text(encoding="utf-8")
        except OSError:
            pass
        provider = self.Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        self.Gtk.StyleContext.add_provider_for_screen(
            self.Gdk.Screen.get_default(), provider,
            self.Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _label(self, text, css="", wrap=True):
        label = self.Gtk.Label(label=text)
        label.set_xalign(0)
        label.set_line_wrap(wrap)
        if css:
            label.get_style_context().add_class(css)
        return label

    def _page(self, title, subtitle):
        scroller = self.Gtk.ScrolledWindow()
        scroller.set_policy(self.Gtk.PolicyType.NEVER, self.Gtk.PolicyType.AUTOMATIC)
        box = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_start(12); box.set_margin_end(12)
        box.add(self._label(title, "hero-title"))
        box.add(self._label(subtitle, "hero-subtitle"))
        line = self.Gtk.Box(); line.get_style_context().add_class("accent-line")
        line.set_size_request(-1, 4); box.add(line)
        scroller.add(box)
        return scroller, box

    def _card(self, title, body):
        card = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=5)
        card.get_style_context().add_class("glass-card")
        card.add(self._label(title, "metric-value"))
        card.add(self._label(body, "muted-label"))
        return card

    def _action(self, box, label, command, css="primary-button"):
        button = self.Gtk.Button(label=label)
        button.get_style_context().add_class(css)
        button.connect("clicked", lambda _b, c=command: _run(c))
        box.add(button)
        return button

    def show_page(self, page):
        self.stack.set_visible_child_name(page)
        for name, btn in self.sidebar_buttons.items():
            ctx = btn.get_style_context()
            ctx.add_class("active") if name == page else ctx.remove_class("active")

    # ----------------------------------------------------------------- pages
    def _overview_page(self):
        page, box = self._page("Welcome to your universe.",
                               "A calmer, more beautiful way to stay in control.")
        wallpaper = resolve_wallpaper(self.config.wallpaper)
        if wallpaper:
            try:
                pixbuf = self.GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(wallpaper), 980, 300, True)
                box.add(self.Gtk.Image.new_from_pixbuf(pixbuf))
            except Exception:
                pass
        status = collect_system_status()
        grid = self.Gtk.Grid(column_spacing=12, row_spacing=12)
        grid.set_column_homogeneous(True)
        cards = [
            (status.network, f"Signed in as {status.username} on {status.hostname}"),
            (status.battery.label, "Power state"),
            (status.memory_label, "Live memory footprint"),
            ("Permission-first", "Broker decisions stay explicit and auditable."),
        ]
        for i, (title, body) in enumerate(cards):
            grid.attach(self._card(title, body), i % 2, i // 2, 1, 1)
        box.add(grid)
        self._action(box, "Open XOR Shell", ["xor-shell"])
        self._action(box, "Run Broker Demo", ["xor-broker-demo-run"], "panel-button")
        return page

    def _appearance_page(self):
        page, box = self._page("Make it unmistakably yours.",
                               "Changes save to ~/.config/xor/shell.json.")
        box.add(self._label("Accent atmosphere"))
        accent_box = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL, spacing=8)
        self.accent_buttons = {}
        first = None
        for key, palette in ACCENTS.items():
            btn = self.Gtk.RadioButton.new_with_label_from_widget(first, palette["label"])
            first = first or btn
            btn.set_active(self.config.accent == key)
            btn.connect("toggled", lambda b, k=key: self._set_accent(k, b))
            accent_box.add(btn)
            self.accent_buttons[key] = btn
        box.add(accent_box)

        box.add(self._label("Dock icon size"))
        self.dock_scale = self.Gtk.Scale.new_with_range(
            self.Gtk.Orientation.HORIZONTAL, 40, 76, 2)
        self.dock_scale.set_value(self.config.dock_icon_size)
        self.dock_scale.set_digits(0)
        box.add(self.dock_scale)

        box.add(self._label("Panel opacity"))
        self.opacity_scale = self.Gtk.Scale.new_with_range(
            self.Gtk.Orientation.HORIZONTAL, 0.52, 0.96, 0.02)
        self.opacity_scale.set_value(self.config.panel_opacity)
        box.add(self.opacity_scale)

        self.reduce_motion = self.Gtk.Switch()
        self.reduce_motion.set_active(self.config.reduce_motion)
        motion_row = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL, spacing=12)
        motion_row.add(self._label("Reduce motion"))
        motion_row.add(self.reduce_motion)
        box.add(motion_row)

        box.add(self._label("Wallpapers"))
        flow = self.Gtk.FlowBox()
        flow.set_max_children_per_line(3)
        flow.set_selection_mode(self.Gtk.SelectionMode.NONE)
        for wallpaper in self.wallpapers:
            flow.add(self._wallpaper_button(wallpaper))
        box.add(flow)
        self.appearance_status = self._label("", "muted-label")
        box.add(self.appearance_status)
        apply_btn = self.Gtk.Button(label="Save appearance")
        apply_btn.get_style_context().add_class("primary-button")
        apply_btn.connect("clicked", lambda _b: self._save_appearance())
        box.add(apply_btn)
        return page

    def _set_accent(self, key, button):
        if button.get_active():
            self.config.accent = key

    def _wallpaper_button(self, wallpaper: Path):
        btn = self.Gtk.Button()
        btn.get_style_context().add_class("app-card")
        box = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=7)
        try:
            pixbuf = self.GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(wallpaper), 240, 135, True)
            box.add(self.Gtk.Image.new_from_pixbuf(pixbuf))
        except Exception:
            pass
        label = self._label(wallpaper.stem.replace("-", " ").title(), "app-card-title")
        label.set_xalign(0.5)
        box.add(label)
        btn.add(box)
        btn.connect("clicked", lambda _b, p=wallpaper: self._choose_wallpaper(p))
        return btn

    def _choose_wallpaper(self, wallpaper: Path):
        self.config.wallpaper = wallpaper.name
        self._save_appearance()

    def _save_appearance(self):
        self.config.dock_icon_size = int(self.dock_scale.get_value())
        self.config.panel_opacity = float(self.opacity_scale.get_value())
        self.config.reduce_motion = self.reduce_motion.get_active()
        target = save_config(self.config)
        wallpaper = resolve_wallpaper(self.config.wallpaper)
        applied = apply_xfce_wallpaper(wallpaper) if wallpaper else False
        self.appearance_status.set_text(
            f"Saved to {target}. Wallpaper applied via XFCE: {applied}. "
            "Restart XOR Shell to restyle its surfaces.")

    def _security_page(self):
        page, box = self._page("Trust, visible.",
                               "Every broker decision is explicit, local and reviewable.")
        summary = audit_summary()
        if summary.get("available"):
            grid = self.Gtk.Grid(column_spacing=12, row_spacing=12)
            grid.set_column_homogeneous(True)
            data = [
                (str(summary["records"]), "audited broker requests"),
                (str(summary["executed"]), "executed actions"),
                (str(summary["denied"]), "denied requests"),
                ("VALID" if summary["chain_valid"] else "BROKEN", "audit hash chain"),
            ]
            for i, (v, c) in enumerate(data):
                grid.attach(self._card(v, c), i % 2, i // 2, 1, 1)
            box.add(grid)
        else:
            box.add(self._card("Audit unavailable", "Run xor-broker demo or start the broker."))
        box.add(self._card("Assistant: OFF", "No model or cloud service is connected."))
        self._action(box, "Open Activity", ["xor-activity"])
        self._action(box, "Run Broker Demo", ["xor-broker-demo-run"], "panel-button")
        self._action(box, "Verify Audit Chain",
                     ["xfce4-terminal", "--hold", "-e", "xor-broker audit verify"],
                     "panel-button")
        return page

    def _system_page(self):
        page, box = self._page("Your machine, at a glance.",
                               "Live local status. No telemetry leaves this device.")
        status = collect_system_status()
        for title, body in [
            (f"{status.username}@{status.hostname}", status.os_label),
            (status.kernel, "Linux kernel"),
            (status.memory_label, "Memory"),
            (status.network, "Network"),
        ]:
            box.add(self._card(title, body))
        self._action(box, "Task Manager", ["xfce4-taskmanager"])
        self._action(box, "All XFCE Settings", ["xfce4-settings-manager"], "panel-button")
        self._action(box, "Terminal", ["xfce4-terminal"], "panel-button")
        return page

    def _about_page(self):
        page, box = self._page("XOR OS",
                               "Beauty with a permission boundary.")
        box.add(self._card(f"Aurora Shell {SHELL_VERSION}",
                           "Debian GNU/Linux foundation · XFCE fallback session · XOR Shell experience layer"))
        box.add(self._card("Our promise",
                           "Default-deny permissions. Per-write approval. Local, reviewable history. No silent AI actions."))
        box.add(self._card("Honest status",
                           "The broker core is test-proven. The shell now needs daily use on a booted ISO to earn the word “finished.”"))
        return page


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="xor-control-center",
                                     description="XOR Control Centre")
    parser.add_argument("--page", choices=PAGES, default="overview")
    parser.add_argument("--text", action="store_true", help="terminal status mode")
    parser.add_argument("--version", action="version", version=SHELL_VERSION)
    args = parser.parse_args(argv)
    if args.text or not os.environ.get("DISPLAY"):
        print(text_status())
        return 0
    try:
        Gdk, GdkPixbuf, Gtk = _load_gtk()
    except Exception as exc:
        print(f"xor-control-center: GTK unavailable: {exc}", file=sys.stderr)
        print(text_status())
        return 3
    ControlCentre(Gdk, GdkPixbuf, Gtk, args.page)
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
