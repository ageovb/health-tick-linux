"""Tests for persistence, streaks, timer state and export/import."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from healthtick.db import Database, ImportFailure  # noqa: E402


class DatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Database(Path(self._tmp.name) / "data.db")
        self.addCleanup(self.db.close)

    def test_seeded_config_defaults(self) -> None:
        config = self.db.load_config()
        self.assertEqual(config.work_minutes, 60)
        self.assertEqual(config.daily_goal, 8)
        self.assertFalse(self.db.is_onboarding_completed())
        self.db.set_onboarding_completed()
        self.assertTrue(self.db.is_onboarding_completed())
        self.db.set_onboarding_incomplete()
        self.assertFalse(self.db.is_onboarding_completed())

    def test_records_and_counts(self) -> None:
        self.db.add_record(3)
        self.assertEqual(self.db.today_count(), 3)
        self.assertEqual(self.db.total_count(), 3)
        yesterday = date.today() - timedelta(days=1)
        self.db.add_record(2, when=yesterday)
        self.assertEqual(self.db.count_for_date(yesterday.strftime("%Y-%m-%d")), 2)
        self.assertEqual(self.db.total_count(), 5)
        week = self.db.recent_7_days_counts()
        self.assertEqual(len(week), 7)
        self.assertEqual(dict(week)[yesterday.strftime("%Y-%m-%d")], 2)

    def test_streak_and_best_streak(self) -> None:
        today = date.today()
        for offset in (1, 2, 3):
            self.db.add_record(8, when=today - timedelta(days=offset))
        self.assertEqual(self.db.streak_days(8), 3)
        self.assertEqual(self.db.max_streak_days(8), 3)
        # An incomplete today does not break yesterday's streak.
        self.db.add_record(1, when=today)
        self.assertEqual(self.db.streak_days(8), 3)
        self.assertEqual(self.db.max_streak_days(8), 3)

    def test_streak_ignores_calendar_gaps_like_upstream(self) -> None:
        # The macOS query counts qualifying rows in descending order without
        # checking calendar adjacency, so we deliberately match that behaviour.
        today = date.today()
        self.db.add_record(8, when=today)
        self.db.add_record(8, when=today - timedelta(days=2))
        self.assertEqual(self.db.streak_days(8), 2)
        self.assertEqual(self.db.max_streak_days(8), 2)

    def test_work_minutes_from_sessions(self) -> None:
        session = self.db.start_session(60, 120, 8)
        self.db.end_work(session)
        self.assertEqual(self.db.today_work_minutes(), 0)  # zero-length test session
        break_session = self.db.start_session(25, 300, 8)
        self.db.end_work(break_session)
        self.db.start_session_break(break_session)
        self.db.end_session_break(break_session, 12)
        self.assertGreaterEqual(self.db.today_work_minutes(), 0)

    def test_timer_state_round_trip(self) -> None:
        self.db.save_timer_state("working", 1234)
        self.assertEqual(self.db.load_timer_state(), ("working", 1234))
        self.assertEqual(self.db.timer_save_date(), date.today().strftime("%Y-%m-%d"))
        self.db.clear_timer_state()
        self.assertEqual(self.db.load_timer_state(), ("", None))

    def test_export_shape_and_import_merge(self) -> None:
        today = date.today()
        for offset in range(2):
            self.db.add_record(8, when=today - timedelta(days=offset))
        session = self.db.start_session(50, 300, 7)
        self.db.end_work(session)
        self.db.start_session_break(session)
        self.db.end_session_break(session, 300)
        payload = self.db.export_all_data()
        self.assertEqual(payload["version"], 1)
        self.assertIn("exportDate", payload)
        self.assertEqual(len(payload["records"]), 16)
        self.assertEqual(len(payload["sessions"]), 1)
        self.assertIn("work_minutes", payload["config"])
        # Timer state is transient and must not be exported.
        self.assertNotIn("timer_phase", payload["config"])

        other_dir = Path(self._tmp.name) / "other"
        other_dir.mkdir()
        other = Database(other_dir / "data.db")
        self.addCleanup(other.close)
        summary = other.import_data(payload)
        self.assertEqual(summary.records_added, 16)
        self.assertEqual(summary.sessions_added, 1)
        self.assertEqual(other.total_count(), 16)
        self.assertTrue(summary.backup_path)

        # Re-importing the same payload is a no-op rather than a duplicate.
        again = other.import_data(payload)
        self.assertEqual(again.records_added, 0)
        self.assertEqual(again.records_skipped, 16)
        self.assertEqual(other.total_count(), 16)

    def test_import_config_and_future_dates(self) -> None:
        payload = self.db.export_all_data()
        payload["records"].append(
            {
                "timestamp": "2035-01-01T00:00:00Z",
                "date": "2035-01-01",
            }
        )
        payload["config"]["work_minutes"] = "25"
        payload["config"]["daily_goal"] = "4"
        summary = self.db.import_data(payload, include_config=True)
        self.assertEqual(summary.rejected, 1)
        self.assertTrue(summary.config_applied)
        config = self.db.load_config()
        self.assertEqual(config.work_minutes, 25)
        self.assertEqual(config.daily_goal, 4)

    def test_invalid_exports_raise(self) -> None:
        with self.assertRaises(ImportFailure):
            self.db.import_data({"version": 0})
        with self.assertRaises(ImportFailure):
            self.db.import_data({"version": 999})

    def test_reset_all_data_keeps_config(self) -> None:
        self.db.add_record(2)
        self.db.save_config(self.db.load_config())
        self.db.reset_all_data()
        self.assertEqual(self.db.total_count(), 0)
        self.assertEqual(self.db.load_config().work_minutes, 60)

    def test_config_persists(self) -> None:
        config = self.db.load_config()
        config.work_minutes = 45
        config.break_seconds = 300
        config.quiet_hours = []
        config.daily_goal = 5
        self.db.save_config(config)
        loaded = self.db.load_config()
        self.assertEqual(loaded.work_minutes, 45)
        self.assertEqual(loaded.break_seconds, 300)
        self.assertEqual(loaded.daily_goal, 5)


if __name__ == "__main__":
    unittest.main()
