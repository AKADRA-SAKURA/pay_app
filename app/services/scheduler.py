from __future__ import annotations

from datetime import date, timedelta
import calendar
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Plan,
    CashflowEvent,
    Card,
    CardTransaction,
    CardStatement,
    CardRevolving,
    CardInstallment,
)
from app.utils.dates import resolve_day_in_month, apply_business_day_rule


def _month_add(y: int, m: int, add: int) -> tuple[int, int]:
    # month add (1-12)
    total = (y * 12 + (m - 1)) + add
    ny = total // 12
    nm = (total % 12) + 1
    return ny, nm


def _month_index(d: date) -> int:
    return d.year * 12 + (d.month - 1)


def _month_first(d: date) -> date:
    return d.replace(day=1)


def _revolving_due_for_month(item: CardRevolving, month_first: date) -> int:
    remaining = abs(int(item.remaining_yen or 0))
    monthly = abs(int(item.monthly_payment_yen or 0))
    if remaining <= 0 or monthly <= 0:
        return 0

    start_first = _month_first(item.start_month)
    offset = _month_index(month_first) - _month_index(start_first)
    if offset < 0:
        return 0

    paid_before = monthly * offset
    if paid_before >= remaining:
        return 0

    left = remaining - paid_before
    return min(monthly, left)


def _installment_due_for_month(item: CardInstallment, month_first: date) -> int:
    total = abs(int(item.total_amount_yen or 0))
    months = max(1, int(item.months or 1))
    if total <= 0:
        return 0

    start_first = _month_first(item.start_month)
    offset = _month_index(month_first) - _month_index(start_first)
    if offset < 0 or offset >= months:
        return 0

    base = total // months
    remainder = total % months
    return base + (1 if offset < remainder else 0)


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
        # カード支払いの予定は、引落に集計するためここでは生成しない
        if getattr(p, "payment_method", "bank") == "card" and getattr(p, "card_id", None):
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

        desired = p.day or 1
        ev_date = resolve_day_in_month(y, m, desired)

        # 土日補正：incomeは前倒し、支出は後ろ倒し
        ev_date = apply_business_day_rule(
            ev_date,
            cashflow_type="income" if p.type == "income" else "expense",
        )
        # 終了日がある場合はそれ以降を作らない
        if p.end_date and ev_date > p.end_date:
            continue

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
    next2_y, next2_m = _month_add(this_first.year, this_first.month, 2)
    next2_first = date(next2_y, next2_m, 1)

    # 新：再生成対象だけ消す（plan と card 引落だけ）
    db.query(CashflowEvent).filter(
        CashflowEvent.user_id == user_id,
        CashflowEvent.source.in_(["plan", "card"]),
    ).delete(synchronize_session=False)

    # 作って入れる（plan由来）
    events: list[CashflowEvent] = []
    events += build_month_events(db, user_id, this_first)
    events += build_month_events(db, user_id, next_first)
    events += build_month_events(db, user_id, next2_first)

    # ★ 追記：カード引落（今月・来月・再来月の引落分）
    events += build_card_withdraw_events(db, user_id, this_first.year, this_first.month)
    events += build_card_withdraw_events(db, user_id, next_first.year, next_first.month)
    events += build_card_withdraw_events(db, user_id, next2_first.year, next2_first.month)

    db.add_all(events)
    db.commit()


def _clamp_date(y: int, m: int, d: int) -> date:
    return resolve_day_in_month(y, m, d)


def _add_months(y: int, m: int, add: int) -> tuple[int, int]:
    # 既存 _month_add と同じ意味（好みで _month_add を使ってもOK）
    return _month_add(y, m, add)


def card_period_for_withdraw_month(card: Card, withdraw_y: int, withdraw_m: int) -> tuple[date, date, date]:
    end_y, end_m = _month_add(withdraw_y, withdraw_m, -1)
    period_end = _clamp_date(end_y, end_m, card.closing_day)

    prev_end_y, prev_end_m = _month_add(end_y, end_m, -1)
    prev_period_end = _clamp_date(prev_end_y, prev_end_m, card.closing_day)

    period_start = prev_period_end + timedelta(days=1)
    withdraw_date = _clamp_date(withdraw_y, withdraw_m, card.payment_day)
    withdraw_date = apply_business_day_rule(withdraw_date, cashflow_type="expense")

    return period_start, period_end, withdraw_date


