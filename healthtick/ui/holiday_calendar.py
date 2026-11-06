"""Month calendar showing official holiday / makeup-work days."""

from __future__ import annotations

import calendar
from datetime import date, datetime

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from ..config import weekday_order  # noqa: E402
from ..i18n import L  # noqa: E402
from .theme import theme  # noqa: E402
from .widgets import AppWindow, hbox, label, separator, spacer, vbox  # noqa: E402


def _rgba(hex_color: str, alpha: float = 1.0) -> Gdk.RGBA:
    color = Gdk.RGBA()
    color.parse(hex_color)
    color.alpha = alpha
    return color


class HolidayCalendarWindow(AppWindow):
    def __init__(self, state, callbacks: dict | None = None) -> None:
        super().__init__(L.holiday_calendar_title, 760, 660)
        self.state = state
        self.callbacks = callbacks or {}
        today = date.today()
        self._year = today.year
        self._month = today.month
        self._grid_holder = vbox(0)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_top(10)
        outer.set_margin_bottom(10)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        prev = Gtk.Button(label="‹")
        prev.get_style_context().add_class("flat")
        prev.connect("clicked", lambda *_: self._shift_month(-1))
        nxt = Gtk.Button(label="›")
        nxt.get_style_context().add_class("flat")
        nxt.connect("clicked", lambda *_: self._shift_month(1))
        self.title = label("", "title")
        header.pack_start(prev, False, False, 0)
        header.pack_start(nxt, False, False, 0)
        header.pack_start(self.title, False, False, 0)
        self.sync_button = Gtk.Button(label=L.holiday_sync_now)
        self.sync_button.set_sensitive(self.state.config.holiday_sync_enabled)
        self.sync_button.connect("clicked", lambda *_: self._sync())
        header.pack_end(self.sync_button, False, False, 0)
        today_button = Gtk.Button(label="●")
        today_button.get_style_context().add_class("flat")
        today_button.set_tooltip_text(L.today_done)
        today_button.connect("clicked", lambda *_: self._go_today())
        header.pack_end(today_button, False, False, 0)
        outer.pack_start(header, False, False, 0)

        legend = hbox(10)
        for text, css, color in (
            (L.holiday_kind_rest, "chip-orange", theme.palette.orange),
            (L.holiday_kind_makeup, "chip-blue", theme.palette.blue),
            (L.holiday_kind_default_work, "chip-green", theme.palette.green),
            (L.holiday_kind_default_off, "chip-purple", theme.palette.faint),
        ):
            chip = label(text, "header-chip " + css)
            legend.pack_start(chip, False, False, 0)
        outer.pack_start(legend, False, False, 0)

        self._sync_label = label("", "tiny muted")
        outer.pack_start(self._sync_label, False, False, 0)
        outer.pack_start(separator(), False, False, 0)
        outer.pack_start(self._grid_holder, True, True, 0)
        self.set_body(outer)
        self.refresh()
        self.connect("delete-event", lambda *_: (self.hide(), True)[1])

    # ------------------------------------------------------------------ month

    def _shift_month(self, delta: int) -> None:
        month = self._month + delta
        year = self._year
        while month < 1:
            month += 12
            year -= 1
        while month > 12:
            month -= 12
            year += 1
        self._year, self._month = year, month
        self.refresh()

    def _go_today(self) -> None:
        today = date.today()
        self._year, self._month = today.year, today.month
        self.refresh()

    def refresh(self) -> None:
        self.title.set_text(L.holiday_month_title(self._year, self._month))
        synced = self.state.config.holiday_calendar_synced_at
        if synced:
            try:
                text = datetime.fromisoformat(synced.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
            except ValueError:
                text = synced
            self._sync_label.set_text(f"{L.holiday_sync_last}: {text}")
        else:
            self._sync_label.set_text(L.holiday_sync_never)
        self.sync_button.set_sensitive(
            self.state.config.holiday_sync_enabled and not getattr(self, "_busy", False)
        )
        self.sync_button.set_label(L.holiday_syncing if getattr(self, "_busy", False) else L.holiday_sync_now)

        for child in list(self._grid_holder.get_children()):
            self._grid_holder.remove(child)

        header = Gtk.Grid(column_homogeneous=True, row_spacing=4, column_spacing=4)
        for col, swift_day in enumerate(weekday_order()):
            title = label(L.weekday_name(swift_day), "tiny bold muted", xalign=0.5)
            header.attach(title, col, 0, 1, 1)
        self._grid_holder.pack_start(header, False, False, 0)

        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True, row_spacing=5, column_spacing=5)
        self._grid_holder.pack_start(grid, True, True, 0)

        first_weekday, days_in_month = calendar.monthrange(self._year, self._month)
        # calendar.monthrange is Monday=0, matching our Monday-first columns.
        today = date.today()
        for day in range(1, days_in_month + 1):
            index = first_weekday + day - 1
            row, col = divmod(index, 7)
            cell = self._day_cell(date(self._year, self._month, day), today)
            grid.attach(cell, col, row, 1, 1)
        self._grid_holder.show_all()

    def _day_cell(self, day: date, today: date) -> Gtk.Box:
        config = self.state.config
        kind = config.holiday_kind(day)
        name = config.holiday_label(day)
        p = theme.palette
        if kind == "rest":
            color, text_color, tag = p.orange, "#ffffff", L.holiday_kind_rest
        elif kind == "makeup_work":
            color, text_color, tag = p.blue, "#ffffff", L.holiday_kind_makeup
        elif kind == "default_work":
            color, text_color, tag = p.green, "#ffffff", None
        else:
            color, text_color, tag = p.faint, "#ffffff", None

        cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        cell.set_size_request(78, 64)
        cell.set_margin_start(2)
        cell.set_margin_end(2)
        cell.set_margin_top(2)
        cell.set_margin_bottom(2)
        base_alpha = 0.16 if kind in ("default_off", "default_work") else 0.82
        cell.override_background_color(Gtk.StateFlags.NORMAL, _rgba(color, base_alpha))
        if day == today:
            cell.get_style_context().add_class("card-white")
        number = label(str(day.day), "small bold", xalign=0.5)
        number.set_justify(Gtk.Justification.CENTER)
        number.override_color(
            Gtk.StateFlags.NORMAL,
            _rgba(text_color if base_alpha > 0.5 else p.text),
        )
        cell.pack_start(number, False, False, 0)
        if name:
            tag_label = label(name, "tiny", wrap=True, xalign=0.5)
            tag_label.set_justify(Gtk.Justification.CENTER)
            tag_label.override_color(
                Gtk.StateFlags.NORMAL,
                _rgba(text_color if base_alpha > 0.5 else p.sub),
            )
            cell.pack_start(tag_label, False, False, 0)
        elif kind == "default_off":
            cell.pack_start(
                label("·", "tiny", xalign=0.5),
                False,
                False,
                0,
            )
        return cell

    # ------------------------------------------------------------------- sync

    def _sync(self) -> None:
        if getattr(self, "_busy", False):
            return
        self._busy = True
        self.refresh()

        def done(ok: bool, message: str) -> None:
            def finish() -> bool:
                self._busy = False
                self._sync_label.set_text(
                    L.holiday_sync_success if ok else L.holiday_sync_network
                )
                self.refresh()
                return False

            GLib.idle_add(finish)

        self.state.sync_holidays(on_done=done)

    def present_window(self) -> None:
        self.refresh()
        super().present_window()
