"""The alert / break / waiting / off-work card shared by the menu and overlays."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from ..config import AppPhase, format_duration_seconds, format_work_time  # noqa: E402
from ..i18n import L  # noqa: E402
from .theme import theme  # noqa: E402
from .widgets import (  # noqa: E402
    TimerCircle,
    fit_ring_pt,
    hbox,
    label,
    ring_markup,
    spacer,
)


class BreakCard(Gtk.Box):
    def __init__(self, state, *, fullscreen: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.state = state
        self.fullscreen = fullscreen
        if fullscreen:
            self.get_style_context().add_class("fullscreen")
            self.set_halign(Gtk.Align.CENTER)
            self.set_valign(Gtk.Align.CENTER)
        else:
            self.set_size_request(280, -1)
        self._mode: str | None = None
        self._ring: TimerCircle | None = None
        self._time_label: Gtk.Label | None = None
        self._time_pt = 28.0
        self._warning_chip: Gtk.Box | None = None
        self._warning_label: Gtk.Label | None = None
        self.refresh(force=True)

    # ------------------------------------------------------------------ build

    @property
    def mode(self) -> str:
        if self.state.off_work_summary is not None:
            return "summary"
        return self.state.phase.value

    def refresh(self, *, force: bool = False) -> None:
        if not force and self.mode == self._mode:
            self._update()
            return
        self._mode = self.mode
        for child in list(self.get_children()):
            self.remove(child)
        builder = {
            "alerting": self._build_alerting,
            "breaking": self._build_breaking,
            "waiting": self._build_waiting,
            "summary": self._build_summary,
        }.get(self.mode)
        if builder is not None:
            builder()
        self.show_all()
        if self._warning_chip is not None:
            self._warning_chip.set_visible(bool(self.state.break_warning))
        self._update()

    def _update(self) -> None:
        if self._ring is not None:
            total = (
                self.state.current_break_total_seconds
                if self.mode == "breaking"
                else max(1, self.state.config.work_minutes * 60)
            )
            self._ring.ring.set_progress(
                self.state.remaining_seconds / total if total else 0,
                self._phase_color(),
            )
        if self._time_label is not None:
            self._time_label.set_markup(
                ring_markup(self.state.formatted_time, self._time_pt)
            )
        if self._warning_chip is not None and self._warning_label is not None:
            warning = self.state.break_warning
            self._warning_chip.set_visible(bool(warning))
            self._warning_label.set_text(warning or "")

    def _phase_color(self) -> str:
        p = theme.palette
        return p.orange if self.mode == "breaking" else p.blue

    def _sizes(self) -> tuple[int, int]:
        return (240, 64) if self.fullscreen else (130, 32)

    def _pad(self) -> int:
        return 0 if self.fullscreen else 18

    def _primary(self, text: str, color: str, callback, tall: bool = False) -> Gtk.Button:
        button = Gtk.Button(label=text)
        button.get_style_context().add_class("primary" if color == "orange" else "success")
        button.set_size_request(280 if self.fullscreen else 240, 42 if self.fullscreen else 36)
        if self.state.config.shortcut_enabled:
            button.set_tooltip_text("Ctrl+Enter")
        button.connect("clicked", lambda *_: callback())
        return button

    def _pill(self, emoji: str, text: str, callback) -> Gtk.Button:
        button = Gtk.Button()
        button.get_style_context().add_class("pill")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.set_halign(Gtk.Align.CENTER)
        box.pack_start(label(emoji, "small"), False, False, 0)
        box.pack_start(label(text, "small"), False, False, 0)
        button.add(box)
        button.connect("clicked", lambda *_: callback())
        return button

    def _top(self) -> int:
        return 36 if self.fullscreen else 22

    # -------------------------------------------------------------- alerting

    def _build_alerting(self) -> None:
        self.pack_start(spacer(height=self._top()), False, False, 0)
        bell = Gtk.Label()
        bell.set_markup("<span size='48000'>🔔</span>" if self.fullscreen else "<span size='28000'>🔔</span>")
        bell.set_xalign(0.5)
        self.pack_start(bell, False, False, 0)
        self.pack_start(
            label(L.phase_alerting, "subtitle" if not self.fullscreen else "muted", xalign=0.5),
            False,
            False,
            0,
        )
        if self.state.current_reminder:
            reminder = label(
                self.state.current_reminder,
                "title" if not self.fullscreen else "window-title",
                wrap=True,
                xalign=0.5,
            )
            reminder.set_justify(Gtk.Justification.CENTER)
            reminder.set_max_width_chars(30)
            reminder.set_margin_start(16)
            reminder.set_margin_end(16)
            reminder.set_margin_top(6)
            self.pack_start(reminder, False, False, 0)
        self.pack_start(spacer(height=14), False, False, 0)

        self.pack_start(
            self._primary(L.alert_confirm_break, "orange", self.state.confirm_break),
            False,
            False,
            0,
        )
        self.pack_start(spacer(height=12), False, False, 0)

        self.pack_start(
            self._pill(
                "⏳",
                L.delay_break(format_duration_seconds(self.state.delay_seconds)),
                self.state.delay_break,
            ),
            False,
            False,
            0,
        )
        self.pack_start(spacer(height=6), False, False, 0)

        resets = hbox(6)
        resets.set_halign(Gtk.Align.CENTER)
        for text, kind in (
            (L.reset_short_break, "short"),
            (L.reset_long_break, "long"),
        ):
            button = Gtk.Button(label=text)
            button.get_style_context().add_class("pill")
            button.connect(
                "clicked", lambda _b, k=kind: self.state.restart_cycle(k)
            )
            resets.pack_start(button, False, False, 0)
        self.pack_start(resets, False, False, 0)

        self.pack_start(spacer(height=10), False, False, 0)
        self.pack_start(
            self._pill("☕️", L.alert_already_rested, self.state.mark_break_completed),
            False,
            False,
            0,
        )
        self.pack_start(spacer(height=self._pad() + 6), False, False, 0)

    # --------------------------------------------------------------- breaking

    def _build_breaking(self) -> None:
        size, font = self._sizes()
        self.pack_start(spacer(height=10 if not self.fullscreen else 0), False, False, 0)
        self._ring = TimerCircle(size, 5 if self.fullscreen else 4)
        time_label = label("", "mono")
        probe = "8" * len(self.state.formatted_time)
        self._time_pt = fit_ring_pt(
            time_label,
            probe,
            self._ring.content_radius,
            max_pt=58.0 if self.fullscreen else 28.0,
        )
        time_label.set_markup(ring_markup(self.state.formatted_time, self._time_pt))
        self._time_label = time_label
        phase_label = label(L.phase_breaking, "faint tiny", xalign=0.5)
        self._ring.set_content(time_label, phase_label, spacing=2)
        self._ring.set_halign(Gtk.Align.CENTER)
        self.pack_start(self._ring, False, False, 0)
        self.pack_start(spacer(height=10), False, False, 0)

        if self.state.current_reminder:
            reminder = label(
                self.state.current_reminder,
                "small bold" if not self.fullscreen else "title",
                wrap=True,
                xalign=0.5,
            )
            reminder.set_justify(Gtk.Justification.CENTER)
            reminder.set_max_width_chars(30)
            reminder.set_margin_start(14)
            reminder.set_margin_end(14)
            reminder.set_opacity(0.75)
            self.pack_start(reminder, False, False, 0)
            self.pack_start(spacer(height=8), False, False, 0)

        if self.state.current_break_activity:
            activity = hbox(6)
            activity.set_halign(Gtk.Align.CENTER)
            activity.pack_start(
                label(self.state.current_break_activity.icon, "small"), False, False, 0
            )
            text = label(
                self.state.current_break_activity.text,
                "small green" if not self.fullscreen else "muted",
                wrap=True,
            )
            activity.pack_start(text, False, False, 0)
            self.pack_start(activity, False, False, 0)

        self._warning_chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._warning_chip.get_style_context().add_class("chip-orange")
        self._warning_chip.set_halign(Gtk.Align.CENTER)
        self._warning_chip.set_margin_top(8)
        self._warning_chip.pack_start(label("⚠️", "tiny"), False, False, 0)
        self._warning_label = label(self.state.break_warning, "tiny orange", wrap=True)
        self._warning_chip.pack_start(self._warning_label, False, False, 0)
        self.pack_start(self._warning_chip, False, False, 0)
        self.pack_start(spacer(height=self._pad()), False, False, 0)

    # ---------------------------------------------------------------- waiting

    def _build_waiting(self) -> None:
        self.pack_start(spacer(height=self._top()), False, False, 0)
        check = label("✅", "", xalign=0.5)
        if self.fullscreen:
            check.set_markup("<span size='48000'>✅</span>")
        self.pack_start(check, False, False, 0)
        prompt = label(
            L.break_over_prompt,
            "small" if not self.fullscreen else "window-title",
            wrap=True,
            xalign=0.5,
        )
        prompt.set_justify(Gtk.Justification.CENTER)
        prompt.set_max_width_chars(28)
        prompt.set_margin_start(16)
        prompt.set_margin_end(16)
        prompt.set_margin_top(8)
        self.pack_start(prompt, False, False, 0)
        self.pack_start(spacer(height=16), False, False, 0)
        self.pack_start(
            self._primary(L.alert_im_back, "green", self.state.confirm_return),
            False,
            False,
            0,
        )
        self.pack_start(spacer(height=self._pad() + 8), False, False, 0)

    # ---------------------------------------------------------------- summary

    def _build_summary(self) -> None:
        summary = self.state.off_work_summary
        if summary is None:
            return
        self.pack_start(spacer(height=self._top()), False, False, 0)
        sunset = label("🌇", "", xalign=0.5)
        if self.fullscreen:
            sunset.set_markup("<span size='48000'>🌇</span>")
        self.pack_start(sunset, False, False, 0)
        self.pack_start(
            label(L.offwork_title, "title" if not self.fullscreen else "window-title", xalign=0.5),
            False,
            False,
            0,
        )
        stats = label(
            L.offwork_stats(format_work_time(summary.work_minutes), summary.break_count),
            "small muted",
            wrap=True,
            xalign=0.5,
        )
        stats.set_justify(Gtk.Justification.CENTER)
        stats.set_margin_start(18)
        stats.set_margin_end(18)
        stats.set_margin_top(8)
        self.pack_start(stats, False, False, 0)
        self.pack_start(
            label(L.offwork_footer, "tiny faint", xalign=0.5), False, False, 0
        )
        self.pack_start(spacer(height=14), False, False, 0)
        button = self._primary(
            L.offwork_dismiss, "orange", self.state.dismiss_off_work_summary
        )
        self.pack_start(button, False, False, 0)
        self.pack_start(spacer(height=self._pad() + 6), False, False, 0)
