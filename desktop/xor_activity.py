#!/usr/bin/env python3
"""XOR Activity Viewer: inspect broker approvals, denials and failures.

Modes:
  xor-activity            GTK window (desktop session)
  xor-activity --text     terminal table (works over SSH / serial console)
  xor-activity --verify   check the audit hash chain, exit 0/1

Keyboard contract (GTK mode): Ctrl+F focuses search, Ctrl+1..4 switch
filters (All/Executed/Denied/Failed), Ctrl+R reloads, Esc clears the
search, list is fully arrow-key navigable with visible focus.  The
viewer displays only what the log contains - metadata and digests,
never file contents (the broker does not record them).
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import activity_core  # noqa: E402


def run_text(audit_path, filter_name, query) -> int:
    records, ok, n = activity_core.load(audit_path)
    shown = activity_core.search(activity_core.apply_filter(records, filter_name), query)
    print(activity_core.format_text_table(shown))
    print(f"\n{len(shown)}/{len(records)} records shown; "
          f"hash chain valid: {ok} ({n} verified)")
    return 0 if ok else 1


def run_gtk(audit_path) -> int:  # pragma: no cover - needs a display
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, Gtk

    state = {"filter": "all", "query": "", "refreshing": False}

    window = Gtk.Window(title="XOR Activity — broker audit log")
    window.set_default_size(980, 560)
    window.connect("destroy", Gtk.main_quit)

    root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    root_box.set_margin_top(8); root_box.set_margin_bottom(8)
    root_box.set_margin_start(8); root_box.set_margin_end(8)
    window.add(root_box)

    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    root_box.add(toolbar)
    buttons = {}
    for name in ("all", "executed", "denied", "failed"):
        btn = Gtk.ToggleButton(label=name.capitalize())
        btn.set_tooltip_text(f"Show {name} (Ctrl+{list(('all','executed','denied','failed')).index(name)+1})")
        buttons[name] = btn
        toolbar.add(btn)
    search_entry = Gtk.SearchEntry()
    search_entry.set_placeholder_text("Filter text (Ctrl+F)…")
    toolbar.pack_start(search_entry, True, True, 0)
    reload_btn = Gtk.Button(label="_Reload")
    toolbar.add(reload_btn)

    store = Gtk.ListStore(int, str, str, str, str, str, str, str, str)
    tree = Gtk.TreeView(model=store)
    tree.set_search_entry(search_entry)
    columns = [("#", 0), ("Time (UTC)", 1), ("Tool", 2), ("Operation", 3),
               ("Decision", 4), ("Confirm", 5), ("Result", 6), ("Reason", 7),
               ("Resource", 8)]
    for title, idx in columns:
        tree.append_column(Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=idx))
    scroller = Gtk.ScrolledWindow()
    scroller.add(tree)
    root_box.pack_start(scroller, True, True, 0)

    status = Gtk.Label(label="")
    status.set_xalign(0)
    root_box.add(status)

    def refresh(*_args):
        records, ok, n = activity_core.load(audit_path)
        shown = activity_core.search(
            activity_core.apply_filter(records, state["filter"]), state["query"])
        store.clear()
        for r in shown:
            store.append([r["seq"], r["timestamp"].replace("T", " ")[:19], r["tool"],
                          r["operation"], r["decision"], str(r["confirmation"]),
                          r["result"], r["reason"], r["resource"]])
        # set_active() emits "toggled".  Suppress those callbacks
        # while refreshing the radio-like toggle-button group.
        state["refreshing"] = True
        try:
            for name, btn in buttons.items():
                btn.set_active(name == state["filter"])
        finally:
            state["refreshing"] = False
        status.set_text(f"{len(shown)}/{len(records)} records · "
                        f"hash chain: {'VALID' if ok else 'BROKEN'} ({n} verified)")

    def set_filter(name):
        def handler(btn):
            if state["refreshing"]:
                return
            # Keep one filter selected if the active toggle is clicked off.
            if not btn.get_active():
                refresh()
                return
            state["filter"] = name
            refresh()
        return handler

    for name, btn in buttons.items():
        btn.connect("toggled", set_filter(name))
    search_entry.connect("search-changed",
                         lambda e: (state.update(query=e.get_text()), refresh()))
    reload_btn.connect("clicked", refresh)

    def on_key(_w, event):
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        if ctrl and event.keyval in (Gdk.KEY_f, Gdk.KEY_F):
            search_entry.grab_focus(); return True
        if ctrl and event.keyval in (Gdk.KEY_r, Gdk.KEY_R):
            refresh(); return True
        names = ["all", "executed", "denied", "failed"]
        if ctrl and event.keyval in (Gdk.KEY_1, Gdk.KEY_2, Gdk.KEY_3, Gdk.KEY_4):
            state["filter"] = names[event.keyval - Gdk.KEY_1]; refresh(); return True
        if event.keyval == Gdk.KEY_Escape:
            search_entry.set_text(""); tree.grab_focus(); return True
        return False

    window.connect("key-press-event", on_key)
    refresh()
    tree.grab_focus()
    window.show_all()
    Gtk.main()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="xor-activity", description=__doc__)
    parser.add_argument("--audit", help="audit log path (default: XDG state)")
    parser.add_argument("--text", action="store_true", help="terminal mode")
    parser.add_argument("--verify", action="store_true", help="verify hash chain only")
    parser.add_argument("--filter", default="all",
                        choices=["all", "executed", "denied", "failed"])
    parser.add_argument("--search", default="")
    args = parser.parse_args(argv)

    if args.verify:
        _records, ok, n = activity_core.load(args.audit)
        print(f"hash chain valid: {ok} ({n} records)")
        return 0 if ok else 1
    if args.text or not os.environ.get("DISPLAY"):
        return run_text(args.audit, args.filter, args.search)
    return run_gtk(args.audit)


if __name__ == "__main__":
    raise SystemExit(main())
