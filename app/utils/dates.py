from __future__ import annotations
from datetime import date, timedelta
import calendar

def month_range(d: date) -> tuple[date, date]:
    first = d.replace(day=1)
    last_day = calendar.monthrange(first.year, first.month)[1]
    last = first.replace(day=last_day)
    return first, last

# --- 祝日対応（holidaysが入っている前提） ---
try:
    import holidays  # pip install holidays
    _JP_HOLIDAYS = holidays.JP()
except Exception:
    _JP_HOLIDAYS = None  # 依存が無い環境でも最低限動くように

def last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]

def resolve_day_in_month(year: int, month: int, desired_day: int) -> date:
    day = min(desired_day, last_day_of_month(year, month))
    return date(year, month, day)

def is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # Sat/Sun

def is_holiday(d: date) -> bool:
    if _JP_HOLIDAYS is None:
        return False
    return d in _JP_HOLIDAYS

def is_business_day(d: date) -> bool:
    return (not is_weekend(d)) and (not is_holiday(d))

def shift_to_business_day(d: date, *, direction: str) -> date:
    """
    direction:
      - "prev": 直前の営業日へ（前倒し）
      - "next": 直後の営業日へ（後ろ倒し）
    """
    if direction not in ("prev", "next"):
        raise ValueError("direction must be 'prev' or 'next'")

    step = -1 if direction == "prev" else 1
    while not is_business_day(d):
        d = d + timedelta(days=step)
    return d

def apply_business_day_rule(d: date, *, cashflow_type: str) -> date:
    """
    cashflow_type:
      - "income": 休日なら前倒し
      - "expense": 休日なら後ろ倒し
    """
    if cashflow_type == "income":
        return shift_to_business_day(d, direction="prev")
    if cashflow_type == "expense":
        return shift_to_business_day(d, direction="next")
    return d