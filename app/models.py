from datetime import date
from sqlalchemy import Integer, String, Date, Column, ForeignKey, UniqueConstraint, DateTime, Text
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_yen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billing_day: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-31
    freq: Mapped[str] = mapped_column(String(30), nullable=False, default="monthly")
    interval_months: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    billing_month: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payment_method: Mapped[str] = mapped_column(String(20), nullable=False, default="bank")
    account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    card_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # ★追加：資産の種類
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="bank")
    # bank / cash / barcode / emoney / nisa

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
    payment_method: Mapped[str] = mapped_column(String(20), nullable=False, default="bank")
    card_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    freq: Mapped[str] = mapped_column(String(30), nullable=False, default="monthly")
    day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    interval_months: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    month: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    start_date = mapped_column(Date, nullable=True, default=date.today)
    end_date = mapped_column(Date, nullable=True, default=None)

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

    # ★追加：移動（transfer）のペア識別子（UUID文字列など）
    transfer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


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
    revolvings = relationship("CardRevolving", back_populates="card", cascade="all, delete-orphan")
    installments = relationship("CardInstallment", back_populates="card", cascade="all, delete-orphan")


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


class CardRevolving(Base):
    __tablename__ = "card_revolvings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(Integer, ForeignKey("cards.id"), nullable=False)
    start_month: Mapped[date] = mapped_column(Date, nullable=False)
    remaining_yen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    monthly_payment_yen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    card = relationship("Card", back_populates="revolvings")


class CardInstallment(Base):
    __tablename__ = "card_installments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(Integer, ForeignKey("cards.id"), nullable=False)
    start_month: Mapped[date] = mapped_column(Date, nullable=False)
    months: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    total_amount_yen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    card = relationship("Card", back_populates="installments")


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    source = Column(String(50), nullable=False)      # 例: "csv_card"
    file_name = Column(String(255), nullable=True)

    # 任意：取り込むカードをバッチ単位で指定（MVPでは便利）
    card_id = Column(Integer, ForeignKey("cards.id"), nullable=True)

    status = Column(String(20), default="new", nullable=False)  # new/preview/committed

    transactions = relationship("ImportedTransaction", back_populates="batch", cascade="all, delete-orphan")


class ImportedTransaction(Base):
    __tablename__ = "imported_transactions"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_imported_transactions_fingerprint"),
    )

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("import_batches.id"), nullable=False)

    occurred_on = Column(Date, nullable=False)  # 利用日
    amount_yen = Column(Integer, nullable=False)  # 支出はマイナスで統一（推奨）
    merchant = Column(String(255), nullable=False, default="")
    memo = Column(String(255), nullable=False, default="")

    fingerprint = Column(String(64), nullable=False)  # sha256 hex
    raw = Column(Text, nullable=True)  # 元CSV行をJSON文字列などで保存

    state = Column(String(20), default="new", nullable=False)  # new/skipped/committed/error
    committed_event_id = Column(Integer, ForeignKey("cashflow_events.id"), nullable=True)

    batch = relationship("ImportBatch", back_populates="transactions")
