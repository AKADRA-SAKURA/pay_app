# app/services/import_cards.py
from __future__ import annotations
import csv
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, date
from typing import Iterable

@dataclass
class ParsedTxn:
    occurred_on: date
    amount_yen: int          # 支出はマイナス
    merchant: str
    memo: str
    fingerprint: str
    raw_json: str

def _norm_merchant(s: str) -> str:
    s = (s or "").strip()
    s = s.replace("　", " ")
    s = re.sub(r"\s+", " ", s)
    s = s.lower()
    # よくある揺れ対策（必要に応じて増やす）
    s = re.sub(r"[^\wぁ-んァ-ン一-龥 ]+", "", s)  # 記号をざっくり落とす
    return s

def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _parse_date(s: str) -> date:
    s = (s or "").strip()
    # 例: 2026/02/01, 2026-02-01, 02/01(年なし) などは会社による
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"date parse failed: {s!r}")

def _parse_amount_yen(s: str) -> int:
    s = (s or "").strip()
    s = s.replace(",", "")
    if s == "":
        raise ValueError("amount is empty")
    # "1,234" / "-1234" / "1234" 想定
    v = int(float(s))
    return v

def parse_card_csv_bytes(content: bytes, *, encoding_candidates=("utf-8-sig", "cp932", "utf-8")) -> list[dict]:
    """
    1) bytes -> text (encoding候補で試す)
    2) csv.DictReaderで行取得
    戻りは生dict（ヘッダ名はCSV依存）
    """
    last_err = None
    for enc in encoding_candidates:
        try:
            text = content.decode(enc)
            reader = csv.DictReader(text.splitlines())
            return list(reader)
        except Exception as e:
            last_err = e
    raise ValueError(f"CSV decode failed: {last_err}")

def normalize_rows_to_txns(rows: list[dict], *, header_map: dict) -> list[ParsedTxn]:
    """
    header_map例:
      {"date":"利用日", "amount":"利用金額", "merchant":"利用先", "memo":"摘要"}
    """
    txns: list[ParsedTxn] = []
    for r in rows:
        occurred_on = _parse_date(r.get(header_map["date"], ""))
        amount = _parse_amount_yen(r.get(header_map["amount"], ""))
        merchant = (r.get(header_map["merchant"], "") or "").strip()
        memo = (r.get(header_map.get("memo", ""), "") or "").strip() if header_map.get("memo") else ""

        # 支出をマイナスに統一（カード明細MVPは基本支出のみ想定）
        if amount > 0:
            amount_yen = -amount
        else:
            amount_yen = amount

        fp_src = f"{occurred_on.isoformat()}|{amount_yen}|{_norm_merchant(merchant)}"
        fingerprint = _sha256_hex(fp_src)

        raw_json = json.dumps(r, ensure_ascii=False)

        txns.append(
            ParsedTxn(
                occurred_on=occurred_on,
                amount_yen=amount_yen,
                merchant=merchant,
                memo=memo,
                fingerprint=fingerprint,
                raw_json=raw_json,
            )
        )
    return txns
