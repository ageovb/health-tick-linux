"""Behavioural tests for the work/break state machine."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from healthtick.config import (  # noqa: E402
    AppConfig,
    AppLanguage,
    AppPhase,
    QuietHourPeriod,
)
from healthtick.db import Database  # noqa: E402
from healthtick.i18n import L  # noqa: E402
from healthtick.state import AppState  # noqa: E402


class FakeScheduler:
    """Captures timers so tests can drive them explicitly."""

    def __init__(self) -> None:
        self.tasks: dict[int, tuple[str, float, object]] = {}
        self._next = 1

    def every(self, seconds, callback):
        handle = self._next
        self._next += 1
        self.tasks[handle] = ("every", seconds, callback)
        return handle

    def later(self, seconds, callback):
        handle = self._next
        self._next += 1
        self.tasks[handle] = ("later", seconds, callback)
        return handle

    def cancel(self, handle):
        self.tasks.pop(handle, None)

    def cancel_all(self):
        self.tasks.clear()

    def run_later(self, max_seconds: float | None = None) -> None:
        pending = [
            (handle, seconds, callback)
            for handle, (kind, seconds, callback) in list(self.tasks.items())
            if kind == "later" and (max_seconds is None or seconds <= max_seconds)
        ]
        for handle, _seconds, callback in pending:
            self.tasks.pop(handle, None)
            callback()


class FakeIdle:
    def __init__(self, value: float = 10 ** 9) -> None:
        self.value = value

    def idle_seconds(self) -> float:
        return self.value


class FakeSounds:
    def __init__(self) -> None:
        self.played: list[str] = []

    def play(self, name=None) -> None:
        self.played.append(name or "")


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, title, body, **_kwargs) -> None:
        self.sent.append((title, body))


class StateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Database(Path(self._tmp.name) / "data.db")
        self.addCleanup(self.db.close)
        self.db.set_onboarding_completed()
        self._old_lang = L.lang
        self.addCleanup(setattr, L, "lang", self._old_lang)
        self.scheduler = FakeScheduler()
        self.idle = FakeIdle()
        self.sounds = FakeSounds()
        self.notifier = FakeNotifier()

    def make_state(self, configure=None) -> AppState:
        config = self.db.load_config()
        config.auto_check_update = False
        config.sound_enabled = False
        # Keep tests independent of the real weekday.
        config.work_days = {1, 2, 3, 4, 5, 6, 7}
        if configure is not None:
            configure(config)
        self.db.save_config(config)
        state = AppState(
            self.db,
            idle=self.idle,
            sounds=self.sounds,
            notifier=self.notifier,
            scheduler=self.scheduler,
        )
        self.addCleanup(state.shutdown)
        return state

    # ----------------------------------------------------------- work phase

    def test_start_begins_work_phase(self) -> None:
        state = self.make_state()
        state.start()
        self.assertEqual(state.phase, AppPhase.WORKING)
        self.assertEqual(state.remaining_seconds, 60 * 60)
        self.assertIsNotNone(state.current_session_id)
        self.assertEqual(state.formatted_time, "60:00")

    def test_work_end_enters_alerting(self) -> None:
        state = self.make_state()
        state.start()
        state.remaining_seconds = 1
        state._target_time = time.monotonic()
        state._tick_work()
        self.assertEqual(state.phase, AppPhase.ALERTING)
        self.assertEqual(self.db.load_timer_state()[0], "alerting")
        self.assertIsNotNone(state.current_reminder)

    def test_break_without_confirmation_starts_immediately(self) -> None:
        state = self.make_state(lambda cfg: setattr(cfg, "break_confirm", False))
        state.start()
        state._on_work_done()
        self.assertEqual(state.phase, AppPhase.BREAKING)
        self.assertEqual(state.remaining_seconds, state.config.break_seconds)

    def test_confirm_break_then_return_to_work(self) -> None:
        state = self.make_state()
        state.start()
        state._on_work_done()
        self.assertEqual(state.phase, AppPhase.ALERTING)
        state.confirm_break()
        self.assertEqual(state.phase, AppPhase.BREAKING)
        self.assertEqual(state.remaining_seconds, state.config.break_seconds)

        session = state.current_session_id
        state._break_monitor_started_at = time.monotonic() - 10
        state.remaining_seconds = 2
        state._tick_break()
        self.assertEqual(state.phase, AppPhase.BREAKING)
        state._tick_break()
        self.assertEqual(state.phase, AppPhase.WAITING)
        self.assertEqual(state.today_done, 1)

        state.confirm_return()
        self.assertEqual(state.phase, AppPhase.WORKING)
        self.assertEqual(state.remaining_seconds, 60 * 60)
        self.assertNotEqual(state.current_session_id, session)

    def test_delay_break_postpones_by_ten_percent(self) -> None:
        state = self.make_state()
        state.start()
        state._on_work_done()
        self.assertEqual(state.phase, AppPhase.ALERTING)
        state.delay_break()
        self.assertEqual(state.phase, AppPhase.WORKING)
        self.assertTrue(state.snooze_active)
        self.assertEqual(state.remaining_seconds, 360)  # 10% of 60 minutes
        self.assertEqual(state.db.load_timer_state(), ("alerting", None))

    def test_delay_break_from_working_postpones_upcoming_break(self) -> None:
        state = self.make_state()
        state.start()
        self.assertEqual(state.phase, AppPhase.WORKING)
        before = state.remaining_seconds
        state.delay_break()
        self.assertEqual(state.phase, AppPhase.WORKING)
        self.assertFalse(state.snooze_active)
        # Pushed back by 10% of the work duration (360s for a 60min block).
        self.assertGreaterEqual(state.remaining_seconds, before + 359)

    def test_delay_break_ignored_while_breaking(self) -> None:
        state = self.make_state()
        state.start()
        state.manual_break()
        self.assertEqual(state.phase, AppPhase.BREAKING)
        before = state.remaining_seconds
        state.delay_break()
        self.assertEqual(state.phase, AppPhase.BREAKING)
        self.assertEqual(state.remaining_seconds, before)

    # ---------------------------------------------------------- break phase

    def test_break_pauses_when_user_is_idle(self) -> None:
        state = self.make_state()
        state.start()
        state.manual_break()
        self.assertEqual(state.phase, AppPhase.BREAKING)
        state._break_monitor_started_at = time.monotonic() - 10
        self.idle.value = 0
        before = state.remaining_seconds
        state._tick_break()
        self.assertEqual(state.remaining_seconds, before)
        self.assertTrue(state.break_warning)

        self.idle.value = 10 ** 9
        state._tick_break()
        self.assertEqual(state.remaining_seconds, before - 1)
        self.assertEqual(state.break_warning, "")

    def test_restart_cycle_schedules_selected_break_type(self) -> None:
        def configure(config: AppConfig) -> None:
            config.long_break_enabled = True
            config.long_break_interval = 4
            config.break_seconds = 120
            config.long_break_seconds = 600

        state = self.make_state(configure)
        state.start()
        state.restart_cycle("long")
        self.assertEqual(state.phase, AppPhase.WORKING)
        self.assertEqual(state.remaining_seconds, 60 * 60)
        self.assertTrue(state.upcoming_break_is_long)

        state._on_work_done()
        state.confirm_break()
        self.assertEqual(state.remaining_seconds, 600)
        self.assertIsNone(state.next_break_override)

        state.remaining_seconds = 1
        state._tick_break()
        self.assertEqual(state.phase, AppPhase.WAITING)
        state.confirm_return()

        state.completed_cycles = 0
        self.assertFalse(state.upcoming_break_is_long)
        state.manual_break()
        self.assertEqual(state.remaining_seconds, 120)

    def test_restart_cycle_from_alert_restarts_work(self) -> None:
        state = self.make_state()
        state.start()
        state._on_work_done()
        self.assertEqual(state.phase, AppPhase.ALERTING)
        state.restart_cycle("short")
        self.assertEqual(state.phase, AppPhase.WORKING)
        self.assertEqual(state.remaining_seconds, 60 * 60)
        self.assertFalse(state.upcoming_break_is_long)

    def test_long_break_every_n_cycles(self) -> None:
        def configure(config: AppConfig) -> None:
            config.long_break_enabled = True
            config.long_break_interval = 2
            config.break_seconds = 120
            config.long_break_seconds = 600

        state = self.make_state(configure)
        state.start()
        state.completed_cycles = 0
        state.manual_break()
        self.assertEqual(state.remaining_seconds, 120)

        state.remaining_seconds = 1
        state._tick_break()
        self.assertEqual(state.phase, AppPhase.WAITING)
        state.confirm_return()

        state.completed_cycles = 2
        self.assertTrue(state.upcoming_break_is_long)
        state.manual_break()
        self.assertEqual(state.remaining_seconds, 600)
        self.assertEqual(state.current_break_total_seconds, 600)

    def test_pause_and_resume_preserve_remaining(self) -> None:
        state = self.make_state()
        state.start()
        state.remaining_seconds = 1234
        state.toggle_pause()
        self.assertEqual(state.phase, AppPhase.PAUSED)
        self.assertEqual(state.remaining_seconds, 1234)
        self.assertEqual(self.db.load_timer_state(), ("paused", 1234))
        state.toggle_pause()
        self.assertEqual(state.phase, AppPhase.WORKING)
        self.assertEqual(state.remaining_seconds, 1234)

    # ------------------------------------------------------- quiet / day flow

    def test_quiet_hours_pause_and_resume(self) -> None:
        state = self.make_state()
        state.start()
        state.config.quiet_hours = [QuietHourPeriod(start="00:00", end="23:59")]
        state.check_quiet_hours()
        self.assertTrue(state.is_in_quiet_hours)
        self.assertEqual(state.phase, AppPhase.PAUSED)
        self.assertIsNone(state.current_session_id)

        state.config.quiet_hours = []
        state.check_quiet_hours()
        self.assertFalse(state.is_in_quiet_hours)
        self.assertEqual(state.phase, AppPhase.WORKING)

    def test_overtime_overrides_quiet_hours(self) -> None:
        state = self.make_state()
        state.start()
        state.config.quiet_hours = [QuietHourPeriod(start="00:00", end="23:59")]
        state.check_quiet_hours()
        self.assertTrue(state.is_in_quiet_hours)
        self.assertFalse(state.overtime_active)
        state.activate_overtime()
        self.assertTrue(state.overtime_active)
        self.assertFalse(state.is_in_quiet_hours)
        self.assertEqual(state.phase, AppPhase.WORKING)

    def test_day_change_restarts_work(self) -> None:
        state = self.make_state()
        state.start()
        state._last_active_date = "2000-01-01"
        state.check_day_change()
        self.assertEqual(state._last_active_date, date.today().strftime("%Y-%m-%d"))
        self.assertEqual(state.phase, AppPhase.WORKING)
        self.assertEqual(state.remaining_seconds, 60 * 60)
        self.assertEqual(state.completed_cycles, 0)

    def test_auto_pause_when_goal_is_reached(self) -> None:
        def configure(config: AppConfig) -> None:
            config.auto_pause_on_goal = True
            config.daily_goal = 1

        state = self.make_state(configure)
        state.start()
        self.db.add_record(1)
        state.refresh_stats()
        state._on_work_done()
        state.mark_break_completed()
        self.assertEqual(state.phase, AppPhase.PAUSED)
        self.assertTrue(state.goal_auto_stopped)
        state.resume_from_goal_stop()
        self.assertEqual(state.phase, AppPhase.WORKING)

    # ------------------------------------------------------------- restore

    def test_restore_working_timer(self) -> None:
        self.db.save_timer_state("working", 600)
        state = self.make_state()
        state.start()
        self.assertEqual(state.phase, AppPhase.WORKING)
        self.assertEqual(state.remaining_seconds, 600)
        self.assertIsNotNone(state.current_session_id)

    def test_restore_paused_timer(self) -> None:
        self.db.save_timer_state("paused", 300)
        state = self.make_state()
        state.start()
        self.assertEqual(state.phase, AppPhase.PAUSED)
        self.assertEqual(state.remaining_seconds, 300)

    def test_restore_alerting_timer(self) -> None:
        self.db.save_timer_state("alerting", 0)
        state = self.make_state()
        events: list[str] = []
        state.add_listener(lambda name, _payload: events.append(name))
        state.start()
        self.assertEqual(state.phase, AppPhase.ALERTING)
        self.assertIn("alert", events)

    def test_stale_timer_restarts_work(self) -> None:
        self.db.save_timer_state("working", 600)
        with self.db.conn:
            self.db.conn.execute(
                "UPDATE config SET value = ? WHERE key = 'timer_save_date'",
                ("2000-01-01",),
            )
        state = self.make_state()
        state.start()
        self.assertEqual(state.phase, AppPhase.WORKING)
        self.assertEqual(state.remaining_seconds, 60 * 60)

    # --------------------------------------------------------------- extras

    def test_pre_break_notice_notifies_once(self) -> None:
        def configure(config: AppConfig) -> None:
            config.sound_enabled = True
            config.pre_break_notice_enabled = True
            config.pre_break_notice_seconds = 60

        state = self.make_state(configure)
        state.start()
        self.notifier.sent.clear()
        self.sounds.played.clear()
        state._maybe_fire_pre_break_notice(61, 60)
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertTrue(self.sounds.played)
        state._maybe_fire_pre_break_notice(60, 59)
        self.assertEqual(len(self.notifier.sent), 1)

    def test_off_work_summary_show_and_dismiss(self) -> None:
        def configure(config: AppConfig) -> None:
            config.off_work_summary_enabled = True
            config.work_hours_enabled = True

        state = self.make_state(configure)
        state.start()
        events: list[str] = []
        state.add_listener(lambda name, _payload: events.append(name))
        state._maybe_show_off_work_summary()
        self.assertIsNotNone(state.off_work_summary)
        self.assertIn("summary", events)
        state.dismiss_off_work_summary()
        self.assertIsNone(state.off_work_summary)

    def test_screen_unlock_resets_session(self) -> None:
        state = self.make_state()
        state.start()
        first = state.current_session_id
        state.note_screen_lock()
        state._screen_lock_time = time.monotonic() - 11
        state.handle_screen_unlock()
        self.assertEqual(state.phase, AppPhase.WORKING)
        self.assertNotEqual(state.current_session_id, first)
        self.assertEqual(state.remaining_seconds, 60 * 60)

    def test_config_update_persists_and_switches_language(self) -> None:
        state = self.make_state()
        state.start()
        new_config = state.config.copy()
        new_config.work_minutes = 30
        new_config.language = AppLanguage.EN
        state.update_config(new_config)
        self.assertEqual(state.config.work_minutes, 30)
        self.assertEqual(L.lang, AppLanguage.EN)
        self.scheduler.run_later(0.5)
        self.assertEqual(self.db.load_config().work_minutes, 30)
        self.assertEqual(self.db.load_config().language, AppLanguage.EN)

    def test_badge_detection(self) -> None:
        today = date.today()
        for offset in range(3):
            self.db.add_record(8, when=today - timedelta(days=offset))
        state = self.make_state()
        state.start()
        self.assertEqual(state.current_streak, 3)
        self.assertIsNotNone(state.earned_badge)
        self.assertEqual(state.earned_badge.days, 3)
        badge = state._detect_new_badge(2, state.total_count)
        self.assertIsNotNone(badge)
        self.assertEqual(badge.days, 3)
        self.assertIsNone(state._detect_new_badge(3, state.total_count))

    def test_reset_and_manual_break(self) -> None:
        state = self.make_state()
        state.start()
        state.manual_break()
        self.assertEqual(state.phase, AppPhase.BREAKING)
        state.reset()
        self.assertEqual(state.phase, AppPhase.WORKING)
        self.assertEqual(state.remaining_seconds, 60 * 60)


if __name__ == "__main__":
    unittest.main()
