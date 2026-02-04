from __future__ import annotations
from datetime import date
import calendar

def month_range(d: date) -> tuple[date, date]:
    first = d.replace(day=1)
    last_day = calendar.monthrange(first.year, first.month)[1]
    last = first.replace(day=last_day)
    return first, last

def last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]

def resolve_day_in_month(year: int, month: int, desired_day: int) -> date:
    """
    desired_day が存在しない月は末日に丸める
    """
    day = min(desired_day, last_day_of_month(year, month))
    return date(year, month, day)
