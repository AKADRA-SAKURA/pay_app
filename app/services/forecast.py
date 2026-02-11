from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import Account, CashflowEvent


def _iso(d: Any) -> str:
    if d is None:
        return ""
    if isinstance(d, (date, datetime)):
        return d.date().isoformat() if isinstance(d, datetime) else d.isoformat()
    return str(d)


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _is_account_active_on(account: Account, d: date) -> bool:
    start_d = getattr(account, "effective_start_date", None)
    end_d = getattr(account, "effective_end_date", None)
    if start_d and d < start_d:
        return False
    if end_d and d > end_d:
        return False
    return True


def forecast_by_account_events(
    db: Session,
    user_id: int,
    start: date,
    end: date,
    include_start_point: bool = True,
    danger_threshold_yen: int = 0,
) -> dict:
    accounts = (
        db.query(Account)
        .filter(Account.user_id == user_id)
        .order_by(Account.id)
        .all()
    )

    account_by_id = {int(a.id): a for a in accounts}

    # balance_yen is treated as balance at effective_start_date (if set).
    balances = {
        int(a.id): int(a.balance_yen) if _is_account_active_on(a, start) else 0
        for a in accounts
    }
    start_balances = dict(balances)

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

    series: dict[int, list[dict]] = defaultdict(list)
    marker_by_date: dict[date, list[dict]] = defaultdict(list)

    for a in accounts:
        aid = int(a.id)
        start_d = getattr(a, "effective_start_date", None)
        end_d = getattr(a, "effective_end_date", None)

        if start_d and start < start_d <= end:
            marker_by_date[start_d].append(
                {"kind": "activate", "account_id": aid, "balance_yen": int(a.balance_yen)}
            )

        if end_d:
            deact_date = end_d + timedelta(days=1)
            if start < deact_date <= end:
                marker_by_date[deact_date].append({"kind": "deactivate", "account_id": aid})

    if include_start_point:
        for a in accounts:
            aid = int(a.id)
            series[aid].append(
                {
                    "date": _iso(start),
                    "balance_yen": int(balances[aid]),
                    "delta_yen": 0,
                    "event_id": None,
                }
            )

    events_by_date: dict[date, list[CashflowEvent]] = defaultdict(list)
    for ev in events:
        events_by_date[ev.date].append(ev)

    total_balance = sum(int(v) for v in start_balances.values())
    total_series: list[dict] = []
    if include_start_point:
        total_series.append(
            {
                "date": _iso(start),
                "balance_yen": int(total_balance),
                "delta_yen": 0,
                "event_id": None,
            }
        )

    timeline_dates = sorted(set(events_by_date.keys()) | set(marker_by_date.keys()))
    for d in timeline_dates:
        for marker in marker_by_date.get(d, []):
            aid = int(marker["account_id"])
            if aid not in balances:
                continue

            before = int(balances[aid])
            if marker["kind"] == "activate":
                after = int(marker["balance_yen"])
            else:
                after = 0

            delta = after - before
            balances[aid] = after
            series[aid].append(
                {
                    "date": _iso(d),
                    "balance_yen": int(after),
                    "delta_yen": int(delta),
                    "event_id": None,
                }
            )

            total_balance += delta
            total_series.append(
                {
                    "date": _iso(d),
                    "balance_yen": int(total_balance),
                    "delta_yen": int(delta),
                    "event_id": None,
                }
            )

        for ev in events_by_date.get(d, []):
            aid = int(ev.account_id)
            acc = account_by_id.get(aid)
            if acc is None:
                continue
            if not _is_account_active_on(acc, d):
                continue

            delta = int(ev.amount_yen)
            balances[aid] += delta
            series[aid].append(
                {
                    "date": _iso(d),
                    "balance_yen": int(balances[aid]),
                    "delta_yen": int(delta),
                    "event_id": int(ev.id),
                }
            )

            total_balance += delta
            total_series.append(
                {
                    "date": _iso(d),
                    "balance_yen": int(total_balance),
                    "delta_yen": int(delta),
                    "event_id": int(ev.id),
                }
            )

    accounts_out = []
    for a in accounts:
        aid = int(a.id)
        s = list(series.get(aid) or [])
        summary = _summarize_series(s, start, int(start_balances[aid]), danger_threshold_yen)
        accounts_out.append(
            {
                "account_id": aid,
                "name": a.name,
                "start_balance_yen": int(start_balances[aid]),
                "summary": summary,
                "series": s,
            }
        )

    return {
        "start": start,
        "end": end,
        "accounts": accounts_out,
        "total_series": total_series,
    }


def forecast_by_account_daily(db: Session, user_id: int, start: date, end: date) -> dict:
    base = forecast_by_account_events(db, user_id=user_id, start=start, end=end, include_start_point=True)

    out_accounts = []

    for acc in base["accounts"]:
        bal_by_date: dict[str, int] = {}
        for p in acc["series"]:
            bal_by_date[str(p["date"])] = int(p["balance_yen"])

        daily = []
        last_balance = int(acc["start_balance_yen"])

        for d in _daterange(start, end):
            key = _iso(d)
            if key in bal_by_date:
                last_balance = int(bal_by_date[key])
            daily.append({"date": key, "balance_yen": int(last_balance)})

        out_accounts.append(
            {
                "account_id": acc["account_id"],
                "name": acc["name"],
                "start_balance_yen": acc["start_balance_yen"],
                "series": daily,
            }
        )

    total_by_date: dict[str, int] = {}
    for acc in out_accounts:
        for p in acc["series"]:
            total_by_date[p["date"]] = total_by_date.get(p["date"], 0) + int(p["balance_yen"])

    total_daily = [{"date": d, "balance_yen": int(total_by_date[d])} for d in sorted(total_by_date.keys())]

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
    base = forecast_by_account_daily(db, user_id=user_id, start=start, end=end)
    return list(base.get("total_series") or [])
