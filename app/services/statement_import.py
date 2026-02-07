from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date, datetime

DATE_YMD_RE = re.compile(r"(?P<y>\d{4})[/-](?P<m>\d{1,2})[/-](?P<d>\d{1,2})")
DATE_MD_RE = re.compile(r"(?P<m>\d{1,2})[/-](?P<d>\d{1,2})")
MONEY_RE = re.compile(r"[+\-]?\s*[¥￥]?\s*\(?\d[\d,]*\)?\s*円?")

HEADER_FOOTER_RE = re.compile(
    r"^(利用日|請求日|ご利用明細|ご利用金額|合計|小計|ページ|page|お問い合わせ|カード番号)",
    re.IGNORECASE,
)


def normalize_text_line(line: str) -> str:
    s = unicodedata.normalize("NFKC", line or "")
    s = s.replace("\t", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_title(title: str) -> str:
    s = normalize_text_line(title)
    s = s.strip("-:/| ")
    return s or "不明"


def parse_flexible_date(value: str, *, default_year: int | None = None) -> date:
    s = normalize_text_line(value)
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass

    m = DATE_MD_RE.fullmatch(s)
    if m and default_year is not None:
        year = default_year
        return date(year, int(m.group("m")), int(m.group("d")))

    raise ValueError(f"invalid date: {value}")


def parse_money(value: str) -> int:
    s = normalize_text_line(value)
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
    if "-" in s:
        negative = True
    s = s.replace("(", "").replace(")", "")
    s = s.replace("¥", "").replace("￥", "").replace("円", "")
    s = s.replace(",", "").replace(" ", "")
    if s in ("", "+", "-"):
        raise ValueError(f"invalid amount: {value}")
    n = int(float(s))
    return -abs(n) if negative else n


def detect_payment_kind(text: str) -> str | None:
    s = normalize_text_line(text)
    if "分割" in s:
        return "分割"
    if "リボ" in s:
        return "リボ"
    if "1回" in s or "一回" in s:
        return "1回"
    return None


def _append_kind(title: str, kind: str | None) -> str:
    t = normalize_title(title)
    if kind and f"【{kind}】" not in t:
        t = f"{t}【{kind}】"
    return t


def _parse_csv_dict_rows(content: bytes) -> list[dict[str, str]]:
    last_err = None
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            text = content.decode(enc)
            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames:
                raise ValueError("CSV header is required")
            return [dict(r) for r in reader]
        except Exception as e:
            last_err = e
    raise ValueError(f"csv decode failed: {last_err}")


def _resolve_csv_headers(fieldnames: list[str]) -> dict[str, str]:
    m = {f.strip().lower(): f for f in fieldnames if f is not None}

    def pick(*cands: str) -> str | None:
        for c in cands:
            if c in m:
                return m[c]
        return None

    date_key = pick("yyyy/mm/dd", "date", "日付", "利用日")
    title_key = pick("title", "merchant", "加盟店", "摘要", "店名")
    price_key = pick("price", "amount", "金額", "利用金額")

    if not (date_key and title_key and price_key):
        raise ValueError("CSV headers must include date/title/price")

    return {"date": date_key, "title": title_key, "price": price_key}


def parse_card_csv_preview(content: bytes) -> tuple[list[dict], list[str], list[str]]:
    rows = _parse_csv_dict_rows(content)
    if not rows:
        return [], [], []

    try:
        h = _resolve_csv_headers(list(rows[0].keys()))
    except ValueError as e:
        return [], [], [str(e)]

    out: list[dict] = []
    warnings: list[str] = []
    errors: list[str] = []

    for i, r in enumerate(rows, start=2):
        row = {str(k).strip(): (v or "") for k, v in r.items()}
        if not any(v.strip() for v in row.values()):
            continue
        try:
            p = parse_money(row.get(h["price"], ""))
            title_raw = row.get(h["title"], "")
            kind = detect_payment_kind(title_raw)
            t = _append_kind(title_raw, kind)

            raw_date = normalize_text_line(row.get(h["date"], ""))
            md = DATE_MD_RE.fullmatch(raw_date)
            if md and not DATE_YMD_RE.fullmatch(raw_date):
                date_hint = f"{int(md.group('m')):02d}/{int(md.group('d')):02d}"
                warnings.append(f"line {i}: 年がない日付 {date_hint} です。プレビューで年を入力してください")
                out.append({"date": "", "date_hint": date_hint, "title": t, "price": p})
                continue

            d = parse_flexible_date(raw_date)
            out.append({"date": d.strftime("%Y/%m/%d"), "title": t, "price": p})
        except Exception as e:
            errors.append(f"line {i}: {e}")

    return out, warnings, errors


def _extract_date_from_line(
    line: str,
    *,
    default_year: int | None = None,
) -> tuple[str | None, tuple[int, int] | None, str | None]:
    m = DATE_YMD_RE.search(line)
    if m:
        d = date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        return d.strftime("%Y/%m/%d"), m.span(), None

    m2 = DATE_MD_RE.search(line)
    if m2:
        if default_year is None:
            hint = f"{int(m2.group('m')):02d}/{int(m2.group('d')):02d}"
            return "", m2.span(), hint
        d = date(default_year, int(m2.group("m")), int(m2.group("d")))
        return d.strftime("%Y/%m/%d"), m2.span(), None

    return None, None, None


def _extract_amount_from_line(line: str, *, skip_span: tuple[int, int] | None = None) -> tuple[int | None, tuple[int, int] | None]:
    candidates: list[tuple[int, tuple[int, int]]] = []
    for m in MONEY_RE.finditer(line):
        span = m.span()
        if skip_span and not (span[1] <= skip_span[0] or span[0] >= skip_span[1]):
            continue

        token = m.group(0)
        cleaned = token.replace(" ", "")
        # Date fragments noise guard.
        if ("," not in cleaned and "¥" not in cleaned and "￥" not in cleaned and "円" not in cleaned
                and not cleaned.startswith("-") and not cleaned.startswith("+") and not cleaned.startswith("(")
                and len(re.sub(r"\D", "", cleaned)) <= 2):
            continue
        try:
            amount = parse_money(token)
        except Exception:
            continue
        candidates.append((amount, span))

    if not candidates:
        return None, None
    return candidates[-1]


def parse_card_text_preview(text: str, *, default_year: int | None = None) -> tuple[list[dict], list[str], list[str]]:
    lines = [normalize_text_line(x) for x in (text or "").replace("\r\n", "\n").split("\n")]
    lines = [x for x in lines if x]

    rows: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []

    pending_date: str | None = None
    pending_date_hint: str | None = None
    pending_kind: str | None = None
    pending_title_parts: list[str] = []

    for idx, line in enumerate(lines, start=1):
        if HEADER_FOOTER_RE.search(line):
            continue

        d, d_span, d_hint = _extract_date_from_line(line, default_year=default_year)

        if pending_date is not None:
            amount, a_span = _extract_amount_from_line(line)
            if amount is not None and a_span is not None:
                if pending_kind is None:
                    pending_kind = detect_payment_kind(line)
                title_part = normalize_text_line((line[: a_span[0]] + " " + line[a_span[1] :]).strip())
                title = _append_kind(" ".join(pending_title_parts + [title_part]), pending_kind)
                item = {"date": pending_date, "title": title, "price": amount}
                if pending_date_hint:
                    item["date_hint"] = pending_date_hint
                rows.append(item)
                pending_date = None
                pending_date_hint = None
                pending_kind = None
                pending_title_parts = []
                continue

            if d is not None:
                warnings.append(f"line {idx-1}: date found but amount missing")
                pending_date = d
                pending_date_hint = d_hint
                pending_kind = detect_payment_kind(line)
                pending_title_parts = []
                continue

            if pending_kind is None:
                pending_kind = detect_payment_kind(line)
            pending_title_parts.append(line)
            continue

        if d is None:
            amount, _ = _extract_amount_from_line(line)
            if amount is not None:
                errors.append(f"line {idx}: 金額を検出しましたが日付が見つかりません")
            continue

        amount, a_span = _extract_amount_from_line(line, skip_span=d_span)
        kind = detect_payment_kind(line)

        if amount is None or a_span is None:
            pending_date = d
            pending_date_hint = d_hint
            pending_kind = kind
            if d_hint:
                warnings.append(f"line {idx}: 年がない日付 {d_hint} を検出しました。プレビューで年を入力してください")
            title_wo_date = normalize_text_line((line[: d_span[0]] + " " + line[d_span[1] :]).strip()) if d_span else ""
            pending_title_parts = [title_wo_date] if title_wo_date else []
            continue

        title = line
        if d_span:
            title = title[: d_span[0]] + " " + title[d_span[1] :]
        title = title[: a_span[0]] + " " + title[a_span[1] :]
        title = _append_kind(title, kind)

        item = {"date": d, "title": title, "price": amount}
        if d_hint:
            item["date_hint"] = d_hint
            warnings.append(f"line {idx}: 年がない日付 {d_hint} を検出しました。プレビューで年を入力してください")
        rows.append(item)

    if pending_date is not None:
        errors.append("最後の取引候補は金額を抽出できませんでした")

    if not rows and not errors:
        errors.append("明細行を抽出できませんでした")

    return rows, warnings, errors


def build_import_key(date_str: str, title: str, price: int, card_id: int) -> tuple[str, str, int, int]:
    d = parse_flexible_date(date_str)
    return (d.isoformat(), normalize_title(title), int(price), int(card_id))


def detect_duplicates(rows: list[dict], card_id: int, existing_keys: set[tuple[str, str, int, int]]) -> list[dict]:
    details: list[dict] = []
    seen: set[tuple[str, str, int, int]] = set()

    for i, r in enumerate(rows):
        try:
            key = build_import_key(str(r.get("date", "")), str(r.get("title", "")), int(r.get("price", 0)), card_id)
        except Exception:
            continue
        reason = None
        if key in existing_keys:
            reason = "existing"
        elif key in seen:
            reason = "payload"
        if reason:
            details.append({"index": i, "reason": reason, "date": key[0], "title": key[1], "price": key[2]})
        seen.add(key)

    return details
