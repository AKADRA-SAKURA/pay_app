from __future__ import annotations
from collections import defaultdict
from datetime import date, timedelta, datetime
from sqlalchemy.orm import Session
from typing import Any

from app.models import Account, CashflowEvent
from ..crud import total_start_balance


def _iso(d: Any) -> str:
    if d is None:
        return ""
    if isinstance(d, (date, datetime)):
        return d.date().isoformat() if isinstance(d, datetime) else d.isoformat()
    return str(d)


def forecast_by_account_events(
    db: Session,
    user_id: int,
    start: date,
    end: date,
    include_start_point: bool = True,
    danger_threshold_yen: int = 0
) -> dict:
    """
    口座別に、start〜endのイベントを順に適用した「イベント時点の残高推移」を返す
    """
    accounts = (
        db.query(Account)
        .filter(Account.user_id == user_id)
        .order_by(Account.id)
        .all()
    )

    # 起点残高
    balances = {int(a.id): int(a.balance_yen) for a in accounts}

    # イベント取得（口座別に順序が安定するよう date,id）
    events = (
        db.query(CashflowEvent)
        .filter(
            CashflowEvent.user_id == user_id,
            CashflowEvent.date >= start,
            CashflowEvent.date <= end,
            CashflowEvent.status == "expected",
        )
        .order_by(CashflowEvent.date, CashflowEvent.id)
        .all()
    )

    series = defaultdict(list)

    # start時点の点を入れる（UIが分かりやすい）
    if include_start_point:
        for a in accounts:
            aid = int(a.id)
            series[aid].append({"date": _iso(start), "balance_yen": balances[aid], "delta_yen": 0, "event_id": None})

    # イベント適用
    for ev in events:
        aid = int(ev.account_id)
        if aid not in balances:
            # 口座削除などで参照が飛んでる場合はスキップ
            continue
        delta = int(ev.amount_yen)
        balances[aid] += delta
        series[aid].append(
            {
                "date": ev.date,
                "balance_yen": balances[aid],
                "delta_yen": delta,
                "event_id": int(ev.id),
            }
        )

    # 合計残高も出したいなら（口座合算）
    total_start = sum(int(a.balance_yen) for a in accounts)
    total_series = []
    if include_start_point:
        total_series.append({"date": _iso(start), "balance_yen": total_start, "delta_yen": 0, "event_id": None})

    total_balance = total_start
    for ev in events:
        total_balance += int(ev.amount_yen)
        total_series.append(
            {"date": _iso(ev.date), "balance_yen": total_balance, "delta_yen": int(ev.amount_yen), "event_id": int(ev.id)}
        )

    accounts_out = []
    for a in accounts:
        aid = int(a.id)
        s = series[aid]  # list

        summary = _summarize_series(s, start, int(a.balance_yen), danger_threshold_yen)

        accounts_out.append({
            "account_id": aid,
            "name": a.name,
            "start_balance_yen": int(a.balance_yen),
            "summary": summary,
            "series": s,
        })

    return {
        "start": start,
        "end": end,
        "accounts": accounts_out,
        "total_series": total_series,
    }


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def forecast_by_account_daily(db: Session, user_id: int, start: date, end: date) -> dict:
    """
    forecast_by_account_events の結果を、日次で穴埋めして返す
    """
    base = forecast_by_account_events(db, user_id=user_id, start=start, end=end, include_start_point=True)

    out_accounts = []

    for acc in base["accounts"]:
        # イベント時点のバランスを map 化（キーは ISO 文字列に統一）
        bal_by_date = {}
        for p in acc["series"]:
            bal_by_date[str(p["date"])] = int(p["balance_yen"])

        daily = []
        last_balance = int(acc["start_balance_yen"])

        for d in _daterange(start, end):
            key = _iso(d)
            if key in bal_by_date:
                last_balance = bal_by_date[key]
            daily.append({"date": key, "balance_yen": last_balance})

        out_accounts.append(
            {
                "account_id": acc["account_id"],
                "name": acc["name"],
                "start_balance_yen": acc["start_balance_yen"],
                "series": daily,
            }
        )

    # total_series も ISO 文字列キーで統一して穴埋め
    total_map = {}
    for p in base["total_series"]:
        total_map[str(p["date"])] = int(p["balance_yen"])

    total_daily = []
    last_total = total_map.get(_iso(start), 0)

    for d in _daterange(start, end):
        key = _iso(d)
        if key in total_map:
            last_total = total_map[key]
        total_daily.append({"date": key, "balance_yen": last_total})

    # total_daily を accounts の daily series から作り直す（確実に合う）
    total_by_date = {}
    for acc in out_accounts:
        for p in acc["series"]:
            total_by_date[p["date"]] = total_by_date.get(p["date"], 0) + int(p["balance_yen"])

    total_daily = [{"date": d, "balance_yen": total_by_date[d]} for d in sorted(total_by_date.keys())]

    return {"start": start, "end": end, "accounts": out_accounts, "total_series": total_daily}


def _summarize_series(series, start_date, start_balance, danger_threshold_yen=0):
    if not series:
        min_balance = start_balance
        min_date = start_date
        end_balance = start_balance
    else:
        min_point = min(series, key=lambda p: int(p.get("balance_yen", start_balance)))
        min_balance = int(min_point.get("balance_yen", start_balance))
        min_date = min_point.get("date", start_date)
        end_balance = int(series[-1].get("balance_yen", start_balance))

    return {
        "min_balance_yen": min_balance,
        "min_date": min_date,
        "end_balance_yen": end_balance,
        "danger_threshold_yen": danger_threshold_yen,
        "is_danger": (min_balance < danger_threshold_yen),
    }


def forecast_free_daily(db: Session, user_id: int, start: date, end: date) -> list[dict]:
    # 口座合算の開始残高（現金やPayPay等も含めたいなら、ここをtotal化）
    start_balance = total_start_balance(db, user_id)  # 既存のやつがある前提

    # 期間内イベントを日付ごとに合算（口座合算）
    rows = (
        db.query(CashflowEvent.date, CashflowEvent.amount_yen)
        .filter(CashflowEvent.user_id == user_id)
        .filter(CashflowEvent.date >= start)
        .filter(CashflowEvent.date <= end)
        .all()
    )

    delta_by_date = {}
    for d, amt in rows:
        key = _iso(d)            # "YYYY-MM-DD"
        delta_by_date[key] = delta_by_date.get(key, 0) + int(amt)

    series = []
    bal = int(start_balance)

    for d in _daterange(start, end):
        key = _iso(d)
        bal += delta_by_date.get(key, 0)
        series.append({"date": key, "balance_yen": bal})

    return series
