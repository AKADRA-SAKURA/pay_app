import os
import unittest
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Force in-memory DB before importing app.main.
os.environ.setdefault("DB_URL", "sqlite:///:memory:")

import app.main as main
from app.db import Base
from app.models import Account, Card, CardTransaction, CashflowEvent


class ApiIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.app.dependency_overrides.pop(main.get_db, None)
        self.client.close()
        self.engine.dispose()

    def _seed_account(self) -> int:
        db = self.Session()
        try:
            month_first = date.today().replace(day=1)
            acc = Account(
                name="IT Bank",
                kind="bank",
                balance_yen=50000,
                user_id=1,
                effective_start_date=month_first,
                effective_end_date=None,
            )
            db.add(acc)
            db.commit()
            return int(acc.id)
        finally:
            db.close()

    def _seed_card_with_transaction(self, account_id: int) -> tuple[int, date]:
        db = self.Session()
        try:
            month_first = date.today().replace(day=1)
            card = Card(
                name="IT Card",
                closing_day=15,
                payment_day=27,
                payment_account_id=account_id,
                effective_start_date=month_first - timedelta(days=120),
                effective_end_date=None,
            )
            db.add(card)
            db.flush()

            period_start, _, _ = main.card_period_for_withdraw_month(card, month_first.year, month_first.month)
            db.add(CardTransaction(card_id=card.id, date=period_start, amount_yen=1200, merchant="IT Store"))
            db.commit()
            return int(card.id), month_first
        finally:
            db.close()

    def test_api_rebuild_and_forecast_smoke(self) -> None:
        account_id = self._seed_account()
        card_id, month_first = self._seed_card_with_transaction(account_id)

        rebuild_res = self.client.post("/events/rebuild", follow_redirects=False)
        self.assertEqual(rebuild_res.status_code, 303)

        forecast_res = self.client.get("/api/forecast/accounts")
        self.assertEqual(forecast_res.status_code, 200)
        forecast_json = forecast_res.json()
        self.assertIn("accounts", forecast_json)
        self.assertIn("total_series", forecast_json)

        free_res = self.client.get("/api/forecast/free")
        self.assertEqual(free_res.status_code, 200)
        free_json = free_res.json()
        self.assertIn("series", free_json)
        self.assertIsInstance(free_json["series"], list)

        pie_res = self.client.get(
            "/api/cards/merchant-pie",
            params={"card_id": card_id, "withdraw_month": month_first.strftime("%Y-%m")},
        )
        self.assertEqual(pie_res.status_code, 200)
        pie_json = pie_res.json()
        self.assertGreaterEqual(int(pie_json.get("total_yen", 0)), 1200)

    def test_oneoff_import_text_creates_event(self) -> None:
        account_id = self._seed_account()

        import_res = self.client.post(
            "/oneoff/import-text",
            data={
                "text": "2026/02/04 テスト店 1,234円",
                "account_id": str(account_id),
                "default_direction": "auto",
            },
            follow_redirects=False,
        )
        self.assertEqual(import_res.status_code, 303)

        db = self.Session()
        try:
            event = (
                db.query(CashflowEvent)
                .filter(CashflowEvent.source == "oneoff")
                .order_by(CashflowEvent.id.desc())
                .first()
            )
            self.assertIsNotNone(event)
            self.assertEqual(int(event.account_id), account_id)
            self.assertEqual(int(event.amount_yen), -1234)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
