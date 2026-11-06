"""First-run setup wizard (four short steps)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ..config import QuietHourPeriod, weekday_order  # noqa: E402
from ..i18n import L  # noqa: E402
from .theme import theme  # noqa: E402
from .widgets import AppWindow, hbox, label, separator, spacer, vbox  # noqa: E402
from .settings import SettingsWindow, _weekday_short  # noqa: E402


class OnboardingWindow(AppWindow):
    def __init__(self, state, on_done=None) -> None:
        super().__init__(L.onboarding_window, 560, 570)
        self.state = state
        self.on_done = on_done
        self._config = state.config.copy()
        self._step = 0
        self._lunch_enabled = any(
            p.start == "12:00" and p.end == "13:00" for p in self._config.quiet_hours
        )
        self._lunch_start = "12:00"
        self._lunch_end = "13:00"
        for period in self._config.quiet_hours:
            if period.start == "12:00" and period.end == "13:00":
                self._lunch_start, self._lunch_end = period.start, period.end
        self.rebuild()
        self.connect("delete-event", self._on_delete)

    def _on_delete(self, *_args):
        self.hide()
        return True

    # ------------------------------------------------------------------ build

    def rebuild(self) -> None:
        for child in list(self.body.get_children()):
            self.body.remove(child)
        root = vbox(14)
        root.set_margin_start(14)
        root.set_margin_end(14)
        root.set_margin_top(4)
        root.set_margin_bottom(6)

        dots = hbox(6)
        dots.set_halign(Gtk.Align.CENTER)
        for index in range(4):
            dot = label("●", "tiny" if index != self._step else "small")
            dot.set_opacity(0.95 if index == self._step else 0.28)
            dots.pack_start(dot, False, False, 0)
        root.pack_start(dots, False, False, 0)

        content = [self._welcome, self._rhythm, self._schedule, self._summary][self._step]()
        root.pack_start(content, True, True, 0)
        root.pack_start(separator(), False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        skip = Gtk.Button(label=L.onboarding_skip)
        skip.get_style_context().add_class("flat")
        skip.connect("clicked", lambda *_: self._finish(skip_config=True))
        buttons.pack_start(skip, False, False, 0)
        if self._step > 0:
            back = Gtk.Button(label=L.onboarding_back)
            back.connect("clicked", lambda *_: self._go(-1))
            buttons.pack_end(back, False, False, 0)
        next_button = Gtk.Button(
            label=L.onboarding_finish if self._step == 3 else L.onboarding_next
        )
        next_button.get_style_context().add_class("primary")
        next_button.connect(
            "clicked",
            lambda *_: self._finish(skip_config=False)
            if self._step == 3
            else self._go(1),
        )
        buttons.pack_end(next_button, False, False, 0)
        root.pack_start(buttons, False, False, 0)
        self.body.pack_start(root, True, True, 0)
        root.show_all()

    def _go(self, delta: int) -> None:
        self._step = max(0, min(3, self._step + delta))
        self.rebuild()

    def _finish(self, *, skip_config: bool) -> None:
        if not skip_config:
            self._apply_lunch()
            self.state.update_config(self._config, suppress_restart_prompt=True)
            self.state.restart_current_phase()
        self.state.db.set_onboarding_completed()
        self.hide()
        if self.on_done:
            self.on_done()

    def _apply_lunch(self) -> None:
        existing = [
            p for p in self._config.quiet_hours if p.start == "12:00" and p.end == "13:00"
        ]
        if self._lunch_enabled:
            if existing:
                existing[0].start = self._lunch_start
                existing[0].end = self._lunch_end
            else:
                self._config.quiet_hours.append(
                    QuietHourPeriod(start=self._lunch_start, end=self._lunch_end)
                )
        else:
            self._config.quiet_hours = [
                p for p in self._config.quiet_hours if not (p.start == "12:00" and p.end == "13:00")
            ]

    # ------------------------------------------------------------------ pages

    def _page(self) -> Gtk.Box:
        page = vbox(14)
        page.set_margin_top(8)
        return page

    def _welcome(self) -> Gtk.Box:
        page = self._page()
        icon = label("🌱", "", xalign=0.5)
        icon.set_markup("<span size='56000'>🌱</span>")
        page.pack_start(icon, False, False, 0)
        page.pack_start(
            label(L.onboarding_welcome, "title", xalign=0.5), False, False, 0
        )
        page.pack_start(
            label(L.onboarding_welcome_desc, "small muted", wrap=True, xalign=0.5),
            False,
            False,
            0,
        )
        features = vbox(8)
        features.get_style_context().add_class("card")
        for emoji, text in (
            ("⏱", L.onboarding_interval),
            ("☕", L.onboarding_break),
            ("🎯", L.onboarding_daily_goal),
            ("📊", L.achievements),
        ):
            row = hbox(8)
            row.pack_start(label(emoji, "small"), False, False, 0)
            row.pack_start(label(text, "small"), False, False, 0)
            features.pack_start(row, False, False, 0)
        page.pack_start(features, False, False, 0)
        page.pack_start(spacer(height=4), False, False, 0)
        page.pack_start(
            label(L.onboarding_settings_hint, "tiny faint", wrap=True, xalign=0.5),
            False,
            False,
            0,
        )
        return page

    def _rhythm(self) -> Gtk.Box:
        page = self._page()
        page.pack_start(label(L.onboarding_rhythm, "title"), False, False, 0)
        page.pack_start(
            label(L.onboarding_effective, "tiny muted"), False, False, 0
        )
        work_box = self._scale("work_minutes", 1, 120, 1, lambda v: f"{v} {L.unit_minutes}")
        page.pack_start(
            self._labeled(L.onboarding_interval, work_box), False, False, 0
        )
        break_box = self._scale(
            "break_seconds", 20, 900, 10, SettingsWindow._format_seconds
        )
        page.pack_start(
            self._labeled(L.onboarding_break, break_box), False, False, 0
        )
        page.pack_start(separator(), False, False, 0)
        goal_row = hbox(8)
        goal_row.pack_start(label(L.onboarding_daily_goal, "small bold"), False, False, 0)
        goal = Gtk.SpinButton.new_with_range(1, 20, 1)
        goal.set_value(self._config.daily_goal)
        goal.connect(
            "value-changed",
            lambda w: setattr(self._config, "daily_goal", int(w.get_value())),
        )
        goal_row.pack_end(goal, False, False, 0)
        page.pack_start(goal_row, False, False, 0)
        page.pack_start(
            label(L.onboarding_goal_recommendation(8), "tiny faint"),
            False,
            False,
            0,
        )
        return page

    def _schedule(self) -> Gtk.Box:
        page = self._page()
        page.pack_start(label(L.onboarding_work_schedule, "title"), False, False, 0)
        days_row = hbox(5)
        for day in weekday_order():
            button = Gtk.ToggleButton(label=_weekday_short(day))
            button.get_style_context().add_class("tab")
            button.set_active(day in self._config.work_days)
            button.set_size_request(40, 34)

            def toggled(widget, swift_day=day) -> None:
                if widget.get_active():
                    self._config.work_days.add(swift_day)
                else:
                    self._config.work_days.discard(swift_day)

            button.connect("toggled", toggled)
            days_row.pack_start(button, False, False, 0)
        page.pack_start(self._labeled(L.onboarding_work_days, days_row), False, False, 0)

        page.pack_start(separator(), False, False, 0)
        hours_row = hbox(6)
        start = Gtk.Entry()
        start.set_text(self._config.work_start_time)
        start.set_width_chars(6)
        start.set_alignment(0.5)
        end = Gtk.Entry()
        end.set_text(self._config.work_end_time)
        end.set_width_chars(6)
        end.set_alignment(0.5)

        def commit(widget, attr) -> None:
            text = widget.get_text().strip()
            if _valid_time(text):
                h, m = text.split(":")
                setattr(self._config, attr, f"{int(h):02d}:{int(m):02d}")
            else:
                widget.set_text(getattr(self._config, attr))

        start.connect("focus-out-event", lambda w, _e: (commit(w, "work_start_time"), False)[1])
        end.connect("focus-out-event", lambda w, _e: (commit(w, "work_end_time"), False)[1])
        hours_row.pack_start(label(L.onboarding_work_start, "small"), False, False, 0)
        hours_row.pack_start(start, False, False, 0)
        hours_row.pack_start(label("→", "muted"), False, False, 0)
        hours_row.pack_start(end, False, False, 0)
        page.pack_start(hours_row, False, False, 0)

        lunch_row = hbox(6)
        lunch_switch = Gtk.Switch()
        lunch_switch.set_active(self._lunch_enabled)
        lunch_start = Gtk.Entry()
        lunch_start.set_text(self._lunch_start)
        lunch_start.set_width_chars(6)
        lunch_start.set_alignment(0.5)
        lunch_end = Gtk.Entry()
        lunch_end.set_text(self._lunch_end)
        lunch_end.set_width_chars(6)
        lunch_end.set_alignment(0.5)

        def lunch_toggle(widget, _p=None) -> None:
            self._lunch_enabled = bool(widget.get_active())
            lunch_start.set_sensitive(self._lunch_enabled)
            lunch_end.set_sensitive(self._lunch_enabled)

        def lunch_commit(widget, attr) -> None:
            text = widget.get_text().strip()
            if _valid_time(text):
                h, m = text.split(":")
                if attr == "start":
                    self._lunch_start = f"{int(h):02d}:{int(m):02d}"
                else:
                    self._lunch_end = f"{int(h):02d}:{int(m):02d}"

        lunch_switch.connect("notify::active", lunch_toggle)
        lunch_start.connect("focus-out-event", lambda w, _e: (lunch_commit(w, "start"), False)[1])
        lunch_end.connect("focus-out-event", lambda w, _e: (lunch_commit(w, "end"), False)[1])
        lunch_row.pack_start(label(L.onboarding_lunch, "small"), False, False, 0)
        lunch_row.pack_start(lunch_start, False, False, 0)
        lunch_row.pack_start(label("→", "muted"), False, False, 0)
        lunch_row.pack_start(lunch_end, False, False, 0)
        lunch_row.pack_end(lunch_switch, False, False, 0)
        page.pack_start(lunch_row, False, False, 0)
        return page

    def _summary(self) -> Gtk.Box:
        page = self._page()
        page.pack_start(label(L.onboarding_summary, "title"), False, False, 0)
        card = vbox(10)
        card.get_style_context().add_class("card")
        work = self._config.work_minutes
        rest = self._config.break_seconds
        rest_text = (
            f"{rest}{L.unit_seconds}"
            if rest < 60
            else f"{rest // 60}{L.unit_minutes}"
        )
        rows = [
            ("⏱", L.onboarding_interval, f"{work} {L.unit_minutes}"),
            ("☕", L.onboarding_break, rest_text),
            ("🎯", L.onboarding_daily_goal, f"{self._config.daily_goal} {L.unit_times}"),
            (
                "📅",
                L.onboarding_work_days,
                " ".join(_weekday_short(d) for d in weekday_order() if d in self._config.work_days),
            ),
            (
                "🏢",
                L.onboarding_work_schedule,
                f"{self._config.work_start_time} – {self._config.work_end_time}",
            ),
        ]
        for emoji, title, value in rows:
            row = hbox(8)
            row.pack_start(label(emoji, "small"), False, False, 0)
            row.pack_start(label(title, "small"), True, True, 0)
            row.pack_start(label(value, "small bold blue"), False, False, 0)
            card.pack_start(row, False, False, 0)
        page.pack_start(card, False, False, 0)
        page.pack_start(
            label(L.onboarding_settings_hint, "tiny faint", wrap=True), False, False, 0
        )
        return page

    # ---------------------------------------------------------------- helpers

    def _scale(self, attr: str, lo: int, hi: int, step: int, formatter):
        box = hbox(8)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        scale.set_value(getattr(self._config, attr))
        scale.set_draw_value(False)
        scale.set_size_request(-1, -1)
        scale.set_hexpand(True)
        value = label(formatter(int(getattr(self._config, attr))), "small bold blue")

        def changed(widget) -> None:
            v = int(round(widget.get_value()))
            setattr(self._config, attr, v)
            value.set_text(formatter(v))

        scale.connect("value-changed", changed)
        box.pack_start(scale, True, True, 0)
        box.pack_start(value, False, False, 0)
        return box

    def _labeled(self, text: str, control: Gtk.Widget) -> Gtk.Box:
        box = vbox(4)
        box.pack_start(label(text, "small bold"), False, False, 0)
        box.pack_start(control, False, False, 0)
        return box

    def present_window(self) -> None:
        super().present_window()


def _valid_time(text: str) -> bool:
    try:
        h, m = text.split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except (ValueError, AttributeError):
        return False
