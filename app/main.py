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

from app.services.scheduler import rebuild_events as rebuild_events_scheduler
from .db import Base, engine, get_db, SessionLocal
from .schemas import SubscriptionCreate, SubscriptionOut
from . import crud
from .models import Account, Card, CardTransaction, CashflowEvent, Subscription, Plan
from .crud import list_accounts, create_account
from app.services.forecast import forecast_by_account_events, forecast_by_account_daily
from .services.forecast import forecast_free_daily
from app.advice.service import get_today_advice
from app.utils.dates import month_range
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
        if "billing_month" not in cols:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN billing_month INTEGER NOT NULL DEFAULT 1"))
        if "payment_method" not in cols:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN payment_method VARCHAR(20) NOT NULL DEFAULT 'bank'"))
        if "account_id" not in cols:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN account_id INTEGER"))
        if "card_id" not in cols:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN card_id INTEGER"))

_ensure_subscription_columns()

app = FastAPI(title="家計簿・口座管理マネージャー（ローカル）")
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
    if s in ("expense", "exp", "-") or "支出" in s:
        return "expense"
    if s in ("income", "inc", "+") or "収入" in s:
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
        warnings = list(warnings) + [f"年未設定の日付が {missing_date}件あります。プレビューで補完してください"]

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
        warnings = list(warnings) + [f"年未設定の日付が {missing_date}件あります。プレビューで補完してください"]

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

    # 来月
    if this_first.month == 12:
        next_first = date(this_first.year + 1, 1, 1)
    else:
        next_first = date(this_first.year, this_first.month + 1, 1)
    next_first, next_last = month_range(next_first)

    # 再来月
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

    start_balance = crud.total_start_balance(db, 1)
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
        start = int(a.balance_yen)
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

    # --- card section (phase 1) ---
    cards = db.query(Card).order_by(Card.id.asc()).all()

    card_transactions = (
        db.query(CardTransaction)
        .options(joinedload(CardTransaction.card))
        .order_by(CardTransaction.date.desc(), CardTransaction.id.desc())
        .limit(50)
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
            "this_range": (this_first, this_last),
            "next_range": (next_first, next_last),
            "next2_range": (next2_first, next2_last),
            "account_summaries": account_summaries,
            "forecast": forecast,
            "cards": cards,
            "card_transactions": card_transactions,
            "oneoffs": oneoffs,
            "transfers": transfers,
            "card_charges": card_charges,
            "pay_pie_this": pay_pie_this,
            "pay_pie_next": pay_pie_next,
            "advice": get_today_advice(db, user_id=1),
        },
    )


# API: list (JSON)
@app.get("/api/subscriptions", response_model=list[SubscriptionOut])
def api_list_subscriptions(db: Session = Depends(get_db)):
    return crud.list_subscriptions(db)


# 逕ｻ髱｢繝輔か繝ｼ繝: 霑ｽ蜉
@app.post("/subscriptions")
def create_subscription(
    name: str = Form(...),
    amount_yen: int = Form(...),
    billing_day: int = Form(...),
    freq: str = Form("monthly"),
    interval_months: str | None = Form(None),
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
    month_i = _to_int(billing_month) or 1
    account_i = _to_int(account_id)
    card_i = _to_int(card_id)

    if freq == "monthly":
        interval_i = 1
        month_i = 1
    elif freq == "yearly":
        interval_i = 1
    elif freq == "monthly_interval":
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
        billing_month=month_i,
        payment_method=payment_method,
        account_id=account_i,
        card_id=card_i,
    )
    crud.create_subscription(db, data)
    return RedirectResponse(url="/", status_code=303)


# 逕ｻ髱｢繝輔か繝ｼ繝: 蜑企勁
@app.post("/subscriptions/{sub_id}/update")
def update_subscription(
    sub_id: int,
    name: str = Form(...),
    amount_yen: int = Form(...),
    billing_day: int = Form(...),
    freq: str = Form("monthly"),
    interval_months: str | None = Form(None),
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
    month_i = _to_int(billing_month) or 1
    account_i = _to_int(account_id)
    card_i = _to_int(card_id)

    if freq == "monthly":
        interval_i = 1
        month_i = 1
    elif freq == "yearly":
        interval_i = 1
    elif freq == "monthly_interval":
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
    db: Session = Depends(get_db),
):
    create_account(db, name=name, balance_yen=balance_yen, kind=kind)
    return RedirectResponse(url="/", status_code=303)


# plans逋ｻ骭ｲ
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
    db: Session = Depends(get_db),
):
    acc = db.query(Account).filter(Account.id == account_id).first()
    if acc:
        acc.name = name
        acc.balance_yen = int(balance_yen)
        acc.kind = kind
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


@app.post("/cards")
def create_card(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    closing_day: int = Form(...),
    payment_day: int = Form(...),
    payment_account_id: int = Form(...),
):
    c = Card(
        name=name,
        closing_day=int(closing_day),
        payment_day=int(payment_day),
        payment_account_id=int(payment_account_id),
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
    db: Session = Depends(get_db),
):
    c = db.query(Card).filter(Card.id == card_id).first()
    if c:
        c.name = name
        c.closing_day = int(closing_day)
        c.payment_day = int(payment_day)
        c.payment_account_id = int(payment_account_id)
        db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/cards/{card_id}/delete")
def delete_card(card_id: int, db: Session = Depends(get_db)):
    db.query(Card).filter(Card.id == card_id).delete(synchronize_session=False)
    db.commit()
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


@app.post("/transfer")
def create_transfer(
    db: Session = Depends(get_db),
    date_: date = Form(..., alias="date"),
    from_account_id: int = Form(...),
    to_account_id: int = Form(...),
    amount_yen: int = Form(...),
    method: str = Form(...),  # "bank" / "debit" / "card"
    description: str = Form("チャージ"),
    card_id: int | None = Form(None),
):
    amt = abs(int(amount_yen))
    tid = str(uuid4())

    # to側は必ず +（残高が増える）
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
        # from側は同日に -（残高から減る）
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

