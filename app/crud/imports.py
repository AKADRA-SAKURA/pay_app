# app/routers/imports.py
from fastapi import Request, UploadFile, File, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import ImportBatch, ImportedTransaction, Card
from app.crud import imports as crud_imports
from app.services.import_cards import parse_card_csv_bytes, normalize_rows_to_txns

def register_import_routes(app, templates):
    @app.get("/imports/new", response_class=HTMLResponse)
    def new_import(request: Request):
        db = SessionLocal()
        try:
            cards = db.query(Card).all()
            return templates.TemplateResponse(
                "imports/new.html",
                {"request": request, "cards": cards},
            )
        finally:
            db.close()

    @app.post("/imports/new")
    async def create_import(
        request: Request,
        file: UploadFile = File(...),
        card_id: int | None = Form(None),
    ):
        db = SessionLocal()
        try:
            content = await file.read()
            rows = parse_card_csv_bytes(content)
            txns = normalize_rows_to_txns(rows, header_map={
                "date": "利用日",
                "merchant": "利用先",
                "amount": "利用金額",
                "memo": "摘要",
            })

            batch = crud_imports.create_batch(
                db,
                source="csv_card",
                file_name=file.filename,
                card_id=card_id,
            )
            crud_imports.add_imported_transactions(db, batch=batch, txns=txns)
            db.commit()

            return RedirectResponse(
                url=f"/imports/{batch.id}",
                status_code=303,
            )
        finally:
            db.close()

    @app.get("/imports/{batch_id}", response_class=HTMLResponse)
    def preview_import(request: Request, batch_id: int):
        db = SessionLocal()
        try:
            batch = db.query(ImportBatch).get(batch_id)
            txns = (
                db.query(ImportedTransaction)
                .filter(ImportedTransaction.batch_id == batch_id)
                .all()
            )
            return templates.TemplateResponse(
                "imports/preview.html",
                {"request": request, "batch": batch, "txns": txns},
            )
        finally:
            db.close()

    @app.post("/imports/{batch_id}/commit")
    async def commit_import(request: Request, batch_id: int):
        form = await request.form()
        take_ids = [
            int(k.split("_")[1])
            for k, v in form.items()
            if k.startswith("take_")
        ]

        db = SessionLocal()
        try:
            batch = db.query(ImportBatch).get(batch_id)
            crud_imports.commit_batch_to_cashflow_events(
                db, batch=batch, take_ids=take_ids
            )
            db.commit()
            return RedirectResponse(
                url=f"/imports/{batch_id}",
                status_code=303,
            )
        finally:
            db.close()
