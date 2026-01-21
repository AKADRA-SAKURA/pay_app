import calendar
from datetime import date
from sqlalchemy.orm import Session

from app.models import Plan, CashflowEvent

def month_range(d: date):
    first = d.replace(day=1)
    last_day = calendar.monthrange(d.year, d.month)[1]
    last = d.replace(day=last_day)
    return first, last

def next_month_first(d: date) -> date:
    y, m = d.year, d.month
    if m == 12:
        return date(y + 1, 1, 1)
    return date(y, m + 1, 1)

def date_in_month(year: int, month: int, day: int) -> date:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))

def occurs_monthly_interval(base: date, target_first: date, interval_months: int) -> bool:
    # base=今月を起点に、差分月がintervalで割り切れたら発生（簡易版）
    interval = max(1, int(interval_months))
    base_num = base.year * 12 + base.month
    target_num = target_first.year * 12 + target_first.month
    return (target_num - base_num) % interval == 0

def rebuild_events_for_two_months(db: Session, user_id: int, today: date):
    this_first, this_last = month_range(today)
    next_first = next_month_first(this_first)
    next_first, next_last = month_range(next_first)

    # 期間内イベントを作り直す（M1の割り切り）
    db.query(CashflowEvent).filter(
        CashflowEvent.user_id == user_id,
        CashflowEvent.date >= this_first,
        CashflowEvent.date <= next_last,
    ).delete(synchronize_session=False)
    db.commit()

    plans = db.query(Plan).filter(Plan.user_id == user_id).all()

    def add_event(p: Plan, d: date):
        sign = 1 if p.type == "income" else -1
        db.add(CashflowEvent(
            user_id=user_id,
            date=d,
            amount_yen=sign * int(p.amount_yen),
            account_id=int(p.account_id),
            plan_id=int(p.id),
            status="expected",
        ))

    for p in plans:
        # monthly / monthly_interval
        if p.freq in ("monthly", "monthly_interval"):
            # 今月
            if p.freq == "monthly" or occurs_monthly_interval(today, this_first, p.interval_months):
                add_event(p, date_in_month(this_first.year, this_first.month, int(p.day)))
            # 来月
            if p.freq == "monthly" or occurs_monthly_interval(today, next_first, p.interval_months):
                add_event(p, date_in_month(next_first.year, next_first.month, int(p.day)))

        # yearly
        elif p.freq == "yearly":
            if int(p.month) == this_first.month:
                add_event(p, date_in_month(this_first.year, this_first.month, int(p.day)))
            if int(p.month) == next_first.month:
                add_event(p, date_in_month(next_first.year, next_first.month, int(p.day)))

    db.commit()
