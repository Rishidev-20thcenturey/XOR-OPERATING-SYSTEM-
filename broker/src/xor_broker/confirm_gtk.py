#!/usr/bin/env python3
"""GTK 3 confirmation provider for the desktop session.

Keyboard contract (Milestone 2):
  * Focus starts on **Deny**.  Pressing Enter/Space immediately denies.
  * Escape, closing the window, or 120 s timeout all deny.
  * Alt+D = Deny, Alt+A = Approve Once (mnemonics, underlined in UI).
  * Tab order: details -> Deny -> Approve Once; visible focus via theme.
There is no "always approve" control, by design.

Used by the broker daemon on the XOR desktop:
    ConfirmationManager(GtkConfirmation())
Falls back to fail-closed behaviour: any GTK error returns False.
Not exercised in headless CI (no display); syntax-checked and reviewed,
marked as requiring on-desktop verification in docs/TEST_RESULTS.md.
"""

from __future__ import annotations

from .confirm import CONFIRMATION_TTL_SECONDS, ConfirmationPrompt


class GtkConfirmation:
    def confirm(self, prompt: ConfirmationPrompt) -> bool:
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import GLib, Gtk
        except Exception:
            return False  # no desktop -> fail closed

        result = {"approved": False}
        dialog = Gtk.Dialog(title="XOR Broker — confirmation required")
        dialog.set_modal(True)
        dialog.set_default_size(520, -1)

        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        heading = Gtk.Label()
        heading.set_markup(
            f"<b>{GLib.markup_escape_text(prompt.tool)}</b> wants to "
            f"<b>{GLib.markup_escape_text(prompt.operation)}</b>")
        heading.set_xalign(0)
        box.add(heading)

        details = Gtk.Label(label=(
            f"Target:  {prompt.target}\n"
            f"Change:  {prompt.description or '(no description provided)'}\n"
            f"Preview: {prompt.preview}\n\n"
            "This approval applies only to this one action. "
            "When in doubt, choose Deny."))
        details.set_xalign(0)
        details.set_line_wrap(True)
        details.set_selectable(True)
        box.add(details)

        deny_btn = dialog.add_button("_Deny", Gtk.ResponseType.REJECT)
        approve_btn = dialog.add_button("_Approve Once", Gtk.ResponseType.ACCEPT)
        approve_btn.get_style_context().add_class("destructive-action")
        deny_btn.grab_focus()  # safe default for keyboard users

        def on_timeout():
            dialog.response(Gtk.ResponseType.REJECT)
            return False

        def on_key(_widget, event):
            from gi.repository import Gdk
            if event.keyval == Gdk.KEY_Escape:
                dialog.response(Gtk.ResponseType.REJECT)
                return True
            return False

        dialog.connect("key-press-event", on_key)
        GLib.timeout_add_seconds(CONFIRMATION_TTL_SECONDS, on_timeout)
        dialog.show_all()
        response = dialog.run()
        result["approved"] = (response == Gtk.ResponseType.ACCEPT)
        dialog.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration()
        return result["approved"]
