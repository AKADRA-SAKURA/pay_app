from datetime import date, timedelta
from sqlalchemy.orm import Session
from .models import Subscription
from .schemas import SubscriptionCreate
from .models import CashflowEvent, Account, Plan
from sqlalchemy import and_
from sqlalchemy import func

def list_subscriptions(db: Session) -> list[Subscription]:
    return db.query(Subscription).order_by(Subscription.billing_day, Subscription.id).all()


def create_subscription(db: Session, data: SubscriptionCreate) -> Subscription:
    sub = Subscription(
        name=data.name,
        amount_yen=data.amount_yen,
        billing_day=data.billing_day,
        freq=data.freq,
        interval_months=data.interval_months,
        interval_weeks=data.interval_weeks,
        billing_month=data.billing_month,
        payment_method=data.payment_method,
        account_id=data.account_id,
        card_id=data.card_id,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def delete_subscription(db: Session, sub_id: int) -> None:
    sub = db.query(Subscription).filter(Subscription.id == sub_id).first()
    if sub:
        db.delete(sub)
        db.commit()

def list_accounts(db):
    return db.query(Account).order_by(Account.id).all()

def create_account(db, name: str, balance_yen: int, kind: str = "bank"):
    acc = Account(name=name, balance_yen=balance_yen, kind=kind)
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc

def list_plans(db, user_id: int = 1) -> list[Plan]:
    return (
        db.query(Plan)
        .filter(Plan.user_id == user_id)
        .order_by(Plan.type, Plan.title, Plan.id)
        .all()
    )

def create_plan(
    db,
    type,
    title,
    amount_yen,
    account_id,
    freq,
    day,
    interval_months,
    month,
    start_date=None,
    user_id=1,
    payment_method="bank",
    card_id=None,
    end_date=None,
):
    p = Plan(
        user_id=user_id,
        type=type,
        title=title,
        amount_yen=amount_yen,
        account_id=account_id,
        freq=freq,
        day=day,
        interval_months=interval_months,
        month=month,
        start_date=start_date,
        payment_method=payment_method,
        card_id=card_id,
        end_date=end_date,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p

def list_events_between(db, user_id: int, start: date, end: date):
    return (
        db.query(CashflowEvent)
        .filter(CashflowEvent.user_id == user_id,
                CashflowEvent.date >= start,
                CashflowEvent.date <= end)
        .order_by(CashflowEvent.date, CashflowEvent.id)
        .all()
    )

def total_start_balance(db, user_id: int = 1) -> int:
    accounts = db.query(Account).filter(Account.user_id == user_id).all()
    return sum(int(a.balance_yen) for a in accounts)

def delete_plan(db, plan_id: int, user_id: int = 1) -> None:
    p = db.query(Plan).filter(Plan.id == plan_id, Plan.user_id == user_id).first()
    if p:
        db.delete(p)
        db.commit()

def list_events_between_with_plan(db: Session, user_id: int, start, end):
    q = (
        db.query(
            CashflowEvent.id,
            CashflowEvent.date,
            CashflowEvent.amount_yen,
            CashflowEvent.account_id,
            CashflowEvent.source,
            CashflowEvent.description,
            Plan.title.label("plan_title"),
        )
        .outerjoin(Plan, Plan.id == CashflowEvent.plan_id)  # ★ここがポイント
        .filter(CashflowEvent.user_id == user_id)
        .filter(CashflowEvent.date >= start, CashflowEvent.date <= end)
        .order_by(CashflowEvent.date.asc(), CashflowEvent.id.asc())
    )

    rows = []
    for r in q.all():
        title = r.plan_title or r.description or "-"
        rows.append(
            {
                "id": r.id,
                "date": r.date,
                "amount_yen": r.amount_yen,
                "account_id": r.account_id,
                "plan_title": title,     # ★テンプレは今まで通りこれを表示できる
                "source": r.source,
            }
        )
    return rows

def list_withdraw_schedule(
    db: Session,
    user_id: int,
    start: date,
    days: int = 60,
) -> list[dict]:
    """
    将来のカード引落（source='card'）を日付ごとに合算して返す。
    amount_yen は通常マイナス（口座から出ていく）想定。
    """
    end = start + timedelta(days=days)

    rows = (
        db.query(CashflowEvent.date, func.sum(CashflowEvent.amount_yen))
        .filter(CashflowEvent.user_id == user_id)
        .filter(CashflowEvent.source == "card")
        .filter(CashflowEvent.date >= start)
        .filter(CashflowEvent.date <= end)
        .group_by(CashflowEvent.date)
        .order_by(CashflowEvent.date.asc())
        .all()
    )

    out: list[dict] = []
    for d, total in rows:
        out.append(
            {
                "date": d.isoformat(),
                "amount_yen": int(total or 0),
            }
        )
    return out