def build_card_withdraw_events(db: Session, user_id: int, withdraw_y: int, withdraw_m: int) -> list[CashflowEvent]:
    """
    指定の引落月(YYYY,MM)について、カードごとに締め期間を集計し、
    withdraw_date に引落イベントを作る（DBにはまだ入れない）
    """
    cards = db.query(Card).all()
    created: list[CashflowEvent] = []
    withdraw_month_first = date(withdraw_y, withdraw_m, 1)

    for card in cards:
        period_start, period_end, withdraw_date = card_period_for_withdraw_month(card, withdraw_y, withdraw_m)

        total = db.query(func.coalesce(func.sum(CardTransaction.amount_yen), 0)).filter(
            CardTransaction.card_id == card.id,
            CardTransaction.date >= period_start,
            CardTransaction.date <= period_end,
        ).scalar()

        total = int(total or 0)

        # plan (payment_method=card) の予定支出も加算
        plans = (
            db.query(Plan)
            .filter(Plan.user_id == user_id)
            .filter(Plan.payment_method == "card")
            .filter(Plan.card_id == card.id)
            .all()
        )

        def _plan_occurs_in_range(p: Plan, start: date, end: date) -> list[date]:
            dates: list[date] = []
            cur_first = date(start.year, start.month, 1)
            while cur_first <= end:
                should = False
                if p.freq == "monthly":
                    should = True
                elif p.freq == "yearly":
                    should = (p.month == cur_first.month)
                elif p.freq == "monthly_interval":
                    should = occurs_monthly_interval(p.start_date or date.today(), cur_first, p.interval_months or 1)
                if should:
                    desired = p.day or 1
                    ev_date = resolve_day_in_month(cur_first.year, cur_first.month, desired)
                    ev_date = apply_business_day_rule(
                        ev_date,
                        cashflow_type="income" if p.type == "income" else "expense",
                    )
                    if p.end_date and ev_date > p.end_date:
                        pass
                    elif start <= ev_date <= end:
                        dates.append(ev_date)
                ny, nm = _month_add(cur_first.year, cur_first.month, 1)
                cur_first = date(ny, nm, 1)
            return dates

        for p in plans:
            if p.type == "income":
                continue
            amount = abs(int(p.amount_yen or 0))
            for _ in _plan_occurs_in_range(p, period_start, period_end):
                total += amount

        # cardごとのリボ支払い
        revolvings = (
            db.query(CardRevolving)
            .filter(CardRevolving.card_id == card.id)
            .all()
        )
        for rv in revolvings:
            total += _revolving_due_for_month(rv, withdraw_month_first)

        # cardごとの分割支払い
        installments = (
            db.query(CardInstallment)
            .filter(CardInstallment.card_id == card.id)
            .all()
        )
        for inst in installments:
            total += _installment_due_for_month(inst, withdraw_month_first)


        # statement（保存しておくと後で整合性が取れる）
        stmt = db.query(CardStatement).filter(
            CardStatement.card_id == card.id,
            CardStatement.withdraw_date == withdraw_date,
        ).one_or_none()

        if stmt is None:
            stmt = CardStatement(
                card_id=card.id,
                period_start=period_start,
                period_end=period_end,
                amount_yen=total,
                withdraw_date=withdraw_date,
            )
            db.add(stmt)
        else:
            stmt.period_start = period_start
            stmt.period_end = period_end
            stmt.amount_yen = total

        desc = f"カード引落: {card.name} ({period_start}〜{period_end})"

        created.append(
            CashflowEvent(
                user_id=user_id,
                plan_id=None,  # ★ 重要：nullable化必須
                account_id=card.payment_account_id,
                date=withdraw_date,
                amount_yen=-abs(total),   # 引落はマイナス
                description=desc,
                source="card",
                status="expected",
            )
        )

    return created
