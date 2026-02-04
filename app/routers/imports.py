# app/routers/imports.py
from __future__ import annotations
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import ImportBatch, Card, ImportedTransaction  # Cardがある前提
from app.crud import imports as crud_imports
from app.services.import_cards import parse_card_csv_bytes, normalize_rows_to_txns

router = APIRouter(prefix="/imports", tags=["imports"])

def get_db() -> Session:
    db = SessionLocal()
    try:
        return db
    finally:
        pass  # closeは各ハンドラ末尾で

# 会社別にCSVヘッダが違うので、まずは1種類を想定してmapを置く（後で増やす）
# 例（仮）: 利用日, 利用先, 利用金額, 摘要
DEFAULT_HEADER_MAP = {
    "date": "利用日",
    "merchant": "利用先",
    "amount": "利用金額",
    "memo": "摘要",
}

@router.get("/new")
def new_import(request: Request):
    db = SessionLocal()
    try:
        cards = db.query(Card).order_by(Card.id.desc()).all()
        return request.app.state.templates.TemplateResponse(
            "imports/new.html",
            {"request": request, "cards": cards},
        )
    finally:
        db.close()

@router.post("/new")
async def create_import(
    request: Request,
    file: UploadFile = File(...),
    card_id: int | None = Form(None),
):
    db = SessionLocal()
    try:
        content = await file.read()
        rows = parse_card_csv_bytes(content)
        txns = normalize_rows_to_txns(rows, header_map=DEFAULT_HEADER_MAP)

        batch = crud_imports.create_batch(db, source="csv_card", file_name=file.filename, card_id=card_id)
        inserted, skipped = crud_imports.add_imported_transactions(db, batch=batch, txns=txns)
        db.commit()

        url = request.url_for("preview_import", batch_id=batch.id)
        return RedirectResponse(url, status_code=303)
    finally:
        db.close()

@router.get("/{batch_id}", name="preview_import")
def preview_import(request: Request, batch_id: int):
    db = SessionLocal()
    try:
        batch = db.query(ImportBatch).get(batch_id)
        txns = (
            db.query(ImportedTransaction)
            .filter(ImportedTransaction.batch_id == batch_id)
            .order_by(ImportedTransaction.occurred_on.desc(), ImportedTransaction.id.desc())
            .all()
        )
        new_count = sum(1 for t in txns if t.state == "new")
        committed_count = sum(1 for t in txns if t.state == "committed")
        return request.app.state.templates.TemplateResponse(
            "imports/preview.html",
            {
                "request": request,
                "batch": batch,
                "txns": txns,
                "new_count": new_count,
                "committed_count": committed_count,
            },
        )
    finally:
        db.close()

@router.post("/{batch_id}/commit")
async def commit_import(request: Request, batch_id: int):
    form = await request.form()
    take_ids = [int(k.split("_", 1)[1]) for k, v in form.items() if k.startswith("take_") and v == "on"]

    db = SessionLocal()
    try:
        batch = db.query(ImportBatch).get(batch_id)
        committed = crud_imports.commit_batch_to_cashflow_events(db, batch=batch, take_ids=take_ids)
        db.commit()

        url = request.url_for("preview_import", batch_id=batch_id)
        return RedirectResponse(url, status_code=303)
    finally:
        db.close()
