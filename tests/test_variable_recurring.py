import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    Account,
    Card,
    CardTransaction,
    VariableRecurringPayment,
    VariableRecurringConfirmation,
)
from app.services.scheduler import (
    build_month_variable_recurring_events,
    build_card_withdraw_events,
)


class VariableRecurringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_bank_variable_payment_uses_confirmed_amount_when_present(self) -> None:
        self.db.add(
            VariableRecurringPayment(
                name="Gas",
                estimated_amount_yen=6000,
                billing_day=10,
                freq="monthly",
                interval_months=1,
                interval_weeks=1,
                billing_month=1,
                payment_method="bank",
                account_id=1,
                card_id=None,
                effective_start_date=date(2026, 1, 1),
                effective_end_date=None,
            )
        )
        self.db.commit()

        without_confirm = build_month_variable_recurring_events(self.db, user_id=1, month_first=date(2026, 2, 1))
        self.assertEqual(len(without_confirm), 1)
        self.assertEqual(int(without_confirm[0].amount_yen), -6000)

        self.db.add(
            VariableRecurringConfirmation(
                variable_payment_id=1,
                occurrence_date=without_confirm[0].date,
                confirmed_amount_yen=5800,
            )
        )
        self.db.commit()

        with_confirm = build_month_variable_recurring_events(self.db, user_id=1, month_first=date(2026, 2, 1))
        self.assertEqual(len(with_confirm), 1)
        self.assertEqual(int(with_confirm[0].amount_yen), -5800)

    def test_card_withdraw_adds_variable_payment_amount(self) -> None:
        self.db.add(
            Account(
                id=1,
                name="Bank",
                kind="bank",
                balance_yen=100000,
                user_id=1,
                effective_start_date=date(2026, 1, 1),
                effective_end_date=None,
            )
        )
        self.db.add(
            Card(
                name="Card",
                closing_day=31,
                payment_day=27,
                payment_account_id=1,
                effective_start_date=date(2026, 1, 1),
                effective_end_date=None,
            )
        )
        self.db.commit()

        self.db.add(
            VariableRecurringPayment(
                name="Water",
                estimated_amount_yen=4500,
                billing_day=15,
                freq="monthly",
                interval_months=1,
                interval_weeks=1,
                billing_month=1,
                payment_method="card",
                account_id=None,
                card_id=1,
                effective_start_date=date(2026, 1, 1),
                effective_end_date=None,
            )
        )
        self.db.commit()

        events = build_card_withdraw_events(self.db, user_id=1, withdraw_y=2026, withdraw_m=2)
        self.assertEqual(len(events), 1)
        self.assertEqual(int(events[0].amount_yen), -4500)

        self.db.add(
            VariableRecurringConfirmation(
                variable_payment_id=1,
                occurrence_date=date(2026, 1, 15),
                confirmed_amount_yen=4300,
            )
        )
        self.db.commit()

        events2 = build_card_withdraw_events(self.db, user_id=1, withdraw_y=2026, withdraw_m=2)
        self.assertEqual(len(events2), 1)
        self.assertEqual(int(events2[0].amount_yen), -4300)

    def test_card_withdraw_is_skipped_after_card_effective_end_date(self) -> None:
        self.db.add(
            Account(
                id=1,
                name="Bank",
                kind="bank",
                balance_yen=100000,
                user_id=1,
                effective_start_date=date(2026, 1, 1),
                effective_end_date=None,
            )
        )
        self.db.add(
            Card(
                name="EPOS",
                closing_day=15,
                payment_day=27,
                payment_account_id=1,
                effective_start_date=date(2020, 1, 1),
                effective_end_date=date(2026, 2, 21),
            )
        )
        self.db.commit()

        # This transaction belongs to Feb withdraw period (Jan16-Feb15),
        # but withdraw date is 2026-02-27 > effective_end_date, so it must be skipped.
        self.db.add(
            CardTransaction(
                card_id=1,
                date=date(2026, 2, 10),
                amount_yen=1200,
                merchant="Store",
            )
        )
        self.db.commit()

        events = build_card_withdraw_events(self.db, user_id=1, withdraw_y=2026, withdraw_m=2)
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
