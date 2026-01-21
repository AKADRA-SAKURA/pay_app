from fastapi import FastAPI, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from datetime import date
import calendar
from app.services.scheduler import rebuild_events_for_two_months

from .db import Base, engine, get_db
from .schemas import SubscriptionCreate, SubscriptionOut
from . import crud

from .models import Account
from .crud import list_accounts, create_account

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

    events_this = crud.list_events_between(db, 1, this_first, this_last)
    events_next = crud.list_events_between(db, 1, next_first, next_last)

    start_balance = crud.total_start_balance(db, 1)
    this_net = sum(e.amount_yen for e in events_this)
    next_net = sum(e.amount_yen for e in events_next)

    free_this = start_balance + this_net
    free_next = start_balance + this_net + next_net

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
@app.post("/subscriptions/{sub_id}/delete")
def delete_subscription(sub_id: int, db: Session = Depends(get_db)):
    crud.delete_subscription(db, sub_id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/accounts")
def add_account(
    name: str = Form(...),
    balance_yen: int = Form(...),
    db: Session = Depends(get_db),
):
    create_account(db, name=name, balance_yen=balance_yen)
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
    month: int = Form(1),
    db: Session = Depends(get_db),
):
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
        month=month,
    )
    return RedirectResponse(url="/", status_code=303)


@app.post("/accounts/{account_id}/delete")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    # 超簡易：存在したら削除
    acc = db.query(Account).filter(Account.id == account_id).first()
    if acc:
        db.delete(acc)
        db.commit()
    return RedirectResponse(url="/", status_code=303)


def month_range(d: date):
    first = d.replace(day=1)
    last_day = calendar.monthrange(d.year, d.month)[1]
    last = d.replace(day=last_day)
    return first, last


@app.post("/events/rebuild")
def rebuild_events(db: Session = Depends(get_db)):
    rebuild_events_for_two_months(db, user_id=1, today=date.today())
    return RedirectResponse(url="/", status_code=303)

@app.post("/plans/{plan_id}/delete")
def delete_plan(plan_id: int, db: Session = Depends(get_db)):
    crud.delete_plan(db, plan_id=plan_id, user_id=1)
    return RedirectResponse(url="/", status_code=303)
