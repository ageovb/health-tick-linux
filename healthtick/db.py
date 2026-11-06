"""SQLite persistence for HealthTick Linux.

Schema and exported JSON match the macOS app (version 1).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import AppConfig, AppLanguage, clamp_int
from .i18n import L

EXPORT_VERSION = 1
TRANSIENT_CONFIG_KEYS = {
    "timer_phase",
    "timer_target_time",
    "timer_paused_remaining",
    "timer_save_date",
    "overtime_active",
}


def default_data_dir() -> Path:
    override = os.environ.get("HEALTHTICK_DATA_DIR")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "health-tick"


def today_string() -> str:
    return date.today().strftime("%Y-%m-%d")


def date_string(value: date) -> str:
    return value.strftime("%Y-%m-%d")


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def latest_importable_date() -> str:
    return (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")


def is_importable_date(value: str, latest: str | None = None) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    if value[4] != "-" or value[7] != "-":
        return False
    if not (value[:4] + value[5:7] + value[8:10]).isdigit():
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return value <= (latest or latest_importable_date())


class ImportFailure(Exception):
    pass


@dataclass
class ImportSummary:
    records_added: int = 0
    records_skipped: int = 0
    sessions_added: int = 0
    sessions_skipped: int = 0
    rejected: int = 0
    config_applied: bool = False
    backup_path: str = ""


class Database:
    def __init__(self, path: str | Path | None = None):
        if path is None:
            data_dir = default_data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            path = data_dir / "data.db"
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        self._create_tables()
        self._seed_defaults()

    # ----------------------------------------------------------------- setup

    def _create_tables(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                date TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_records_date ON records(date);
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                work_start TEXT NOT NULL,
                work_end TEXT,
                work_minutes INTEGER NOT NULL,
                break_start TEXT,
                break_end TEXT,
                break_minutes INTEGER NOT NULL,
                break_actual_seconds INTEGER,
                skipped INTEGER NOT NULL DEFAULT 0,
                daily_goal INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def _seed_defaults(self) -> None:
        row = self.conn.execute("SELECT COUNT(*) FROM config").fetchone()
        if row and row[0] == 0:
            values = AppConfig().to_db_values()
            values["onboarding_completed"] = "0"
            with self.conn:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                    list(values.items()),
                )

    # ---------------------------------------------------------------- config

    def load_config(self) -> AppConfig:
        values = {
            row["key"]: row["value"]
            for row in self.conn.execute("SELECT key, value FROM config")
        }
        cfg = AppConfig.from_db_values(values)
        L.lang = cfg.language
        return cfg

    def save_config(self, config: AppConfig) -> None:
        values = config.to_db_values()
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                list(values.items()),
            )
            self.conn.execute("DELETE FROM config WHERE key = 'break_minutes'")

    def reset_config(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM config")
            values = AppConfig().to_db_values()
            values["onboarding_completed"] = "0"
            self.conn.executemany(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                list(values.items()),
            )

    # --------------------------------------------------------------- records

    def add_record(self, count: int = 1, when: date | None = None) -> None:
        stamp = iso_now()
        day = date_string(when or date.today())
        with self.conn:
            self.conn.executemany(
                "INSERT INTO records (timestamp, date) VALUES (?, ?)",
                [(stamp, day) for _ in range(max(0, count))],
            )

    def today_count(self) -> int:
        return self.count_for_date(today_string())

    def count_for_date(self, day: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM records WHERE date = ?", (day,)
        ).fetchone()
        return int(row[0]) if row else 0

    def total_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM records").fetchone()
        return int(row[0]) if row else 0

    def _goal_rows(self, order: str = "r.date DESC") -> list[sqlite3.Row]:
        goal = 8
        sql = f"""
            SELECT r.date AS date, r.cnt AS cnt, COALESCE(s.goal, ?) AS day_goal
            FROM (SELECT date, COUNT(*) AS cnt FROM records GROUP BY date) r
            LEFT JOIN (
                SELECT date, daily_goal AS goal FROM sessions
                GROUP BY date HAVING id = MAX(id)
            ) s ON r.date = s.date
            ORDER BY {order}
        """
        return list(self.conn.execute(sql, (goal,)))

    def streak_days(self, goal: int) -> int:
        rows = list(
            self.conn.execute(
                """
                SELECT r.date AS date, r.cnt AS cnt, COALESCE(s.goal, ?) AS day_goal
                FROM (SELECT date, COUNT(*) AS cnt FROM records GROUP BY date) r
                LEFT JOIN (
                    SELECT date, daily_goal AS goal FROM sessions
                    GROUP BY date HAVING id = MAX(id)
                ) s ON r.date = s.date
                ORDER BY r.date DESC
                """,
                (goal,),
            )
        )
        today = today_string()
        streak = 0
        for row in rows:
            if row["date"] == today and row["cnt"] < row["day_goal"]:
                continue
            if row["cnt"] >= row["day_goal"]:
                streak += 1
            else:
                break
        return streak

    def max_streak_days(self, goal: int) -> int:
        rows = list(
            self.conn.execute(
                """
                SELECT r.date AS date, r.cnt AS cnt, COALESCE(s.goal, ?) AS day_goal
                FROM (SELECT date, COUNT(*) AS cnt FROM records GROUP BY date) r
                LEFT JOIN (
                    SELECT date, daily_goal AS goal FROM sessions
                    GROUP BY date HAVING id = MAX(id)
                ) s ON r.date = s.date
                ORDER BY r.date
                """,
                (goal,),
            )
        )
        best = cur = 0
        for row in rows:
            if row["cnt"] >= row["day_goal"]:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        return best

    def recent_7_days_counts(self, end: date | None = None) -> list[tuple[str, int]]:
        end = end or date.today()
        start = end - timedelta(days=6)
        rows = self.conn.execute(
            "SELECT date, COUNT(*) FROM records WHERE date >= ? AND date <= ? GROUP BY date",
            (date_string(start), date_string(end)),
        )
        mapping = {row[0]: int(row[1]) for row in rows}
        return [
            (date_string(end - timedelta(days=i)), mapping.get(date_string(end - timedelta(days=i)), 0))
            for i in range(6, -1, -1)
        ]

    def last_30_days_counts(self, end: date | None = None) -> list[tuple[str, int]]:
        end = end or date.today()
        start = end - timedelta(days=29)
        rows = self.conn.execute(
            "SELECT date, COUNT(*) FROM records WHERE date >= ? AND date <= ? GROUP BY date",
            (date_string(start), date_string(end)),
        )
        mapping = {row[0]: int(row[1]) for row in rows}
        return [
            (date_string(end - timedelta(days=i)), mapping.get(date_string(end - timedelta(days=i)), 0))
            for i in range(29, -1, -1)
        ]

    def _completion_rate(self, start: date, goal: int, days_passed: int) -> tuple[int, int]:
        rows = self.conn.execute(
            "SELECT date, COUNT(*) FROM records WHERE date >= ? GROUP BY date",
            (date_string(start),),
        )
        completed = sum(1 for row in rows if int(row[1]) >= goal)
        return completed, max(1, days_passed)

    def week_completion_rate(self, goal: int) -> tuple[int, int]:
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        return self._completion_rate(monday, goal, (today - monday).days + 1)

    def month_completion_rate(self, goal: int) -> tuple[int, int]:
        today = date.today()
        first = today.replace(day=1)
        return self._completion_rate(first, goal, (today - first).days + 1)

    def days_since_last_goal(self, goal: int) -> int:
        today = date.today()
        for row in self.conn.execute(
            "SELECT date, COUNT(*) AS cnt FROM records GROUP BY date ORDER BY date DESC"
        ):
            if int(row["cnt"]) >= goal:
                try:
                    day = datetime.strptime(row["date"], "%Y-%m-%d").date()
                except ValueError:
                    continue
                return (today - day).days
        return -1

    def all_records_for_stats(self) -> list[dict[str, Any]]:
        return [
            {"date": row["date"], "count": int(row["cnt"])}
            for row in self.conn.execute(
                "SELECT date, COUNT(*) AS cnt FROM records GROUP BY date ORDER BY date"
            )
        ]

    def active_days(self) -> int:
        row = self.conn.execute("SELECT COUNT(DISTINCT date) FROM records").fetchone()
        return int(row[0]) if row else 0

    def best_day_count(self) -> tuple[str, int]:
        row = self.conn.execute(
            "SELECT date, COUNT(*) AS cnt FROM records GROUP BY date ORDER BY cnt DESC LIMIT 1"
        ).fetchone()
        return (row["date"], int(row["cnt"])) if row else ("", 0)

    def first_record_date(self) -> str | None:
        row = self.conn.execute(
            "SELECT date FROM records ORDER BY date ASC LIMIT 1"
        ).fetchone()
        return row["date"] if row else None

    # -------------------------------------------------------------- sessions

    def start_session(self, work_minutes: int, break_seconds: int, daily_goal: int) -> int:
        break_minutes = max(1, break_seconds // 60)
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO sessions
                   (date, work_start, work_minutes, break_minutes, daily_goal)
                   VALUES (?, ?, ?, ?, ?)""",
                (today_string(), iso_now(), work_minutes, break_minutes, daily_goal),
            )
        return int(cur.lastrowid)

    def end_work(self, session_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE sessions SET work_end = ? WHERE id = ?", (iso_now(), session_id)
            )

    def close_orphan_sessions(self, before_date: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE sessions SET work_end = work_start WHERE date < ? AND work_end IS NULL",
                (before_date,),
            )

    def close_today_orphan_sessions(self) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE sessions SET work_end = ? WHERE date = ? AND work_end IS NULL",
                (iso_now(), today_string()),
            )

    def start_session_break(self, session_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE sessions SET break_start = ? WHERE id = ?", (iso_now(), session_id)
            )

    def end_session_break(
        self, session_id: int, actual_seconds: int | None
    ) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE sessions SET break_end = ?, break_actual_seconds = ?
                   WHERE id = ?""",
                (iso_now(), actual_seconds, session_id),
            )

    # ---------------------------------------------------------- work minutes

    @staticmethod
    def _session_minutes(start: str, end: str | None, configured: int) -> int:
        start_dt = parse_iso(start)
        if start_dt is None:
            return 0
        end_dt = parse_iso(end or "") or start_dt
        elapsed = int((end_dt - start_dt).total_seconds() // 60)
        return max(0, min(elapsed, configured))

    def today_work_minutes(self) -> int:
        return self.work_minutes_for_date(today_string())

    def work_minutes_for_date(self, day: str) -> int:
        total = 0
        for row in self.conn.execute(
            """SELECT work_start, work_end, work_minutes FROM sessions
               WHERE date = ? AND work_end IS NOT NULL""",
            (day,),
        ):
            total += self._session_minutes(
                row["work_start"], row["work_end"], int(row["work_minutes"])
            )
        return total

    def _range_work_minutes(self, days: int) -> list[tuple[str, int]]:
        today = date.today()
        start = today - timedelta(days=days - 1)
        mapping: dict[str, int] = {}
        for row in self.conn.execute(
            """SELECT date, work_start, work_end, work_minutes FROM sessions
               WHERE date >= ? AND work_end IS NOT NULL""",
            (date_string(start),),
        ):
            mapping[row["date"]] = mapping.get(row["date"], 0) + self._session_minutes(
                row["work_start"], row["work_end"], int(row["work_minutes"])
            )
        return [
            (date_string(today - timedelta(days=i)), mapping.get(date_string(today - timedelta(days=i)), 0))
            for i in range(days - 1, -1, -1)
        ]

    def recent_7_days_work_minutes(self) -> list[tuple[str, int]]:
        return self._range_work_minutes(7)

    def last_30_days_work_minutes(self) -> list[tuple[str, int]]:
        return self._range_work_minutes(30)

    def week_work_minutes(self) -> list[tuple[str, int]]:
        return self.recent_7_days_work_minutes()

    # ------------------------------------------------------------- onboarding

    def is_onboarding_completed(self) -> bool:
        row = self.conn.execute(
            "SELECT value FROM config WHERE key = 'onboarding_completed'"
        ).fetchone()
        return bool(row and row[0] == "1")

    def set_onboarding_completed(self) -> None:
        self._set_config_value("onboarding_completed", "1")

    def set_onboarding_incomplete(self) -> None:
        self._set_config_value("onboarding_completed", "0")

    def _set_config_value(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value)
            )

    # ------------------------------------------------------------ timer state

    def save_timer_state(
        self, phase: str, remaining: int | None = None
    ) -> None:
        values = {
            "timer_phase": phase,
            "timer_target_time": "",
            "timer_paused_remaining": "" if remaining is None else str(remaining),
            "timer_save_date": today_string(),
        }
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                list(values.items()),
            )

    def load_timer_state(self) -> tuple[str, int | None]:
        rows = {
            row["key"]: row["value"]
            for row in self.conn.execute(
                "SELECT key, value FROM config WHERE key IN "
                "('timer_phase', 'timer_paused_remaining')"
            )
        }
        remaining = rows.get("timer_paused_remaining", "")
        return rows.get("timer_phase", ""), int(remaining) if remaining else None

    def timer_save_date(self) -> str:
        row = self.conn.execute(
            "SELECT value FROM config WHERE key = 'timer_save_date'"
        ).fetchone()
        return row[0] if row else ""

    def clear_timer_state(self) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM config WHERE key IN "
                "('timer_phase', 'timer_target_time', 'timer_paused_remaining', 'timer_save_date')"
            )

    def save_flag(self, key: str, value: bool) -> None:
        self._set_config_value(key, "1" if value else "0")

    def load_flag(self, key: str) -> bool:
        row = self.conn.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        ).fetchone()
        return bool(row and row[0] == "1")

    # ----------------------------------------------------------------- reset

    def reset_all_data(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM records")
            self.conn.execute("DELETE FROM sessions")

    # ---------------------------------------------------------------- export

    def export_all_data(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "version": EXPORT_VERSION,
            "exportDate": iso_now(),
            "records": [
                {"id": row["id"], "timestamp": row["timestamp"], "date": row["date"]}
                for row in self.conn.execute(
                    "SELECT id, timestamp, date FROM records ORDER BY id"
                )
            ],
            "sessions": [],
            "config": {},
        }
        sessions: list[dict[str, Any]] = []
        for row in self.conn.execute(
            """SELECT id, date, work_start, work_end, work_minutes, break_start,
                      break_end, break_minutes, break_actual_seconds, skipped, daily_goal
               FROM sessions ORDER BY id"""
        ):
            item: dict[str, Any] = {
                "id": row["id"],
                "date": row["date"],
                "work_start": row["work_start"],
                "work_minutes": row["work_minutes"],
                "break_minutes": row["break_minutes"],
                "skipped": row["skipped"],
                "daily_goal": row["daily_goal"],
            }
            for key in (
                "work_end",
                "break_start",
                "break_end",
                "break_actual_seconds",
            ):
                if row[key] is not None:
                    item[key] = row[key]
            sessions.append(item)
        result["sessions"] = sessions
        result["config"] = {
            row["key"]: row["value"]
            for row in self.conn.execute("SELECT key, value FROM config")
            if row["key"] not in TRANSIENT_CONFIG_KEYS
        }
        return result

    def backup_database(self) -> str:
        backup_dir = Path(self.path).parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = backup_dir / f"data-{stamp}.db"
        suffix = 2
        while target.exists():
            target = backup_dir / f"data-{stamp}-{suffix}.db"
            suffix += 1
        dest = sqlite3.connect(str(target))
        try:
            self.conn.backup(dest)
        except sqlite3.DatabaseError as exc:
            raise ImportFailure(L.import_failed) from exc
        finally:
            dest.close()
        return str(target)

    # ---------------------------------------------------------------- import

    def import_data(
        self, payload: dict[str, Any], include_config: bool = False
    ) -> ImportSummary:
        try:
            version = int(payload.get("version", 0))
        except (TypeError, ValueError):
            version = 0
        if version < 1:
            raise ImportFailure(L.import_malformed)
        if version > EXPORT_VERSION:
            raise ImportFailure(L.import_unsupported.format(version=version))

        summary = ImportSummary()
        summary.backup_path = self.backup_database()
        latest = latest_importable_date()

        try:
            self.conn.execute("BEGIN IMMEDIATE")
            records = payload.get("records") or []
            if isinstance(records, list):
                self._import_records(records, latest, summary)
            sessions = payload.get("sessions") or []
            if isinstance(sessions, list):
                self._import_sessions(sessions, latest, summary)
            if include_config and isinstance(payload.get("config"), dict):
                self._import_config(payload["config"])
                summary.config_applied = True
            self.conn.commit()
        except Exception as exc:
            self.conn.rollback()
            if isinstance(exc, ImportFailure):
                raise
            raise ImportFailure(L.import_failed) from exc
        return summary

    def _import_records(
        self, rows: Iterable[Any], latest: str, summary: ImportSummary
    ) -> None:
        existing: dict[tuple[str, str], int] = {}
        for row in self.conn.execute("SELECT timestamp, date FROM records"):
            key = (row["timestamp"], row["date"])
            existing[key] = existing.get(key, 0) + 1
        for raw in rows:
            if not isinstance(raw, dict):
                summary.rejected += 1
                continue
            stamp = raw.get("timestamp")
            day = raw.get("date")
            if not isinstance(stamp, str) or not isinstance(day, str):
                summary.rejected += 1
                continue
            if not is_importable_date(day, latest):
                summary.rejected += 1
                continue
            key = (stamp, day)
            if existing.get(key, 0) > 0:
                existing[key] -= 1
                summary.records_skipped += 1
                continue
            self.conn.execute(
                "INSERT INTO records (timestamp, date) VALUES (?, ?)", (stamp, day)
            )
            summary.records_added += 1

    def _import_sessions(
        self, rows: Iterable[Any], latest: str, summary: ImportSummary
    ) -> None:
        existing: dict[tuple[str, str], int] = {}
        for row in self.conn.execute("SELECT date, work_start FROM sessions"):
            key = (row["date"], row["work_start"])
            existing[key] = existing.get(key, 0) + 1
        for raw in rows:
            if not isinstance(raw, dict):
                summary.rejected += 1
                continue
            day = raw.get("date")
            work_start = raw.get("work_start")
            if not isinstance(day, str) or not isinstance(work_start, str):
                summary.rejected += 1
                continue
            if not is_importable_date(day, latest):
                summary.rejected += 1
                continue
            key = (day, work_start)
            if existing.get(key, 0) > 0:
                existing[key] -= 1
                summary.sessions_skipped += 1
                continue
            self.conn.execute(
                """INSERT INTO sessions
                   (date, work_start, work_end, work_minutes, break_start, break_end,
                    break_minutes, break_actual_seconds, skipped, daily_goal)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    day,
                    work_start,
                    raw.get("work_end"),
                    self._as_int(raw.get("work_minutes"), 60),
                    raw.get("break_start"),
                    raw.get("break_end"),
                    self._as_int(raw.get("break_minutes"), 2),
                    self._as_int(raw.get("break_actual_seconds"), None),
                    self._as_int(raw.get("skipped"), 0),
                    self._as_int(raw.get("daily_goal"), 8),
                ),
            )
            summary.sessions_added += 1

    def _import_config(self, config: dict[str, Any]) -> None:
        for key, raw in config.items():
            if key in TRANSIENT_CONFIG_KEYS:
                continue
            value = str(raw)
            if key in {
                "work_minutes",
                "saved_work_minutes",
                "break_seconds",
                "saved_break_seconds",
                "daily_goal",
                "long_break_interval",
                "long_break_seconds",
                "pre_break_notice_seconds",
                "pre_break_notice_long_seconds",
                "alert_sound_repeat_count",
            }:
                try:
                    value = str(clamp_int(key, int(value), 1))
                except (TypeError, ValueError):
                    continue
            self.conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (key, value),
            )

    @staticmethod
    def _as_int(value: Any, default: int | None) -> int | None:
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    # ----------------------------------------------------------------- close

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass
