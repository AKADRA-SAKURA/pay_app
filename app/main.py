from dotenv import load_dotenv
load_dotenv()

from uuid import uuid4
from fastapi import FastAPI, Depends, Request, Form, HTTPException, Query, UploadFile, File
from sqlalchemy import text
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel, Field
from datetime import date, datetime
import calendar
import re
import csv
import io

from app.services.scheduler import (
    rebuild_events as rebuild_events_scheduler,
    card_period_for_withdraw_month,
    _revolving_due_for_month,
    _installment_due_for_month,
    occurs_monthly_interval,
    _subscription_occurrences_in_range,
)
from .db import Base, engine, get_db, SessionLocal
from .schemas import SubscriptionCreate, SubscriptionOut
from . import crud
from .models import (
    Account,
    Card,
    CardTransaction,
    CashflowEvent,
    Subscription,
    Plan,
    CardRevolving,
    CardInstallment,
)
from .crud import list_accounts, create_account
from app.services.forecast import forecast_by_account_events, forecast_by_account_daily
from .services.forecast import forecast_free_daily
from app.advice.service import get_today_advice
from app.utils.dates import month_range, resolve_day_in_month, apply_business_day_rule
from app.services.statement_import import (
    parse_card_text_preview,
    parse_card_csv_preview,
    detect_duplicates,
    build_import_key,
    parse_flexible_date,
    normalize_title,
)

# create tables at startup (local/dev only)
Base.metadata.create_all(bind=engine)

# ---- lightweight migration for new Plan columns (local sqlite only) ----
def _ensure_plan_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(plans)")).fetchall()]
        if "payment_method" not in cols:
            conn.execute(text("ALTER TABLE plans ADD COLUMN payment_method VARCHAR(20) NOT NULL DEFAULT 'bank'"))
        if "card_id" not in cols:
            conn.execute(text("ALTER TABLE plans ADD COLUMN card_id INTEGER"))
        if "end_date" not in cols:
            conn.execute(text("ALTER TABLE plans ADD COLUMN end_date DATE"))

_ensure_plan_columns()


# ---- lightweight migration for new Subscription columns (local sqlite only) ----
def _ensure_subscription_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(subscriptions)")).fetchall()]
        if "freq" not in cols:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN freq VARCHAR(30) NOT NULL DEFAULT 'monthly'"))
        if "interval_months" not in cols:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN interval_months INTEGER NOT NULL DEFAULT 1"))
        if "interval_weeks" not in cols:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN interval_weeks INTEGER NOT NULL DEFAULT 1"))
        if "billing_month" not in cols:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN billing_month INTEGER NOT NULL DEFAULT 1"))
        if "payment_method" not in cols:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN payment_method VARCHAR(20) NOT NULL DEFAULT 'bank'"))
        if "account_id" not in cols:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN account_id INTEGER"))
        if "card_id" not in cols:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN card_id INTEGER"))

_ensure_subscription_columns()


def _ensure_account_card_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.connect() as conn:
        acc_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(accounts)")).fetchall()]
        if "effective_start_date" not in acc_cols:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN effective_start_date DATE"))
        if "effective_end_date" not in acc_cols:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN effective_end_date DATE"))

        card_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(cards)")).fetchall()]
        if "effective_start_date" not in card_cols:
            conn.execute(text("ALTER TABLE cards ADD COLUMN effective_start_date DATE"))
        if "effective_end_date" not in card_cols:
            conn.execute(text("ALTER TABLE cards ADD COLUMN effective_end_date DATE"))


_ensure_account_card_columns()

app = FastAPI(title="pay_app")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("app/static/favicon.png", media_type="image/png")


def _decode_csv_bytes(content: bytes) -> str:
    last_err = None
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            return content.decode(enc)
        except Exception as e:
            last_err = e
    raise HTTPException(status_code=400, detail=f"CSV decode failed: {last_err}")


def _csv_dict_rows(content: bytes) -> list[dict[str, str]]:
    text = _decode_csv_bytes(content)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV header is required")
    return [dict(r) for r in reader]


def _parse_csv_date(v: str) -> date:
    s = (v or "").strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"invalid date: {v}")


def _parse_csv_amount(v: str) -> int:
    s = (v or "").strip().replace(",", "").replace("円", "")
    if s == "":
        raise ValueError("empty price")
    return int(float(s))


def _parse_month_start(v: str) -> date:
    s = (v or "").strip()
    if not s:
        raise ValueError("start_month is required")

    for fmt in ("%Y-%m", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s, fmt).date()
            return date(d.year, d.month, 1)
        except ValueError:
            pass
    raise ValueError(f"invalid month: {v}")


def _parse_optional_date(v: str | None, field_name: str) -> date | None:
    s = (v or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field_name} must be YYYY-MM-DD")


def _parse_required_date(v: str | None, field_name: str) -> date:
    d = _parse_optional_date(v, field_name)
    if d is None:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return d


def _ensure_effective_range(start_d: date | None, end_d: date | None, target: str) -> None:
    if start_d and end_d and end_d < start_d:
        raise HTTPException(status_code=400, detail=f"{target} end date must be on/after start date")


def _parse_bulk_ids(ids: str) -> list[int]:
    parsed_ids: list[int] = []
    for part in re.split(r"[,\s]+", (ids or "").strip()):
        if not part:
            continue
        if not part.isdigit():
            continue
        v = int(part)
        if v > 0:
            parsed_ids.append(v)
    return sorted(set(parsed_ids))


def _resolve_account_id(db: Session, key: str) -> int:
    s = (key or "").strip()
    if not s:
        raise ValueError("account is empty")
    if s.isdigit():
        acc = db.query(Account).filter(Account.id == int(s)).first()
    else:
        acc = db.query(Account).filter(Account.name == s).first()
    if acc is None:
        raise ValueError(f"account not found: {s}")
    return int(acc.id)


def _resolve_card_id(db: Session, key: str) -> int:
    s = (key or "").strip()
    if not s:
        raise ValueError("card is empty")
    if s.isdigit():
        card = db.query(Card).filter(Card.id == int(s)).first()
    else:
        card = db.query(Card).filter(Card.name == s).first()
    if card is None:
        raise ValueError(f"card not found: {s}")
    return int(card.id)


def _parse_direction(v: str) -> str:
    s = (v or "").strip().lower()
    # Accept CSV direction tokens in EN/JA.
    if s in ("expense", "exp", "-") or "隰ｾ・ｯ陷・ｽｺ" in s:
        return "expense"
    if s in ("income", "inc", "+") or "陷ｿ荳ｻ繝ｻ" in s:
        return "income"
    raise ValueError(f"invalid type: {v}")


class ImportRowIn(BaseModel):
    date: str
    title: str
    price: int


class ImportPreviewTextIn(BaseModel):
    text: str = Field(min_length=1)
    card: int


class ImportCommitIn(BaseModel):
    card: int
    rows: list[ImportRowIn]
    allow_duplicates: bool = False


def _existing_card_keys(db: Session, card_id: int, date_strings: list[str]) -> set[tuple[str, str, int, int]]:
    dates: set[date] = set()
    for s in date_strings:
        try:
            dates.add(parse_flexible_date(s))
        except Exception:
            continue
    if not dates:
        return set()

    existing = (
        db.query(CardTransaction)
        .filter(CardTransaction.card_id == card_id)
        .filter(CardTransaction.date.in_(list(dates)))
        .all()
    )
    out: set[tuple[str, str, int, int]] = set()
    for t in existing:
        out.add((t.date.isoformat(), normalize_title(t.merchant or ""), int(t.amount_yen), int(card_id)))
    return out


