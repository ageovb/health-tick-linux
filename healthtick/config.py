"""Configuration model for the Linux port of HealthTick.

The field names, defaults and on-disk keys intentionally mirror the macOS
application so that data exported by HealthTick on macOS can be imported here
(and vice versa).
"""

from __future__ import annotations

import copy
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Iterable

from .i18n import L, AppLanguage


class BreakPosition(str, Enum):
    MENU_WINDOW = "menu_window"
    TOP_RIGHT = "top_right"
    TOP_LEFT = "top_left"
    CENTER = "center"
    FULLSCREEN = "fullscreen"

    @property
    def label(self) -> str:
        return {
            self.MENU_WINDOW: L.pos_menu_window,
            self.TOP_RIGHT: L.pos_top_right,
            self.TOP_LEFT: L.pos_top_left,
            self.CENTER: L.pos_center,
            self.FULLSCREEN: L.pos_fullscreen,
        }[self]


class BreakDisplayTarget(str, Enum):
    ACTIVE = "active"
    ALL = "all"
    SPECIFIC = "specific"

    @property
    def label(self) -> str:
        return {
            self.ACTIVE: L.display_target_active,
            self.ALL: L.display_target_all,
            self.SPECIFIC: L.display_target_specific,
        }[self]


class AppAppearance(str, Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


class AppPhase(str, Enum):
    WORKING = "working"
    ALERTING = "alerting"
    BREAKING = "breaking"
    WAITING = "waiting"
    PAUSED = "paused"


def _swift_weekday(day: date) -> int:
    """Swift Calendar weekday: Sunday=1 ... Saturday=7."""
    return (day.isoweekday() % 7) + 1


def _parse_hhmm(value: str) -> int | None:
    try:
        h, m = value.split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return h * 60 + m
    return None


@dataclass
class QuietHourPeriod:
    start: str = "12:00"
    end: str = "13:00"
    weekdays: set[int] | None = None  # Swift weekday numbers; empty/None = every day
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "start": self.start, "end": self.end}
        if self.weekdays:
            data["weekdays"] = sorted(self.weekdays)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QuietHourPeriod":
        days = data.get("weekdays")
        weekdays = {int(x) for x in days} if days else None
        return cls(
            start=str(data.get("start", "12:00")),
            end=str(data.get("end", "13:00")),
            weekdays=weekdays,
            id=str(data.get("id") or uuid.uuid4()).upper(),
        )

    def is_active(self, at: datetime | date) -> bool:
        if isinstance(at, date) and not isinstance(at, datetime):
            at = datetime.combine(at, datetime.min.time())
        if self.weekdays and _swift_weekday(at.date()) not in self.weekdays:
            return False
        start = _parse_hhmm(self.start)
        end = _parse_hhmm(self.end)
        if start is None or end is None:
            return False
        now = at.hour * 60 + at.minute
        if start <= end:
            return start <= now < end
        return now >= start or now < end

    def end_date(self, ref: datetime) -> datetime | None:
        end = _parse_hhmm(self.end)
        if end is None:
            return None
        result = ref.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
        if result <= ref:
            result += timedelta(days=1)
        return result

    def start_date(self, ref: datetime) -> datetime | None:
        start = _parse_hhmm(self.start)
        if start is None:
            return None
        result = ref.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0)
        if result <= ref:
            result += timedelta(days=1)
        return result


@dataclass
class BreakActivity:
    icon: str
    text_zh: str
    text_en: str

    @property
    def text(self) -> str:
        return self.text_zh if L.is_zh else self.text_en


