import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Account, Card, Plan
from app.services.scheduler import build_card_withdraw_events, build_month_events


class PlanCardStartDateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _seed_account_and_card(self) -> None:
        self.db.add(
            Account(
                id=1,
                name="Bank",
                kind="bank",
                balance_yen=100000,
                user_id=1,
                effective_start_date=date(2020, 1, 1),
                effective_end_date=None,
            )
        )
        self.db.add(
            Card(
                name="EPOS",
                closing_day=31,
                payment_day=27,
                payment_account_id=1,
                effective_start_date=date(2020, 1, 1),
                effective_end_date=None,
            )
        )
        self.db.commit()

    def test_card_payment_plan_is_not_counted_before_start_date(self) -> None:
        self._seed_account_and_card()
        self.db.add(
            Plan(
                user_id=1,
                type="subscription",
                title="Card Plan",
                amount_yen=1000,
                account_id=1,
                payment_method="card",
                card_id=1,
                freq="monthly",
                day=10,
                interval_months=1,
                month=1,
                start_date=date(2026, 1, 20),
                end_date=None,
            )
        )
        self.db.commit()

        # Feb withdraw covers Jan period. Jan 10 is before start_date (Jan 20), so not counted.
        feb_withdraw = build_card_withdraw_events(self.db, user_id=1, withdraw_y=2026, withdraw_m=2)
        self.assertEqual(len(feb_withdraw), 1)
        self.assertEqual(int(feb_withdraw[0].amount_yen), 0)

        # Mar withdraw covers Feb period. Feb 10 is valid and should be counted.
        mar_withdraw = build_card_withdraw_events(self.db, user_id=1, withdraw_y=2026, withdraw_m=3)
        self.assertEqual(len(mar_withdraw), 1)
        self.assertEqual(int(mar_withdraw[0].amount_yen), -1000)

    def test_bank_payment_plan_is_not_created_before_start_date(self) -> None:
        self.db.add(
            Account(
                id=1,
                name="Bank",
                kind="bank",
                balance_yen=100000,
                user_id=1,
                effective_start_date=date(2020, 1, 1),
                effective_end_date=None,
            )
        )
        self.db.add(
            Plan(
                user_id=1,
                type="subscription",
                title="Bank Plan",
                amount_yen=3000,
                account_id=1,
                payment_method="bank",
                card_id=None,
                freq="monthly",
                day=10,
                interval_months=1,
                month=1,
                start_date=date(2026, 1, 20),
                end_date=None,
            )
        )
        self.db.commit()

        jan = build_month_events(self.db, user_id=1, month_first=date(2026, 1, 1))
        feb = build_month_events(self.db, user_id=1, month_first=date(2026, 2, 1))
        self.assertEqual(jan, [])
        self.assertEqual(len(feb), 1)
        self.assertEqual(int(feb[0].amount_yen), -3000)


if __name__ == "__main__":
    unittest.main()
