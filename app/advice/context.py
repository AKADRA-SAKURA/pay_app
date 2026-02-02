from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app import crud
from app.services.forecast import forecast_free_daily  # さっき作ったfreeのseries関数を使う想定
from app.utils.dates import month_range


@dataclass
class AdviceContext:
    asof: str
    metric: str  # "free"
    start: int
    end: int
    min_value: int
    min_date: str
    days_to_min: int
    trend_7d: int  # 7日前との差分（取れなければ0）


def build_advice_context_free(db: Session, user_id: int, start: date, end: date) -> AdviceContext:
    """
    自由に使えるお金（free）の日次seriesから、匿名化コンテキストを作る
    """
    series = forecast_free_daily(db, user_id=user_id, start=start, end=end)
    # series: [{"date":"YYYY-MM-DD","balance_yen":...}, ...]

    if not series:
        # データ無い場合の保険
        return AdviceContext(
            asof=str(date.today()),
            metric="free",
            start=0,
            end=0,
            min_value=0,
            min_date=str(end),
            days_to_min=999,
            trend_7d=0,
        )

    start_value = int(series[0]["balance_yen"])
    end_value = int(series[-1]["balance_yen"])

    # min
    min_point = min(series, key=lambda p: int(p.get("balance_yen") or 0))
    min_value = int(min_point["balance_yen"])
    min_date = str(min_point["date"])

    # days_to_min
    asof_dt = date.fromisoformat(str(series[0]["date"]))  # start日
    min_dt = date.fromisoformat(min_date)
    days_to_min = (min_dt - asof_dt).days

    # trend_7d（7日前が無い場合は0）
    trend_7d = 0
    if len(series) >= 8:
        trend_7d = int(series[-1]["balance_yen"]) - int(series[-8]["balance_yen"])

    return AdviceContext(
        asof=str(date.today()),
        metric="free",
        start=start_value,
        end=end_value,
        min_value=min_value,
        min_date=min_date,
        days_to_min=days_to_min,
        trend_7d=trend_7d,
    )

def build_llm_payload_free(db: Session, user_id: int) -> dict:
    today = date.today()
    this_first = today.replace(day=1)

    # 来月末まで
    if this_first.month == 12:
        next_first = date(this_first.year + 1, 1, 1)
    else:
        next_first = date(this_first.year, this_first.month + 1, 1)
    _, next_last = month_range(next_first)

    # free series（日次）
    series = forecast_free_daily(db, user_id=user_id, start=this_first, end=next_last)
    if not series:
        series = [{"date": this_first.isoformat(), "balance_yen": 0}]

    # 今月末 / 来月末（seriesから拾う）
    # seriesが「今月初〜来月末」の日次なので、
    # 今月末の日付キーを探す
    _, this_last = month_range(this_first)
    this_last_key = this_last.isoformat()
    next_last_key = next_last.isoformat()

    by_date = {p["date"]: int(p["balance_yen"]) for p in series}

    free_this_end = by_date.get(this_last_key, int(series[-1]["balance_yen"]))
    free_next_end = by_date.get(next_last_key, int(series[-1]["balance_yen"]))

    # min（期間内）
    min_p = min(series, key=lambda p: int(p.get("balance_yen") or 0))
    min_value = int(min_p["balance_yen"])
    min_date = str(min_p["date"])

    # 引落予定（未来60日）
    withdraw_schedule = crud.list_withdraw_schedule(db, user_id=user_id, start=today, days=60)

    payload = {
        "asof": today.isoformat(),
        "period": {"start": this_first.isoformat(), "end": next_last.isoformat()},
        "free_this_end": free_this_end,
        "free_next_end": free_next_end,
        "free_min_value": min_value,
        "free_min_date": min_date,
        "withdraw_schedule_next_60d": withdraw_schedule,
        # 追加でトレンド等を入れたいならここに
    }

    ws = withdraw_schedule
    withdraw_total_60d = sum(int(x["amount_yen"]) for x in ws) if ws else 0
    withdraw_peak_amount_yen = min((int(x["amount_yen"]) for x in ws), default=0)  # マイナスが大きい=支出大
    withdraw_peak_date = ws[0]["date"] if ws else today.isoformat()
    for x in ws:
        if int(x["amount_yen"]) < withdraw_peak_amount_yen:
            withdraw_peak_amount_yen = int(x["amount_yen"])
            withdraw_peak_date = x["date"]

    peak_days = (date.fromisoformat(withdraw_peak_date) - today).days if ws else 0

    payload.update({
        "withdraw_total_60d": withdraw_total_60d,
        "withdraw_peak_amount_yen": withdraw_peak_amount_yen,
        "withdraw_peak_in_days": peak_days,
    })

    return payload
