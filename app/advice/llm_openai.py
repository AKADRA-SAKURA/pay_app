# app/advice/llm_openai.py
from __future__ import annotations

import json
import re
import os
import time
from typing import Any, Dict, Literal
from openai import OpenAI

AdviceLevel = Literal["info", "warn", "danger"]

ADVICE_JSON_SCHEMA: Dict[str, Any] = {
    "name": "budget_advice_v1",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "level": {"type": "string", "enum": ["info", "warn", "danger"]},
            "headline": {"type": "string", "maxLength": 40},
            "this_month": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "grade": {"type": "string", "enum": ["A", "B", "C", "D"]},
                    "comment": {"type": "string", "maxLength": 120},
                },
                "required": ["grade", "comment"],
            },
            "next_month": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "grade": {"type": "string", "enum": ["A", "B", "C", "D"]},
                    "comment": {"type": "string", "maxLength": 120},
                },
                "required": ["grade", "comment"],
            },
            "actions": {
                "type": "array",
                "items": {"type": "string", "maxLength": 80},
                "minItems": 1,
                "maxItems": 3,
            },
            "watchouts": {
                "type": "array",
                "items": {"type": "string", "maxLength": 80},
                "minItems": 0,
                "maxItems": 2,
            },
        },
        "required": ["level", "headline", "this_month", "next_month", "actions", "watchouts"],
    },
}

def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)

def _parse_json_text(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise
        return json.loads(m.group(0))

def _extract_json(resp: Any) -> dict:
    # SDK差分吸収
    out_text = getattr(resp, "output_text", None)
    if out_text:
        return _parse_json_text(out_text)

    raw = resp.model_dump()
    text = raw.get("output_text") or ""
    return _parse_json_text(text)

def generate_advice_openai(context: Dict[str, Any], *, max_retries: int = 2) -> Dict[str, Any]:
    """
    context: 匿名化済みの数値だけ
    """
    system_instructions = (
        "あなたは家計管理アドバイザーです。"
        "個人情報・口座名・店名・取引名を推測/要求しないでください。"
        "与えられた数値のみで、現実的で安全な一般的助言を返してください。"
        "投資助言は一般論に留め、特定商品の推奨はしないでください。"
        "JSONスキーマに厳密に従って出力してください。"
    )

    user_prompt = {
        "task": "今月と来月の『自由に使えるお金』を評価し、今後の過ごし方を提案して下さい。",
        "data": context,
        "constraints": {
            "output_language": "ja",
            "max_actions": 3,
            "max_watchouts": 2,
            "be_concise": True,
        },
    }

    client = _get_client()
    last_err: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            resp = client.responses.create(
                model="gpt-4.1-mini",
                input=[
                    {"role": "system", "content": system_instructions},
                    {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": ADVICE_JSON_SCHEMA,
                },
            )
            return _extract_json(resp)

        except TypeError as e:
            if "response_format" not in str(e):
                last_err = e
                time.sleep(0.4 * (attempt + 1))
                continue
            try:
                resp = client.responses.create(
                    model="gpt-4.1-mini",
                    input=[
                        {"role": "system", "content": system_instructions + "\n?????JSON???"},
                        {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
                    ],
                )
                return _extract_json(resp)
            except Exception as e2:
                last_err = e2
                time.sleep(0.4 * (attempt + 1))

        except Exception as e:
            last_err = e
            time.sleep(0.4 * (attempt + 1))

    raise last_err
