"""UI-independent core for the XOR Shell.

The GTK layer intentionally stays thin: application discovery, search,
configuration and system-status parsing live here where they can be
unit-tested without a display.  That matters because a beautiful shell
still has to be reliable enough to replace the fallback XFCE panel.
"""

from __future__ import annotations

import configparser
import json
import os
import platform
import shlex
import socket
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

APP_ID = "io.xor.Shell"
SHELL_VERSION = "0.2.0"

ACCENTS: Dict[str, Dict[str, str]] = {
    "aurora": {
        "label": "Aurora Teal",
        "primary": "#2dd4bf",
        "secondary": "#8b5cf6",
        "highlight": "#f5c451",
    },
    "royal": {
        "label": "Royal Violet",
        "primary": "#a78bfa",
        "secondary": "#f472b6",
        "highlight": "#f5c451",
    },
    "solar": {
        "label": "Solar Gold",
        "primary": "#f5c451",
        "secondary": "#fb7185",
        "highlight": "#2dd4bf",
    },
    "rose": {
        "label": "Rose Quartz",
        "primary": "#fb7185",
        "secondary": "#a78bfa",
        "highlight": "#67e8f9",
    },
}

DEFAULT_PINNED_APPS: Tuple[str, ...] = (
    "Thunar.desktop",
    "firefox-esr.desktop",
    "org.mozilla.firefox.desktop",
    "xfce4-terminal.desktop",
    "mousepad.desktop",
    "xor-activity.desktop",
    "xor-control-center.desktop",
    "xfce4-taskmanager.desktop",
)

_CATEGORY_LABELS = (
    ("Settings", "Settings"),
    ("System", "System"),
    ("Network", "Internet"),
    ("WebBrowser", "Internet"),
    ("Office", "Office"),
    ("AudioVideo", "Media"),
    ("Graphics", "Creative"),
    ("Development", "Development"),
    ("Game", "Games"),
    ("Utility", "Utilities"),
)


@dataclass(frozen=True)
class DesktopApp:
    """A launchable desktop application discovered from a .desktop file."""

    app_id: str
    name: str
    comment: str
    exec_cmd: str
    icon: str
    categories: Tuple[str, ...] = ()
    keywords: Tuple[str, ...] = ()
    desktop_file: str = ""
    terminal: bool = False

    @property
    def primary_category(self) -> str:
        cats = set(self.categories)
        for raw, label in _CATEGORY_LABELS:
            if raw in cats:
                return label
        return "Applications"

    def launch_argv(self) -> List[str]:
        """Convert an Exec= line to argv, removing desktop field codes.

        The GTK layer prefers Gio.DesktopAppInfo, which understands the
        full Desktop Entry spec.  This is a safe fallback for unusual
        entries and for the headless smoke CLI.
        """
        field_codes = {"%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N", "%i", "%c", "%k", "%v", "%m"}
        parts: List[str] = []
        for token in shlex.split(self.exec_cmd):
            if token in field_codes:
                continue
            if token == "%%":
                token = "%"
            parts.append(token)
        if self.terminal and parts:
            return ["xfce4-terminal", "-e", shlex.join(parts)]
        return parts


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split_list(value: str) -> Tuple[str, ...]:
    return tuple(item.strip() for item in value.split(";") if item.strip())


def parse_desktop_file(path: str | Path) -> Optional[DesktopApp]:
    """Parse one application entry.  Returns None for hidden/invalid rows."""
    path = Path(path)
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # desktop keys are case-sensitive
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError, OSError):
        return None
    if "Desktop Entry" not in parser:
        return None
    entry = parser["Desktop Entry"]
    if entry.get("Type", "Application") != "Application":
        return None
    if _truthy(entry.get("NoDisplay", "false")) or _truthy(entry.get("Hidden", "false")):
        return None
    name = entry.get("Name", "").strip()
    exec_cmd = entry.get("Exec", "").strip()
    if not name or not exec_cmd:
        return None
    return DesktopApp(
        app_id=path.name,
        name=name,
        comment=entry.get("Comment", entry.get("GenericName", "")).strip(),
        exec_cmd=exec_cmd,
        icon=entry.get("Icon", "application-x-executable").strip(),
        categories=_split_list(entry.get("Categories", "")),
        keywords=_split_list(entry.get("Keywords", "")),
        desktop_file=str(path),
        terminal=_truthy(entry.get("Terminal", "false")),
    )


