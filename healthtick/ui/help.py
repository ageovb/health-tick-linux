"""In-app help / quick guide."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ..i18n import L  # noqa: E402
from .widgets import AppWindow, hbox, label, separator, spacer, vbox  # noqa: E402


class HelpWindow(AppWindow):
    def __init__(self) -> None:
        super().__init__(L.help_window, 680, 720, subtitle=L.help_title)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        inner = vbox(16)
        inner.set_margin_start(4)
        inner.set_margin_end(8)
        inner.set_margin_top(14)
        inner.set_margin_bottom(24)
        scroller.add(inner)

        inner.pack_start(label(L.help_title, "title"), False, False, 0)
        inner.pack_start(label(L.app_slogan, "small muted"), False, False, 0)
        inner.pack_start(separator(), False, False, 0)

        inner.pack_start(label(L.help_core, "small bold"), False, False, 0)
        steps = [
            ("1", L.help_step1_title, L.help_step1_desc, "⏱"),
            ("2", L.help_step2_title, L.help_step2_desc, "🔔"),
            ("3", L.help_step3_title, L.help_step3_desc, "☕"),
            ("4", L.help_step4_title, L.help_step4_desc, "▶"),
        ]
        for number, title, desc, icon in steps:
            card = vbox(4)
            card.get_style_context().add_class("card")
            header = hbox(8)
            badge = label(number, "small bold", xalign=0.5)
            badge.set_size_request(26, 26)
            badge.get_style_context().add_class("surface-3")
            header.pack_start(badge, False, False, 0)
            header.pack_start(label(icon, "small"), False, False, 0)
            header.pack_start(label(title, "small bold"), False, False, 0)
            card.pack_start(header, False, False, 0)
            card.pack_start(label(desc, "small muted", wrap=True), False, False, 0)
            inner.pack_start(card, False, False, 0)

        inner.pack_start(label(L.help_features, "small bold"), False, False, 0)
        features = [
            ("🪟", L.help_break_window, L.help_break_window_desc),
            ("👀", L.help_activity, ""),
            ("⏳", L.help_delay, ""),
            ("🔁", L.help_reset_cycle, ""),
            ("🏅", L.help_badge_system, L.help_badge_desc),
        ]
        card = vbox(10)
        card.get_style_context().add_class("card")
        for index, (icon, title, desc) in enumerate(features):
            if index:
                card.pack_start(separator(), False, False, 0)
            row = hbox(8)
            row.pack_start(label(icon, "small"), False, False, 0)
            text = vbox(1)
            text.pack_start(label(title, "small bold"), False, False, 0)
            if desc:
                text.pack_start(label(desc, "tiny muted", wrap=True), False, False, 0)
            row.pack_start(text, True, True, 0)
            card.pack_start(row, False, False, 0)
        inner.pack_start(card, False, False, 0)

        inner.pack_start(label(L.help_tips, "small bold"), False, False, 0)
        tips = vbox(6)
        tips.get_style_context().add_class("card")
        for key in ("help_tip1", "help_tip2", "help_tip3", "help_tip4", "help_tip5"):
            row = hbox(6)
            row.pack_start(label("•", "muted"), False, False, 0)
            row.pack_start(label(getattr(L, key), "small muted", wrap=True), True, True, 0)
            tips.pack_start(row, False, False, 0)
        inner.pack_start(tips, False, False, 0)

        inner.pack_start(separator(), False, False, 0)
        inner.pack_start(label(L.help_about, "small bold"), False, False, 0)
        inner.pack_start(label(L.help_about_desc, "tiny muted", wrap=True), False, False, 0)
        inner.pack_start(label(L.made_with, "tiny faint"), False, False, 0)

        self.set_body(scroller)
        self.connect("delete-event", lambda *_: (self.hide(), True)[1])

    def present_window(self) -> None:
        super().present_window()
