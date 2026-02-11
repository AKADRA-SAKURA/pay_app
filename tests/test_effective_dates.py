import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Account
from app.services.forecast import forecast_by_account_daily


class EffectiveDateForecastTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_account_balance_is_applied_on_effective_start_date(self) -> None:
        self.db.add(
            Account(
                name="Future Account",
                kind="bank",
                balance_yen=1000,
                user_id=1,
                effective_start_date=date(2026, 2, 10),
                effective_end_date=None,
            )
        )
        self.db.commit()

        out = forecast_by_account_daily(
            self.db,
            user_id=1,
            start=date(2026, 2, 1),
            end=date(2026, 2, 12),
        )

        total_map = {str(p["date"]): int(p["balance_yen"]) for p in out["total_series"]}
        self.assertEqual(total_map["2026-02-09"], 0)
        self.assertEqual(total_map["2026-02-10"], 1000)

    def test_account_balance_becomes_zero_after_effective_end_date(self) -> None:
        self.db.add(
            Account(
                name="Expired Account",
                kind="bank",
                balance_yen=2000,
                user_id=1,
                effective_start_date=date(2026, 2, 1),
                effective_end_date=date(2026, 2, 5),
            )
        )
        self.db.commit()

        out = forecast_by_account_daily(
            self.db,
            user_id=1,
            start=date(2026, 2, 1),
            end=date(2026, 2, 7),
        )

        total_map = {str(p["date"]): int(p["balance_yen"]) for p in out["total_series"]}
        self.assertEqual(total_map["2026-02-05"], 2000)
        self.assertEqual(total_map["2026-02-06"], 0)


if __name__ == "__main__":
    unittest.main()
