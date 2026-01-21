from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column
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

    # 現在残高（計算の起点）
    balance_yen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 将来 multi-user 用（今は1ユーザー想定でも入れておく）
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # income / subscription （M1ではこの2つだけ）
    type: Mapped[str] = mapped_column(String(30), nullable=False)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_yen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # どの口座に紐づくか（入金口座 or 引落口座）
    account_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # monthly / yearly / monthly_interval
    freq: Mapped[str] = mapped_column(String(30), nullable=False, default="monthly")

    # 毎月の何日（1-31）
    day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # monthly_interval のときに使用（例：2ヶ月に1回）
    interval_months: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # yearly のときに使用（1-12）
    month: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

