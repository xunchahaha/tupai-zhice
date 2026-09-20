"""公开课表的 ICS 订阅日历生成（docs/roadmap/06 §3 A5）。

有 ``lesson_date`` 的课次生成普通 VEVENT；循环课次（无 ``lesson_date``、只有
``TimeSlot.weekday``）生成 ``RRULE:FREQ=WEEKLY;BYDAY=XX;UNTIL=窗口末`` 的
VEVENT，``DTSTART`` 取窗口内首个匹配该 weekday 的日期 + 时段起止。

已知取舍：RRULE 只能表达「每周固定重复」，无法表达单次调课 override（需要
RECURRENCE-ID 例外事件，而公开投影只有「周期课次」口径，没有「周期 + 例外」）。
因此 RRULE 课次的时效性由 H5 调课横幅兜底（schedule.json 的 ``adjustments``），
订阅方以调课公告为准。时间口径与业务层一致：DTSTART/DTEND 用 Asia/Shanghai
墙上时间（静态 VTIMEZONE，+0800 无夏令时），DTSTAMP 用发布时间的 UTC 瞬间。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from icalendar import Calendar, Event, Timezone, TimezoneStandard

from ..timezone import SHANGHAI_TZ, as_utc, shanghai_now

PRODID = "-//tupai-zhice//公开课表//CN"
TIMEZONE_ID = "Asia/Shanghai"

# ``TimeSlot.weekday`` 是中文字符串（「周一」…），映射到 RFC 5545 的 BYDAY 值。
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_WEEKDAY_TO_BYDAY = dict(
    zip(_WEEKDAYS, ("MO", "TU", "WE", "TH", "FR", "SA", "SU"), strict=True)
)
# 无任何有日期课次锚点时，循环课次展开窗口的回退：今天−7 ～ +60 天。
_FALLBACK_WINDOW_DAYS_BACK = 7
_FALLBACK_WINDOW_DAYS_FORWARD = 60


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


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _combine(lesson_date: str, clock: str) -> datetime | None:
    day = _parse_date(lesson_date)
    parsed = _parse_clock(clock)
    if day is None or parsed is None:
        return None
    return datetime.combine(day, parsed, tzinfo=SHANGHAI_TZ)


def _recurrence_window(rows: list[dict[str, Any]]) -> tuple[date, date]:
    """循环课次的 RRULE 展开窗口：有日期课次的 min/max（同一发布版本）。

    版本内没有任何有日期课次（无锚点）时回退：今天−7 ～ +60 天。
    """

    dated = [
        day
        for day in (_parse_date(str(row.get("date") or "")) for row in rows)
        if day is not None
    ]
    if dated:
        return dated[0], dated[-1]
    today = shanghai_now().date()
    return (
        today - timedelta(days=_FALLBACK_WINDOW_DAYS_BACK),
        today + timedelta(days=_FALLBACK_WINDOW_DAYS_FORWARD),
    )


def _recurring_event_span(
    row: dict[str, Any], window_start: date, window_end: date
) -> tuple[datetime, datetime, dict[str, list[Any]]] | None:
    """把循环课次行变成 DTSTART/DTEND/RRULE 三元组；无法定位时返回 None。"""

    weekday_name = str(row.get("weekday") or "")
    byday = _WEEKDAY_TO_BYDAY.get(weekday_name)
    start_clock = _parse_clock(str(row.get("start") or ""))
    end_clock = _parse_clock(str(row.get("end") or ""))
    if byday is None or start_clock is None or end_clock is None or end_clock <= start_clock:
        return None
    # DTSTART = 窗口内首个匹配该 weekday 的日期 + 时段起止。
    target = _WEEKDAYS.index(weekday_name)
    first_day = window_start + timedelta(days=(target - window_start.weekday()) % 7)
    start = datetime.combine(first_day, start_clock, tzinfo=SHANGHAI_TZ)
    end = datetime.combine(first_day, end_clock, tzinfo=SHANGHAI_TZ)
    # RFC 5545：带时区 DTSTART 的 UNTIL 必须是 UTC 瞬间；取窗口末整天，
    # 窗口不含该 weekday 时至少保住 DTSTART 的首次出现。
    until_day = max(window_end, first_day)
    until = as_utc(datetime.combine(until_day, time(23, 59, 59), tzinfo=SHANGHAI_TZ))
    return start, end, {"FREQ": ["WEEKLY"], "BYDAY": [byday], "UNTIL": [until]}


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
    客户端订阅在重新发布后按 DTSTAMP/SEQUENCE 原位更新既有事件。循环课次
    （无 ``lesson_date``）按 WEEKLY RRULE 展开，取舍见模块 docstring。
    """

    calendar = Calendar()
    calendar.add("prodid", PRODID)
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", display_name or "公开课表")
    calendar.add("x-wr-timezone", TIMEZONE_ID)
    calendar.add_component(_static_vtimezone())

    window_start, window_end = _recurrence_window(rows)
    stamp = as_utc(published_at) or datetime.now(UTC)
    # 每个事件的 DTSTAMP 相同（同一发布瞬间），重新发布时整体前移触发刷新。
    for row in rows:
        lesson_date = str(row.get("date") or "")
        if lesson_date:
            start = _combine(lesson_date, str(row.get("start") or ""))
            end = _combine(lesson_date, str(row.get("end") or ""))
            rrule: dict[str, list[Any]] | None = None
        else:
            # 循环课次（无 lesson_date，仅 TimeSlot.weekday）：WEEKLY RRULE 展开。
            span = _recurring_event_span(row, window_start, window_end)
            if span is None:
                continue
            start, end, rrule = span
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
        if rrule is not None:
            event.add("rrule", rrule)
        if show_teacher_names:
            teacher_text = "、".join(row.get("teacher_names") or [])
            if teacher_text:
                event.add("description", teacher_text)
        calendar.add_component(event)
    return calendar.to_ical()
