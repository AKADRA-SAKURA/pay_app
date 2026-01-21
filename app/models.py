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
