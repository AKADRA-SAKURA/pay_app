from __future__ import annotations

from datetime import date
import calendar
from sqlalchemy.orm import Session

from app.models import Plan, CashflowEvent


def _last_day_of_month(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def _clamp_day(y: int, m: int, d: int) -> int:
    return min(d, _last_day_of_month(y, m))


def _month_add(y: int, m: int, add: int) -> tuple[int, int]:
    # month add (1-12)
    total = (y * 12 + (m - 1)) + add
    ny = total // 12
    nm = (total % 12) + 1
    return ny, nm


def occurs_monthly_interval(start: date, target_month_first: date, interval_months: int) -> bool:
    """start を基準に interval_months ごとの月が target_month_first(月初)に一致するか"""
    if interval_months <= 0:
        interval_months = 1
    start_index = start.year * 12 + (start.month - 1)
    target_index = target_month_first.year * 12 + (target_month_first.month - 1)
    if target_index < start_index:
        return False
    return (target_index - start_index) % interval_months == 0


def build_month_events(db: Session, user_id: int, month_first: date) -> list[CashflowEvent]:
    """指定月のイベントを plans から生成して返す（DBにはまだ入れない）"""
    plans = db.query(Plan).filter(Plan.user_id == user_id).all()
    y, m = month_first.year, month_first.month

    created: list[CashflowEvent] = []

    for p in plans:
        if not p.account_id:
            # 口座がない plan はイベント生成しない
            continue
        # start_date が NULL の古いデータ対策
        p_start = p.start_date or date.today()

        should_create = False

        if p.freq == "monthly":
            should_create = True
        elif p.freq == "yearly":
            should_create = (p.month == m)
        elif p.freq == "monthly_interval":
            should_create = occurs_monthly_interval(p_start, month_first, p.interval_months or 1)
        else:
            # 未知freqは無視
            continue

        if not should_create:
            continue

        d = _clamp_day(y, m, p.day or 1)
        ev_date = date(y, m, d)

        # amount: incomeは+、subscription(支出)は-
        amount = int(p.amount_yen or 0)
        if p.type != "income":
            amount = -abs(amount)
        else:
            amount = abs(amount)

        created.append(
            CashflowEvent(
                user_id=user_id,
                plan_id=p.id,
                account_id=p.account_id,   # ← これを追加！
                date=ev_date,
                amount_yen=amount,
                status="expected",         # statusがNOT NULLならこれも入れる（モデル次第）
            )
        )

    return created


def rebuild_events(db: Session, user_id: int) -> None:
    """今月＋来月のイベントを作り直す（イベントだけを消して作る）"""
    today = date.today()
    this_first = today.replace(day=1)
    next_y, next_m = _month_add(this_first.year, this_first.month, 1)
    next_first = date(next_y, next_m, 1)

    # いったんイベントを全部消す（ユーザー単位）
    db.query(CashflowEvent).filter(CashflowEvent.user_id == user_id).delete(synchronize_session=False)

    # 作って入れる
    events = []
    events += build_month_events(db, user_id, this_first)
    events += build_month_events(db, user_id, next_first)

    db.add_all(events)
    db.commit()
