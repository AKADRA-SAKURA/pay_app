from dotenv import load_dotenv
load_dotenv()

from uuid import uuid4
from fastapi import FastAPI, Depends, Request, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, joinedload
from datetime import date, datetime
import calendar
import re

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

# 起動時にテーブル作成（簡易版）
Base.metadata.create_all(bind=engine)

app = FastAPI(title="期限・固定費マネージャ（ローカル）")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


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

    events_this = crud.list_events_between_with_plan(db, 1, this_first, this_last)
    events_next = crud.list_events_between_with_plan(db, 1, next_first, next_last)
    from collections import defaultdict

    start_balance = crud.total_start_balance(db, 1)
    this_net = sum(e["amount_yen"] for e in events_this)
    next_net = sum(e["amount_yen"] for e in events_next)

    free_this = start_balance + this_net
    free_next = start_balance + this_net + next_net

    # --- 口座別集計（M1-6） ---
    # events_* は dict の配列（e["account_id"], e["amount_yen"] がある前提）
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

    # 表示を安定させる（口座名順など）
    account_summaries.sort(key=lambda x: x["id"])

    forecast = forecast_by_account_daily(db, user_id=1, start=this_first, end=next_last)

    # --- カード（フェーズ1）---
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

    # account_id -> "名前(kind)" の表示名辞書
    acc_label = {int(a.id): f"{a.name}（{getattr(a, 'kind', 'bank')}）" for a in accounts}

    # transferイベント（bank/debit）を最新から取得
    transfer_events = (
        db.query(CashflowEvent)
        .filter(CashflowEvent.user_id == 1)
        .filter(CashflowEvent.source == "transfer")
        .filter(CashflowEvent.transfer_id.isnot(None))
        .order_by(CashflowEvent.date.desc(), CashflowEvent.id.desc())
        .limit(80)  # 2行で1件なので少し多めに取る
        .all()
    )

    # transfer_id ごとにまとめる（from/to が揃ったら1件にする）
    group = {}
    for e in transfer_events:
        tid = e.transfer_id
        if tid not in group:
            group[tid] = {"evs": [], "date": e.date}
        group[tid]["evs"].append(e)

    transfers = []
    # date desc で並び替え
    for tid, g in sorted(group.items(), key=lambda kv: kv[1]["date"], reverse=True):
        evs = g["evs"]

        # from = マイナス、to = プラス とみなす
        ev_from = next((x for x in evs if int(x.amount_yen) < 0), None)
        ev_to = next((x for x in evs if int(x.amount_yen) > 0), None)

        # 片方しかない場合はスキップ（データ不整合対策）
        if not ev_from or not ev_to:
            continue

        amt = int(ev_to.amount_yen)

        # method は description か別カラムが無いので暫定で "transfer"
        # もし create_transfer で description に bank/debit を入れてるならそこから推定もできる
        method = "transfer"

        transfers.append(
            {
                "transfer_id": tid,
                "id": ev_to.id,  # 表示用（to側のidを代表に）
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

    # accounts -> 表示ラベル（名前(kind)）
    acc_label = {int(a.id): f"{a.name}（{getattr(a, 'kind', 'bank')}）" for a in accounts}

    # note から "charge to account_id=123" を抜く
    charge_re = re.compile(r"charge to account_id=(\d+)")

    # クレカチャージ（CardTransaction）を最新から取る
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
            "this_range": (this_first, this_last),
            "next_range": (next_first, next_last),
            "account_summaries": account_summaries,
            "forecast": forecast,
            "cards": cards,
            "card_transactions": card_transactions,
            "oneoffs": oneoffs,
            "transfers": transfers,
            "card_charges": card_charges,
            "advice": get_today_advice(db, user_id=1),
        },
    )


# API: 一覧（JSON）
@app.get("/api/subscriptions", response_model=list[SubscriptionOut])
def api_list_subscriptions(db: Session = Depends(get_db)):
    return crud.list_subscriptions(db)


# 画面フォーム: 追加
@app.post("/subscriptions")
def create_subscription(
    name: str = Form(...),
    amount_yen: int = Form(...),
    billing_day: int = Form(...),
    db: Session = Depends(get_db),
):
    data = SubscriptionCreate(name=name, amount_yen=amount_yen, billing_day=billing_day)
    crud.create_subscription(db, data)
    return RedirectResponse(url="/", status_code=303)


# 画面フォーム: 削除
@app.post("/subscriptions/{sub_id}/update")
def update_subscription(
    sub_id: int,
    name: str = Form(...),
    amount_yen: int = Form(...),
    billing_day: int = Form(...),
    db: Session = Depends(get_db),
):
    sub = db.query(Subscription).filter(Subscription.id == sub_id).first()
    if sub:
        sub.name = name
        sub.amount_yen = int(amount_yen)
        sub.billing_day = int(billing_day)
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


# plans登録
@app.post("/plans")
def add_plan(
    type: str = Form(...),            # "income" or "subscription"
    title: str = Form(...),
    amount_yen: int = Form(...),
    account_id: int = Form(...),
    freq: str = Form(...),            # monthly/yearly/monthly_interval
    day: int = Form(1),
    interval_months: int = Form(1),
    start_date: str | None = Form(default=None),
    month: int = Form(1),
    db: Session = Depends(get_db),
):
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id is required")

    if start_date:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        sd = date.today()
    crud.create_plan(
        db,
        user_id=1,
        type=type,
        title=title,
        amount_yen=amount_yen,
        account_id=account_id,
        freq=freq,
        day=day,
        interval_months=interval_months,
        start_date=sd,
        month=month,
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
    # 超簡易：存在したら削除
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
    account_id: int = Form(...),
    freq: str = Form(...),
    day: int = Form(1),
    interval_months: int = Form(1),
    start_date: str | None = Form(default=None),
    month: int = Form(1),
    db: Session = Depends(get_db),
):
    p = db.query(Plan).filter(Plan.id == plan_id, Plan.user_id == 1).first()
    if p:
        p.type = type
        p.title = title
        p.amount_yen = int(amount_yen)
        p.account_id = int(account_id)
        p.freq = freq
        p.day = int(day)
        p.interval_months = int(interval_months)
        p.month = int(month)
        if start_date:
            p.start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
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

    # 今月初〜来月末（既存画面の発想と同じ）
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
        # カード存在チェック（雑に落ちるの防止）
        card = db.query(Card).filter(Card.id == card_id).one_or_none()
        if card is None:
            # 画面は同じでOK。必要なら flash 的な仕組み後で。
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
        description=f"{description}（IN）",
        source="transfer",
        transfer_id=tid,
        status="expected",
    )
    db.add(ev_to)

    if method in ("bank", "debit"):
        # from側も即時に -（口座から差し引き / デビッド＝口座即時）
        ev_from = CashflowEvent(
            user_id=1,
            date=date_,
            account_id=int(from_account_id),
            amount_yen=-amt,
            plan_id=None,
            description=f"{description}（OUT）",
            source="transfer",
            transfer_id=tid,
            status="expected",
        )
        db.add(ev_from)

    elif method == "card":
        # ★クレカチャージ：from側の即時マイナスはしない（引落日に減る）
        # 代わりに CardTransaction を追加して、既存の引落生成で bank が減る
        if not card_id:
            return RedirectResponse(url="/", status_code=303)

        tx = CardTransaction(
            card_id=int(card_id),
            date=date_,
            amount_yen=amt,  # 支出=正
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
    end = month_range(next_first)[1]  # 来月末

    series = forecast_free_daily(db, user_id=1, start=this_first, end=end)
    return {"series": series}
