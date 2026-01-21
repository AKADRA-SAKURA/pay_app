from sqlalchemy.orm import Session
from .models import Subscription
from .schemas import SubscriptionCreate
from .models import Account
from .models import Plan


def list_subscriptions(db: Session) -> list[Subscription]:
    return db.query(Subscription).order_by(Subscription.billing_day, Subscription.id).all()


def create_subscription(db: Session, data: SubscriptionCreate) -> Subscription:
    sub = Subscription(
        name=data.name,
        amount_yen=data.amount_yen,
        billing_day=data.billing_day,
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

def create_account(db, name: str, balance_yen: int):
    acc = Account(name=name, balance_yen=balance_yen)
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
    *,
    user_id: int,
    type: str,
    title: str,
    amount_yen: int,
    account_id: int,
    freq: str,
    day: int,
    interval_months: int = 1,
    month: int = 1,
) -> Plan:
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
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p

