# XOR desktop theme (baseline)

XFCE defaults for every new user, staged into `/etc/skel/.config/xfce4/`
by the live-build hook. Palette: deep slate (`#101826`→`#0a0e16`) with
light-sea-green teal (`#20b2aa`) accent, Inter type, Arc-Dark widgets,
Papirus-Dark icons.

* `wallpaper.png` — regenerate with `python3 tools/make_wallpaper.py`.
* `xfce4/xfconf/xfce-perchannel-xml/*.xml` — xsettings (theme/fonts),
  xfwm4 (window borders), xfce4-desktop (backdrop).

Deliberately **not** customised yet: the XFCE panel layout. Hand-written
panel xfconf is fragile to produce untested; the baseline keeps the
distribution panel and exposes XOR tools via the applications menu and
desktop files. Panel branding is a Milestone-2 polish task to be done
on a running desktop where the result can be eyeballed.