@app.post("/import/preview_text")
def import_preview_text(payload: ImportPreviewTextIn, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == int(payload.card)).first()
    if card is None:
        raise HTTPException(status_code=400, detail="card not found")

    rows, warnings, errors = parse_card_text_preview(payload.text)
    existing_keys = _existing_card_keys(db, int(payload.card), [str(r.get("date", "")) for r in rows])
    duplicate_candidates = detect_duplicates(rows, int(payload.card), existing_keys)
    if duplicate_candidates:
        warnings = list(warnings) + [f"重複候補: {len(duplicate_candidates)}件"]
    missing_date = sum(1 for r in rows if not str(r.get("date", "")).strip())
    if missing_date:
        warnings = list(warnings) + [f"日付未抽出の行があります: {missing_date}件（プレビューで編集してください）"]

    return {
        "rows": rows,
        "warnings": warnings,
        "errors": errors,
        "duplicate_candidates": duplicate_candidates,
        "can_commit": len(errors) == 0,
    }


@app.post("/import/preview_csv")
async def import_preview_csv(
    card: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    card_obj = db.query(Card).filter(Card.id == int(card)).first()
    if card_obj is None:
        raise HTTPException(status_code=400, detail="card not found")

    content = await file.read()
    rows, warnings, errors = parse_card_csv_preview(content)
    existing_keys = _existing_card_keys(db, int(card), [str(r.get("date", "")) for r in rows])
    duplicate_candidates = detect_duplicates(rows, int(card), existing_keys)
    if duplicate_candidates:
        warnings = list(warnings) + [f"重複候補: {len(duplicate_candidates)}件"]
    missing_date = sum(1 for r in rows if not str(r.get("date", "")).strip())
    if missing_date:
        warnings = list(warnings) + [f"日付未抽出の行があります: {missing_date}件（プレビューで編集してください）"]

    return {
        "rows": rows,
        "warnings": warnings,
        "errors": errors,
        "duplicate_candidates": duplicate_candidates,
        "can_commit": len(errors) == 0,
    }


@app.post("/import/commit")
def import_commit(payload: ImportCommitIn, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == int(payload.card)).first()
    if card is None:
        raise HTTPException(status_code=400, detail="card not found")

    normalized_rows: list[dict] = []
    for i, r in enumerate(payload.rows):
        try:
            d = parse_flexible_date(r.date)
            t = normalize_title(r.title)
            p = int(r.price)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"row {i + 1} parse error: {e}")
        normalized_rows.append({"date": d.strftime("%Y/%m/%d"), "title": t, "price": p})

    existing_keys = _existing_card_keys(db, int(payload.card), [x["date"] for x in normalized_rows])

    inserted = 0
    skipped: list[dict] = []
    seen_payload: set[tuple[str, str, int, int]] = set()

    for i, row in enumerate(normalized_rows):
        key = build_import_key(row["date"], row["title"], int(row["price"]), int(payload.card))
        reason = None
        if key in existing_keys:
            reason = "existing"
        elif key in seen_payload:
            reason = "payload"

        if reason and not payload.allow_duplicates:
            skipped.append(
                {
                    "index": i,
                    "reason": reason,
                    "date": key[0],
                    "title": key[1],
                    "price": key[2],
                }
            )
            continue

        db.add(
            CardTransaction(
                card_id=int(payload.card),
                date=parse_flexible_date(row["date"]),
                amount_yen=int(row["price"]),
                merchant=row["title"],
            )
        )
        inserted += 1
        seen_payload.add(key)

    if inserted > 0:
        db.commit()

    return {
        "inserted": inserted,
        "skipped_duplicates": len(skipped),
        "duplicates_detail": skipped,
    }


