from datetime import date
from sqlalchemy import Integer, String, Date, Column, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_yen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billing_day: Mapped[int] = mapped_column(Integer, nullable=False)  # 1〜31（簡易版）


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    balance_yen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    type: Mapped[str] = mapped_column(String(30), nullable=False)  # income / subscription
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_yen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    account_id: Mapped[int] = mapped_column(Integer, nullable=False)

    freq: Mapped[str] = mapped_column(String(30), nullable=False, default="monthly")
    day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    interval_months: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    month: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    start_date = mapped_column(Date, nullable=True, default=date.today)

    events = relationship("CashflowEvent", back_populates="plan", cascade="all, delete-orphan")


class CashflowEvent(Base):
    __tablename__ = "cashflow_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_yen: Mapped[int] = mapped_column(Integer, nullable=False)

    account_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # ★ Plan由来じゃないイベント（カード引落など）のためにnullable化
    plan_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("plans.id"), nullable=True)

    # ★ 表示用（planが無いときのタイトル）
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # plan / card など（任意だけど便利）
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="plan")

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="expected")

    plan = relationship("Plan", back_populates="events")


# ====== Phase1: Card ======

class Card(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    closing_day: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-31
    payment_day: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-31

    payment_account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=False)

    transactions = relationship("CardTransaction", back_populates="card", cascade="all, delete-orphan")
    statements = relationship("CardStatement", back_populates="card", cascade="all, delete-orphan")


class CardTransaction(Base):
    __tablename__ = "card_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(Integer, ForeignKey("cards.id"), nullable=False)

    date: Mapped[date] = mapped_column(Date, nullable=False)

    # 支出=正 / 返金=負 で統一（集計が楽）
    amount_yen: Mapped[int] = mapped_column(Integer, nullable=False)

    merchant: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    card = relationship("Card", back_populates="transactions")


class CardStatement(Base):
    __tablename__ = "card_statements"
    __table_args__ = (
        UniqueConstraint("card_id", "withdraw_date", name="uq_card_withdraw_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(Integer, ForeignKey("cards.id"), nullable=False)

    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    amount_yen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ★ 引落日（period_end の翌月 payment_day、月末補正あり）
    withdraw_date: Mapped[date] = mapped_column(Date, nullable=False)

    card = relationship("Card", back_populates="statements")
