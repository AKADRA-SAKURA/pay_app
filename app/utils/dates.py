# app/utils/dates.py
from __future__ import annotations
from datetime import date
import calendar

def month_range(d: date) -> tuple[date, date]:
    first = d.replace(day=1)
    last_day = calendar.monthrange(first.year, first.month)[1]
    last = first.replace(day=last_day)
    return first, last
