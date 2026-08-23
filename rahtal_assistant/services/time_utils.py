"""Centralized Jalali date and performance interval conversion."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import jdatetime

from ..errors import RahtalValidationError


TEHRAN_TIMEZONE = ZoneInfo("Asia/Tehran")


def to_rahtal_jalali_date(
    value: str | None,
    *,
    calendar: str = "gregorian",
) -> str:
    """Return the Jalali YYYY-MM-DD string expected by Rahtal."""

    if value is None or value.strip().lower() == "today":
        gregorian = datetime.now(TEHRAN_TIMEZONE).date()
        jalali = jdatetime.date.fromgregorian(date=gregorian)
        return jalali.strftime("%Y-%m-%d")

    normalized_calendar = calendar.strip().lower()
    try:
        year, month, day = (int(part) for part in value.strip().split("-"))
    except (TypeError, ValueError) as exc:
        raise RahtalValidationError(
            "date must use YYYY-MM-DD, or be 'today'."
        ) from exc

    try:
        if normalized_calendar == "gregorian":
            gregorian = date(year, month, day)
            jalali = jdatetime.date.fromgregorian(date=gregorian)
        elif normalized_calendar == "jalali":
            jalali = jdatetime.date(year, month, day)
        else:
            raise RahtalValidationError(
                "date_calendar must be 'gregorian' or 'jalali'."
            )
    except ValueError as exc:
        raise RahtalValidationError(
            f"{normalized_calendar} date is not valid."
        ) from exc
    return jalali.strftime("%Y-%m-%d")


def resolve_time_interval(
    *,
    start_time: str | None,
    end_time: str | None,
    duration_minutes: int | None,
) -> tuple[str, str, int]:
    """Resolve a same-day interval without inventing a time anchor."""

    parsed_start = _parse_time(start_time, "start_time") if start_time else None
    parsed_end = _parse_time(end_time, "end_time") if end_time else None

    if duration_minutes is not None:
        if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int):
            raise RahtalValidationError("duration_minutes must be an integer.")
        if duration_minutes <= 0 or duration_minutes >= 24 * 60:
            raise RahtalValidationError(
                "duration_minutes must be between 1 and 1439."
            )

    if parsed_start is None and parsed_end is None:
        raise RahtalValidationError(
            "A start_time or end_time is required; duration alone cannot safely "
            "select a Rahtal time range."
        )
    if parsed_start is None and duration_minutes is None:
        raise RahtalValidationError(
            "start_time is required when duration_minutes is not provided."
        )
    if parsed_end is None and duration_minutes is None:
        raise RahtalValidationError(
            "end_time is required when duration_minutes is not provided."
        )

    anchor = date(2000, 1, 1)
    if parsed_start is None:
        end_dt = datetime.combine(anchor, parsed_end)
        start_dt = end_dt - timedelta(minutes=duration_minutes)
    elif parsed_end is None:
        start_dt = datetime.combine(anchor, parsed_start)
        end_dt = start_dt + timedelta(minutes=duration_minutes)
    else:
        start_dt = datetime.combine(anchor, parsed_start)
        end_dt = datetime.combine(anchor, parsed_end)

    if start_dt.date() != anchor or end_dt.date() != anchor or end_dt <= start_dt:
        raise RahtalValidationError(
            "Performance times must form a positive interval within one day."
        )

    calculated_minutes = int((end_dt - start_dt).total_seconds() // 60)
    if duration_minutes is not None and calculated_minutes != duration_minutes:
        raise RahtalValidationError(
            "duration_minutes does not match the supplied start_time and end_time."
        )

    return (
        start_dt.strftime("%H:%M"),
        end_dt.strftime("%H:%M"),
        calculated_minutes,
    )


def _parse_time(value: str, field_name: str) -> time:
    for format_string in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), format_string).time()
        except ValueError:
            continue
    raise RahtalValidationError(f"{field_name} must use 24-hour HH:MM format.")
