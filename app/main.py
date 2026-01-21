from fastapi import FastAPI, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .db import Base, engine, get_db
from .schemas import SubscriptionCreate, SubscriptionOut
from . import crud

# 起動時にテーブル作成（簡易版）
Base.metadata.create_all(bind=engine)

app = FastAPI(title="期限・固定費マネージャ（ローカル）")

templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
def page_index(request: Request, db: Session = Depends(get_db)):
    subs = crud.list_subscriptions(db)
    return templates.TemplateResponse("index.html", {"request": request, "subs": subs})


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
