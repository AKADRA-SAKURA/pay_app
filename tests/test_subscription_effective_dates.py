import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Subscription
from app.services.scheduler import build_month_subscription_events


class SubscriptionEffectiveDateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_bank_subscription_is_created_within_effective_range(self) -> None:
        self.db.add(
            Subscription(
                name="Video",
                amount_yen=1200,
                billing_day=5,
                freq="monthly",
                interval_months=1,
                interval_weeks=1,
                billing_month=1,
                payment_method="bank",
                account_id=1,
                card_id=None,
                effective_start_date=date(2026, 2, 1),
                effective_end_date=date(2026, 2, 28),
            )
        )
        self.db.commit()

        events = build_month_subscription_events(self.db, user_id=1, month_first=date(2026, 2, 1))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].date, date(2026, 2, 5))
        self.assertEqual(int(events[0].amount_yen), -1200)

    def test_bank_subscription_is_skipped_outside_effective_range(self) -> None:
        self.db.add(
            Subscription(
                name="Music",
                amount_yen=800,
                billing_day=5,
                freq="monthly",
                interval_months=1,
                interval_weeks=1,
                billing_month=1,
                payment_method="bank",
                account_id=1,
                card_id=None,
                effective_start_date=date(2026, 2, 10),
                effective_end_date=date(2026, 2, 20),
            )
        )
        self.db.commit()

        # Feb billing day is before effective_start_date.
        feb_events = build_month_subscription_events(self.db, user_id=1, month_first=date(2026, 2, 1))
        self.assertEqual(feb_events, [])

        # March is after effective_end_date.
        mar_events = build_month_subscription_events(self.db, user_id=1, month_first=date(2026, 3, 1))
        self.assertEqual(mar_events, [])


if __name__ == "__main__":
    unittest.main()
