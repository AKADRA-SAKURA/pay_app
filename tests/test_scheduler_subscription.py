import unittest
from datetime import date

from app.models import Subscription
from app.services.scheduler import _subscription_occurrences_in_range


class SchedulerSubscriptionTests(unittest.TestCase):
    def test_weekly_interval_occurrences_stay_in_range(self) -> None:
        sub = Subscription(
            name="Weekly",
            amount_yen=1000,
            billing_day=5,
            freq="weekly_interval",
            interval_months=1,
            interval_weeks=1,
            billing_month=1,
            payment_method="bank",
            account_id=1,
            card_id=None,
        )
        start = date(2026, 2, 1)
        end = date(2026, 2, 28)
        dates = _subscription_occurrences_in_range(sub, start, end)

        self.assertGreaterEqual(len(dates), 4)
        self.assertEqual(sorted(dates), dates)
        self.assertEqual(len(set(dates)), len(dates))
        self.assertTrue(all(start <= d <= end for d in dates))

    def test_monthly_interval_every_two_months(self) -> None:
        sub = Subscription(
            name="Bi-monthly",
            amount_yen=2000,
            billing_day=10,
            freq="monthly_interval",
            interval_months=2,
            interval_weeks=1,
            billing_month=1,
            payment_method="bank",
            account_id=1,
            card_id=None,
        )
        feb = _subscription_occurrences_in_range(sub, date(2026, 2, 1), date(2026, 2, 28))
        mar = _subscription_occurrences_in_range(sub, date(2026, 3, 1), date(2026, 3, 31))

        self.assertEqual(feb, [])
        self.assertEqual(len(mar), 1)
        self.assertEqual(mar[0].month, 3)


if __name__ == "__main__":
    unittest.main()
