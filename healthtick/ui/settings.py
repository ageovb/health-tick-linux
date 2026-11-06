"""Preferences window — mirrors the tabs of the macOS ``SettingsView``."""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import APP_NAME, __version__  # noqa: E402
from ..config import (  # noqa: E402
    AppAppearance,
    AppConfig,
    AppLanguage,
    BreakDisplayTarget,
    BreakPosition,
    QuietHourPeriod,
    weekday_order,
)
from ..db import ImportFailure  # noqa: E402
from ..i18n import SOUND_NAMES, L  # noqa: E402
from ..sound import player as sound_player  # noqa: E402
from .theme import theme  # noqa: E402
from .widgets import AppWindow, hbox, label, separator, spacer, vbox  # noqa: E402


def _weekday_short(swift_day: int) -> str:
    name = L.weekday_name(swift_day)
    if L.is_zh:
        return name.replace("周", "") or name
    return name[:3]


def _parse_hhmm(value: str) -> bool:
    try:
        h, m = value.split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except (ValueError, AttributeError):
        return False


def _confirm(parent, title: str, text: str, confirm_label: str) -> bool:
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.NONE,
        text=title,
    )
    dialog.format_secondary_text(text)
    dialog.add_button(L.cancel, Gtk.ResponseType.CANCEL)
    dialog.add_button(confirm_label, Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    response = dialog.run()
    dialog.destroy()
    return response == Gtk.ResponseType.OK


def _info(parent, title: str, text: str = "") -> None:
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.OK,
        text=title,
    )
    if text:
        dialog.format_secondary_text(text)
    dialog.run()
    dialog.destroy()


def _error(parent, title: str, text: str = "") -> None:
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.OK,
        text=title,
    )
    if text:
        dialog.format_secondary_text(text)
    dialog.run()
    dialog.destroy()