@app.get("/", response_class=HTMLResponse)
def page_index(request: Request, db: Session = Depends(get_db)):
    subs = crud.list_subscriptions(db)
    accounts = crud.list_accounts(db)
    plans = crud.list_plans(db)

    today = date.today()
    this_first, this_last = month_range(today)

    # 隴夲ｽ･隴帙・
    if this_first.month == 12:
        next_first = date(this_first.year + 1, 1, 1)
    else:
        next_first = date(this_first.year, this_first.month + 1, 1)
    next_first, next_last = month_range(next_first)

    # 陷閧ｴ謫りｭ帙・
    if next_first.month == 12:
        next2_first = date(next_first.year + 1, 1, 1)
    else:
        next2_first = date(next_first.year, next_first.month + 1, 1)
    next2_first, next2_last = month_range(next2_first)

    events_this = crud.list_events_between_with_plan(db, 1, this_first, this_last)
    events_next = crud.list_events_between_with_plan(db, 1, next_first, next_last)
    events_next2 = crud.list_events_between_with_plan(db, 1, next2_first, next2_last)

    def _build_payment_pie(events):
        totals = {}
        for e in events:
            try:
                amt = int(e.get("amount_yen") or 0)
            except Exception:
                amt = 0
            if amt >= 0:
                continue
            if e.get("source") == "transfer":
                continue
            name = e.get("plan_title") or "-"
            totals[name] = totals.get(name, 0) + abs(amt)

        items = sorted(totals.items(), key=lambda x: x[1], reverse=True)
        max_items = 8
        if len(items) > max_items:
            top = items[: max_items - 1]
            rest = sum(v for _, v in items[max_items - 1 :])
            items = top + [("Other", rest)]

        return [{"label": k, "value": v} for k, v in items]

    pay_pie_this = _build_payment_pie(events_this)
    pay_pie_next = _build_payment_pie(events_next)
    from collections import defaultdict

    def _month_shift(d: date, add: int) -> date:
        total = (d.year * 12 + (d.month - 1)) + add
        y = total // 12
        m = (total % 12) + 1
        return date(y, m, 1)

    def _month_list(start_first: date, months: int) -> list[date]:
        return [_month_shift(start_first, i) for i in range(months)]

    def _to_pie_items(totals: dict[str, int], max_items: int = 8) -> list[dict]:
        pairs = sorted(((k, int(v)) for k, v in totals.items() if int(v) > 0), key=lambda x: x[1], reverse=True)
        if len(pairs) > max_items:
            head = pairs[: max_items - 1]
            rest = sum(v for _, v in pairs[max_items - 1 :])
            pairs = head + [("その他", rest)]
        return [{"label": k, "value": v} for k, v in pairs]

    def _plan_occurs_in_month(plan: Plan, month_first: date) -> int:
        y, m = month_first.year, month_first.month
        month_last = date(y, m, calendar.monthrange(y, m)[1])
        start_d = plan.start_date or today
        if start_d > month_last:
            return 0
        if plan.end_date and plan.end_date < month_first:
            return 0

        should = False
        freq = str(plan.freq or "monthly")
        if freq == "monthly":
            should = True
        elif freq == "yearly":
            should = int(plan.month or 1) == m
        elif freq == "monthly_interval":
            should = occurs_monthly_interval(start_d, month_first, int(plan.interval_months or 1))
        if not should:
            return 0

        desired_day = max(1, int(plan.day or 1))
        ev_date = resolve_day_in_month(y, m, desired_day)
        ev_date = apply_business_day_rule(
            ev_date,
            cashflow_type="income" if str(plan.type or "") == "income" else "expense",
        )
        if ev_date < start_d:
            return 0
        if plan.end_date and ev_date > plan.end_date:
            return 0
        if not (month_first <= ev_date <= month_last):
            return 0
        return 1

    def _build_recurring_cost_summary() -> dict:
        months = _month_list(this_first, 12)

        plan_monthly_totals: list[dict] = []
        plan_pie_totals: dict[str, int] = {}
        for month_first in months:
            month_total = 0
            for p in plans:
                if str(getattr(p, "type", "")) == "income":
                    continue
                count = _plan_occurs_in_month(p, month_first)
                if count <= 0:
                    continue
                amount = abs(int(getattr(p, "amount_yen", 0) or 0))
                if amount <= 0:
                    continue
                val = amount * count
                month_total += val
                key = str(getattr(p, "title", "") or "-")
                plan_pie_totals[key] = plan_pie_totals.get(key, 0) + val
            plan_monthly_totals.append({"month": month_first.strftime("%Y-%m"), "value": int(month_total)})

        sub_monthly_totals: list[dict] = []
        sub_pie_totals: dict[str, int] = {}
        for month_first in months:
            month_last = date(month_first.year, month_first.month, calendar.monthrange(month_first.year, month_first.month)[1])
            month_total = 0
            for s in subs:
                amount = abs(int(getattr(s, "amount_yen", 0) or 0))
                if amount <= 0:
                    continue
                occ_count = len(_subscription_occurrences_in_range(s, month_first, month_last))
                if occ_count <= 0:
                    continue
                val = amount * occ_count
                month_total += val
                key = str(getattr(s, "name", "") or "-")
                sub_pie_totals[key] = sub_pie_totals.get(key, 0) + val
            sub_monthly_totals.append({"month": month_first.strftime("%Y-%m"), "value": int(month_total)})

        plan_annual_total = sum(int(x["value"]) for x in plan_monthly_totals)
        sub_annual_total = sum(int(x["value"]) for x in sub_monthly_totals)

        return {
            "plans": {
                "monthly_avg_yen": int(round(plan_annual_total / 12)) if plan_annual_total else 0,
                "annual_total_yen": int(plan_annual_total),
                "monthly_totals": plan_monthly_totals,
                "pie": _to_pie_items(plan_pie_totals),
            },
            "subs": {
                "monthly_avg_yen": int(round(sub_annual_total / 12)) if sub_annual_total else 0,
                "annual_total_yen": int(sub_annual_total),
                "monthly_totals": sub_monthly_totals,
                "pie": _to_pie_items(sub_pie_totals),
            },
        }

    recurring_cost_summary = _build_recurring_cost_summary()

    def _account_active_on(acc: Account, d: date) -> bool:
        start_d = getattr(acc, "effective_start_date", None)
        end_d = getattr(acc, "effective_end_date", None)
        if start_d and d < start_d:
            return False
        if end_d and d > end_d:
            return False
        return True

    start_balance = crud.total_start_balance(db, 1, as_of=this_first)
    this_net = sum(e["amount_yen"] for e in events_this)
    next_net = sum(e["amount_yen"] for e in events_next)
    next2_net = sum(e["amount_yen"] for e in events_next2)

    free_this = start_balance + this_net
    free_next = start_balance + this_net + next_net
    free_next2 = start_balance + this_net + next_net + next2_net

    # --- account summary (M1-6) ---
    # events_* are dict rows with e["account_id"] and e["amount_yen"].
    this_by_acc = defaultdict(int)
    next_by_acc = defaultdict(int)

    for e in events_this:
        this_by_acc[int(e["account_id"])] += int(e["amount_yen"])

    for e in events_next:
        next_by_acc[int(e["account_id"])] += int(e["amount_yen"])

    account_summaries = []
    for a in accounts:
        acc_id = int(a.id)
        start = int(a.balance_yen) if _account_active_on(a, this_first) else 0
        this_net_acc = this_by_acc[acc_id]
        next_net_acc = next_by_acc[acc_id]

        account_summaries.append(
            {
                "id": acc_id,
                "name": a.name,
                "start": start,
                "this_net": this_net_acc,
                "next_net": next_net_acc,
                "free_this": start + this_net_acc,
                "free_next": start + this_net_acc + next_net_acc,
            }
        )

    # keep output order stable (by account id)
    account_summaries.sort(key=lambda x: x["id"])

    forecast = forecast_by_account_daily(db, user_id=1, start=this_first, end=next_last)
    total_series = list((forecast or {}).get("total_series") or [])
    if total_series:
        min_total_point = min(total_series, key=lambda p: int(p.get("balance_yen", 0)))
        total_min_balance = int(min_total_point.get("balance_yen", 0))
        total_min_date = str(min_total_point.get("date") or "")
        by_date = {str(p.get("date")): int(p.get("balance_yen", 0)) for p in total_series}
        free_this = by_date.get(this_last.isoformat(), free_this)
        free_next = by_date.get(next_last.isoformat(), free_next)
        free_next2 = by_date.get(next2_last.isoformat(), free_next2)
    else:
        total_min_balance = 0
        total_min_date = ""
    total_is_danger = total_min_balance < 0

    # --- card section (phase 1) ---
    cards = db.query(Card).order_by(Card.id.asc()).all()

    card_transactions = (
        db.query(CardTransaction)
        .options(joinedload(CardTransaction.card))
        .order_by(CardTransaction.date.desc(), CardTransaction.id.desc())
        .limit(50)
        .all()
    )
    card_revolvings = (
        db.query(CardRevolving)
        .options(joinedload(CardRevolving.card))
        .order_by(CardRevolving.id.desc())
        .all()
    )
    card_installments = (
        db.query(CardInstallment)
        .options(joinedload(CardInstallment.card))
        .order_by(CardInstallment.id.desc())
        .all()
    )

    oneoffs = (
        db.query(CashflowEvent)
        .filter(CashflowEvent.user_id == 1, CashflowEvent.source == "oneoff")
        .order_by(CashflowEvent.date.desc(), CashflowEvent.id.desc())
        .limit(30)
        .all()
    )

    # account_id -> display label (name(kind))
    acc_label = {int(a.id): f"{a.name} ({getattr(a, 'kind', 'bank')})" for a in accounts}

    # load recent transfer events (bank/debit)
    transfer_events = (
        db.query(CashflowEvent)
        .filter(CashflowEvent.user_id == 1)
        .filter(CashflowEvent.source == "transfer")
        .filter(CashflowEvent.transfer_id.isnot(None))
        .order_by(CashflowEvent.date.desc(), CashflowEvent.id.desc())
        .limit(80)  # one transfer is two rows, so fetch a bit more
        .all()
    )

    # group by transfer_id, then build one item from from/to pair
    group = {}
    for e in transfer_events:
        tid = e.transfer_id
        if tid not in group:
            group[tid] = {"evs": [], "date": e.date}
        group[tid]["evs"].append(e)

    transfers = []
    # sort by date desc
    for tid, g in sorted(group.items(), key=lambda kv: kv[1]["date"], reverse=True):
        evs = g["evs"]

        # from is negative side, to is positive side
        ev_from = next((x for x in evs if int(x.amount_yen) < 0), None)
        ev_to = next((x for x in evs if int(x.amount_yen) > 0), None)

        # skip incomplete pairs (defensive against broken data)
        if not ev_from or not ev_to:
            continue

        amt = int(ev_to.amount_yen)

        # method is not persisted, so use a temporary label

        method = "transfer"

        transfers.append(
            {
                "transfer_id": tid,
                "id": ev_to.id,  # representative id for display (to-side)
                "date": ev_to.date,
                "method": method,
                "amount_yen": amt,
                "from_id": int(ev_from.account_id),
                "to_id": int(ev_to.account_id),
                "from_label": acc_label.get(int(ev_from.account_id), f"ID:{ev_from.account_id}"),
                "to_label": acc_label.get(int(ev_to.account_id), f"ID:{ev_to.account_id}"),
            }
        )

        if len(transfers) >= 30:
            break

    # accounts -> display label (name(kind))
    acc_label = {int(a.id): f"{a.name} ({getattr(a, 'kind', 'bank')})" for a in accounts}

    # parse "charge to account_id=123" from note
    charge_re = re.compile(r"charge to account_id=(\d+)")

    # load recent card charge rows
    charge_txs = (
        db.query(CardTransaction)
        .options(joinedload(CardTransaction.card))
        .filter(CardTransaction.note.isnot(None))
        .filter(CardTransaction.note.like("charge to account_id=%"))
        .order_by(CardTransaction.date.desc(), CardTransaction.id.desc())
        .limit(30)
        .all()
    )

    card_charges = []
    for tx in charge_txs:
        m = charge_re.search(tx.note or "")
        to_id = int(m.group(1)) if m else None
        card_charges.append(
            {
                "id": tx.id,
                "date": tx.date,
                "amount_yen": int(tx.amount_yen),
                "card_id": int(tx.card_id) if tx.card_id else None,
                "card_name": tx.card.name if tx.card else "-",
                "to_account_id": to_id,
                "to_label": acc_label.get(to_id, f"ID:{to_id}" if to_id else "-"),
            }
        )

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "subs": subs,
            "accounts": accounts,
            "plans": plans,
            "events_this": events_this,
            "events_next": events_next,
            "free_this": free_this,
            "free_next": free_next,
            "free_next2": free_next2,
            "recurring_cost_summary": recurring_cost_summary,
            "this_range": (this_first, this_last),
            "next_range": (next_first, next_last),
            "next2_range": (next2_first, next2_last),
            "account_summaries": account_summaries,
            "forecast": forecast,
            "cards": cards,
            "card_transactions": card_transactions,
            "card_revolvings": card_revolvings,
            "card_installments": card_installments,
            "oneoffs": oneoffs,
            "transfers": transfers,
            "card_charges": card_charges,
            "card_merchant_default_month": today.strftime("%Y-%m"),
            "pay_pie_this": pay_pie_this,
            "pay_pie_next": pay_pie_next,
            "advice": get_today_advice(db, user_id=1),
            "total_min_balance": total_min_balance,
            "total_min_date": total_min_date,
            "total_is_danger": total_is_danger,
        },
    )


