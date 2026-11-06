"""Application state machine — a direct port of ``AppState.swift``.

The state object owns the work/break countdown, persistence of the current
phase, day-change and quiet-hour handling, statistics and sounds.  It knows
nothing about GTK: the UI subscribes to events through :meth:`add_listener`
and calls the public action methods.
"""

from __future__ import annotations

import copy
import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .config import (
    ALL_BADGES,
    ALL_TOTAL_BADGES,
    BREAK_ACTIVITIES,
    AppConfig,
    AppPhase,
    Badge,
    BreakPosition,
    QuietHourPeriod,
    format_work_time,
)
from .db import Database, today_string
from .i18n import L
from .idle import IdleMonitor
from .notify import notifier as default_notifier
from .sound import player as default_sound_player

try:  # GLib is always present in the GTK application; tests may run without.
    from gi.repository import GLib
except Exception:  # pragma: no cover - fallback used by unit tests
    GLib = None


# ---------------------------------------------------------------------------
# Scheduling helpers


class Scheduler:
    """Minimal main-loop timer abstraction.

    Uses GLib when available, otherwise ``threading.Timer`` (used by tests).
    """

    def __init__(self) -> None:
        self._handles: dict[int, Any] = {}
        self._next = 1
        import threading

        self._lock = threading.Lock()

    def every(self, seconds: float, callback: Callable[[], bool | None]) -> int:
        if GLib is not None:
            def _run() -> bool:
                try:
                    result = callback()
                except Exception:
                    result = False
                return result is not False

            handle = GLib.timeout_add(max(1, int(seconds * 1000)), _run)
            self._handles[handle] = ("glib", handle)
            return handle

        import threading

        cancelled = {"value": False}

        def _loop() -> None:
            while not cancelled["value"]:
                time.sleep(seconds)
                if cancelled["value"]:
                    return
                try:
                    if callback() is False:
                        return
                except Exception:
                    return

        with self._lock:
            handle = self._next
            self._next += 1
        thread = threading.Thread(target=_loop, daemon=True)
        thread.start()
        self._handles[handle] = ("thread", cancelled)
        return handle

    def later(self, seconds: float, callback: Callable[[], None]) -> int:
        if GLib is not None:
            handle = GLib.timeout_add(max(1, int(seconds * 1000)), self._once(callback))
            self._handles[handle] = ("glib", handle)
            return handle

        import threading

        cancelled = {"value": False}

        def _run() -> None:
            timer = threading.Timer(seconds, callback)
            timer.daemon = True
            timer.start()
            cancelled["timer"] = timer

        with self._lock:
            handle = self._next
            self._next += 1
        _run()
        self._handles[handle] = ("thread", cancelled)
        return handle

    @staticmethod
    def _once(callback: Callable[[], None]) -> Callable[[], bool]:
        def _run() -> bool:
            try:
                callback()
            except Exception:
                pass
            return False

        return _run

    def cancel(self, handle: int | None) -> None:
        if handle is None:
            return
        entry = self._handles.pop(handle, None)
        if entry is None:
            return
        kind, value = entry
        if kind == "glib" and GLib is not None:
            try:
                # One-shot sources remove themselves; calling source_remove()
                # for them prints a GLib warning.  Only remove live sources.
                if GLib.MainContext.default().find_source_by_id(value) is not None:
                    GLib.source_remove(value)
            except Exception:
                pass
        elif kind == "thread":
            value["value"] = True
            timer = value.get("timer")
            if timer is not None:
                timer.cancel()

    def cancel_all(self) -> None:
        for handle in list(self._handles):
            self.cancel(handle)


@dataclass
class OffWorkSummary:
    work_minutes: int
    break_count: int


