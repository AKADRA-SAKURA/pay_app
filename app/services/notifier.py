import os
import requests
from datetime import date, timedelta
from sqlalchemy.orm import Session, joinedload

from app.db import SessionLocal
from app.models import CashflowEvent

def send_discord(message: str):
    url = os.getenv("DISCORD_WEBHOOK_URL")  # import時固定じゃなく毎回読む
    if not url:
        print("DISCORD_WEBHOOK_URL is not set. skip.")
        return

    r = requests.post(url, json={"content": message}, timeout=10)
    print("discord status:", r.status_code, "body:", r.text[:200])
    r.raise_for_status()

def notify_upcoming(days_before: int = 3):
    user_id = 1
    events = []
    today = date.today()
    target = today + timedelta(days=days_before)

    db: Session = SessionLocal()
    try:
        events = (
            db.query(CashflowEvent)
            .options(joinedload(CashflowEvent.plan))
            .filter(
                CashflowEvent.user_id == user_id,
                CashflowEvent.date >= today,
                CashflowEvent.date <= target,
                CashflowEvent.status == "expected",
            )
            .all()
        )

        if not events:
            return

        lines = [f"📅 **{target.isoformat()} の予定（{days_before}日前）**"]

        for e in events:
            sign = "➕" if e.amount_yen > 0 else "➖"
            title = e.plan.title if e.plan else f"plan_id={e.plan_id}"  # 念のため
            lines.append(f"{sign} {title}：{abs(e.amount_yen):,} 円")

        send_discord("\n".join(lines))

    finally:
        print("notify target:", target, "events:", len(events))
        db.close()
