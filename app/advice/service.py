# app/advice/service.py
from __future__ import annotations

import os
from datetime import date
from sqlalchemy.orm import Session

from app.advice.context import build_llm_payload_free, build_advice_context_free
from app.advice.rules import generate_advice_rules
from app.advice.llm_openai import generate_advice_openai
from app.utils.dates import month_range  # もし必要なら（下で使う）

_ADVICE_CACHE: dict[str, dict] = {}

def _payload_stats(payload: dict) -> dict:
    ws = payload.get("withdraw_schedule_next_60d") or []
    total = sum(int(x.get("amount_yen") or 0) for x in ws)
    return {
        "asof": payload.get("asof"),
        "free_this_end": payload.get("free_this_end"),
        "free_next_end": payload.get("free_next_end"),
        "withdraw_days": len(ws),
        "withdraw_total": total,
    }

def _llm_to_ui(j: dict, payload: dict) -> dict:
    bullets: list[str] = []
    bullets.append(f"今月評価: {j['this_month']['grade']} - {j['this_month']['comment']}")
    bullets.append(f"来月評価: {j['next_month']['grade']} - {j['next_month']['comment']}")
    bullets.extend(j.get("actions", []))
    bullets.extend(j.get("watchouts", []))

    return {
        "title": j.get("headline", "今日の一言"),
        "level": j["level"],
        "bullets": bullets[:6],
        "context": payload,  # 画面に出さず、デバッグ用に持つだけならOK
    }

def get_today_advice_llm_cached(db: Session, user_id: int) -> dict:
    key = f"{user_id}:{date.today().isoformat()}"
    if key in _ADVICE_CACHE:
        return _ADVICE_CACHE[key]

    payload = build_llm_payload_free(db, user_id=user_id)
    j = generate_advice_openai(payload)
    result = _llm_to_ui(j, payload)

    _ADVICE_CACHE[key] = result
    return result

def get_today_advice(db: Session, user_id: int) -> dict:
    mode = os.getenv("ADVICE_MODE", "rules").lower()

    if mode == "llm":
        try:
            return get_today_advice_llm_cached(db, user_id=user_id)
        except Exception as e:
            # 機密を出さず統計だけログ
            try:
                payload = build_llm_payload_free(db, user_id=user_id)
                print("[advice llm] failed:", type(e).__name__, str(e))
                print("[advice llm] payload_stats:", _payload_stats(payload))
            except Exception:
                print("[advice llm] failed and payload_stats failed too")

    # ---- rules fallback ----
    # rulesの方は start/end が必要なので、ここで期間を決める
    today = date.today()
    this_first = today.replace(day=1)

    # 来月末まで
    if this_first.month == 12:
        next_first = date(this_first.year + 1, 1, 1)
    else:
        next_first = date(this_first.year, this_first.month + 1, 1)
    _, next_last = month_range(next_first)

    ctx = build_advice_context_free(db, user_id=user_id, start=this_first, end=next_last)
    res = generate_advice_rules(ctx)
    return {"title": res.title, "level": res.level, "bullets": res.bullets, "context": {}}