BREAK_ACTIVITIES: list[BreakActivity] = [
    BreakActivity("🚶", "起来走走，活动一下身体", "Take a walk and stretch your body"),
    BreakActivity("👀", "远眺窗外，放松眼睛", "Look out the window, relax your eyes"),
    BreakActivity("💧", "喝杯水，补充水分", "Drink some water, stay hydrated"),
    BreakActivity("🧘", "做几个简单的拉伸动作", "Do some simple stretches"),
    BreakActivity("🌬️", "深呼吸，放松身心", "Take deep breaths, relax your mind"),
    BreakActivity("🤲", "活动手腕，预防鼠标手", "Flex your wrists to prevent strain"),
    BreakActivity("🌙", "闭眼休息，让大脑放松", "Close your eyes and rest your mind"),
    BreakActivity("🧍", "伸展脊柱，改善坐姿", "Stretch your spine, improve posture"),
]


@dataclass
class Badge:
    days: int
    icon: str
    is_total: bool = False

    @property
    def name(self) -> str:
        return L.total_badge_name(self.days) if self.is_total else L.badge_name(self.days)

    @property
    def desc(self) -> str:
        return L.total_badge_desc(self.days) if self.is_total else L.badge_desc(self.days)


ALL_BADGES: list[Badge] = [
    Badge(3, "👣"),
    Badge(7, "🌱"),
    Badge(14, "🌿"),
    Badge(21, "🌳"),
    Badge(30, "🛡️"),
    Badge(50, "⭐"),
    Badge(60, "💪"),
    Badge(90, "👑"),
    Badge(100, "🏆"),
    Badge(180, "💎"),
    Badge(365, "🐉"),
]

ALL_TOTAL_BADGES: list[Badge] = [
    Badge(10, "📌", True),
    Badge(20, "✌️", True),
    Badge(50, "🎖️", True),
    Badge(100, "💯", True),
    Badge(200, "🎯", True),
    Badge(500, "🚀", True),
    Badge(1000, "🌟", True),
    Badge(2000, "🔥", True),
    Badge(5000, "🏅", True),
]

INT_RANGES: dict[str, tuple[int, int]] = {
    "work_minutes": (1, 120),
    "saved_work_minutes": (1, 120),
    "break_seconds": (20, 900),
    "saved_break_seconds": (20, 900),
    "daily_goal": (1, 20),
    "long_break_interval": (2, 8),
    "long_break_seconds": (300, 1800),
    "pre_break_notice_seconds": (0, 3600),
    "pre_break_notice_long_seconds": (0, 3600),
    "alert_sound_repeat_count": (1, 10),
}


def clamp_int(key: str, value: int, default: int) -> int:
    lo, hi = INT_RANGES.get(key, (default, default))
    return max(lo, min(hi, value))