def application_directories(environ: Mapping[str, str] | None = None,
                              extra_dirs: Sequence[str | Path] = ()) -> List[Path]:
    """Desktop-entry search path, highest priority first."""
    env = os.environ if environ is None else environ
    dirs: List[Path] = []
    data_home = env.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    dirs.append(Path(data_home) / "applications")
    data_dirs = env.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    for root in data_dirs.split(":"):
        if root:
            dirs.append(Path(root) / "applications")
    # Project-local entries make repo development easy and let first-party
    # XOR tools appear even before an OS image is installed.
    for extra in extra_dirs:
        dirs.append(Path(extra))
    # Package-relative fallback: desktop/xor_shell -> desktop/applications
    dirs.append(Path(__file__).resolve().parents[1] / "applications")

    unique: List[Path] = []
    seen = set()
    for directory in dirs:
        key = str(directory)
        if key not in seen:
            unique.append(directory)
            seen.add(key)
    return unique


def discover_apps(environ: Mapping[str, str] | None = None,
                  extra_dirs: Sequence[str | Path] = ()) -> List[DesktopApp]:
    found: Dict[str, DesktopApp] = {}
    for directory in application_directories(environ, extra_dirs):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.desktop")):
            app = parse_desktop_file(path)
            if app and app.app_id not in found:
                found[app.app_id] = app
    return sorted(found.values(), key=lambda a: (a.name.casefold(), a.app_id))


def search_apps(apps: Iterable[DesktopApp], query: str) -> List[DesktopApp]:
    query = query.strip().casefold()
    items = list(apps)
    if not query:
        return sorted(items, key=lambda a: a.name.casefold())

    scored: List[Tuple[int, str, DesktopApp]] = []
    for app in items:
        hay_name = app.name.casefold()
        hayrest = " ".join((app.comment, app.exec_cmd, " ".join(app.categories),
                            " ".join(app.keywords))).casefold()
        if hay_name == query:
            score = 0
        elif hay_name.startswith(query):
            score = 1
        elif query in hay_name:
            score = 2
        elif any(word.startswith(query) for word in hayrest.split()):
            score = 3
        elif query in hayrest:
            score = 4
        else:
            continue
        scored.append((score, app.name.casefold(), app))
    scored.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in scored]


def group_apps(apps: Iterable[DesktopApp]) -> Dict[str, List[DesktopApp]]:
    groups: Dict[str, List[DesktopApp]] = {"All Apps": []}
    for app in sorted(apps, key=lambda a: a.name.casefold()):
        groups["All Apps"].append(app)
        groups.setdefault(app.primary_category, []).append(app)
    return groups


@dataclass
class ShellConfig:
    accent: str = "aurora"
    wallpaper: str = "xor-royal-aurora.jpg"
    dock_position: str = "bottom"
    dock_icon_size: int = 54
    panel_opacity: float = 0.82
    reduce_motion: bool = False
    show_seconds: bool = False
    pinned_apps: List[str] = field(default_factory=lambda: list(DEFAULT_PINNED_APPS))

    @classmethod
    def from_dict(cls, data: Mapping[str, object] | None) -> "ShellConfig":
        cfg = cls()
        if not data:
            return cfg
        if str(data.get("accent", "")) in ACCENTS:
            cfg.accent = str(data["accent"])
        if isinstance(data.get("wallpaper"), str) and data["wallpaper"]:
            # Config stores a wallpaper identifier/name, not an arbitrary
            # shell command.  Path traversal is stripped to a basename.
            cfg.wallpaper = Path(str(data["wallpaper"])).name
        if data.get("dock_position") in {"bottom", "left"}:
            cfg.dock_position = str(data["dock_position"])
        try:
            cfg.dock_icon_size = max(40, min(76, int(data.get("dock_icon_size", cfg.dock_icon_size))))
        except (TypeError, ValueError):
            pass
        try:
            cfg.panel_opacity = max(0.52, min(0.96, float(data.get("panel_opacity", cfg.panel_opacity))))
        except (TypeError, ValueError):
            pass
        cfg.reduce_motion = bool(data.get("reduce_motion", cfg.reduce_motion))
        cfg.show_seconds = bool(data.get("show_seconds", cfg.show_seconds))
        pinned = data.get("pinned_apps")
        if isinstance(pinned, list) and all(isinstance(x, str) and x.endswith(".desktop") for x in pinned):
            cfg.pinned_apps = pinned[:14]
        return cfg

    @property
    def accent_palette(self) -> Dict[str, str]:
        return ACCENTS[self.accent]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def config_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    root = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(root) / "xor" / "shell.json"