# API: list (JSON)
@app.get("/api/subscriptions", response_model=list[SubscriptionOut])
def api_list_subscriptions(db: Session = Depends(get_db)):
    return crud.list_subscriptions(db)


# 鬨ｾ蛹・ｽｽ・ｻ鬯ｮ・ｱ繝ｻ・｢驛｢譎・ｽｼ譁青ｰ驛｢譎｢・ｽ・ｼ驛｢譎｢・｣・ｰ: 鬮ｴ謇假ｽｽ・ｽ髯ｷ莨夲ｽ｣・ｰ
@app.post("/subscriptions")
def create_subscription(
    name: str = Form(...),
    amount_yen: int = Form(...),
    billing_day: int = Form(...),
    freq: str = Form("monthly"),
    interval_months: str | None = Form(None),
    interval_weeks: str | None = Form(None),
    billing_month: str | None = Form(None),
    payment_method: str = Form("bank"),
    account_id: str | None = Form(None),
    card_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    def _to_int(v: str | None) -> int | None:
        try:
            return int(v) if v not in (None, "") else None
        except Exception:
            return None

    interval_i = _to_int(interval_months) or 1
    interval_w = _to_int(interval_weeks) or 1
    month_i = _to_int(billing_month) or 1
    account_i = _to_int(account_id)
    card_i = _to_int(card_id)

    if freq == "monthly":
        interval_i = 1
        interval_w = 1
        month_i = 1
    elif freq == "yearly":
        interval_i = 1
        interval_w = 1
    elif freq == "monthly_interval":
        interval_w = 1
        month_i = 1
    elif freq == "weekly_interval":
        interval_i = 1
        month_i = 1

    if payment_method == "bank":
        card_i = None
    elif payment_method == "card":
        account_i = None

    data = SubscriptionCreate(
        name=name,
        amount_yen=amount_yen,
        billing_day=billing_day,
        freq=freq,
        interval_months=interval_i,
        interval_weeks=interval_w,
        billing_month=month_i,
        payment_method=payment_method,
        account_id=account_i,
        card_id=card_i,
    )
    crud.create_subscription(db, data)
    return RedirectResponse(url="/", status_code=303)


# 鬨ｾ蛹・ｽｽ・ｻ鬯ｮ・ｱ繝ｻ・｢驛｢譎・ｽｼ譁青ｰ驛｢譎｢・ｽ・ｼ驛｢譎｢・｣・ｰ: 髯ｷ蜿ｰ・ｼ竏晄ｱ・
@app.post("/subscriptions/{sub_id}/update")
def update_subscription(
    sub_id: int,
    name: str = Form(...),
    amount_yen: int = Form(...),
    billing_day: int = Form(...),
    freq: str = Form("monthly"),
    interval_months: str | None = Form(None),
    interval_weeks: str | None = Form(None),
    billing_month: str | None = Form(None),
    payment_method: str = Form("bank"),
    account_id: str | None = Form(None),
    card_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    def _to_int(v: str | None) -> int | None:
        try:
            return int(v) if v not in (None, "") else None
        except Exception:
            return None

    interval_i = _to_int(interval_months) or 1
    interval_w = _to_int(interval_weeks) or 1
    month_i = _to_int(billing_month) or 1
    account_i = _to_int(account_id)
    card_i = _to_int(card_id)

    if freq == "monthly":
        interval_i = 1
        interval_w = 1
        month_i = 1
    elif freq == "yearly":
        interval_i = 1
        interval_w = 1
    elif freq == "monthly_interval":
        interval_w = 1
        month_i = 1
    elif freq == "weekly_interval":
        interval_i = 1
        month_i = 1

    if payment_method == "bank":
        card_i = None
    elif payment_method == "card":
        account_i = None
    sub = db.query(Subscription).filter(Subscription.id == sub_id).first()
    if sub:
        sub.name = name
        sub.amount_yen = int(amount_yen)
        sub.billing_day = int(billing_day)
        sub.freq = freq
        sub.interval_months = int(interval_i)
        sub.interval_weeks = int(interval_w)
        sub.billing_month = int(month_i)
        sub.payment_method = payment_method
        sub.account_id = int(account_i) if account_i is not None else None
        sub.card_id = int(card_i) if card_i is not None else None
        db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/subscriptions/{sub_id}/delete")
def delete_subscription(sub_id: int, db: Session = Depends(get_db)):
    crud.delete_subscription(db, sub_id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/accounts")
def add_account(
    name: str = Form(...),
    balance_yen: int = Form(...),
    kind: str = Form("bank"),
    effective_start_date: str = Form(...),
    effective_end_date: str | None = Form(None),
    db: Session = Depends(get_db),
):
    start_d = _parse_required_date(effective_start_date, "effective_start_date")
    end_d = _parse_optional_date(effective_end_date, "effective_end_date")
    _ensure_effective_range(start_d, end_d, "account")

    create_account(
        db,
        name=name,
        balance_yen=balance_yen,
        kind=kind,
        effective_start_date=start_d,
        effective_end_date=end_d,
    )
    return RedirectResponse(url="/", status_code=303)


# plans鬨ｾ蜈ｷ・ｽ・ｻ鬯ｪ・ｭ繝ｻ・ｲ
@app.post("/plans")
def add_plan(
    type: str = Form(...),            # "income" or "subscription"
    title: str = Form(...),
    amount_yen: int = Form(...),
    account_id: str | None = Form(None),
    payment_method: str = Form("bank"),
    card_id: str | None = Form(None),
    freq: str = Form(...),            # monthly/yearly/monthly_interval
    day: str | None = Form(None),
    interval_months: str | None = Form(None),
    start_date: str | None = Form(default=None),
    end_date: str | None = Form(default=None),
    month: str | None = Form(None),
    db: Session = Depends(get_db),
):
    def _to_int(v: str | None) -> int | None:
        try:
            return int(v) if v not in (None, "") else None
        except Exception:
            return None

    account_i = _to_int(account_id)
    card_i = _to_int(card_id)
    day_i = _to_int(day) or 1
    interval_i = _to_int(interval_months) or 1
    month_i = _to_int(month) or 1

    if freq == "monthly":
        interval_i = 1
        month_i = 1
    elif freq == "yearly":
        interval_i = 1
    elif freq == "monthly_interval":
        month_i = 1

    if payment_method == "bank":
        if not account_i:
            raise HTTPException(status_code=400, detail="account_id is required")
        card_i = None
    elif payment_method == "card":
        if not card_i:
            raise HTTPException(status_code=400, detail="card_id is required for card payment")
        card = db.query(Card).filter(Card.id == card_i).one_or_none()
        if card is None:
            raise HTTPException(status_code=400, detail="card not found")
        account_i = int(card.payment_account_id)

    if start_date:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        sd = date.today()
    if end_date:
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
    else:
        ed = None
    crud.create_plan(
        db,
        user_id=1,
        type=type,
        title=title,
        amount_yen=amount_yen,
        account_id=account_i,
        freq=freq,
        day=day_i,
        interval_months=interval_i,
        month=month_i,
        start_date=sd,
        end_date=ed,
        payment_method=payment_method,
        card_id=card_i,
    )
    return RedirectResponse(url="/", status_code=303)


@app.post("/accounts/{account_id}/update")
def update_account(
    account_id: int,
    name: str = Form(...),
    balance_yen: int = Form(...),
    kind: str = Form("bank"),
    effective_start_date: str = Form(...),
    effective_end_date: str | None = Form(None),
    db: Session = Depends(get_db),
):
    start_d = _parse_required_date(effective_start_date, "effective_start_date")
    end_d = _parse_optional_date(effective_end_date, "effective_end_date")
    _ensure_effective_range(start_d, end_d, "account")

    acc = db.query(Account).filter(Account.id == account_id).first()
    if acc:
        acc.name = name
        acc.balance_yen = int(balance_yen)
        acc.kind = kind
        acc.effective_start_date = start_d
        acc.effective_end_date = end_d
        db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/accounts/{account_id}/delete")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    # delete when record exists
    acc = db.query(Account).filter(Account.id == account_id).first()
    if acc:
        db.delete(acc)
        db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/events/rebuild")
def rebuild_events(db: Session = Depends(get_db)):
    rebuild_events_scheduler(db, user_id=1)
    return RedirectResponse(url="/", status_code=303)

@app.post("/plans/{plan_id}/update")
def update_plan(
    plan_id: int,
    type: str = Form(...),
    title: str = Form(...),
    amount_yen: int = Form(...),
    account_id: str | None = Form(None),
    payment_method: str = Form("bank"),
    card_id: str | None = Form(None),
    freq: str = Form(...),
    day: str | None = Form(None),
    interval_months: str | None = Form(None),
    start_date: str | None = Form(default=None),
    end_date: str | None = Form(default=None),
    month: str | None = Form(None),
    db: Session = Depends(get_db),
):
    p = db.query(Plan).filter(Plan.id == plan_id, Plan.user_id == 1).first()
    if p:
        def _to_int(v: str | None) -> int | None:
            try:
                return int(v) if v not in (None, "") else None
            except Exception:
                return None

        account_i = _to_int(account_id)
        card_i = _to_int(card_id)
        day_i = _to_int(day) or 1
        interval_i = _to_int(interval_months) or 1
        month_i = _to_int(month) or 1

        if freq == "monthly":
            interval_i = 1
            month_i = 1
        elif freq == "yearly":
            interval_i = 1
        elif freq == "monthly_interval":
            month_i = 1

        if payment_method == "bank":
            if not account_i:
                raise HTTPException(status_code=400, detail="account_id is required")
            card_i = None
        elif payment_method == "card":
            if not card_i:
                raise HTTPException(status_code=400, detail="card_id is required for card payment")
            card = db.query(Card).filter(Card.id == card_i).one_or_none()
            if card is None:
                raise HTTPException(status_code=400, detail="card not found")
            account_i = int(card.payment_account_id)

        p.type = type
        p.title = title
        p.amount_yen = int(amount_yen)
        p.account_id = int(account_i)
        p.payment_method = payment_method
        p.card_id = int(card_i) if card_i is not None else None
        p.freq = freq
        p.day = int(day_i)
        p.interval_months = int(interval_i)
        p.month = int(month_i)
        if start_date:
            p.start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        if end_date:
            p.end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        else:
            p.end_date = None
        db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/plans/{plan_id}/delete")
def delete_plan(plan_id: int, db: Session = Depends(get_db)):
    crud.delete_plan(db, plan_id=plan_id, user_id=1)
    return RedirectResponse(url="/", status_code=303)

@app.get("/api/forecast/accounts")
def api_forecast_accounts(
    danger_threshold_yen: int = Query(0),
    db: Session = Depends(get_db),
):
    today = date.today()

    # from this month start to next month end
    this_first, this_last = month_range(today)
    if this_first.month == 12:
        next_first = date(this_first.year + 1, 1, 1)
    else:
        next_first = date(this_first.year, this_first.month + 1, 1)
    next_first, next_last = month_range(next_first)

    return forecast_by_account_events(
        db, user_id=1, start=this_first, end=next_last, danger_threshold_yen=danger_threshold_yen
    )


@app.get("/api/cards/merchant-pie")
def api_card_merchant_pie(
    card_id: int = Query(..., ge=1),
    withdraw_month: str = Query(...),
    top_n: int = Query(8, ge=3, le=20),
    db: Session = Depends(get_db),
):
    card = db.query(Card).filter(Card.id == int(card_id)).one_or_none()
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")

    try:
        month_first = _parse_month_start(withdraw_month)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    period_start, period_end, withdraw_date = card_period_for_withdraw_month(
        card, month_first.year, month_first.month
    )

    effective_start = getattr(card, "effective_start_date", None)
    effective_end = getattr(card, "effective_end_date", None)

    analyzed_start = period_start
    analyzed_end = period_end
    if effective_start and effective_start > analyzed_start:
        analyzed_start = effective_start
    if effective_end and effective_end < analyzed_end:
        analyzed_end = effective_end

    if analyzed_start > analyzed_end:
        rows = []
    else:
        rows = (
            db.query(CardTransaction.merchant, CardTransaction.amount_yen, CardTransaction.note)
            .filter(CardTransaction.card_id == int(card_id))
            .filter(CardTransaction.date >= analyzed_start, CardTransaction.date <= analyzed_end)
            .all()
        )

    account_names = {int(a.id): a.name for a in db.query(Account).all()}

    def _charge_label_from_note(note: str | None) -> str | None:
        s = (note or "").strip()
        m = re.search(r"charge to account_id=(\d+)", s)
        if not m:
            return None
        aid = int(m.group(1))
        return f"\u30c1\u30e3\u30fc\u30b8: {account_names.get(aid, f'ID:{aid}')}"

    totals: dict[str, int] = {}
    total_yen = 0
    for merchant, amount, note in rows:
        amount_i = abs(int(amount or 0))
        if amount_i <= 0:
            continue
        charge_label = _charge_label_from_note(note)
        if charge_label:
            name = charge_label
        else:
            name = (merchant or "").strip() or "(\u672a\u8a2d\u5b9a)"
        totals[name] = totals.get(name, 0) + amount_i
        total_yen += amount_i

    # Add revolving dues for selected withdraw month.
    revolvings = db.query(CardRevolving).filter(CardRevolving.card_id == int(card_id)).all()
    for rv in revolvings:
        due = int(_revolving_due_for_month(rv, month_first) or 0)
        if due <= 0:
            continue
        label = (getattr(rv, "note", None) or "").strip() or "\u30ea\u30dc"
        totals[label] = totals.get(label, 0) + due
        total_yen += due

    # Add installment dues for selected withdraw month.
    installments = db.query(CardInstallment).filter(CardInstallment.card_id == int(card_id)).all()
    for inst in installments:
        due = int(_installment_due_for_month(inst, month_first) or 0)
        if due <= 0:
            continue
        label = (getattr(inst, "note", None) or "").strip() or "\u5206\u5272"
        totals[label] = totals.get(label, 0) + due
        total_yen += due

    pairs = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    if len(pairs) > top_n:
        head = pairs[: top_n - 1]
        tail_total = sum(v for _, v in pairs[top_n - 1 :])
        pairs = head + [("その他", tail_total)]

    items = []
    for label, value in pairs:
        ratio = (value / total_yen * 100.0) if total_yen > 0 else 0.0
        items.append({"label": label, "value": int(value), "ratio": round(ratio, 2)})

    return {
        "card_id": int(card.id),
        "card_name": card.name,
        "withdraw_month": month_first.strftime("%Y-%m"),
        "withdraw_date": withdraw_date.isoformat(),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "analyzed_start": analyzed_start.isoformat(),
        "analyzed_end": analyzed_end.isoformat(),
        "effective_start_date": effective_start.isoformat() if effective_start else None,
        "effective_end_date": effective_end.isoformat() if effective_end else None,
        "total_yen": int(total_yen),
        "items": items,
    }


@app.post("/cards")
def create_card(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    closing_day: int = Form(...),
    payment_day: int = Form(...),
    payment_account_id: int = Form(...),
    effective_start_date: str = Form(...),
    effective_end_date: str | None = Form(None),
):
    start_d = _parse_required_date(effective_start_date, "effective_start_date")
    end_d = _parse_optional_date(effective_end_date, "effective_end_date")
    _ensure_effective_range(start_d, end_d, "card")

    c = Card(
        name=name,
        closing_day=int(closing_day),
        payment_day=int(payment_day),
        payment_account_id=int(payment_account_id),
        effective_start_date=start_d,
        effective_end_date=end_d,
    )
    db.add(c)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/cards/{card_id}/update")
def update_card(
    card_id: int,
    name: str = Form(...),
    closing_day: int = Form(...),
    payment_day: int = Form(...),
    payment_account_id: int = Form(...),
    effective_start_date: str = Form(...),
    effective_end_date: str | None = Form(None),
    db: Session = Depends(get_db),
):
    start_d = _parse_required_date(effective_start_date, "effective_start_date")
    end_d = _parse_optional_date(effective_end_date, "effective_end_date")
    _ensure_effective_range(start_d, end_d, "card")

    c = db.query(Card).filter(Card.id == card_id).first()
    if c:
        c.name = name
        c.closing_day = int(closing_day)
        c.payment_day = int(payment_day)
        c.payment_account_id = int(payment_account_id)
        c.effective_start_date = start_d
        c.effective_end_date = end_d
        db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/cards/{card_id}/delete")
def delete_card(card_id: int, db: Session = Depends(get_db)):
    db.query(Card).filter(Card.id == card_id).delete(synchronize_session=False)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/card-revolvings")
def create_card_revolving(
    card_id: int = Form(...),
    start_month: str = Form(...),
    remaining_yen: int = Form(...),
    monthly_payment_yen: int = Form(...),
    note: str | None = Form(None),
    db: Session = Depends(get_db),
):
    card = db.query(Card).filter(Card.id == int(card_id)).one_or_none()
    if card is None:
        raise HTTPException(status_code=400, detail="card not found")

    try:
        month_first = _parse_month_start(start_month)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    remaining = abs(int(remaining_yen or 0))
    monthly = abs(int(monthly_payment_yen or 0))
    if remaining <= 0:
        raise HTTPException(status_code=400, detail="remaining_yen must be > 0")
    if monthly <= 0:
        raise HTTPException(status_code=400, detail="monthly_payment_yen must be > 0")

    db.add(
        CardRevolving(
            card_id=int(card_id),
            start_month=month_first,
            remaining_yen=remaining,
            monthly_payment_yen=monthly,
            note=(note or None),
        )
    )
    db.commit()
    rebuild_events_scheduler(db, user_id=1)
    return RedirectResponse(url="/", status_code=303)


@app.post("/card-revolvings/{revolving_id}/update")
def update_card_revolving(
    revolving_id: int,
    card_id: int = Form(...),
    start_month: str = Form(...),
    remaining_yen: int = Form(...),
    monthly_payment_yen: int = Form(...),
    note: str | None = Form(None),
    db: Session = Depends(get_db),
):
    card = db.query(Card).filter(Card.id == int(card_id)).one_or_none()
    if card is None:
        raise HTTPException(status_code=400, detail="card not found")

    rv = db.query(CardRevolving).filter(CardRevolving.id == revolving_id).first()
    if rv:
        try:
            month_first = _parse_month_start(start_month)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        remaining = abs(int(remaining_yen or 0))
        monthly = abs(int(monthly_payment_yen or 0))
        if remaining <= 0:
            raise HTTPException(status_code=400, detail="remaining_yen must be > 0")
        if monthly <= 0:
            raise HTTPException(status_code=400, detail="monthly_payment_yen must be > 0")

        rv.card_id = int(card_id)
        rv.start_month = month_first
        rv.remaining_yen = remaining
        rv.monthly_payment_yen = monthly
        rv.note = note or None
        db.commit()
        rebuild_events_scheduler(db, user_id=1)
    return RedirectResponse(url="/", status_code=303)


@app.post("/card-revolvings/{revolving_id}/delete")
def delete_card_revolving(revolving_id: int, db: Session = Depends(get_db)):
    db.query(CardRevolving).filter(CardRevolving.id == revolving_id).delete(synchronize_session=False)
    db.commit()
    rebuild_events_scheduler(db, user_id=1)
    return RedirectResponse(url="/", status_code=303)


@app.post("/card-revolvings/bulk-delete")
def bulk_delete_card_revolvings(
    ids: str = Form(""),
    db: Session = Depends(get_db),
):
    unique_ids = _parse_bulk_ids(ids)
    if not unique_ids:
        return RedirectResponse(url="/", status_code=303)

    db.query(CardRevolving).filter(CardRevolving.id.in_(unique_ids)).delete(synchronize_session=False)
    db.commit()
    rebuild_events_scheduler(db, user_id=1)
    return RedirectResponse(url="/", status_code=303)


@app.post("/card-installments")
def create_card_installment(
    card_id: int = Form(...),
    start_month: str = Form(...),
    months: int = Form(...),
    total_amount_yen: int = Form(...),
    note: str | None = Form(None),
    db: Session = Depends(get_db),
):
    card = db.query(Card).filter(Card.id == int(card_id)).one_or_none()
    if card is None:
        raise HTTPException(status_code=400, detail="card not found")

    try:
        month_first = _parse_month_start(start_month)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    months_i = max(1, int(months or 1))
    total = abs(int(total_amount_yen or 0))
    if total <= 0:
        raise HTTPException(status_code=400, detail="total_amount_yen must be > 0")

    db.add(
        CardInstallment(
            card_id=int(card_id),
            start_month=month_first,
            months=months_i,
            total_amount_yen=total,
            note=(note or None),
        )
    )
    db.commit()
    rebuild_events_scheduler(db, user_id=1)
    return RedirectResponse(url="/", status_code=303)


@app.post("/card-installments/{installment_id}/update")
def update_card_installment(
    installment_id: int,
    card_id: int = Form(...),
    start_month: str = Form(...),
    months: int = Form(...),
    total_amount_yen: int = Form(...),
    note: str | None = Form(None),
    db: Session = Depends(get_db),
):
    card = db.query(Card).filter(Card.id == int(card_id)).one_or_none()
    if card is None:
        raise HTTPException(status_code=400, detail="card not found")

    inst = db.query(CardInstallment).filter(CardInstallment.id == installment_id).first()
    if inst:
        try:
            month_first = _parse_month_start(start_month)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        months_i = max(1, int(months or 1))
        total = abs(int(total_amount_yen or 0))
        if total <= 0:
            raise HTTPException(status_code=400, detail="total_amount_yen must be > 0")

        inst.card_id = int(card_id)
        inst.start_month = month_first
        inst.months = months_i
        inst.total_amount_yen = total
        inst.note = note or None
        db.commit()
        rebuild_events_scheduler(db, user_id=1)
    return RedirectResponse(url="/", status_code=303)


@app.post("/card-installments/{installment_id}/delete")
def delete_card_installment(installment_id: int, db: Session = Depends(get_db)):
    db.query(CardInstallment).filter(CardInstallment.id == installment_id).delete(synchronize_session=False)
    db.commit()
    rebuild_events_scheduler(db, user_id=1)
    return RedirectResponse(url="/", status_code=303)


@app.post("/card-installments/bulk-delete")
def bulk_delete_card_installments(
    ids: str = Form(""),
    db: Session = Depends(get_db),
):
    unique_ids = _parse_bulk_ids(ids)
    if not unique_ids:
        return RedirectResponse(url="/", status_code=303)

    db.query(CardInstallment).filter(CardInstallment.id.in_(unique_ids)).delete(synchronize_session=False)
    db.commit()
    rebuild_events_scheduler(db, user_id=1)
    return RedirectResponse(url="/", status_code=303)


@app.post("/card-transactions")
def create_card_transaction(
    request: Request,
    card_id: int = Form(...),
    date_: date = Form(..., alias="date"),
    amount_yen: int = Form(...),
    merchant: str | None = Form(None),
):
    db = SessionLocal()
    try:
        # guard: ensure card exists to avoid crashes
        card = db.query(Card).filter(Card.id == card_id).one_or_none()
        if card is None:
            # keep UX simple: redirect to top when card is not found
            return RedirectResponse(url="/", status_code=303)

        t = CardTransaction(
            card_id=card_id,
            date=date_,
            amount_yen=int(amount_yen),
            merchant=(merchant or None),
        )
        db.add(t)
        db.commit()
    finally:
        db.close()

    return RedirectResponse(url="/", status_code=303)


@app.post("/card-transactions/import-csv")
async def import_card_transactions_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    content = await file.read()
    rows = _csv_dict_rows(content)

    required = {"yyyy/mm/dd", "title", "price", "card"}
    headers = {h.strip().lower() for h in (rows[0].keys() if rows else [])}
    if rows and not required.issubset(headers):
        raise HTTPException(
            status_code=400,
            detail="CSV headers must include: yyyy/mm/dd, title, price, card",
        )

    created = 0
    for r in rows:
        row = {str(k).strip().lower(): (v or "").strip() for k, v in r.items()}
        if not any(row.values()):
            continue
        try:
            tx_date = _parse_csv_date(row.get("yyyy/mm/dd", ""))
            merchant = row.get("title", "")
            amount = abs(_parse_csv_amount(row.get("price", "")))
            card_id = _resolve_card_id(db, row.get("card", ""))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"card csv parse error: {e}")

        db.add(
            CardTransaction(
                card_id=card_id,
                date=tx_date,
                amount_yen=amount,
                merchant=merchant or None,
            )
        )
        created += 1

    if created > 0:
        db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/card-transactions/{tx_id}/update")
def update_card_transaction(
    tx_id: int,
    card_id: int = Form(...),
    date_: date = Form(..., alias="date"),
    amount_yen: int = Form(...),
    merchant: str | None = Form(None),
    db: Session = Depends(get_db),
):
    t = db.query(CardTransaction).filter(CardTransaction.id == tx_id).first()
    if t:
        t.card_id = int(card_id)
        t.date = date_
        t.amount_yen = int(amount_yen)
        t.merchant = merchant or None
        db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/card-transactions/{tx_id}/delete")
def delete_card_transaction(tx_id: int):
    db = SessionLocal()
    try:
        db.query(CardTransaction).filter(CardTransaction.id == tx_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()

    return RedirectResponse(url="/", status_code=303)


@app.post("/card-transactions/bulk-delete")
def bulk_delete_card_transactions(
    ids: str = Form(""),
    db: Session = Depends(get_db),
):
    unique_ids = _parse_bulk_ids(ids)
    if not unique_ids:
        return RedirectResponse(url="/", status_code=303)

    db.query(CardTransaction).filter(CardTransaction.id.in_(unique_ids)).delete(synchronize_session=False)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/oneoff")
def create_oneoff(
    db: Session = Depends(get_db),
    date_: date = Form(..., alias="date"),
    account_id: int = Form(...),
    amount_yen: int = Form(...),
    direction: str = Form(...),  # "expense" or "income"
    description: str = Form(...),
):
    amt = int(amount_yen)
    if direction == "expense":
        amt = -abs(amt)
    else:
        amt = abs(amt)

    ev = CashflowEvent(
        user_id=1,
        date=date_,
        account_id=int(account_id),
        amount_yen=amt,
        plan_id=None,
        description=description,
        source="oneoff",
        status="expected",
    )
    db.add(ev)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/oneoff/import-csv")
async def import_oneoff_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    content = await file.read()
    rows = _csv_dict_rows(content)

    required = {"yyyy/mm/dd", "type", "price", "account", "memo"}
    headers = {h.strip().lower() for h in (rows[0].keys() if rows else [])}
    if rows and not required.issubset(headers):
        raise HTTPException(
            status_code=400,
            detail="CSV headers must include: yyyy/mm/dd, type, price, account, memo",
        )

    created = 0
    for r in rows:
        row = {str(k).strip().lower(): (v or "").strip() for k, v in r.items()}
        if not any(row.values()):
            continue
        try:
            ev_date = _parse_csv_date(row.get("yyyy/mm/dd", ""))
            direction = _parse_direction(row.get("type", ""))
            account_id = _resolve_account_id(db, row.get("account", ""))
            amount = _parse_csv_amount(row.get("price", ""))
            memo = row.get("memo", "")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"oneoff csv parse error: {e}")

        amt = abs(amount)
        if direction == "expense":
            amt = -amt

        db.add(
            CashflowEvent(
                user_id=1,
                date=ev_date,
                account_id=account_id,
                amount_yen=amt,
                plan_id=None,
                description=memo or None,
                source="oneoff",
                status="expected",
            )
        )
        created += 1

    if created > 0:
        db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/oneoff/import-text")
def import_oneoff_text(
    text: str = Form(...),
    account_id: int = Form(...),
    default_direction: str = Form("auto"),  # auto / expense / income
    db: Session = Depends(get_db),
):
    account = db.query(Account).filter(Account.id == int(account_id)).one_or_none()
    if account is None:
        raise HTTPException(status_code=400, detail="account not found")

    mode = str(default_direction or "auto").strip().lower()
    if mode not in ("auto", "expense", "income"):
        raise HTTPException(status_code=400, detail="default_direction must be auto/expense/income")

    rows, warnings, errors = parse_card_text_preview(text or "")
    if errors:
        raise HTTPException(status_code=400, detail=f"oneoff text parse error: {' | '.join(errors[:5])}")
    if not rows:
        raise HTTPException(status_code=400, detail="no rows parsed from text")

    created = 0
    for i, row in enumerate(rows, start=1):
        try:
            ev_date = parse_flexible_date(str(row.get("date", "")))
            title = normalize_title(str(row.get("title", "")))
            raw_price = int(row.get("price", 0))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"row {i} parse error: {e}")

        base = abs(raw_price)
        if mode == "expense":
            signed_amount = -base
        elif mode == "income":
            signed_amount = base
        else:
            # Auto mode: positive text amount as expense, negative text amount as income.
            signed_amount = base if raw_price < 0 else -base

        db.add(
            CashflowEvent(
                user_id=1,
                date=ev_date,
                account_id=int(account_id),
                amount_yen=signed_amount,
                plan_id=None,
                description=title,
                source="oneoff",
                status="expected",
            )
        )
        created += 1

    if created > 0:
        db.commit()

    # Keep warnings observable in server logs; import still succeeds.
    if warnings:
        print(f"[oneoff/import-text] warnings: {warnings}")

    return RedirectResponse(url="/", status_code=303)


@app.post("/oneoff/{event_id}/update")
def update_oneoff(
    event_id: int,
    date_: date = Form(..., alias="date"),
    account_id: int = Form(...),
    amount_yen: int = Form(...),
    direction: str = Form(...),
    description: str = Form(...),
    db: Session = Depends(get_db),
):
    ev = db.query(CashflowEvent).filter(
        CashflowEvent.id == event_id,
        CashflowEvent.source == "oneoff",
    ).first()
    if ev:
        amt = int(amount_yen)
        if direction == "expense":
            amt = -abs(amt)
        else:
            amt = abs(amt)
        ev.date = date_
        ev.account_id = int(account_id)
        ev.amount_yen = amt
        ev.description = description
        db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/oneoff/{event_id}/delete")
def delete_oneoff(event_id: int, db: Session = Depends(get_db)):
    db.query(CashflowEvent).filter(
        CashflowEvent.id == event_id,
        CashflowEvent.source == "oneoff",
    ).delete(synchronize_session=False)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/oneoff/bulk-delete")
def bulk_delete_oneoff(
    ids: str = Form(""),
    db: Session = Depends(get_db),
):
    unique_ids = _parse_bulk_ids(ids)
    if not unique_ids:
        return RedirectResponse(url="/", status_code=303)

    db.query(CashflowEvent).filter(
        CashflowEvent.id.in_(unique_ids),
        CashflowEvent.source == "oneoff",
    ).delete(synchronize_session=False)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/transfer")
def create_transfer(
    db: Session = Depends(get_db),
    date_: date = Form(..., alias="date"),
    from_account_id: int = Form(...),
    to_account_id: int = Form(...),
    amount_yen: int = Form(...),
    method: str = Form(...),  # "bank" / "debit" / "card"
    description: str = Form("郢昶・ﾎ慕ｹ晢ｽｼ郢ｧ・ｸ"),
    card_id: int | None = Form(None),
):
    amt = abs(int(amount_yen))
    tid = str(uuid4())

    # to陋幢ｽｴ邵ｺ・ｯ陟｢繝ｻ笘・+繝ｻ蝓滂ｽｮ遏ｩ・ｫ蛟･窶ｲ陟・干竏ｴ郢ｧ蜈ｷ・ｼ繝ｻ
    ev_to = CashflowEvent(
        user_id=1,
        date=date_,
        account_id=int(to_account_id),
        amount_yen=amt,
        plan_id=None,
        description=f"{description} IN",
        source="transfer",
        transfer_id=tid,
        status="expected",
    )
    db.add(ev_to)

    if method in ("bank", "debit"):
        # from陋幢ｽｴ邵ｺ・ｯ陷ｷ譴ｧ蠕狗ｸｺ・ｫ -繝ｻ蝓滂ｽｮ遏ｩ・ｫ蛟･ﾂｰ郢ｧ逕ｻ・ｸ蟶呻ｽ九・繝ｻ
        ev_from = CashflowEvent(
            user_id=1,
            date=date_,
            account_id=int(from_account_id),
            amount_yen=-amt,
            plan_id=None,
            description=f"{description} OUT",
            source="transfer",
            transfer_id=tid,
            status="expected",
        )
        db.add(ev_from)

    elif method == "card":
        # card charge: do not create immediate minus on from side
        # add CardTransaction and let withdrawal event reduce bank later
        if not card_id:
            return RedirectResponse(url="/", status_code=303)

        tx = CardTransaction(
            card_id=int(card_id),
            date=date_,
            amount_yen=amt,  # expense is positive in card_transactions
            merchant=description,
            note=f"charge to account_id={to_account_id}",
        )
        db.add(tx)

    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/transfer/{transfer_id}/update")
def update_transfer(
    transfer_id: str,
    date_: date = Form(..., alias="date"),
    from_account_id: int = Form(...),
    to_account_id: int = Form(...),
    amount_yen: int = Form(...),
    db: Session = Depends(get_db),
):
    evs = (
        db.query(CashflowEvent)
        .filter(CashflowEvent.user_id == 1)
        .filter(CashflowEvent.source == "transfer")
        .filter(CashflowEvent.transfer_id == transfer_id)
        .all()
    )
    if not evs:
        return RedirectResponse(url="/", status_code=303)

    ev_from = next((x for x in evs if int(x.amount_yen) < 0), None)
    ev_to = next((x for x in evs if int(x.amount_yen) > 0), None)
    amt = abs(int(amount_yen))

    if ev_to:
        ev_to.date = date_
        ev_to.account_id = int(to_account_id)
        ev_to.amount_yen = amt
    if ev_from:
        ev_from.date = date_
        ev_from.account_id = int(from_account_id)
        ev_from.amount_yen = -amt

    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/transfer/{transfer_id}/delete")
def delete_transfer(transfer_id: str, db: Session = Depends(get_db)):
    db.query(CashflowEvent).filter(
        CashflowEvent.user_id == 1,
        CashflowEvent.source == "transfer",
        CashflowEvent.transfer_id == transfer_id,
    ).delete(synchronize_session=False)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/card_charges/{tx_id}/update")
def update_card_charge(
    tx_id: int,
    date_: date = Form(..., alias="date"),
    amount_yen: int = Form(...),
    card_id: int = Form(...),
    to_account_id: int = Form(...),
    db: Session = Depends(get_db),
):
    tx = db.query(CardTransaction).filter(CardTransaction.id == tx_id).first()
    if tx:
        tx.date = date_
        tx.amount_yen = int(amount_yen)
        tx.card_id = int(card_id)
        tx.note = f"charge to account_id={to_account_id}"
        db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/card_charges/{tx_id}/delete")
def delete_card_charge(tx_id: int, db: Session = Depends(get_db)):
    db.query(CardTransaction).filter(CardTransaction.id == tx_id).delete(synchronize_session=False)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/forecast/free")
def api_forecast_free(db: Session = Depends(get_db)):
    today = date.today()
    this_first = today.replace(day=1)
    next_first = date(this_first.year + (1 if this_first.month == 12 else 0),
                      1 if this_first.month == 12 else this_first.month + 1,
                      1)
    end = month_range(next_first)[1]  # end of next month

    series = forecast_free_daily(db, user_id=1, start=this_first, end=end)
    return {"series": series}


