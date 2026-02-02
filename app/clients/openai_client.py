from __future__ import annotations
import os, json, time
from openai import OpenAI

class OpenAIClient:
    def __init__(self) -> None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        self.client = OpenAI(api_key=api_key)

    def call_json_schema(self, *, model: str, system: str, user: str, response_format: dict, max_retries: int = 2) -> dict:
        last_err: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                resp = self.client.responses.create(
                    model=model,
                    input=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format=response_format,
                    # SDK/環境によってtimeout指定の方法が違うことがあるので、
                    # 必要なら http_client を差し込む方式に発展させる（後でOK）
                )
                text = getattr(resp, "output_text", None)
                if not text:
                    raw = resp.model_dump()
                    text = raw.get("output_text") or ""
                return json.loads(text)

            except Exception as e:
                last_err = e
                # 簡易バックオフ（課金/瞬断対策）
                time.sleep(0.4 * (attempt + 1))

        raise last_err  # service側で握りつぶしてrulesにフォールバック