class SettingsWindow(AppWindow):
    def __init__(self, state, callbacks: dict) -> None:
        super().__init__(L.settings_window, 800, 720, subtitle=L.app_slogan)
        self.state = state
        self.callbacks = callbacks
        self._config = state.config.copy()
        self._language = self._config.language
        self._applying = False
        self._tab_index = 0
        self._notebook: Gtk.Notebook | None = None
        self._sync_status = ""
        self._sync_busy = False
        self.connect("delete-event", self._on_delete)
        self.rebuild()

    def _on_delete(self, *_args):
        self.hide()
        return True

    # ------------------------------------------------------------------ build

    def rebuild(self) -> None:
        if self._notebook is not None:
            current = self._notebook.get_current_page()
            if current >= 0:
                self._tab_index = current
        self._config = self.state.config.copy()
        self._language = self._config.language
        self.set_title_text(L.settings_window)
        self.set_subtitle(L.app_slogan)
        notebook = Gtk.Notebook()
        notebook.set_scrollable(True)
        pages = [
            (L.tab_rhythm, self._page_rhythm()),
            (L.tab_schedule, self._page_schedule()),
            (L.tab_reminders, self._page_reminders()),
            (L.tab_break, self._page_break()),
            (L.tab_system, self._page_system()),
            (L.tab_data, self._page_data()),
            (L.tab_about, self._page_about()),
        ]
        for title, page in pages:
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.add(page)
            notebook.append_page(scroller, Gtk.Label(label=title))
        notebook.set_current_page(min(self._tab_index, len(pages) - 1))
        notebook.connect("switch-page", self._on_tab_switch)
        self._notebook = notebook
        self.set_body(notebook)
        self.queue_draw()

    def _on_tab_switch(self, _notebook, _page, index: int) -> None:
        self._tab_index = index

    def refresh_from_state(self) -> None:
        if self._applying:
            return
        if self.state.config != self._config:
            if self.get_visible():
                self.rebuild()
            else:
                self._config = self.state.config.copy()

    def _apply(self, *, suppress_restart: bool = False) -> None:
        self._applying = True
        try:
            self.state.update_config(
                self._config.copy(), suppress_restart_prompt=suppress_restart
            )
            self._config = self.state.config.copy()
        finally:
            self._applying = False

    # ---------------------------------------------------------------- helpers

    def _scroll_page(self) -> tuple[Gtk.Box, Gtk.Box]:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner = vbox(14)
        inner.set_margin_start(4)
        inner.set_margin_end(8)
        inner.set_margin_top(14)
        inner.set_margin_bottom(20)
        outer.pack_start(inner, False, False, 0)
        return outer, inner

    def _page(self) -> tuple[Gtk.Box, Gtk.Box]:
        return self._scroll_page()

    def _section(self, title: str, description: str = "") -> Gtk.Box:
        box = vbox(2)
        box.pack_start(label(title, "small bold"), False, False, 0)
        if description:
            box.pack_start(label(description, "tiny muted", wrap=True), False, False, 0)
        return box

    def _card(self, *children) -> Gtk.Box:
        card = vbox(10)
        card.get_style_context().add_class("card")
        card.set_margin_top(2)
        for child in children:
            if child is not None:
                card.pack_start(child, False, False, 0)
        return card

    def _row(self, icon: str, title: str, desc: str, control: Gtk.Widget) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        if icon:
            row.pack_start(label(icon, "small"), False, False, 0)
        text = vbox(1)
        text.pack_start(label(title, "small"), False, False, 0)
        if desc:
            text.pack_start(label(desc, "tiny muted", wrap=True), False, False, 0)
        row.pack_start(text, True, True, 0)
        control.set_valign(Gtk.Align.CENTER)
        row.pack_end(control, False, False, 0)
        return row

    def _switch(self, attr: str, *, callback=None) -> Gtk.Switch:
        switch = Gtk.Switch()
        switch.set_active(bool(getattr(self._config, attr)))
        switch.set_valign(Gtk.Align.CENTER)

        def changed(widget, _state=None) -> None:
            setattr(self._config, attr, bool(widget.get_active()))
            self._apply()
            if callback:
                callback(widget.get_active())

        switch.connect("notify::active", changed)
        return switch

    def _spin(self, attr: str, lo: int, hi: int, step: int = 1) -> Gtk.SpinButton:
        adjustment = Gtk.Adjustment(
            value=int(getattr(self._config, attr)),
            lower=lo,
            upper=hi,
            step_increment=step,
            page_increment=step * 5,
        )
        spin = Gtk.SpinButton(adjustment=adjustment, climb_rate=1, digits=0)
        spin.set_numeric(True)
        spin.set_width_chars(5)

        def changed(widget) -> None:
            setattr(self._config, attr, int(widget.get_value()))
            self._apply()

        spin.connect("value-changed", changed)
        return spin

    def _scale(
        self, attr: str, lo: int, hi: int, step: int, formatter
    ) -> tuple[Gtk.Widget, Gtk.Scale]:
        box = hbox(8)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        scale.set_value(int(getattr(self._config, attr)))
        scale.set_draw_value(False)
        scale.set_size_request(230, -1)
        value_label = label(formatter(int(getattr(self._config, attr))), "small bold blue")
        value_label.set_size_request(78, -1)
        value_label.set_xalign(1)

        def changed(widget) -> None:
            value = int(round(widget.get_value()))
            if abs(widget.get_value() - value) > 0.01:
                widget.set_value(value)
            value_label.set_text(formatter(value))
            setattr(self._config, attr, value)
            self._apply()

        scale.connect("value-changed", changed)
        box.pack_start(scale, True, True, 0)
        box.pack_start(value_label, False, False, 0)
        return box, scale

    def _combo(self, values: list[tuple[str, str]], current: str, callback) -> Gtk.ComboBoxText:
        combo = Gtk.ComboBoxText()
        for value, text in values:
            combo.append(value, text)
        combo.set_active_id(str(current))
        combo.connect("changed", lambda widget: callback(widget.get_active_id()))
        return combo

    def _time_entry(self, attr: str) -> Gtk.Entry:
        entry = Gtk.Entry()
        entry.set_text(str(getattr(self._config, attr)))
        entry.set_width_chars(6)
        entry.set_max_length(5)
        entry.set_alignment(0.5)
        entry.set_placeholder_text("HH:MM")

        def commit(widget, *_args) -> bool:
            text = widget.get_text().strip()
            if _parse_hhmm(text):
                h, m = text.split(":")
                normalized = f"{int(h):02d}:{int(m):02d}"
                widget.set_text(normalized)
                setattr(self._config, attr, normalized)
                self._apply()
            else:
                widget.set_text(str(getattr(self._config, attr)))
                widget.get_style_context().add_class("error")
            return False

        entry.connect("activate", commit)
        entry.connect("focus-out-event", commit)
        return entry

    # -------------------------------------------------------------- rhythm tab

    def _page_rhythm(self) -> Gtk.Box:
        page, inner = self._page()
        inner.pack_start(self._section(L.section_rhythm), False, False, 0)

        work_box, _ = self._scale(
            "work_minutes", 1, 120, 1, lambda v: f"{v} {L.unit_minutes}"
        )
        break_box, _ = self._scale(
            "break_seconds", 20, 900, 10, lambda v: self._format_seconds(v)
        )
        inner.pack_start(
            self._card(
                self._row("⏱", L.work_duration, "", work_box),
                separator(),
                self._row("☕", L.break_duration, L.eye_care_desc if self._config.eye_care_mode else "", break_box),
            ),
            False,
            False,
            0,
        )

        goal = self._spin("daily_goal", 1, 20)
        inner.pack_start(
            self._card(
                self._row("🎯", L.daily_goal, "", goal),
            ),
            False,
            False,
            0,
        )

        # Long break
        long_switch = self._switch("long_break_enabled", callback=lambda _v: self._defer_rebuild())
        interval = self._spin("long_break_interval", 2, 8)
        interval.set_sensitive(self._config.long_break_enabled)
        duration_box, _ = self._scale(
            "long_break_seconds", 300, 1800, 60, lambda v: self._format_seconds(v)
        )
        duration_box.set_sensitive(self._config.long_break_enabled)
        long_card = self._card(
            self._row("🌿", L.long_break, L.long_break_desc, long_switch),
            separator(),
            self._row(
                "🔁",
                L.long_break_every,
                "",
                hbox(6, interval, label(L.long_break_every and L.long_break_cycles, "small muted")),
            ),
            self._row("🌙", L.long_break_duration, "", duration_box),
        )
        if self._config.long_break_enabled and self._config.eye_care_mode:
            hint = label(
                L.fmt(
                    "eye_care_hint",
                    minutes=self._config.long_break_interval * 20,
                    duration=self._format_seconds(self._config.long_break_seconds),
                ),
                "tiny muted",
                wrap=True,
            )
            long_card.pack_start(hint, False, False, 0)
        inner.pack_start(long_card, False, False, 0)

        # Pre-break notices
        notice_switch = self._switch("pre_break_notice_enabled", callback=lambda _v: self._defer_rebuild())
        normal_lead = self._spin("pre_break_notice_seconds", 0, 3600, 15)
        normal_lead.set_sensitive(self._config.pre_break_notice_enabled)
        long_lead = self._spin("pre_break_notice_long_seconds", 0, 3600, 15)
        long_lead.set_sensitive(self._config.pre_break_notice_enabled)
        inner.pack_start(
            self._card(
                self._row("🔔", L.prebreak_notice, L.prebreak_desc, notice_switch),
                separator(),
                self._row("⏳", L.prebreak_lead, "", hbox(6, normal_lead, label(L.unit_seconds, "tiny muted"))),
                self._row("⏳", L.prebreak_lead_long, "", hbox(6, long_lead, label(L.unit_seconds, "tiny muted"))),
            ),
            False,
            False,
            0,
        )

        eye_switch = self._switch("eye_care_mode", callback=self._on_eye_care)
        inner.pack_start(
            self._card(
                self._row("👀", L.eye_care, L.eye_care_desc, eye_switch),
            ),
            False,
            False,
            0,
        )
        return page

    @staticmethod
    def _format_seconds(seconds: int) -> str:
        if seconds < 60:
            return f"{seconds}{L.unit_seconds}"
        minutes, rest = divmod(seconds, 60)
        if rest:
            return f"{minutes}{L.unit_minutes}{rest}{L.unit_seconds}"
        return f"{minutes} {L.unit_minutes}"

    def _on_eye_care(self, enabled: bool) -> None:
        cfg = self._config
        cfg.saved_work_minutes = cfg.work_minutes
        cfg.saved_break_seconds = cfg.break_seconds
        cfg.saved_break_confirm = cfg.break_confirm
        if enabled:
            cfg.eye_care_mode = True
            cfg.work_minutes = 20
            cfg.break_seconds = 20
            cfg.break_confirm = False
        else:
            cfg.eye_care_mode = False
            cfg.work_minutes = cfg.saved_work_minutes
            cfg.break_seconds = cfg.saved_break_seconds
            cfg.break_confirm = cfg.saved_break_confirm
        self._apply(suppress_restart=True)
        self.state.restart_current_phase()
        self._defer_rebuild()

    def _defer_rebuild(self) -> None:
        GLib.idle_add(lambda: (self.rebuild(), False)[1])

    # ------------------------------------------------------------ schedule tab

    def _page_schedule(self) -> Gtk.Box:
        page, inner = self._page()
        inner.pack_start(self._section(L.section_schedule), False, False, 0)

        days_row = hbox(5)
        for day in weekday_order():
            button = Gtk.ToggleButton(label=_weekday_short(day))
            button.get_style_context().add_class("tab")
            button.set_active(day in self._config.work_days)
            button.set_size_request(38, 32)

            def toggled(widget, swift_day=day) -> None:
                if widget.get_active():
                    self._config.work_days.add(swift_day)
                else:
                    self._config.work_days.discard(swift_day)
                self._apply()

            button.connect("toggled", toggled)
            days_row.pack_start(button, False, False, 0)
        inner.pack_start(
            self._card(self._row("📅", L.work_days, L.holiday_by_weekday, days_row)),
            False,
            False,
            0,
        )

        # Quiet hours
        quiet_rows = vbox(8)
        for period in self._config.quiet_hours:
            quiet_rows.pack_start(self._quiet_period_row(period), False, False, 0)
        add_button = Gtk.Button(label=f"＋ {L.add_quiet_hour}")
        add_button.connect("clicked", lambda *_: self._add_quiet_period())
        quiet_card = self._card(
            self._section(L.quiet_hours, L.quiet_hours_help),
            quiet_rows,
            add_button,
        )
        inner.pack_start(quiet_card, False, False, 0)

        work_hours_switch = self._switch(
            "work_hours_enabled", callback=lambda _v: self._defer_rebuild()
        )
        start_entry = self._time_entry("work_start_time")
        end_entry = self._time_entry("work_end_time")
        start_entry.set_sensitive(self._config.work_hours_enabled)
        end_entry.set_sensitive(self._config.work_hours_enabled)
        offwork_switch = self._switch("off_work_summary_enabled")
        offwork_switch.set_sensitive(self._config.work_hours_enabled)
        work_hours_card = self._card(
            self._row("🏢", L.work_hours, L.work_hours_help, work_hours_switch),
            separator(),
            self._row(L.start_time and "🕘", L.work_start, "", start_entry),
            self._row("🕕", L.work_end, "", end_entry),
            self._row("🌇", L.offwork_summary, L.offwork_footer, offwork_switch),
        )
        inner.pack_start(work_hours_card, False, False, 0)

        # Holidays
        holiday_switch = self._switch("holiday_sync_enabled", callback=lambda _v: self._defer_rebuild())
        last = self._config.holiday_calendar_synced_at
        if last:
            try:
                last_text = datetime.fromisoformat(last.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
            except ValueError:
                last_text = last
        else:
            last_text = L.holiday_sync_never
        status = self._sync_status or f"{L.holiday_sync_last}: {last_text}"
        self._sync_label = label(status, "tiny muted")
        sync_button = Gtk.Button(label=L.holiday_syncing if self._sync_busy else L.holiday_sync_now)
        sync_button.set_sensitive(self._config.holiday_sync_enabled and not self._sync_busy)
        sync_button.connect("clicked", lambda *_: self._sync_holidays())
        calendar_button = Gtk.Button(label=L.holiday_view_calendar)
        calendar_button.connect("clicked", lambda *_: self.callbacks.get("holiday_calendar", lambda: None)())
        holiday_actions = hbox(8, sync_button, calendar_button)
        holiday_card = self._card(
            self._row("🇨🇳", L.holiday_sync, L.holiday_sync_desc, holiday_switch),
            separator(),
            self._sync_label,
            holiday_actions,
        )
        inner.pack_start(holiday_card, False, False, 0)
        return page

    def _quiet_period_row(self, period: QuietHourPeriod) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        start = Gtk.Entry()
        start.set_text(period.start)
        start.set_width_chars(5)
        start.set_max_length(5)
        start.set_alignment(0.5)
        end = Gtk.Entry()
        end.set_text(period.end)
        end.set_width_chars(5)
        end.set_max_length(5)
        end.set_alignment(0.5)

        def commit(widget, attr: str) -> bool:
            text = widget.get_text().strip()
            if _parse_hhmm(text):
                h, m = text.split(":")
                normalized = f"{int(h):02d}:{int(m):02d}"
                widget.set_text(normalized)
                setattr(period, attr, normalized)
                self._apply()
            else:
                widget.set_text(getattr(period, attr))
            return False

        start.connect("activate", lambda w: commit(w, "start"))
        start.connect("focus-out-event", lambda w, _e: commit(w, "start"))
        end.connect("activate", lambda w: commit(w, "end"))
        end.connect("focus-out-event", lambda w, _e: commit(w, "end"))
        row.pack_start(label("🕛", "small"), False, False, 0)
        row.pack_start(start, False, False, 0)
        row.pack_start(label("→", "muted"), False, False, 0)
        row.pack_start(end, False, False, 0)

        if period.weekdays:
            days_text = " ".join(_weekday_short(d) for d in sorted(period.weekdays))
        else:
            days_text = L.quiet_weekdays_all
        days_button = Gtk.Button(label=days_text)
        days_button.get_style_context().add_class("pill")
        days_button.connect("clicked", lambda *_: self._edit_quiet_days(period))
        row.pack_start(days_button, False, False, 0)

        row.pack_end(self._delete_button(lambda: self._remove_quiet_period(period)), False, False, 0)
        return row

    def _delete_button(self, callback) -> Gtk.Button:
        button = Gtk.Button(label="🗑")
        button.get_style_context().add_class("flat")
        button.set_tooltip_text(L.delete)
        button.connect("clicked", lambda *_: callback())
        return button

    def _add_quiet_period(self) -> None:
        self._config.quiet_hours.append(QuietHourPeriod())
        self._apply()
        self.rebuild()

    def _remove_quiet_period(self, period: QuietHourPeriod) -> None:
        self._config.quiet_hours = [p for p in self._config.quiet_hours if p.id != period.id]
        self._apply()
        self.rebuild()

    def _edit_quiet_days(self, period: QuietHourPeriod) -> None:
        dialog = Gtk.Dialog(
            title=L.quiet_specify_days,
            transient_for=self,
            modal=True,
        )
        dialog.add_button(L.cancel, Gtk.ResponseType.CANCEL)
        dialog.add_button(L.confirm, Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(6)
        box.set_margin_start(18)
        box.set_margin_end(18)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.pack_start(label(L.quiet_hours_help, "tiny muted", wrap=True), False, False, 0)
        checks: dict[int, Gtk.CheckButton] = {}
        for day in weekday_order():
            check = Gtk.CheckButton(label=L.weekday_name(day))
            check.set_active(bool(period.weekdays and day in period.weekdays))
            checks[day] = check
            box.pack_start(check, False, False, 0)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            selected = {day for day, check in checks.items() if check.get_active()}
            period.weekdays = selected or None
            self._apply()
            self.rebuild()
        dialog.destroy()

    def _sync_holidays(self) -> None:
        if self._sync_busy:
            return
        self._sync_busy = True
        self._sync_status = L.holiday_syncing

        def done(ok: bool, message: str) -> None:
            def finish() -> bool:
                self._sync_busy = False
                self._sync_status = L.holiday_sync_success if ok else L.holiday_sync_network
                self.rebuild()
                return False

            GLib.idle_add(finish)

        self.state.sync_holidays(on_done=done)
        self.rebuild()

    # ----------------------------------------------------------- reminders tab

    def _page_reminders(self) -> Gtk.Box:
        page, inner = self._page()
        inner.pack_start(self._section(L.section_reminder_texts, L.reminder_hint), False, False, 0)

        reminder_rows = vbox(6)
        for index, text in enumerate(self._config.reminders):
            row = hbox(6)
            entry = Gtk.Entry()
            entry.set_text(text)
            entry.set_hexpand(True)

            def changed(widget, i=index) -> None:
                if i < len(self._config.reminders):
                    self._config.reminders[i] = widget.get_text()
                    self._apply()

            entry.connect("changed", changed)
            row.pack_start(entry, True, True, 0)
            if len(self._config.reminders) > 1:
                row.pack_end(
                    self._delete_button(lambda i=index: self._remove_reminder(i)),
                    False,
                    False,
                    0,
                )
            reminder_rows.pack_start(row, False, False, 0)
        inner.pack_start(self._card(reminder_rows), False, False, 0)

        add_row = hbox(6)
        new_entry = Gtk.Entry()
        new_entry.set_placeholder_text(L.add_reminder_placeholder)
        new_entry.set_hexpand(True)
        add_button = Gtk.Button(label=f"＋ {L.add}")

        def add_reminder(*_args) -> None:
            text = new_entry.get_text().strip()
            if not text:
                return
            self._config.reminders.append(text)
            self._apply()
            self.rebuild()

        add_button.connect("clicked", add_reminder)
        new_entry.connect("activate", add_reminder)
        add_row.pack_start(new_entry, True, True, 0)
        add_row.pack_start(add_button, False, False, 0)
        inner.pack_start(add_row, False, False, 0)

        inner.pack_start(self._section(L.section_alert_style), False, False, 0)
        sound_combo = self._combo(
            [(name, name) for name in SOUND_NAMES],
            self._config.alert_sound,
            self._set_alert_sound,
        )
        preview = Gtk.Button(label=f"▶ {L.preview}")
        preview.connect(
            "clicked",
            lambda *_: self.state.preview_sound_burst(
                self._config.alert_sound, self._config.alert_sound_repeat_count
            ),
        )
        repeat = self._spin("alert_sound_repeat_count", 1, 10)
        detect_combo = self._combo(
            [(name, name) for name in SOUND_NAMES],
            self._config.break_detect_sound_name,
            self._set_detect_sound,
        )
        detect_combo.set_sensitive(self._config.break_detect_sound)
        sound_card = self._card(
            self._row("🔊", L.reminder_sound, "", hbox(8, sound_combo, preview)),
            separator(),
            self._row("🔁", L.alert_sound_repeat, "", hbox(6, repeat, label(L.unit_times, "tiny muted"))),
            self._row("🔔", L.sound_enabled, "", self._switch("sound_enabled")),
            separator(),
            self._row(
                "👂",
                L.detect_sound,
                L.break_detected_pause,
                self._switch("break_detect_sound", callback=lambda _v: self._defer_rebuild()),
            ),
            self._row("🎵", L.detect_sound, "", detect_combo),
        )
        inner.pack_start(sound_card, False, False, 0)
        return page

    def _set_alert_sound(self, value: str | None) -> None:
        if value:
            self._config.alert_sound = value
            self._apply()

    def _set_detect_sound(self, value: str | None) -> None:
        if value:
            self._config.break_detect_sound_name = value
            self._apply()

    def _remove_reminder(self, index: int) -> None:
        if 0 <= index < len(self._config.reminders):
            del self._config.reminders[index]
            self._apply()
            self.rebuild()

    # --------------------------------------------------------------- break tab

    def _page_break(self) -> Gtk.Box:
        page, inner = self._page()
        inner.pack_start(self._section(L.section_break_window), False, False, 0)

        position_values = [(p.value, p.label) for p in BreakPosition]
        position_combo = self._combo(
            position_values, self._config.break_position.value, self._set_position
        )

        display_values = [(t.value, t.label) for t in BreakDisplayTarget]
        target_combo = self._combo(
            display_values,
            self._config.break_display_target.value,
            self._set_display_target,
        )
        monitors = self._monitor_values()
        monitor_combo = self._combo(
            monitors,
            str(self._config.break_display_specific_uuid or "0"),
            self._set_specific_monitor,
        )
        monitor_combo.set_sensitive(
            self._config.break_display_target is BreakDisplayTarget.SPECIFIC
        )

        inner.pack_start(
            self._card(
                self._row("🪟", L.break_position, "", position_combo),
                separator(),
                self._row("🖥", L.display_target_label, "", target_combo),
                self._row("🖥", L.monitor, "", monitor_combo),
            ),
            False,
            False,
            0,
        )

        inner.pack_start(self._section(L.section_alert_style), False, False, 0)
        inner.pack_start(
            self._card(
                self._row("✅", L.break_confirm, L.prebreak_desc, self._switch("break_confirm")),
                separator(),
                self._row("🔒", L.reset_on_lock, L.reset_on_lock_hint, self._switch("reset_on_screen_lock")),
            ),
            False,
            False,
            0,
        )

        shortcut_hint = L.shortcut_linux_hint
        inner.pack_start(
            self._card(
                self._row("⌨️", L.shortcut, shortcut_hint, self._switch("shortcut_enabled")),
            ),
            False,
            False,
            0,
        )
        return page

    def _set_position(self, value: str | None) -> None:
        if not value:
            return
        self._config.break_position = BreakPosition(value)
        self._apply()

    def _set_display_target(self, value: str | None) -> None:
        if not value:
            return
        self._config.break_display_target = BreakDisplayTarget(value)
        if (
            self._config.break_display_target is BreakDisplayTarget.SPECIFIC
            and not self._config.break_display_specific_uuid
        ):
            self._config.break_display_specific_uuid = "0"
        self._apply()
        self._defer_rebuild()

    def _set_specific_monitor(self, value: str | None) -> None:
        if value is not None:
            self._config.break_display_specific_uuid = value
            self._apply()

    @staticmethod
    def _monitor_values() -> list[tuple[str, str]]:
        values: list[tuple[str, str]] = []
        display = Gdk.Display.get_default()
        if display is None:
            return [("0", L.no_monitors)]
        for index in range(display.get_n_monitors()):
            monitor = display.get_monitor(index)
            geo = monitor.get_geometry() if monitor is not None else None
            if geo is None:
                continue
            values.append(
                (
                    str(index),
                    L.fmt("display_monitor", n=index + 1, w=geo.width, h=geo.height),
                )
            )
        return values or [("0", L.no_monitors)]

    # -------------------------------------------------------------- system tab

    def _page_system(self) -> Gtk.Box:
        page, inner = self._page()
        inner.pack_start(self._section(L.section_app), False, False, 0)

        language_values = [(lang.value, lang.display_name) for lang in AppLanguage]
        language_combo = self._combo(
            language_values, self._config.language.value, self._set_language
        )
        appearance_values = [(app.value, L.appearance_system if app is AppAppearance.SYSTEM else (L.appearance_light if app is AppAppearance.LIGHT else L.appearance_dark)) for app in AppAppearance]
        appearance_combo = self._combo(
            appearance_values, self._config.appearance.value, self._set_appearance
        )
        inner.pack_start(
            self._card(
                self._row("🌐", L.language, "", language_combo),
                separator(),
                self._row("🎨", L.appearance, "", appearance_combo),
            ),
            False,
            False,
            0,
        )

        autostart = Gtk.Switch()
        autostart.set_active(self._autostart_exists())
        autostart.connect("notify::active", self._toggle_autostart)
        inner.pack_start(
            self._card(
                self._row("🚀", L.launch_at_login, "", autostart),
                separator(),
                self._row("🔄", L.auto_check_update, "", self._switch("auto_check_update")),
            ),
            False,
            False,
            0,
        )

        check_button = Gtk.Button(label=L.check_update)
        check_button.connect("clicked", lambda *_: self.callbacks.get("check_update", lambda: None)())
        onboarding_button = Gtk.Button(label=L.reopen_onboarding)
        onboarding_button.connect("clicked", lambda *_: self.callbacks.get("onboarding", lambda: None)())
        inner.pack_start(
            self._card(
                self._row("⬆️", L.check_update, f"{APP_NAME} {__version__}", check_button),
                separator(),
                self._row("🧭", L.reopen_onboarding, L.onboarding_settings_hint, onboarding_button),
            ),
            False,
            False,
            0,
        )
        return page

    def _set_language(self, value: str | None) -> None:
        if not value:
            return
        self._config.language = AppLanguage(value)
        self._apply()
        self._defer_rebuild()

    def _set_appearance(self, value: str | None) -> None:
        if not value:
            return
        self._config.appearance = AppAppearance(value)
        self._apply()
        theme.apply(self._config.appearance)
        self._defer_rebuild()

    # ---------------------------------------------------------- autostart file

    @staticmethod
    def _autostart_path() -> Path:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        return Path(base) / "autostart" / "health-tick.desktop"

    def _autostart_exists(self) -> bool:
        return self._autostart_path().exists()

    def _toggle_autostart(self, switch, _param=None) -> None:
        path = self._autostart_path()
        if switch.get_active():
            root = Path(__file__).resolve().parents[2]
            executable = root / "bin" / "health-tick"
            icon = root / "healthtick" / "assets" / "health-tick.svg"
            content = f"""[Desktop Entry]
Type=Application
Name=HealthTick
Comment=Sedentary break reminder
Exec={executable} --hidden
Icon={icon}
Terminal=false
X-GNOME-Autostart-enabled=true
"""
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                try:
                    executable.chmod(0o755)
                except OSError:
                    pass
            except OSError as exc:
                _error(self, L.autostart_failed, str(exc))
                switch.set_active(False)
        else:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    # ---------------------------------------------------------------- data tab

    def _page_data(self) -> Gtk.Box:
        page, inner = self._page()
        inner.pack_start(self._section(L.section_data), False, False, 0)

        records = self.state.db.total_count()
        sessions_row = self.state.db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
        sessions = int(sessions_row[0]) if sessions_row else 0
        summary = label(
            L.fmt("data_summary", records=records, sessions=sessions),
            "tiny muted",
        )

        export_button = Gtk.Button(label=L.export_data)
        export_button.get_style_context().add_class("primary")
        export_button.connect("clicked", lambda *_: self._export_data())
        import_button = Gtk.Button(label=L.import_data)
        import_button.connect("clicked", lambda *_: self._import_data())

        inner.pack_start(
            self._card(
                summary,
                separator(),
                self._row("📤", L.export_data, L.export_desc, export_button),
                separator(),
                self._row("📥", L.import_data, L.import_desc, import_button),
                label(L.import_backup_hint.format(path=self._backup_dir()), "tiny faint", wrap=True),
            ),
            False,
            False,
            0,
        )

        reset_data = Gtk.Button(label=L.reset_data)
        reset_data.get_style_context().add_class("danger-ghost")
        reset_data.connect("clicked", lambda *_: self._reset_data())
        reset_settings = Gtk.Button(label=L.reset_settings)
        reset_settings.get_style_context().add_class("danger-ghost")
        reset_settings.connect("clicked", lambda *_: self._reset_settings())
        inner.pack_start(
            self._card(
                self._row("🗑", L.reset_data, L.reset_data_warning, reset_data),
                separator(),
                self._row("↺", L.reset_settings, L.reset_settings_warning, reset_settings),
            ),
            False,
            False,
            0,
        )
        return page

    def _backup_dir(self) -> str:
        return str(Path(self.state.db.path).parent / "backups")

    def _export_data(self) -> None:
        dialog = Gtk.FileChooserDialog(
            title=L.export_data,
            transient_for=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(L.cancel, Gtk.ResponseType.CANCEL, L.export_data, Gtk.ResponseType.OK)
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name(
            L.fmt("export_file_name", date=date.today().strftime("%Y%m%d"))
        )
        response = dialog.run()
        path = dialog.get_filename()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not path:
            return
        try:
            payload = self.state.db.export_all_data()
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            _info(self, L.export_success, path)
        except Exception as exc:
            _error(self, L.export_failed, str(exc))

    def _import_data(self) -> None:
        dialog = Gtk.FileChooserDialog(
            title=L.import_data,
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(L.cancel, Gtk.ResponseType.CANCEL, L.import_data, Gtk.ResponseType.OK)
        filter = Gtk.FileFilter()
        filter.set_name(L.import_file_filter)
        filter.add_pattern("*.json")
        filter.add_pattern("*.dat")
        dialog.add_filter(filter)
        response = dialog.run()
        path = dialog.get_filename()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not path:
            return
        try:
            raw = Path(path).read_bytes()
            if len(raw) > 50 * 1024 * 1024:
                _error(self, L.import_too_large)
                return
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = self._decrypt_backup(raw)
                if payload is None:
                    return
            if not isinstance(payload, dict):
                _error(self, L.import_malformed)
                return
            include_config = self._ask_import_options(payload)
            if include_config is None:
                return
            self._applying = True
            try:
                summary = self.state.db.import_data(payload, include_config=include_config)
                self.state.reload_after_import(summary.config_applied)
            finally:
                self._applying = False
            text = L.import_summary(
                summary.records_added, summary.sessions_added,
                summary.records_skipped + summary.sessions_skipped,
            )
            if summary.backup_path:
                text += "\n" + L.import_backup_hint.format(path=summary.backup_path)
            _info(self, L.import_done, text)
            self.rebuild()
        except ImportFailure as exc:
            _error(self, L.import_failed, str(exc))
        except Exception as exc:
            _error(self, L.import_failed, str(exc))

    def _ask_import_options(self, payload: dict) -> bool | None:
        dialog = Gtk.Dialog(title=L.import_data, transient_for=self, modal=True)
        dialog.add_button(L.cancel, Gtk.ResponseType.CANCEL)
        dialog.add_button(L.import_data, Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.pack_start(label(L.import_confirm_title, "bold"), False, False, 0)
        box.pack_start(
            label(L.import_confirm_msg.format(count=len(payload.get("records") or [])), "small muted", wrap=True),
            False,
            False,
            0,
        )
        check = Gtk.CheckButton(label=L.import_include_config)
        check.set_active(False)
        box.pack_start(check, False, False, 0)
        box.show_all()
        response = dialog.run()
        result = check.get_active() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        return result

    def _decrypt_backup(self, raw: bytes) -> dict | None:
        password = self._ask_password()
        if password is None:
            return None
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

            if len(raw) <= 44:
                raise ValueError("too short")
            salt, nonce = raw[:16], raw[16:28]
            ciphertext, tag = raw[28:-16], raw[-16:]
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100_000
            )
            key = kdf.derive(password.encode("utf-8"))
            plaintext = AESGCM(key).decrypt(nonce, ciphertext + tag, None)
            data = json.loads(plaintext.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
            return data
        except Exception:
            _error(self, L.import_failed, L.import_wrong_password)
            return None

    def _ask_password(self) -> str | None:
        dialog = Gtk.Dialog(title=L.import_password_title, transient_for=self, modal=True)
        dialog.add_button(L.cancel, Gtk.ResponseType.CANCEL)
        dialog.add_button(L.confirm, Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.pack_start(label(L.import_password_desc, "small muted", wrap=True), False, False, 0)
        entry = Gtk.Entry()
        entry.set_visibility(False)
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box.show_all()
        response = dialog.run()
        text = entry.get_text()
        dialog.destroy()
        return text if response == Gtk.ResponseType.OK else None

    def _reset_data(self) -> None:
        if not _confirm(self, L.reset_data, L.reset_data_warning, L.reset_data):
            return
        self.state.reset_all_records()
        _info(self, L.data_cleared)
        self.rebuild()

    def _reset_settings(self) -> None:
        if not _confirm(self, L.reset_settings, L.reset_settings_warning, L.reset_settings):
            return
        self._applying = True
        try:
            self.state.reset_to_defaults()
        finally:
            self._applying = False
        self._config = self.state.config.copy()
        _info(self, L.settings_reset)
        self.rebuild()

    # --------------------------------------------------------------- about tab

    def _page_about(self) -> Gtk.Box:
        page, inner = self._page()
        inner.pack_start(self._section(L.about), False, False, 0)
        inner.pack_start(
            self._card(
                label(f"🌱 {APP_NAME}", "title"),
                label(L.app_slogan, "small muted"),
                label(f"{L.version} {__version__}", "tiny faint"),
                separator(),
                label(L.help_about_desc, "small muted", wrap=True),
                label(L.license, "tiny muted"),
                label(L.made_with, "tiny faint"),
            ),
            False,
            False,
            0,
        )
        links = hbox(8)
        for text, url in (
            ("GitHub", "https://github.com/lifedever/health-tick-release"),
            ("GPL-3.0", "https://www.gnu.org/licenses/gpl-3.0.html"),
        ):
            button = Gtk.Button(label=text)
            button.get_style_context().add_class("flat")
            button.connect("clicked", lambda _b, u=url: self._open_url(u))
            links.pack_start(button, False, False, 0)
        inner.pack_start(links, False, False, 0)
        return page

    @staticmethod
    def _open_url(url: str) -> None:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass

    # ------------------------------------------------------------------ public

    def present_window(self) -> None:
        if self.state.config != self._config:
            self._config = self.state.config.copy()
            self.rebuild()
        super().present_window()