@dataclass
class AppConfig:
    work_minutes: int = 60
    break_seconds: int = 120
    eye_care_mode: bool = False
    saved_work_minutes: int = 60
    saved_break_seconds: int = 120
    saved_break_confirm: bool = True
    daily_goal: int = 8
    reminders: list[str] = field(default_factory=lambda: [L.default_reminder_1, L.default_reminder_2])
    sound_enabled: bool = True
    break_detect_sound: bool = False
    break_position: BreakPosition = BreakPosition.MENU_WINDOW
    break_display_target: BreakDisplayTarget = BreakDisplayTarget.ACTIVE
    break_display_specific_uuid: str | None = None
    break_confirm: bool = True
    alert_sound: str = "Glass"
    alert_sound_repeat_count: int = 1
    break_detect_sound_name: str = "Tink"
    language: AppLanguage = AppLanguage.SYSTEM
    appearance: AppAppearance = AppAppearance.SYSTEM
    quiet_hours: list[QuietHourPeriod] = field(default_factory=list)
    work_days: set[int] = field(default_factory=lambda: {2, 3, 4, 5, 6})
    holiday_sync_enabled: bool = False
    holiday_calendar: dict[str, bool] = field(default_factory=dict)
    holiday_calendar_names: dict[str, str] = field(default_factory=dict)
    holiday_calendar_synced_at: str | None = None
    work_hours_enabled: bool = False
    work_start_time: str = "09:00"
    work_end_time: str = "18:00"
    off_work_summary_enabled: bool = False
    shortcut_enabled: bool = False
    auto_pause_on_goal: bool = False
    long_break_enabled: bool = False
    long_break_interval: int = 4
    long_break_seconds: int = 900
    pre_break_notice_enabled: bool = False
    pre_break_notice_seconds: int = 60
    pre_break_notice_long_seconds: int = 120
    auto_check_update: bool = True
    reset_on_screen_lock: bool = True
    shortcut_keycode: int = 36   # Return (kept for macOS export compatibility)
    shortcut_modifiers: int = 1048576

    def copy(self) -> "AppConfig":
        return copy.deepcopy(self)

    # ---------------------------------------------------------------- helpers

    def is_work_day(self, at: datetime | date) -> bool:
        return self.holiday_kind(at) in ("makeup_work", "default_work")

    def holiday_kind(self, at: datetime | date) -> str:
        key = holiday_key(at)
        if self.holiday_sync_enabled and key in self.holiday_calendar:
            return "makeup_work" if self.holiday_calendar[key] else "rest"
        return "default_work" if _swift_weekday(_as_date(at)) in self.work_days else "default_off"

    def holiday_label(self, at: datetime | date) -> str | None:
        return self.holiday_calendar_names.get(holiday_key(at))

    def holiday_years(self) -> list[int]:
        years: set[int] = set()
        for key in self.holiday_calendar:
            try:
                years.add(int(key[:4]))
            except (ValueError, TypeError):
                pass
        return sorted(years)

    # ------------------------------------------------------------- DB mapping

    def to_db_values(self) -> dict[str, str]:
        def b(value: bool) -> str:
            return "1" if value else "0"

        return {
            "work_minutes": str(self.work_minutes),
            "break_seconds": str(self.break_seconds),
            "eye_care_mode": b(self.eye_care_mode),
            "saved_work_minutes": str(self.saved_work_minutes),
            "saved_break_seconds": str(self.saved_break_seconds),
            "saved_break_confirm": b(self.saved_break_confirm),
            "daily_goal": str(self.daily_goal),
            "reminders": json.dumps(self.reminders, ensure_ascii=False),
            "sound_enabled": b(self.sound_enabled),
            "break_detect_sound": b(self.break_detect_sound),
            "break_position": self.break_position.value,
            "break_display_target": self.break_display_target.value,
            "break_display_specific_uuid": self.break_display_specific_uuid or "",
            "break_confirm": b(self.break_confirm),
            "alert_sound": self.alert_sound,
            "alert_sound_repeat_count": str(self.alert_sound_repeat_count),
            "break_detect_sound_name": self.break_detect_sound_name,
            "language": self.language.value,
            "appearance": self.appearance.value,
            "quiet_hours": json.dumps(
                [p.to_dict() for p in self.quiet_hours], ensure_ascii=False
            ),
            "work_days": json.dumps(sorted(self.work_days)),
            "holiday_sync_enabled": b(self.holiday_sync_enabled),
            "holiday_calendar": json.dumps(self.holiday_calendar, ensure_ascii=False),
            "holiday_calendar_names": json.dumps(
                self.holiday_calendar_names, ensure_ascii=False
            ),
            "holiday_calendar_synced_at": self.holiday_calendar_synced_at or "",
            "work_hours_enabled": b(self.work_hours_enabled),
            "work_start_time": self.work_start_time,
            "work_end_time": self.work_end_time,
            "off_work_summary_enabled": b(self.off_work_summary_enabled),
            "shortcut_enabled": b(self.shortcut_enabled),
            "auto_pause_on_goal": b(self.auto_pause_on_goal),
            "long_break_enabled": b(self.long_break_enabled),
            "long_break_interval": str(self.long_break_interval),
            "long_break_seconds": str(self.long_break_seconds),
            "pre_break_notice_enabled": b(self.pre_break_notice_enabled),
            "pre_break_notice_seconds": str(self.pre_break_notice_seconds),
            "pre_break_notice_long_seconds": str(self.pre_break_notice_long_seconds),
            "auto_check_update": b(self.auto_check_update),
            "reset_on_screen_lock": b(self.reset_on_screen_lock),
            "shortcut_keycode": str(self.shortcut_keycode),
            "shortcut_modifiers": str(self.shortcut_modifiers),
        }

    @classmethod
    def from_db_values(cls, values: dict[str, str]) -> "AppConfig":
        cfg = cls()

        def get_int(key: str, default: int) -> int:
            try:
                return int(values[key])
            except (KeyError, TypeError, ValueError):
                return default

        def get_bool(key: str, default: bool) -> bool:
            if key not in values:
                return default
            return values[key] == "1"

        def get_json(key: str, default: Any) -> Any:
            try:
                return json.loads(values[key])
            except (KeyError, TypeError, ValueError):
                return default

        cfg.work_minutes = get_int("work_minutes", cfg.work_minutes)
        if "break_minutes" in values:  # migration from very old macOS builds
            cfg.break_seconds = get_int("break_minutes", 2) * 60
        cfg.break_seconds = get_int("break_seconds", cfg.break_seconds)
        cfg.eye_care_mode = get_bool("eye_care_mode", cfg.eye_care_mode)
        cfg.saved_work_minutes = get_int("saved_work_minutes", cfg.saved_work_minutes)
        cfg.saved_break_seconds = get_int("saved_break_seconds", cfg.saved_break_seconds)
        cfg.saved_break_confirm = get_bool("saved_break_confirm", cfg.saved_break_confirm)
        cfg.daily_goal = get_int("daily_goal", cfg.daily_goal)
        reminders = get_json("reminders", None)
        if isinstance(reminders, list) and reminders:
            cfg.reminders = [str(x) for x in reminders if str(x).strip()]
        cfg.sound_enabled = get_bool("sound_enabled", cfg.sound_enabled)
        cfg.break_detect_sound = get_bool("break_detect_sound", cfg.break_detect_sound)
        try:
            cfg.break_position = BreakPosition(values.get("break_position", ""))
        except ValueError:
            pass
        try:
            cfg.break_display_target = BreakDisplayTarget(
                values.get("break_display_target", "")
            )
        except ValueError:
            pass
        cfg.break_display_specific_uuid = (
            values.get("break_display_specific_uuid") or None
        )
        cfg.break_confirm = get_bool("break_confirm", cfg.break_confirm)
        cfg.alert_sound = values.get("alert_sound", cfg.alert_sound)
        cfg.alert_sound_repeat_count = get_int(
            "alert_sound_repeat_count", cfg.alert_sound_repeat_count
        )
        cfg.break_detect_sound_name = values.get(
            "break_detect_sound_name", cfg.break_detect_sound_name
        )
        try:
            cfg.language = AppLanguage(values.get("language", ""))
        except ValueError:
            pass
        try:
            cfg.appearance = AppAppearance(values.get("appearance", ""))
        except ValueError:
            pass
        quiet = get_json("quiet_hours", [])
        if isinstance(quiet, list):
            cfg.quiet_hours = [
                QuietHourPeriod.from_dict(item) for item in quiet if isinstance(item, dict)
            ]
        days = get_json("work_days", [2, 3, 4, 5, 6])
        if isinstance(days, list):
            cfg.work_days = {int(x) for x in days if str(x).lstrip("-").isdigit()}
        cfg.holiday_sync_enabled = get_bool(
            "holiday_sync_enabled", cfg.holiday_sync_enabled
        )
        holidays = get_json("holiday_calendar", {})
        if isinstance(holidays, dict):
            cfg.holiday_calendar = {str(k): bool(v) for k, v in holidays.items()}
        names = get_json("holiday_calendar_names", {})
        if isinstance(names, dict):
            cfg.holiday_calendar_names = {str(k): str(v) for k, v in names.items()}
        cfg.holiday_calendar_synced_at = (
            values.get("holiday_calendar_synced_at") or None
        )
        cfg.work_hours_enabled = get_bool("work_hours_enabled", cfg.work_hours_enabled)
        cfg.work_start_time = values.get("work_start_time", cfg.work_start_time)
        cfg.work_end_time = values.get("work_end_time", cfg.work_end_time)
        cfg.off_work_summary_enabled = get_bool(
            "off_work_summary_enabled", cfg.off_work_summary_enabled
        )
        cfg.shortcut_enabled = get_bool("shortcut_enabled", cfg.shortcut_enabled)
        cfg.auto_pause_on_goal = get_bool("auto_pause_on_goal", cfg.auto_pause_on_goal)
        cfg.long_break_enabled = get_bool("long_break_enabled", cfg.long_break_enabled)
        cfg.long_break_interval = get_int("long_break_interval", cfg.long_break_interval)
        cfg.long_break_seconds = get_int("long_break_seconds", cfg.long_break_seconds)
        cfg.pre_break_notice_enabled = get_bool(
            "pre_break_notice_enabled", cfg.pre_break_notice_enabled
        )
        cfg.pre_break_notice_seconds = get_int(
            "pre_break_notice_seconds", cfg.pre_break_notice_seconds
        )
        cfg.pre_break_notice_long_seconds = get_int(
            "pre_break_notice_long_seconds", cfg.pre_break_notice_long_seconds
        )
        cfg.auto_check_update = get_bool("auto_check_update", cfg.auto_check_update)
        cfg.reset_on_screen_lock = get_bool(
            "reset_on_screen_lock", cfg.reset_on_screen_lock
        )
        cfg.shortcut_keycode = get_int("shortcut_keycode", cfg.shortcut_keycode)
        cfg.shortcut_modifiers = get_int("shortcut_modifiers", cfg.shortcut_modifiers)
        if not cfg.reminders:
            cfg.reminders = [L.default_reminder_1, L.default_reminder_2]
        return cfg.normalized()

    def normalized(self) -> "AppConfig":
        for key, (lo, hi) in INT_RANGES.items():
            value = getattr(self, key)
            setattr(self, key, max(lo, min(hi, int(value))))
        # Keep saved values in range too.
        self.work_days = {d for d in self.work_days if 1 <= d <= 7}
        if not isinstance(self.break_position, BreakPosition):
            self.break_position = BreakPosition(self.break_position)
        if not isinstance(self.break_display_target, BreakDisplayTarget):
            self.break_display_target = BreakDisplayTarget(self.break_display_target)
        if not isinstance(self.language, AppLanguage):
            self.language = AppLanguage(self.language)
        if not isinstance(self.appearance, AppAppearance):
            self.appearance = AppAppearance(self.appearance)
        return self