def load_config(path: str | Path | None = None) -> ShellConfig:
    target = Path(path) if path else config_path()
    try:
        return ShellConfig.from_dict(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return ShellConfig()


def save_config(cfg: ShellConfig, path: str | Path | None = None) -> Path:
    target = Path(path) if path else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def wallpaper_search_dirs() -> List[Path]:
    dirs = [Path("/usr/share/xor/wallpapers")]
    package_parent = Path(__file__).resolve().parents[1]
    dirs.append(package_parent / "theme" / "wallpapers")
    dirs.append(Path.home() / ".local" / "share" / "xor" / "wallpapers")
    return dirs


def discover_wallpapers(extra_dirs: Sequence[str | Path] = ()) -> List[Path]:
    found: Dict[str, Path] = {}
    for directory in list(extra_dirs) + wallpaper_search_dirs():
        d = Path(directory)
        if not d.is_dir():
            continue
        for pattern in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            for path in sorted(d.glob(pattern)):
                found.setdefault(path.name, path)
    return sorted(found.values(), key=lambda p: p.name)


def resolve_wallpaper(wallpaper_name: str) -> Optional[Path]:
    wanted = Path(wallpaper_name).name
    for path in discover_wallpapers():
        if path.name == wanted:
            return path
    return None


@dataclass(frozen=True)
class BatteryStatus:
    percent: Optional[int] = None
    state: str = "Unknown"

    @property
    def label(self) -> str:
        if self.percent is None:
            return "AC power"
        suffix = " charging" if self.state.lower() == "charging" else ""
        return f"{self.percent}%{suffix}"


def parse_battery(root: str | Path = "/sys/class/power_supply") -> BatteryStatus:
    base = Path(root)
    if not base.exists():
        return BatteryStatus()
    for supply in sorted(base.glob("BAT*")):
        try:
            percent = int((supply / "capacity").read_text(encoding="utf-8").strip())
            state = (supply / "status").read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            continue
        return BatteryStatus(max(0, min(100, percent)), state)
    return BatteryStatus()


def memory_summary(meminfo_text: str) -> Tuple[Optional[float], Optional[float]]:
    values: Dict[str, float] = {}
    for line in meminfo_text.splitlines():
        key, _, rest = line.partition(":")
        if key in {"MemTotal", "MemAvailable"}:
            try:
                # /proc/meminfo reports kB (KiB); convert KiB -> GiB.
                values[key] = float(rest.strip().split()[0]) / (1024.0 * 1024.0)
            except (ValueError, IndexError):
                pass
    if "MemTotal" not in values or "MemAvailable" not in values:
        return None, None
    total = values["MemTotal"]
    used = max(0.0, total - values["MemAvailable"])
    return round(used, 1), round(total, 1)


def network_label(root: str | Path = "/sys/class/net") -> str:
    base = Path(root)
    if not base.exists():
        return "Offline"
    active: List[str] = []
    for iface in sorted(base.iterdir()):
        if iface.name == "lo":
            continue
        try:
            state = (iface / "operstate").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if state == "up":
            active.append(iface.name)
    if not active:
        return "Offline"
    first = active[0]
    if first.startswith(("wl", "wlan")):
        return "Wi‑Fi connected"
    return "Network connected"


@dataclass(frozen=True)
class SystemStatus:
    hostname: str
    username: str
    os_label: str
    kernel: str
    battery: BatteryStatus
    network: str
    memory_used_gib: Optional[float]
    memory_total_gib: Optional[float]

    @property
    def memory_label(self) -> str:
        if self.memory_used_gib is None or self.memory_total_gib is None:
            return "Memory unavailable"
        return f"{self.memory_used_gib:.1f} / {self.memory_total_gib:.1f} GiB memory"


def collect_system_status() -> SystemStatus:
    mem_text = ""
    try:
        mem_text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        pass
    used, total = memory_summary(mem_text)
    return SystemStatus(
        hostname=socket.gethostname(),
        username=os.environ.get("USER") or Path.home().name,
        os_label="XOR OS · Aurora Shell preview",
        kernel=platform.release(),
        battery=parse_battery(),
        network=network_label(),
        memory_used_gib=used,
        memory_total_gib=total,
    )
