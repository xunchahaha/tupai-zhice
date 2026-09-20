"""公开课表的 ICS 订阅日历生成（docs/roadmap/06 §3 A5）。

P0 只生成有 ``lesson_date`` 的课次；循环课次的 RRULE 周重复展开属 P1。
时间口径与业务层一致：DTSTART/DTEND 用 Asia/Shanghai 墙上时间（静态
VTIMEZONE，+0800 无夏令时），DTSTAMP 用发布时间的 UTC 瞬间。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, time, timedelta
from typing import Any

from icalendar import Calendar, Event, Timezone, TimezoneStandard

from ..timezone import SHANGHAI_TZ, as_utc

PRODID = "-//tupai-zhice//公开课表//CN"
TIMEZONE_ID = "Asia/Shanghai"


def calendar_etag(version_id: str | None, published_at: datetime | None) -> str:
    """发布版本身份（version_id + published_at）的强 ETag（06 §3 A5）。"""

    stamp = as_utc(published_at)
    raw = f"{version_id or 'unpublished'}|{stamp.isoformat() if stamp else ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_clock(value: str) -> time | None:
    parts = value.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return time(hour, minute)


def _combine(lesson_date: str, clock: str) -> datetime | None:
    try:
        day = datetime.strptime(lesson_date, "%Y-%m-%d").date()
    except ValueError:
        return None
    parsed = _parse_clock(clock)
    if parsed is None:
        return None
    return datetime.combine(day, parsed, tzinfo=SHANGHAI_TZ)


def _static_vtimezone() -> Timezone:
    """Asia/Shanghai 的静态 VTIMEZONE：恒为 +0800，无夏令时切换。"""

    timezone = Timezone()
    timezone.add("tzid", TIMEZONE_ID)
    standard = TimezoneStandard()
    standard.add("dtstart", datetime(1970, 1, 1, 0, 0, 0))
    standard.add("tzoffsetfrom", timedelta(hours=8))
    standard.add("tzoffsetto", timedelta(hours=8))
    standard.add("tzname", "CST")
    timezone.add_component(standard)
    return timezone


def _event_summary(row: dict[str, Any]) -> str:
    subject = str(row.get("subject") or "")
    lesson_name = str(row.get("lesson_name") or "")
    if lesson_name and (not subject or subject in lesson_name):
        return lesson_name
    if subject and lesson_name:
        return f"{subject}·{lesson_name}"
    return lesson_name or subject or "课程安排"


def build_public_calendar_ics(
    *,
    display_name: str,
    scope: str,
    resource_business_id: str | None,
    show_teacher_names: bool,
    version_no: int | None,
    published_at: datetime | None,
    rows: list[dict[str, Any]],
) -> bytes:
    """把 ``public_schedule_entries`` 的行组装成 VCALENDAR 字节流。

    UID 跨版本稳定（``tupai-{scope}-{resource_business_id}-{assignment_id}``），
    客户端订阅在重新发布后按 DTSTAMP/SEQUENCE 原位更新既有事件。
    """

    calendar = Calendar()
    calendar.add("prodid", PRODID)
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", display_name or "公开课表")
    calendar.add("x-wr-timezone", TIMEZONE_ID)
    calendar.add_component(_static_vtimezone())

    stamp = as_utc(published_at) or datetime.now(UTC)
    # 每个事件的 DTSTAMP 相同（同一发布瞬间），重新发布时整体前移触发刷新。
    for row in rows:
        lesson_date = str(row.get("date") or "")
        start_clock = str(row.get("start") or "")
        end_clock = str(row.get("end") or "")
        if not lesson_date or not start_clock or not end_clock:
            # 循环课次（无 lesson_date）与缺起止时刻的课次 P0 跳过，RRULE 属 P1。
            continue
        start = _combine(lesson_date, start_clock)
        end = _combine(lesson_date, end_clock)
        if start is None or end is None or end <= start:
            continue
        event = Event()
        event.add(
            "uid",
            f"tupai-{scope}-{resource_business_id or 'all'}-{row['assignment_id']}@public.tupai",
        )
        event.add("dtstamp", stamp)
        event.add("dtstart", start)
        event.add("dtend", end)
        event.add("sequence", version_no or 0)
        event.add("summary", _event_summary(row))
        event.add("location", str(row.get("location") or "待定"))
        if show_teacher_names:
            teacher_text = "、".join(row.get("teacher_names") or [])
            if teacher_text:
                event.add("description", teacher_text)
        calendar.add_component(event)
    return calendar.to_ical()
