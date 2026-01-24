from __future__ import annotations
from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy.orm import Session

from app.models import Account, CashflowEvent


def forecast_by_account_events(
    db: Session,
    user_id: int,
    start: date,
    end: date,
    include_start_point: bool = True,
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
            series[aid].append({"date": start, "balance_yen": balances[aid], "delta_yen": 0, "event_id": None})

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
        total_series.append({"date": start, "balance_yen": total_start, "delta_yen": 0, "event_id": None})

    total_balance = total_start
    for ev in events:
        total_balance += int(ev.amount_yen)
        total_series.append(
            {"date": ev.date, "balance_yen": total_balance, "delta_yen": int(ev.amount_yen), "event_id": int(ev.id)}
        )

    return {
        "start": start,
        "end": end,
        "accounts": [
            {
                "account_id": int(a.id),
                "name": a.name,
                "start_balance_yen": int(a.balance_yen),
                "series": series[int(a.id)],
            }
            for a in accounts
        ],
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

    # account_id -> (date -> balance) を作る
    out_accounts = []

    for acc in base["accounts"]:
        # イベント時点のバランスをmap化
        bal_by_date = {}
        for p in acc["series"]:
            bal_by_date[p["date"]] = int(p["balance_yen"])

        # 日次で穴埋め
        daily = []
        last_balance = int(acc["start_balance_yen"])

        for d in _daterange(start, end):
            if d in bal_by_date:
                last_balance = bal_by_date[d]
            daily.append({"date": d, "balance_yen": last_balance})

        out_accounts.append(
            {
                "account_id": acc["account_id"],
                "name": acc["name"],
                "start_balance_yen": acc["start_balance_yen"],
                "series": daily,
            }
        )

    # totalも日次にするなら同様（ここでは省略しないで一応作る）
    total_map = {}
    for p in base["total_series"]:
        total_map[p["date"]] = int(p["balance_yen"])

    total_daily = []
    last_total = total_map.get(start, 0)
    for d in _daterange(start, end):
        if d in total_map:
            last_total = total_map[d]
        total_daily.append({"date": d, "balance_yen": last_total})

    return {"start": start, "end": end, "accounts": out_accounts, "total_series": total_daily}
