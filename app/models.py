from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_yen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billing_day: Mapped[int] = mapped_column(Integer, nullable=False)  # 1〜31（簡易版）
