"""XOR Shell core tests: app discovery, config, status, wallpapers."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "broker", "src"))

from xor_shell import control_center  # noqa: E402
from xor_shell.core import (  # noqa: E402
    DesktopApp,
    ShellConfig,
    discover_apps,
    discover_wallpapers,
    group_apps,
    load_config,
    memory_summary,
    network_label,
    panel_gradient_css,
    parse_battery,
    parse_desktop_file,
    save_config,
    search_apps,
)


def write_desktop(directory: Path, filename: str, **fields) -> Path:
    path = directory / filename
    body = {
        "Type": "Application",
        "Name": filename.removesuffix(".desktop").title(),
        "Exec": "/bin/true",
        "Icon": "application-x-executable",
        "Categories": "Utility;",
    }
    body.update(fields)
    path.write_text("[Desktop Entry]\n" + "\n".join(f"{k}={v}" for k, v in body.items()) + "\n")
    return path


class TestDesktopDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_search_and_group(self):
        files = write_desktop(
            self.dir, "files.desktop", Name="Files", Comment="Browse documents",
            Exec="Thunar %U", Icon="system-file-manager",
            Categories="System;FileTools;", Keywords="folder;documents;")
        browser = write_desktop(
            self.dir, "browser.desktop", Name="Aurora Browser",
            Exec="firefox %u", Categories="Network;WebBrowser;", Keywords="web;")
        write_desktop(self.dir, "hidden.desktop", NoDisplay="true")

        app = parse_desktop_file(files)
        self.assertEqual(app.name, "Files")
        self.assertEqual(app.launch_argv(), ["Thunar"])
        self.assertEqual(app.primary_category, "System")

        apps = discover_apps(environ={"XDG_DATA_HOME": str(self.dir / "empty"),
                                      "XDG_DATA_DIRS": ""},
                             extra_dirs=[self.dir])
        names = [a.name for a in apps]
        self.assertIn("Files", names)
        self.assertIn("Aurora Browser", names)
        self.assertNotIn("Hidden", names)
        self.assertEqual(search_apps([app, parse_desktop_file(browser)], "doc")[0].name, "Files")
        groups = group_apps(apps)
        self.assertIn("Internet", groups)

    def test_terminal_field_codes_are_removed(self):
        app = DesktopApp("term.desktop", "Term", "", "demo %F --name %c %%",
                         "terminal", terminal=True)
        self.assertEqual(app.launch_argv(),
                         ["xfce4-terminal", "-e", "demo --name %"])


class TestConfigAndStatus(unittest.TestCase):
    def test_config_sanitizes_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shell.json"
            cfg = ShellConfig.from_dict({
                "accent": "solar",
                "wallpaper": "../../evil/../xor-royal-aurora.jpg",
                "dock_icon_size": 999,
                "panel_opacity": 0.1,
                "pinned_apps": ["Thunar.desktop", "bad"],
            })
            self.assertEqual(cfg.wallpaper, "xor-royal-aurora.jpg")
            self.assertEqual(cfg.dock_icon_size, 76)
            self.assertEqual(cfg.panel_opacity, 0.52)
            # Invalid mixed pinned list falls back to defaults rather than
            # retaining a malformed entry.
            self.assertNotIn("bad", cfg.pinned_apps)
            cfg.pinned_apps = ["Thunar.desktop"]
            save_config(cfg, path)
            loaded = load_config(path)
            self.assertEqual(loaded.accent, "solar")
            self.assertEqual(json.loads(path.read_text())["pinned_apps"], ["Thunar.desktop"])

    def test_malformed_config_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shell.json"
            path.write_text('["not", "an", "object"]', encoding="utf-8")
            cfg = load_config(path)
            self.assertEqual(cfg.accent, "aurora")
            self.assertEqual(cfg.wallpaper, "xor-royal-aurora.jpg")

    def test_panel_opacity_is_actually_emitted(self):
        css = panel_gradient_css(0.70)
        self.assertIn("rgba(10, 16, 30, 0.70)", css)
        # Values outside the UI range are clamped, not trusted.
        self.assertIn("rgba(10, 16, 30, 0.52)", panel_gradient_css(0.1))
        self.assertIn("rgba(10, 16, 30, 0.96)", panel_gradient_css(2.0))

    def test_battery_memory_network_parsers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bat = root / "BAT0"
            bat.mkdir()
            (bat / "capacity").write_text("84\n")
            (bat / "status").write_text("Charging\n")
            self.assertEqual(parse_battery(root).label, "84% charging")

            net = root / "net" / "wlan0"
            net.mkdir(parents=True)
            (net / "operstate").write_text("up\n")
            self.assertEqual(network_label(root / "net"), "Wi‑Fi connected")

        used, total = memory_summary("MemTotal:       16384000 kB\nMemAvailable:    8192000 kB\n")
        self.assertEqual((used, total), (7.8, 15.6))

    def test_repository_wallpapers_are_discoverable(self):
        paths = discover_wallpapers()
        names = {p.name for p in paths}
        self.assertIn("xor-royal-aurora.jpg", names)
        self.assertIn("xor-midnight-x.png", names)

    def test_control_center_text_is_useful_and_safe(self):
        text = control_center.text_status()
        self.assertIn("XOR Control Centre", text)
        self.assertIn("Aurora", text)
        self.assertIn("Broker log:", text)


if __name__ == "__main__":
    unittest.main()
