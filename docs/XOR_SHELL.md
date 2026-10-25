# XOR Shell — Aurora desktop experience

Aurora is XOR’s original, lavish desktop layer: glass surfaces, aurora
gradients, a centred floating dock and a fast, keyboard-first launcher.
It runs over the proven XFCE/X11 session while it matures. XFWM still
manages windows and XFCE remains the fallback if the shell is closed.

## Surfaces

| Surface | What it does |
|---|---|
| Glass top panel | XOR launcher, workspaces, clock, active window, network/battery pills, Command Centre, Control Centre, session menu |
| Floating dock | Pinned applications, live running-window buttons through optional Wnck, Files Trash shortcut |
| Full-screen launcher | Discovers real `.desktop` apps; weighted search, category pills, app cards, Enter launches first match, Esc closes |
| Command Centre | Live local network/battery/memory cards plus Files, network, sound, display, screenshot, appearance, security and settings actions |
| Control Centre | Overview, Appearance, Security, System and About pages; accents, wallpaper selection, dock size/opacity, broker audit summary |

## Run from the repository

A graphical Linux session with Python GTK (`python3-gi`, GTK 3) is required:

```sh
PYTHONPATH=desktop:broker/src python3 -m xor_shell
PYTHONPATH=desktop:broker/src python3 -m xor_shell --launcher
PYTHONPATH=desktop:broker/src python3 -m xor_shell.control_center
```

Headless development commands work anywhere:

```sh
PYTHONPATH=desktop:broker/src python3 -m xor_shell --list-apps
PYTHONPATH=desktop:broker/src python3 -m xor_shell --status-json
PYTHONPATH=desktop:broker/src python3 -m xor_shell.control_center --text
tools/test.sh
```

## Configuration

Aurora saves validated settings to `~/.config/xor/shell.json`:

* accent: Aurora Teal, Royal Violet, Solar Gold or Rose Quartz
* wallpaper basename (path traversal is sanitised)
* dock position/icon size, panel opacity
* reduce-motion and clock-seconds preferences
* up to 14 pinned `.desktop` application IDs

Installed wallpapers live in `/usr/share/xor/wallpapers/`; repository
development reads `desktop/theme/wallpapers/` as a fallback.

## Keyboard contract

* Launcher search receives focus immediately.
* Enter opens the best match; Esc closes.
* Category and app controls are native GTK buttons with visible focus.
* Control Centre sidebar uses Alt-key mnemonics.
* Broker GTK confirmations remain separate and deny-focused; Aurora
  never turns a visual gesture into a permission approval.

## Architecture

```text
xor_shell/core.py           testable discovery/search/config/status logic
xor_shell/app.py            GTK panel, dock, launcher, command centre
xor_shell/control_center.py GTK settings + --text status
xor_shell/assets/style.css  Aurora visual tokens/classes
desktop/theme/              wallpapers, logo, XFCE defaults
desktop/applications/       menu and session-autostart entries
```

Application launching prefers `Gio.DesktopAppInfo`; the core module
contains a tested fallback that strips desktop-entry field codes from
`Exec=` lines. Wnck is optional: without it, the shell remains usable
but live running-window/workspace integration is unavailable.

## Honest limits

* This is an experience layer, not yet a window-manager replacement.
* GTK surfaces were compile- and logic-tested in the headless sandbox;
  they still require daily-use verification on a booted ISO.
* Translucent GTK/CSS surfaces provide the glass look through XFWM’s
  compositor; this is not claimed to be a true background-blur compositor.
* XFCE panel is retained as fallback during the preview, so first boot
  may show both shells until Phase 2’s replacement gate passes.
