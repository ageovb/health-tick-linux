"""Tests for the configuration model and workday/holiday decisions."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from healthtick.config import (  # noqa: E402
    AppConfig,
    BreakPosition,
    QuietHourPeriod,
)


class DefaultConfigTests(unittest.TestCase):
    def test_defaults_match_upstream(self) -> None:
        config = AppConfig()
        self.assertEqual(config.work_minutes, 60)
        self.assertEqual(config.break_seconds, 120)
        self.assertEqual(config.daily_goal, 8)
        self.assertEqual(config.break_position, BreakPosition.MENU_WINDOW)
        self.assertTrue(config.break_confirm)
        self.assertTrue(config.sound_enabled)
        self.assertTrue(config.reset_on_screen_lock)
        self.assertFalse(config.long_break_enabled)
        self.assertFalse(config.pre_break_notice_enabled)
        self.assertEqual(config.work_days, {2, 3, 4, 5, 6})

    def test_normalized_clamps_and_coerces(self) -> None:
        config = AppConfig(
            work_minutes=0,
            break_seconds=10 ** 6,
            daily_goal=-4,
            break_position="center",  # type: ignore[arg-type]
            work_days={0, 1, 2, 8},
        ).normalized()
        self.assertGreaterEqual(config.work_minutes, 1)
        self.assertLessEqual(config.break_seconds, 24 * 60 * 60)
        self.assertGreaterEqual(config.daily_goal, 1)
        self.assertEqual(config.break_position, BreakPosition.CENTER)
        self.assertEqual(config.work_days, {1, 2})

    def test_db_values_round_trip(self) -> None:
        config = AppConfig()
        config.quiet_hours = [QuietHourPeriod(start="12:00", end="13:30")]
        config.work_days = {2, 4}
        config.language = config.language.EN
        restored = AppConfig.from_db_values(config.to_db_values())
        self.assertEqual(restored.work_minutes, config.work_minutes)
        self.assertEqual(restored.break_seconds, config.break_seconds)
        self.assertEqual(restored.work_days, {2, 4})
        self.assertEqual(len(restored.quiet_hours), 1)
        self.assertEqual(restored.quiet_hours[0].start, "12:00")
        self.assertEqual(restored.language, config.language.EN)


class WorkdayTests(unittest.TestCase):
    def test_swift_weekday_mapping(self) -> None:
        config = AppConfig()
        # 2026-10-26 is a Monday (Swift weekday 2).
        self.assertTrue(config.is_work_day(date(2026, 10, 26)))
        self.assertTrue(config.is_work_day(date(2026, 10, 30)))  # Friday
        self.assertFalse(config.is_work_day(date(2026, 10, 31)))  # Saturday
        self.assertFalse(config.is_work_day(date(2026, 11, 1)))  # Sunday

    def test_holiday_overrides(self) -> None:
        config = AppConfig(holiday_sync_enabled=True)
        config.holiday_calendar = {
            "2026-10-28": False,  # Wednesday holiday
            "2026-10-31": True,  # Saturday make-up work day
        }
        self.assertFalse(config.is_work_day(date(2026, 10, 28)))
        self.assertTrue(config.is_work_day(date(2026, 10, 31)))
        # Unlisted days fall back to the weekly schedule.
        self.assertTrue(config.is_work_day(date(2026, 10, 27)))
        self.assertFalse(config.is_work_day(date(2026, 11, 1)))

    def test_holiday_calendar_disabled_ignores_overrides(self) -> None:
        config = AppConfig(holiday_sync_enabled=False)
        config.holiday_calendar = {"2026-10-28": False}
        self.assertTrue(config.is_work_day(date(2026, 10, 28)))

    def test_holiday_years(self) -> None:
        config = AppConfig()
        config.holiday_calendar = {
            "2026-10-01": False,
            "2025-05-01": False,
            "bad-key": False,
        }
        self.assertEqual(config.holiday_years(), [2025, 2026])


class QuietHourTests(unittest.TestCase):
    def test_simple_period(self) -> None:
        period = QuietHourPeriod(start="12:00", end="13:00")
        self.assertFalse(period.is_active(datetime(2026, 10, 28, 11, 59)))
        self.assertTrue(period.is_active(datetime(2026, 10, 28, 12, 0)))
        self.assertTrue(period.is_active(datetime(2026, 10, 28, 12, 59)))
        self.assertFalse(period.is_active(datetime(2026, 10, 28, 13, 0)))

    def test_period_across_midnight(self) -> None:
        period = QuietHourPeriod(start="23:00", end="07:00")
        self.assertTrue(period.is_active(datetime(2026, 10, 28, 23, 30)))
        self.assertTrue(period.is_active(datetime(2026, 10, 28, 2, 0)))
        self.assertFalse(period.is_active(datetime(2026, 10, 28, 12, 0)))
        end = period.end_date(datetime(2026, 10, 28, 23, 30))
        self.assertEqual(end, datetime(2026, 10, 29, 7, 0))

    def test_weekday_filter(self) -> None:
        # Wednesday is Swift weekday 4.
        period = QuietHourPeriod(start="09:00", end="10:00", weekdays={4})
        self.assertTrue(period.is_active(datetime(2026, 10, 28, 9, 30)))
        self.assertFalse(period.is_active(datetime(2026, 10, 29, 9, 30)))

    def test_invalid_times_are_inactive(self) -> None:
        period = QuietHourPeriod(start="25:00", end="26:00")
        self.assertFalse(period.is_active(datetime(2026, 10, 28, 12, 0)))


if __name__ == "__main__":
    unittest.main()