def _as_date(at: datetime | date) -> date:
    return at.date() if isinstance(at, datetime) else at


def holiday_key(at: datetime | date) -> str:
    return _as_date(at).strftime("%Y-%m-%d")


def format_break_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} {L.unit_seconds}"
    if seconds % 60 == 0:
        return f"{seconds // 60} {L.unit_minutes}"
    return f"{seconds // 60}{L.unit_minutes}{seconds % 60}{L.unit_seconds}"


def format_work_time(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}{L.unit_minutes_short}" if L.is_zh else f"{minutes}m"
    h, m = divmod(minutes, 60)
    if L.is_zh:
        return f"{h}小时{m}分" if m else f"{h}小时"
    return f"{h}h {m}m" if m else f"{h}h"


def format_work_time_short(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}分" if L.is_zh else f"{minutes}m"
    h, m = divmod(minutes, 60)
    if L.is_zh:
        return f"{h}时{m}分" if m else f"{h}时"
    return f"{h}h{m}m" if m else f"{h}h"


def format_duration_seconds(seconds: int) -> str:
    minutes, secs = divmod(max(0, int(round(seconds))), 60)
    if L.is_zh:
        if minutes:
            return f"{minutes}分{secs}秒" if secs else f"{minutes}分"
        return f"{secs}秒"
    if minutes:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    return f"{secs}s"


def weekday_order() -> Iterable[int]:
    return (2, 3, 4, 5, 6, 7, 1)  # Monday .. Sunday, Swift numbering