class AppState:
    ALERT_SOUND_REPEAT_INTERVAL = 0.5
    BREAK_KINDS = ("short", "long")

    def __init__(
        self,
        db: Database | None = None,
        *,
        idle: IdleMonitor | None = None,
        sounds: Any = None,
        notifier: Any = None,
        scheduler: Scheduler | None = None,
    ) -> None:
        self.db = db or Database()
        self.config: AppConfig = self.db.load_config()
        self._idle = idle or IdleMonitor()
        self._sounds = sounds if sounds is not None else default_sound_player
        self._notifier = notifier if notifier is not None else default_notifier
        self._scheduler = scheduler or Scheduler()

        # Observable state (names mirror AppState.swift where practical).
        self.phase: AppPhase = AppPhase.WORKING
        self.remaining_seconds = 0
        self.today_done = 0
        self.current_streak = 0
        self.max_streak = 0
        self.break_warning = ""
        self.week_data: list[tuple[str, int]] = []
        self.total_count = 0
        self.today_work_minutes = 0
        self.week_work_data: list[tuple[str, int]] = []
        self.is_in_quiet_hours = False
        self.goal_auto_stopped = False
        self.current_break_activity = None
        self.current_reminder: str | None = None
        self.off_work_summary: OffWorkSummary | None = None
        self.celebrate_badge: Badge | None = None
        self.next_break_override: str | None = None
        self.quiet_remaining_seconds = 0
        self.completed_cycles = 0
        self.current_break_total_seconds = 0
        self.snooze_active = False
        self.restart_prompt = False
        self.current_session_id: int | None = None

        # Internal state.
        self._current_session_work_config = 0
        self._break_started_at: float | None = None
        self._break_monitor_started_at: float | None = None
        self._target_time: float = time.monotonic()
        self._paused_remaining = 0
        self._paused_phase: AppPhase | None = None
        self._pre_break_notice_fired = False
        self._skipped_quiet_reasons: set[str] = set()
        self._auto_quiet_paused = False
        self._last_outside_work_active: bool | None = None
        self._off_work_summary_shown_date: str | None = None
        self._last_active_date = today_string()
        self._last_work_minutes_refresh = 0.0
        self._last_saved_config = copy.deepcopy(self.config)
        self._alert_sound_token = 0
        self._pending_badge: Badge | None = None
        self._screen_lock_time: float | None = None
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._timers: dict[str, int | None] = {
            "work": None,
            "break": None,
            "quiet": None,
            "quiet_countdown": None,
            "autosave": None,
            "restart_prompt": None,
            "offwork_dismiss": None,
            "badge": None,
        }
        self._badge_timeouts: list[int] = []
        self._started = False

    # ------------------------------------------------------------------ events

    def add_listener(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    def emit(self, event: str, **payload: Any) -> None:
        for listener in list(self._listeners):
            try:
                listener(event, payload)
            except Exception:
                # A broken window must never take the timer down with it, but
                # surface the traceback so runtime integration bugs are visible.
                import traceback

                traceback.print_exc()

    # -------------------------------------------------------------- convenient

    @property
    def formatted_time(self) -> str:
        minutes, seconds = divmod(max(0, self.remaining_seconds), 60)
        return f"{minutes:02d}:{seconds:02d}"

    @property
    def formatted_quiet_time(self) -> str:
        hours, rem = divmod(max(0, self.quiet_remaining_seconds), 3600)
        minutes, seconds = divmod(rem, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @property
    def phase_icon(self) -> str:
        return {
            AppPhase.WORKING: "🟢",
            AppPhase.ALERTING: "🟡",
            AppPhase.BREAKING: "🟡",
            AppPhase.WAITING: "🔴",
            AppPhase.PAUSED: "⏸",
        }.get(self.phase, "🟢")

    @property
    def phase_label(self) -> str:
        if self.phase is AppPhase.WORKING:
            return L.phase_snoozed if self.snooze_active else L.phase_working
        return {
            AppPhase.ALERTING: L.phase_alerting,
            AppPhase.BREAKING: L.phase_breaking,
            AppPhase.WAITING: L.phase_waiting,
            AppPhase.PAUSED: L.phase_paused,
        }.get(self.phase, "")

    @property
    def goal_progress(self) -> float:
        goal = max(1, self.config.daily_goal)
        return min(self.today_done, goal) / goal

    @property
    def encourage_text(self) -> str:
        gap = self.db.days_since_last_goal(self.config.daily_goal)
        if gap == 0:
            return L.encourage_goal_met
        if gap == -1:
            return L.encourage_no_record
        if gap == 1:
            return L.encourage_yesterday
        if 1 < gap <= 3:
            return L.encourage_gap_short(gap)
        return L.encourage_gap_long(gap)

    @property
    def earned_badge(self) -> Badge | None:
        found = None
        for badge in ALL_BADGES:
            if self.max_streak >= badge.days:
                found = badge
        return found

    @property
    def next_badge(self) -> Badge | None:
        for badge in ALL_BADGES:
            if self.max_streak < badge.days:
                return badge
        return None

    @property
    def earned_total_badges(self) -> list[Badge]:
        return [badge for badge in ALL_TOTAL_BADGES if self.total_count >= badge.days]

    @property
    def next_total_badge(self) -> Badge | None:
        for badge in ALL_TOTAL_BADGES:
            if self.total_count < badge.days:
                return badge
        return None

    @property
    def upcoming_break_is_long(self) -> bool:
        if self.next_break_override is not None:
            return self.next_break_override == "long"
        return (
            self.config.long_break_enabled
            and self.config.long_break_interval > 0
            and self.completed_cycles > 0
            and self.completed_cycles % self.config.long_break_interval == 0
        )

    @property
    def delay_seconds(self) -> int:
        """10% of the configured work duration, used by "delay break"."""
        return max(1, int(round(self.config.work_minutes * 60 * 0.1)))

    @property
    def current_session_work_minutes(self) -> int:
        if self.current_session_id is None:
            return 0
        if self.snooze_active:
            return self._current_session_work_config
        if self.phase is AppPhase.WORKING:
            return max(
                0, self._current_session_work_config * 60 - self.remaining_seconds
            ) // 60
        if self.phase is AppPhase.PAUSED and self._paused_phase is AppPhase.WORKING:
            return max(
                0, self._current_session_work_config * 60 - self._paused_remaining
            ) // 60
        return 0

    @property
    def overtime_active(self) -> bool:
        return bool(self._skipped_quiet_reasons)

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Restore the persisted phase and start background timers."""
        if self._started:
            return
        self._started = True
        self.restore_timer_state()
        self.refresh_stats()
        self._start_quiet_check_timer()
        if not self.db.is_onboarding_completed():
            self.emit("onboarding")
        self._refresh_holiday_calendar_if_stale()
        self.emit("state")

    def shutdown(self) -> None:
        self._scheduler.cancel_all()
        self._stop_badge_timeouts()
        if self.current_session_id is not None:
            try:
                self.db.end_work(self.current_session_id)
            except Exception:
                pass
            self.current_session_id = None
        try:
            self.save_timer_state()
            self.db.save_config(self.config)
        except Exception:
            pass
        self.emit("shutdown")

    def _set_timer(self, key: str, handle: int | None) -> None:
        old = self._timers.get(key)
        if old is not None:
            self._scheduler.cancel(old)
        self._timers[key] = handle

    # ------------------------------------------------------------ timer restore

    def restore_timer_state(self) -> None:
        saved_date = self.db.timer_save_date()
        today = today_string()

        if saved_date and saved_date != today:
            self.db.clear_timer_state()
            self.handle_day_change()
            return

        self.db.close_today_orphan_sessions()
        phase, remaining = self.db.load_timer_state()
        seconds = remaining or 0

        if phase == "working" and seconds > 0:
            self.phase = AppPhase.WORKING
            self._current_session_work_config = self.config.work_minutes
            self.remaining_seconds = seconds
            self._target_time = time.monotonic() + seconds
            self.current_session_id = self.db.start_session(
                self.config.work_minutes,
                self.config.break_seconds,
                self.config.daily_goal,
            )
            self._start_work_tick()
        elif phase == "paused" and seconds > 0:
            self.phase = AppPhase.PAUSED
            self._current_session_work_config = self.config.work_minutes
            self._paused_remaining = seconds
            self._paused_phase = AppPhase.WORKING
            self.remaining_seconds = seconds
        elif phase == "alerting":
            self.current_reminder = random.choice(self.config.reminders) if self.config.reminders else L.default_break_reminder
            if self.config.break_confirm:
                self.phase = AppPhase.ALERTING
                self.remaining_seconds = 0
                self.save_timer_state()
                self.emit("alert")
                self.emit("state")
            else:
                self.current_session_id = self.db.start_session(
                    self.config.work_minutes,
                    self.config.break_seconds,
                    self.config.daily_goal,
                )
                self.start_break()
        else:
            self.start_work()

    def save_timer_state(self) -> None:
        if self.phase is AppPhase.WORKING:
            persist_phase = "alerting" if self.snooze_active else "working"
            self.db.save_timer_state(
                persist_phase, None if self.snooze_active else self.remaining_seconds
            )
        elif self.phase is AppPhase.PAUSED:
            self.db.save_timer_state("paused", self._paused_remaining)
        else:
            self.db.save_timer_state("alerting", 0)

    # -------------------------------------------------------------- work phase

    def start_work(self) -> None:
        self.goal_auto_stopped = False
        self._pre_break_notice_fired = False
        self.snooze_active = False
        self.phase = AppPhase.WORKING
        self._current_session_work_config = self.config.work_minutes
        duration = self.config.work_minutes * 60
        self._target_time = time.monotonic() + duration
        self.remaining_seconds = duration
        self.current_break_total_seconds = 0
        self.current_session_id = self.db.start_session(
            self.config.work_minutes,
            self.config.break_seconds,
            self.config.daily_goal,
        )
        self._last_work_minutes_refresh = 0.0
        self.save_timer_state()
        self._start_work_tick()
        self.emit("state")

    def _start_work_tick(self) -> None:
        self._set_timer("break", None)
        self._set_timer("work", self._scheduler.every(1.0, self._tick_work))

    def _tick_work(self) -> bool | None:
        if self.phase is not AppPhase.WORKING:
            return False
        old_value = self.remaining_seconds
        new_value = max(0, int(round(self._target_time - time.monotonic())))
        if new_value != self.remaining_seconds:
            self.remaining_seconds = new_value
        self._maybe_fire_pre_break_notice(old_value, new_value)

        if time.monotonic() - self._last_work_minutes_refresh >= 60:
            minutes = self.db.today_work_minutes() + self.current_session_work_minutes
            self.today_work_minutes = minutes
            today = today_string()
            for index, (day, _count) in enumerate(self.week_work_data):
                if day == today:
                    self.week_work_data[index] = (day, minutes)
            self._last_work_minutes_refresh = time.monotonic()
            self.emit("stats")

        if self.remaining_seconds <= 0:
            self._on_work_done()
            return False
        self.emit("tick")
        return None

    def _maybe_fire_pre_break_notice(self, old_value: int, new_value: int) -> None:
        if not self.config.pre_break_notice_enabled or self._pre_break_notice_fired:
            return
        lead = (
            self.config.pre_break_notice_long_seconds
            if self.upcoming_break_is_long
            else self.config.pre_break_notice_seconds
        )
        if not (lead > 0 and old_value > lead >= new_value and new_value > 0):
            return
        self._pre_break_notice_fired = True
        title = L.prebreak_title(format_work_time(max(1, new_value // 60)) if new_value >= 60 else f"{new_value} {L.unit_seconds}", self.upcoming_break_is_long)
        body = L.prebreak_body
        self._notifier.send(title, body, urgency="normal", icon="alarm-symbolic")
        if self.config.sound_enabled:
            self._sounds.play(self.config.alert_sound)
        self.emit("prebreak", seconds=new_value, is_long=self.upcoming_break_is_long)

    def _on_work_done(self) -> None:
        self.snooze_active = False
        self.current_reminder = (
            random.choice(self.config.reminders)
            if self.config.reminders
            else L.default_break_reminder
        )
        self.play_alert_sound()
        if self.config.break_confirm:
            self.phase = AppPhase.ALERTING
            self.remaining_seconds = 0
            self.save_timer_state()
            self.emit("alert")
            self.emit("state")
        else:
            self.start_break()

    def confirm_break(self) -> None:
        if self.phase not in (AppPhase.ALERTING, AppPhase.WORKING):
            return
        self._cancel_alert_sound_burst()
        self.start_break()

    def delay_break(self) -> None:
        """Postpone the next break by ``delay_seconds``.

        From ALERTING this resets the reminder; from WORKING it pushes the
        upcoming break back so the feature stays reachable even when the break
        confirmation card never appears (e.g. eye-care mode).
        """
        self._cancel_alert_sound_burst()
        if self.phase is AppPhase.ALERTING:
            self.emit("hide")
            self.snooze_active = True
            self.phase = AppPhase.WORKING
            self._target_time = time.monotonic() + self.delay_seconds
            self.remaining_seconds = self.delay_seconds
            self.save_timer_state()
            self._start_work_tick()
            self.emit("state")
        elif self.phase is AppPhase.WORKING:
            self._target_time += self.delay_seconds
            self.remaining_seconds = max(
                0, int(round(self._target_time - time.monotonic()))
            )
            self.save_timer_state()
            self.emit("state")

    # ------------------------------------------------------------- break phase

    def start_break(self) -> None:
        self.phase = AppPhase.BREAKING
        self._set_timer("work", None)
        self.break_warning = ""
        self._break_started_at = time.monotonic()
        self._break_monitor_started_at = time.monotonic()
        self.current_break_activity = random.choice(BREAK_ACTIVITIES)
        self.current_reminder = (
            random.choice(self.config.reminders) if self.config.reminders else None
        )
        is_long = self.upcoming_break_is_long
        self.next_break_override = None
        seconds = (
            self.config.long_break_seconds
            if is_long
            else self.config.break_seconds
        )
        self.remaining_seconds = seconds
        self.current_break_total_seconds = seconds

        if self.current_session_id is not None:
            self.db.end_work(self.current_session_id)
            self.db.start_session_break(self.current_session_id)

        self.save_timer_state()
        self._set_timer("break", self._scheduler.every(1.0, self._tick_break))
        self.emit("break")
        self.emit("state")

    def _tick_break(self) -> bool | None:
        if self.phase is not AppPhase.BREAKING:
            return False
        idle = self._idle.idle_seconds()
        now = time.monotonic()
        grace = (
            self._break_monitor_started_at is not None
            and now - self._break_monitor_started_at < 4
        )
        should_warn = idle < 3 and self.remaining_seconds > 0 and not grace
        if should_warn:
            if not self.break_warning:
                self.break_warning = L.break_detected_pause
                self.play_break_detect_sound()
                self.emit("tick")
            self.emit("break_warning")
            return None

        if self.break_warning:
            self.break_warning = ""
            self.emit("break_warning")
        if self.remaining_seconds > 0:
            self.remaining_seconds -= 1
        self.emit("tick")
        if self.remaining_seconds <= 0:
            self._on_break_done()
            return False
        return None

    def _on_break_done(self) -> None:
        self.phase = AppPhase.WAITING
        self.remaining_seconds = 0
        self.break_warning = ""
        actual_seconds = None
        if self._break_started_at is not None:
            actual_seconds = int(time.monotonic() - self._break_started_at)
        if self.current_session_id is not None:
            self.db.end_session_break(
                self.current_session_id, actual_seconds
            )
        self.completed_cycles += 1
        old_streak, old_total = self.max_streak, self.total_count
        self.db.add_record()
        self.refresh_stats()
        self._pending_badge = self._detect_new_badge(old_streak, old_total)
        self.emit("waiting")
        self.emit("state")

    def mark_break_completed(self) -> None:
        if self.phase is not AppPhase.ALERTING:
            return
        self._set_timer("work", None)
        self._cancel_alert_sound_burst()
        self.emit("hide")
        if self.current_session_id is not None:
            session = self.current_session_id
            self.db.end_work(session)
            self.db.start_session_break(session)
            self.db.end_session_break(session, None)
        self.completed_cycles += 1
        old_streak, old_total = self.max_streak, self.total_count
        self.db.add_record()
        self.refresh_stats()
        badge = self._detect_new_badge(old_streak, old_total)
        self.current_session_id = None
        self._break_started_at = None

        if self.config.auto_pause_on_goal and self.today_done >= self.config.daily_goal:
            self.goal_auto_stopped = True
            self.phase = AppPhase.PAUSED
            self.remaining_seconds = 0
            self.save_timer_state()
            self.emit("state")
        else:
            self.start_work()

        if badge:
            self._schedule_badge(badge, 0.3)

    def confirm_return(self) -> None:
        self.emit("hide")
        badge = self._pending_badge
        self._pending_badge = None
        if self.config.auto_pause_on_goal and self.today_done >= self.config.daily_goal:
            self.current_session_id = None
            self.goal_auto_stopped = True
            self.phase = AppPhase.PAUSED
            self.remaining_seconds = 0
            self._set_timer("work", None)
            self._set_timer("break", None)
            self.save_timer_state()
            self.emit("state")
        else:
            self.start_work()
        if badge:
            self._schedule_badge(badge, 0.3)

    # -------------------------------------------------------- pause / actions

    def toggle_pause(self) -> None:
        if self.phase is AppPhase.PAUSED and self._paused_phase is not None:
            if self.is_in_quiet_hours:
                return
            previous = self._paused_phase
            self._paused_phase = None
            self.phase = previous
            self._target_time = time.monotonic() + self._paused_remaining
            self.remaining_seconds = self._paused_remaining
            if previous is AppPhase.BREAKING:
                self._break_monitor_started_at = time.monotonic()
                self._set_timer("break", self._scheduler.every(1.0, self._tick_break))
                self.emit("break")
            else:
                self._start_work_tick()
            self.save_timer_state()
            self.emit("state")
        elif self.phase in (AppPhase.WORKING, AppPhase.BREAKING):
            self._paused_remaining = self.remaining_seconds
            self._paused_phase = self.phase
            self.phase = AppPhase.PAUSED
            self._set_timer("work", None)
            self._set_timer("break", None)
            self.save_timer_state()
            self.emit("hide")
            self.emit("state")

    def manual_break(self) -> None:
        if self.phase is not AppPhase.WORKING:
            return
        self._set_timer("work", None)
        self._cancel_alert_sound_burst()
        if self.current_session_id is not None:
            self.db.end_work(self.current_session_id)
        self.start_break()

    def reset(self) -> None:
        self._set_timer("work", None)
        self._set_timer("break", None)
        self._cancel_alert_sound_burst()
        self.emit("hide")
        self._paused_phase = None
        self._auto_quiet_paused = False
        if self.current_session_id is not None:
            self.db.end_work(self.current_session_id)
            self.current_session_id = None
        self.is_in_quiet_hours = False
        self.start_work()
        self.check_quiet_hours()

    def restart_current_phase(self) -> None:
        self._set_timer("work", None)
        self._set_timer("break", None)
        self._cancel_alert_sound_burst()
        self.emit("hide")
        self._paused_phase = None
        if self.current_session_id is not None:
            self.db.end_work(self.current_session_id)
            self.current_session_id = None
        self.start_work()
        self.check_quiet_hours()

    def restart_cycle(self, next_break: str) -> None:
        """Restart the work countdown; the break after it uses ``next_break``."""
        if next_break not in self.BREAK_KINDS:
            raise ValueError(next_break)
        if self.phase not in (AppPhase.WORKING, AppPhase.ALERTING):
            return
        if self.is_in_quiet_hours:
            return
        self.next_break_override = next_break
        self.restart_current_phase()

    # ------------------------------------------------------------- screen lock

    def note_screen_lock(self) -> None:
        self._screen_lock_time = time.monotonic()

    def handle_screen_unlock(self) -> None:
        if not self.config.reset_on_screen_lock:
            return
        if self._screen_lock_time is None:
            return
        elapsed = time.monotonic() - self._screen_lock_time
        self._screen_lock_time = None
        if elapsed < 10:
            return
        if self.phase is AppPhase.PAUSED:
            return
        self._set_timer("work", None)
        self._set_timer("break", None)
        self._cancel_alert_sound_burst()
        self.emit("hide")
        if self.current_session_id is not None:
            self.db.end_work(self.current_session_id)
            self.current_session_id = None
        self.start_work()
        self.check_quiet_hours()

    # --------------------------------------------------------------- statistics

    def refresh_stats(self) -> None:
        self.today_done = self.db.today_count()
        self.current_streak = self.db.streak_days(self.config.daily_goal)
        self.max_streak = self.db.max_streak_days(self.config.daily_goal)
        self.week_data = self.db.recent_7_days_counts()
        self.total_count = self.db.total_count()
        self.today_work_minutes = (
            self.db.today_work_minutes() + self.current_session_work_minutes
        )
        self.week_work_data = self.db.recent_7_days_work_minutes()
        if self.current_session_work_minutes > 0:
            today = today_string()
            for index, (day, minutes) in enumerate(self.week_work_data):
                if day == today:
                    self.week_work_data[index] = (day, minutes + self.current_session_work_minutes)
        self.emit("stats")

    def _detect_new_badge(self, old_streak: int, old_total: int) -> Badge | None:
        for badge in ALL_BADGES:
            if self.max_streak >= badge.days and old_streak < badge.days:
                return badge
        for badge in ALL_TOTAL_BADGES:
            if self.total_count >= badge.days and old_total < badge.days:
                return badge
        return None

    def _schedule_badge(self, badge: Badge, delay: float) -> None:
        def show() -> None:
            self.celebrate_badge = badge
            self.play_sound("Glass")
            self.emit("celebration", badge=badge)

        handle = self._scheduler.later(delay, show)
        self._badge_timeouts.append(handle)

    def _stop_badge_timeouts(self) -> None:
        for handle in self._badge_timeouts:
            self._scheduler.cancel(handle)
        self._badge_timeouts.clear()

    def dismiss_celebration(self) -> None:
        self.celebrate_badge = None
        self.emit("celebration_dismissed")

    # ------------------------------------------------------------- quiet hours

    def _start_quiet_check_timer(self) -> None:
        self._set_timer(
            "quiet", self._scheduler.every(30.0, self._quiet_check_tick)
        )
        self.check_quiet_hours()

    def _quiet_check_tick(self) -> bool | None:
        self.check_day_change()
        self.check_quiet_hours()
        return None

    def _current_active_quiet_reasons(self) -> set[str]:
        now = datetime.now()
        reasons: set[str] = set()
        if not self.config.is_work_day(now):
            reasons.add("non_work_day")
        for period in self.config.quiet_hours:
            if period.is_active(now):
                reasons.add(f"qh:{period.start}-{period.end}")
        if self.config.work_hours_enabled:
            work_period = QuietHourPeriod(
                start=self.config.work_start_time, end=self.config.work_end_time
            )
            if not work_period.is_active(now):
                reasons.add("outside_work_hours")
        return reasons

    def activate_overtime(self) -> None:
        reasons = self._current_active_quiet_reasons()
        self._skipped_quiet_reasons |= reasons
        if self.is_in_quiet_hours:
            self.is_in_quiet_hours = False
            self._stop_quiet_countdown()
            self._auto_quiet_paused = False
            self.start_work()

    def check_quiet_hours(self) -> None:
        if not self._started:
            # Before start() the config/timers are not ready; still allow an
            # explicit call from tests and settings changes.
            pass
        active_reasons = self._current_active_quiet_reasons()
        outside_now = "outside_work_hours" in active_reasons
        work_just_ended = (
            self._last_outside_work_active is False and outside_now
        )
        self._last_outside_work_active = outside_now

        self._skipped_quiet_reasons &= active_reasons
        non_skipped = active_reasons - self._skipped_quiet_reasons
        should_pause = bool(non_skipped)

        if should_pause and not self.is_in_quiet_hours:
            self.is_in_quiet_hours = True
            if self.phase is AppPhase.BREAKING:
                self._set_timer("break", None)
                self._cancel_alert_sound_burst()
                self.break_warning = ""
                self.emit("hide")
                actual = None
                if self._break_started_at is not None:
                    actual = int(time.monotonic() - self._break_started_at)
                if self.current_session_id is not None:
                    self.db.end_session_break(
                        self.current_session_id, actual
                    )
                    self.current_session_id = None
                self.phase = AppPhase.PAUSED
                self._auto_quiet_paused = True
            elif self.phase is AppPhase.ALERTING:
                self._cancel_alert_sound_burst()
                self.emit("hide")
                self.phase = AppPhase.PAUSED
                self._auto_quiet_paused = True
            elif self.phase is AppPhase.WAITING:
                self.emit("hide")
                self._pending_badge = None
                self.phase = AppPhase.PAUSED
                self._auto_quiet_paused = True
            elif self.phase is AppPhase.WORKING:
                self._set_timer("work", None)
                if self.current_session_id is not None:
                    self.db.end_work(self.current_session_id)
                    self.current_session_id = None
                self.phase = AppPhase.PAUSED
                self._auto_quiet_paused = True
            self._start_quiet_countdown()
            self.emit("state")
        elif not should_pause and self.is_in_quiet_hours:
            self.is_in_quiet_hours = False
            self._stop_quiet_countdown()
            self._auto_quiet_paused = False
            self.start_work()

        if self.is_in_quiet_hours:
            self.update_quiet_remaining()
        if work_just_ended:
            self._maybe_show_off_work_summary()

    def resume_from_goal_stop(self) -> None:
        self.goal_auto_stopped = False
        self.start_work()

    def _start_quiet_countdown(self) -> None:
        self.update_quiet_remaining()
        self._set_timer(
            "quiet_countdown",
            self._scheduler.every(30.0, self._quiet_countdown_tick),
        )

    def _quiet_countdown_tick(self) -> bool | None:
        if not self.is_in_quiet_hours:
            return False
        self.update_quiet_remaining()
        if self.quiet_remaining_seconds <= 0:
            self.check_quiet_hours()
        return None

    def _stop_quiet_countdown(self) -> None:
        self._set_timer("quiet_countdown", None)
        self.quiet_remaining_seconds = 0

    def update_quiet_remaining(self) -> None:
        now = datetime.now()
        end_dates: list[datetime] = []
        for period in self.config.quiet_hours:
            if period.is_active(now):
                end = period.end_date(now)
                if end is not None:
                    end_dates.append(end)
        if self.config.work_hours_enabled and not self.overtime_active:
            work_period = QuietHourPeriod(
                start=self.config.work_start_time, end=self.config.work_end_time
            )
            if not work_period.is_active(now):
                end = work_period.start_date(now)
                if end is not None:
                    end_dates.append(end)
        if not self.config.is_work_day(now):
            tomorrow = datetime.combine(
                now.date() + timedelta(days=1), datetime.min.time()
            )
            end_dates.append(tomorrow)
        if end_dates:
            nearest = min(end_dates)
            self.quiet_remaining_seconds = max(
                0, int((nearest - now).total_seconds())
            )
        else:
            self.quiet_remaining_seconds = 0

    # -------------------------------------------------------- off-work summary

    def _maybe_show_off_work_summary(self) -> None:
        if not (self.config.off_work_summary_enabled and self.config.work_hours_enabled):
            return
        if not self.config.is_work_day(datetime.now()):
            return
        today = today_string()
        if self._off_work_summary_shown_date == today:
            return
        self._off_work_summary_shown_date = today
        minutes = self.db.today_work_minutes() + self.current_session_work_minutes
        self.off_work_summary = OffWorkSummary(minutes, self.today_done)
        self.play_alert_sound()
        self.emit("summary", summary=self.off_work_summary)
        self.emit("state")
        self._set_timer(
            "offwork_dismiss",
            self._scheduler.later(25.0, self.dismiss_off_work_summary),
        )

    def dismiss_off_work_summary(self) -> None:
        if self.off_work_summary is None:
            return
        self._set_timer("offwork_dismiss", None)
        self.off_work_summary = None
        self.emit("hide")
        self.emit("state")

    # ---------------------------------------------------------------- day change

    def handle_day_change(self) -> None:
        self._last_active_date = today_string()
        self._set_timer("work", None)
        self._set_timer("break", None)
        self._cancel_alert_sound_burst()
        self.emit("hide")
        self.phase = AppPhase.PAUSED
        self._paused_phase = None
        self._auto_quiet_paused = False
        self.break_warning = ""
        self.completed_cycles = 0
        self.next_break_override = None
        self.current_break_activity = None
        self.current_reminder = None
        self._pending_badge = None
        self._break_started_at = None
        if self.current_session_id is not None:
            self.db.end_work(self.current_session_id)
            self.current_session_id = None
        self.db.close_orphan_sessions(today_string())
        self.goal_auto_stopped = False
        self._skipped_quiet_reasons.clear()
        self.is_in_quiet_hours = False
        self._stop_quiet_countdown()
        self.db.clear_timer_state()
        self.refresh_stats()
        self.start_work()
        self.check_quiet_hours()

    def check_day_change(self) -> None:
        today = today_string()
        if today != self._last_active_date:
            self.handle_day_change()
        if self._off_work_summary_shown_date not in (None, today):
            self._off_work_summary_shown_date = None

    # ------------------------------------------------------------------ config

    def update_config(
        self, new_config: AppConfig, *, suppress_restart_prompt: bool = False
    ) -> None:
        old = self._last_saved_config or copy.deepcopy(self.config)
        new_config = new_config.copy().normalized()
        if not new_config.reminders:
            new_config.reminders = [L.default_reminder_1, L.default_reminder_2]
        changed = new_config != old
        self.config = new_config
        L.lang = new_config.language
        self.emit("config", changed=changed)
        if not changed:
            return

        if new_config.appearance != old.appearance:
            self.emit("appearance", appearance=new_config.appearance)
        if new_config.daily_goal != old.daily_goal:
            self.refresh_stats()

        if suppress_restart_prompt:
            self._set_timer("restart_prompt", None)
            self.restart_prompt = False
        elif not self.is_in_quiet_hours and (
            (
                new_config.work_minutes != old.work_minutes
                and self.phase in (AppPhase.WORKING, AppPhase.PAUSED)
            )
            or (
                new_config.break_seconds != old.break_seconds
                and self.phase is AppPhase.BREAKING
            )
        ):
            def prompt() -> None:
                self.restart_prompt = True
                self.emit("restart_prompt")

            self._set_timer(
                "restart_prompt", self._scheduler.later(1.5, prompt)
            )

        if (
            new_config.quiet_hours != old.quiet_hours
            or new_config.work_days != old.work_days
            or new_config.holiday_sync_enabled != old.holiday_sync_enabled
            or new_config.holiday_calendar != old.holiday_calendar
            or new_config.holiday_calendar_names != old.holiday_calendar_names
            or new_config.work_hours_enabled != old.work_hours_enabled
            or new_config.work_start_time != old.work_start_time
            or new_config.work_end_time != old.work_end_time
        ):
            self.check_quiet_hours()

        self._schedule_autosave()

    def _schedule_autosave(self) -> None:
        self._set_timer(
            "autosave", self._scheduler.later(0.3, self._autosave)
        )

    def _autosave(self) -> None:
        try:
            self.db.save_config(self.config)
        except Exception:
            pass
        self._last_saved_config = copy.deepcopy(self.config)

    def persist_now(self) -> None:
        self._set_timer("autosave", None)
        self.db.save_config(self.config)
        self._last_saved_config = copy.deepcopy(self.config)

    def apply_config_and_restart(self) -> None:
        self.restart_prompt = False
        self.restart_phase()

    def restart_phase(self) -> None:
        self.restart_current_phase()

    def dismiss_restart_prompt(self) -> None:
        self.restart_prompt = False
        self.emit("restart_dismissed")

    def reset_all_records(self) -> None:
        self.db.reset_all_data()
        self.current_session_id = None
        self.completed_cycles = 0
        self.refresh_stats()
        self.emit("data_changed")

    def reload_after_import(self, config_changed: bool) -> None:
        if config_changed:
            self.config = self.db.load_config()
            self._last_saved_config = copy.deepcopy(self.config)
            L.lang = self.config.language
            self.emit("config", changed=True)
            self.emit("appearance", appearance=self.config.appearance)
            self.check_quiet_hours()
        self.refresh_stats()
        self.emit("data_changed")

    def reset_to_defaults(self) -> None:
        self.db.reset_config()
        self.config = self.db.load_config()
        self._last_saved_config = copy.deepcopy(self.config)
        L.lang = self.config.language
        self._set_timer("work", None)
        self._set_timer("break", None)
        self._cancel_alert_sound_burst()
        self.emit("hide")
        self._paused_phase = None
        if self.current_session_id is not None:
            self.db.end_work(self.current_session_id)
            self.current_session_id = None
        self.emit("config", changed=True)
        self.emit("appearance", appearance=self.config.appearance)
        self.start_work()
        self.refresh_stats()

    # ------------------------------------------------------------------ sounds

    def _cancel_alert_sound_burst(self) -> None:
        self._alert_sound_token += 1

    def play_alert_sound(self) -> None:
        if not self.config.sound_enabled:
            return
        self._play_sound_burst(
            self.config.alert_sound, self.config.alert_sound_repeat_count, True
        )

    def preview_sound_burst(self, sound_name: str, repeat_count: int) -> None:
        self._play_sound_burst(sound_name, repeat_count, False)

    def _play_sound_burst(
        self, sound_name: str, repeat_count: int, require_sound_enabled: bool
    ) -> None:
        if require_sound_enabled and not self.config.sound_enabled:
            return
        self._cancel_alert_sound_burst()
        token = self._alert_sound_token
        count = max(1, min(10, repeat_count))

        def play(index: int) -> None:
            if self._alert_sound_token != token:
                return
            if require_sound_enabled and not self.config.sound_enabled:
                return
            self._sounds.play(sound_name)

        for index in range(count):
            if index == 0:
                play(0)
            else:
                self._scheduler.later(
                    self.ALERT_SOUND_REPEAT_INTERVAL * index,
                    lambda i=index: play(i),
                )

    def play_sound(self, name: str | None = None) -> None:
        if not self.config.sound_enabled:
            return
        self._sounds.play(name or self.config.alert_sound)

    def play_break_detect_sound(self) -> None:
        if not self.config.break_detect_sound:
            return
        self._sounds.play(self.config.break_detect_sound_name)

    # ---------------------------------------------------------------- holidays

    def _refresh_holiday_calendar_if_stale(self) -> None:
        if not self.config.holiday_sync_enabled:
            return
        years = self.config.holiday_years()
        current_year = date.today().year
        if years and years[-1] >= current_year + 1:
            return
        self.sync_holidays(silent=True)

    def sync_holidays(self, *, silent: bool = False, on_done: Callable[[bool, str], None] | None = None) -> None:
        """Fetch holiday data in a background thread (network I/O)."""
        import threading

        from .holiday import sync_national_holidays

        def worker() -> None:
            try:
                result = sync_national_holidays()
            except Exception as exc:
                if on_done:
                    on_done(False, str(exc))
                return

            def apply() -> None:
                self.config.holiday_calendar = dict(result.work_day_overrides)
                self.config.holiday_calendar_names = dict(result.labels)
                from .holiday import iso_now

                self.config.holiday_calendar_synced_at = iso_now()
                self._last_saved_config = copy.deepcopy(self.config)
                self.db.save_config(self.config)
                self.check_quiet_hours()
                self.emit("config", changed=True)
                self.emit("holiday_synced", count=len(result.work_day_overrides))
                if on_done:
                    on_done(True, "")

            if GLib is not None:
                GLib.idle_add(lambda: (apply(), False)[1])
            else:
                apply()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
