from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
import calendar
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import Card, CardTransaction, CardStatement, CashflowEvent
from app.utils.dates import resolve_day_in_month, last_day_of_month, apply_business_day_rule


def _last_day_of_month(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def _clamp_day(y: int, m: int, d: int) -> date:
    return resolve_day_in_month(y, m, d)


def _add_months(y: int, m: int, delta: int) -> tuple[int, int]:
    mm = m + delta
    yy = y + (mm - 1) // 12
    mm = (mm - 1) % 12 + 1
    return yy, mm


def compute_period_for_withdraw_month(card: Card, withdraw_y: int, withdraw_m: int) -> tuple[date, date, date]:
    """
    payment_month_offset = +1 固定
    withdraw月の前月が period_end の月になる
    """
    end_y, end_m = _add_months(withdraw_y, withdraw_m, -1)
    period_end = _clamp_day(end_y, end_m, card.closing_day)

    prev_end_y, prev_end_m = _add_months(end_y, end_m, -1)
    prev_period_end = _clamp_day(prev_end_y, prev_end_m, card.closing_day)
    period_start = prev_period_end + timedelta(days=1)

    withdraw_date = _clamp_day(withdraw_y, withdraw_m, card.payment_day)
    withdraw_date = apply_business_day_rule(withdraw_date, cashflow_type="expense")
    return period_start, period_end, withdraw_date


def upsert_statements_and_events_for_months(
    db: Session,
    user_id: int,
    withdraw_months: list[tuple[int, int]],
) -> None:
    """
    withdraw_months: [(year, month), ...] 例：[(2026, 1), (2026, 2)]
    - statement を upsert
    - 対応する引落イベント（CashflowEvent）を upsert っぽく再作成
    """
    cards = db.query(Card).all()

    # 既存の card 引落イベントを「算出された withdraw_date」単位で消す
    # 休日後ろ倒しで翌月にずれても確実に消せる
    withdraw_dates_to_delete: set[date] = set()

    for card in cards:
        for y, m in withdraw_months:
            _, _, wd = compute_period_for_withdraw_month(card, y, m)
            withdraw_dates_to_delete.add(wd)

    if withdraw_dates_to_delete:
        db.query(CashflowEvent).filter(
            CashflowEvent.user_id == user_id,
            CashflowEvent.source == "card",
            CashflowEvent.date.in_(sorted(withdraw_dates_to_delete)),
        ).delete(synchronize_session=False)

    for card in cards:
        for wy, wm in withdraw_months:
            period_start, period_end, withdraw_date = compute_period_for_withdraw_month(card, wy, wm)

            total = db.query(func.coalesce(func.sum(CardTransaction.amount_yen), 0)).filter(
                CardTransaction.card_id == card.id,
                CardTransaction.date >= period_start,
                CardTransaction.date <= period_end,
            ).scalar()

            # statement upsert（uq_card_withdraw_date で探す）
            stmt = db.query(CardStatement).filter(
                CardStatement.card_id == card.id,
                CardStatement.withdraw_date == withdraw_date,
            ).one_or_none()

            if stmt is None:
                stmt = CardStatement(
                    card_id=card.id,
                    period_start=period_start,
                    period_end=period_end,
                    amount_yen=int(total or 0),
                    withdraw_date=withdraw_date,
                )
                db.add(stmt)
            else:
                stmt.period_start = period_start
                stmt.period_end = period_end
                stmt.amount_yen = int(total or 0)

            # 引落イベント生成（amountはマイナス）
            desc = f"カード引落: {card.name} ({period_start}〜{period_end})"
            ev = CashflowEvent(
                user_id=user_id,
                date=withdraw_date,
                amount_yen=-int(total or 0),
                account_id=card.payment_account_id,
                plan_id=None,
                description=desc,
                source="card",
                status="expected",
            )
            db.add(ev)

    db.commit()
