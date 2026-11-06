"""Badge-unlocked celebration popup."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from ..i18n import L  # noqa: E402
from .widgets import label, setup_rgba, spacer, vbox  # noqa: E402


class CelebrationWindow(Gtk.Window):
    def __init__(self, state) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.state = state
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.set_default_size(400, 460)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_app_paintable(True)
        setup_rgba(self)
        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.root.get_style_context().add_class("celebration")
        self.root.set_margin_start(0)
        self.root.set_margin_end(0)
        self.add(self.root)
        self._timeout = None
        self.connect("delete-event", lambda *_: self.dismiss())
        self.connect("button-press-event", lambda *_: self.dismiss())

    def show_badge(self, badge) -> None:
        for child in list(self.root.get_children()):
            self.root.remove(child)
        inner = vbox(10)
        inner.set_margin_start(28)
        inner.set_margin_end(28)
        inner.set_margin_top(34)
        inner.set_margin_bottom(28)
        inner.set_halign(Gtk.Align.CENTER)

        icon = label(badge.icon, "", xalign=0.5)
        icon.set_markup(f"<span size='84000'>{badge.icon}</span>")
        inner.pack_start(icon, False, False, 0)
        inner.pack_start(label(L.badge_unlocked, "title", xalign=0.5), False, False, 0)
        name = label(badge.name, "", xalign=0.5)
        name.set_markup(f"<span size='18000' weight='bold' color='#ffd43b'>{badge.name}</span>")
        inner.pack_start(name, False, False, 0)
        desc = label(badge.desc, "small muted", wrap=True, xalign=0.5)
        desc.set_justify(Gtk.Justification.CENTER)
        inner.pack_start(desc, False, False, 0)
        inner.pack_start(spacer(height=8), False, False, 0)
        if badge.is_total:
            hint = f"{L.share_total_breaks}: {self.state.total_count}"
        else:
            hint = f"{L.current_streak}: {self.state.current_streak} {L.unit_times}"
        inner.pack_start(label(hint, "small bold blue", xalign=0.5), False, False, 0)

        button = Gtk.Button(label=L.ok)
        button.get_style_context().add_class("primary")
        button.set_size_request(180, 38)
        button.connect("clicked", lambda *_: self.dismiss())
        inner.pack_start(button, False, False, 0)
        inner.pack_start(label(L.badge_tap_dismiss, "tiny faint", xalign=0.5), False, False, 0)
        self.root.pack_start(inner, True, True, 0)
        self.root.show_all()
        self.present()
        if self._timeout is not None:
            GLib.source_remove(self._timeout)
        self._timeout = GLib.timeout_add(8000, self._auto_dismiss)

    def _auto_dismiss(self) -> bool:
        self._timeout = None
        self.dismiss()
        return False

    def dismiss(self) -> bool:
        if self._timeout is not None:
            GLib.source_remove(self._timeout)
            self._timeout = None
        self.hide()
        self.state.dismiss_celebration()
        return True
