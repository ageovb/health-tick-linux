"""China national holiday calendar sync (timor.tech API).

The API returns, for every official holiday arrangement day, whether it is a
day off or a weekend day that becomes a work day (调休).  The same data drives
``AppConfig.is_work_day`` so quiet hours, streaks and reminders follow the
State Council calendar rather than only Monday–Friday.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

API_BASE = "https://timor.tech/api/holiday/year"
USER_AGENT = "HealthTick/1.6.32 (Linux; +https://github.com/lifedever/health-tick-release)"


class HolidaySyncError(Exception):
    pass


@dataclass
class HolidaySyncResult:
    # "YYYY-MM-DD" -> True = makeup work day, False = official day off
    work_day_overrides: dict[str, bool] = field(default_factory=dict)
    # "YYYY-MM-DD" -> display name ("春节", "国庆节", ...)
    labels: dict[str, str] = field(default_factory=dict)
    years: list[int] = field(default_factory=list)


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch_year(year: int, timeout: float = 15.0) -> HolidaySyncResult:
    url = f"{API_BASE}/{year}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if not 200 <= response.status <= 299:
                raise HolidaySyncError(f"HTTP {response.status}")
            payload = json.loads(response.read().decode("utf-8"))
    except HolidaySyncError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HolidaySyncError(str(exc)) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HolidaySyncError("invalid response") from exc

    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise HolidaySyncError("API error")
    days = payload.get("holiday") or {}
    if not isinstance(days, dict):
        raise HolidaySyncError("malformed response")

    result = HolidaySyncResult()
    for entry in days.values():
        if not isinstance(entry, dict):
            continue
        day = entry.get("date")
        if not isinstance(day, str) or len(day) != 10:
            continue
        # holiday: true = 放假; false = 调休上班
        result.work_day_overrides[day] = not bool(entry.get("holiday"))
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            result.labels[day] = name.strip()
    return result


def sync_national_holidays(years: list[int] | None = None) -> HolidaySyncResult:
    """Fetch the current and next year (or the given years), merged."""
    if not years:
        current = date.today().year
        years = [current, current + 1]
    merged = HolidaySyncResult()
    for year in years:
        partial = _fetch_year(year)
        merged.work_day_overrides.update(partial.work_day_overrides)
        merged.labels.update(partial.labels)
        merged.years.append(year)
    return merged
