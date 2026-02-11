from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date, datetime

DATE_YMD_RE = re.compile(r"(?P<y>\d{4})[/-](?P<m>\d{1,2})[/-](?P<d>\d{1,2})")
DATE_MD_RE = re.compile(r"(?P<m>\d{1,2})[/-](?P<d>\d{1,2})")
DATE_JP_YMD_RE = re.compile(r"(?P<y>\d{4})\s*年\s*(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日")
DATE_JP_MD_RE = re.compile(r"(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日")
MONEY_RE = re.compile(r"(?:[+\-](?:[¥￥]\s*)?\(?\d[\d,]*\)?|[¥￥]?\s*\(?\d[\d,]*\)?)\s*円?")

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

    m_jp = DATE_JP_YMD_RE.fullmatch(s)
    if m_jp:
        return date(int(m_jp.group("y")), int(m_jp.group("m")), int(m_jp.group("d")))

    m = DATE_MD_RE.fullmatch(s)
    if m and default_year is not None:
        year = default_year
        return date(year, int(m.group("m")), int(m.group("d")))

    m_jp_md = DATE_JP_MD_RE.fullmatch(s)
    if m_jp_md and default_year is not None:
        year = default_year
        return date(year, int(m_jp_md.group("m")), int(m_jp_md.group("d")))

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

    date_key = pick("yyyy/mm/dd", "date", "日付", "利用日", "ご利用年月日", "ご利用日")
    title_key = pick("title", "merchant", "加盟店", "摘要", "店名", "ご利用場所", "利用場所")
    price_key = pick("price", "amount", "金額", "利用金額", "ご利用金額")

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
    today_str = date.today().strftime("%Y/%m/%d")

    for i, r in enumerate(rows, start=2):
        row = {str(k).strip(): (v or "") for k, v in r.items()}
        if not any(v.strip() for v in row.values()):
            continue
        try:
            p = parse_money(row.get(h["price"], ""))
            title_raw = row.get(h["title"], "")
            kind = detect_payment_kind(title_raw)
            if kind == "リボ":
                warnings.append(f"line {i}: リボ明細をスキップしました")
                continue
            t = _append_kind(title_raw, kind)

            raw_date = normalize_text_line(row.get(h["date"], ""))
            md = DATE_MD_RE.fullmatch(raw_date)
            if md and not DATE_YMD_RE.fullmatch(raw_date):
                date_hint = f"{int(md.group('m')):02d}/{int(md.group('d')):02d}"
                warnings.append(f"line {i}: 年がない日付 {date_hint} のため、今日 {today_str} を仮入力しました")
                out.append({"date": today_str, "date_hint": date_hint, "title": t, "price": p})
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

    m_jp = DATE_JP_YMD_RE.search(line)
    if m_jp:
        d = date(int(m_jp.group("y")), int(m_jp.group("m")), int(m_jp.group("d")))
        return d.strftime("%Y/%m/%d"), m_jp.span(), None

    m2 = DATE_MD_RE.search(line)
    if m2:
        if default_year is None:
            hint = f"{int(m2.group('m')):02d}/{int(m2.group('d')):02d}"
            today_str = date.today().strftime("%Y/%m/%d")
            return today_str, m2.span(), hint
        d = date(default_year, int(m2.group("m")), int(m2.group("d")))
        return d.strftime("%Y/%m/%d"), m2.span(), None

    m2_jp = DATE_JP_MD_RE.search(line)
    if m2_jp:
        if default_year is None:
            hint = f"{int(m2_jp.group('m')):02d}/{int(m2_jp.group('d')):02d}"
            today_str = date.today().strftime("%Y/%m/%d")
            return today_str, m2_jp.span(), hint
        d = date(default_year, int(m2_jp.group("m")), int(m2_jp.group("d")))
        return d.strftime("%Y/%m/%d"), m2_jp.span(), None

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


def _is_placeholder_cell(value: str) -> bool:
    s = normalize_text_line(value)
    return s in ("", "-", "－", "ー", "―", "ｰ", "—", "−")


def _clean_title_candidate(value: str) -> str:
    parts = [p for p in normalize_text_line(value).split(" ") if not _is_placeholder_cell(p)]
    return normalize_title(" ".join(parts))


def _remove_spans(line: str, spans: list[tuple[int, int] | None]) -> str:
    text = line
    valid_spans = [s for s in spans if s is not None]
    for start, end in sorted(valid_spans, key=lambda x: x[0], reverse=True):
        text = text[:start] + " " + text[end:]
    return text


def _parse_tabular_statement_line(
    raw_line: str,
    *,
    default_year: int | None = None,
) -> tuple[dict | None, str | None]:
    if "\t" not in raw_line:
        return None, None

    cells = [normalize_text_line(c) for c in raw_line.split("\t")]
    if not cells:
        return None, None

    date_cell = cells[0] if len(cells) >= 1 else ""
    place_cell = cells[1] if len(cells) >= 2 else ""
    usage_cell = cells[2] if len(cells) >= 3 else ""
    amount_cell = cells[3] if len(cells) >= 4 else (cells[-1] if cells else "")

    # header line
    if (
        "ご利用年月日" in date_cell
        or "ご利用場所" in place_cell
        or "ご利用金額" in amount_cell
        or HEADER_FOOTER_RE.search(date_cell)
    ):
        return {}, None

    if not date_cell:
        return None, None

    d_hint: str | None = None
    try:
        d = parse_flexible_date(date_cell)
        date_str = d.strftime("%Y/%m/%d")
    except Exception:
        date_str, _, d_hint = _extract_date_from_line(date_cell, default_year=default_year)
        if date_str is None:
            return None, None

    amount, _ = _extract_amount_from_line(amount_cell)
    if amount is None:
        try:
            amount = parse_money(amount_cell)
        except Exception:
            return None, None

    kind = detect_payment_kind(f"{place_cell} {usage_cell}")
    if kind == "リボ":
        return {
            "skip": True,
            "warning": "リボ明細をスキップしました",
        }, None

    title_src = place_cell if not _is_placeholder_cell(place_cell) else usage_cell
    title = _append_kind(_clean_title_candidate(title_src), kind)

    item: dict = {"date": date_str, "title": title, "price": amount}
    if d_hint:
        item["date_hint"] = d_hint
    return item, d_hint


def parse_card_text_preview(text: str, *, default_year: int | None = None) -> tuple[list[dict], list[str], list[str]]:
    raw_lines = [(x or "").strip() for x in (text or "").replace("\r\n", "\n").split("\n")]
    raw_lines = [x for x in raw_lines if x]

    rows: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []

    pending_date: str | None = None
    pending_date_hint: str | None = None
    pending_kind: str | None = None
    pending_title_parts: list[str] = []

    for idx, raw_line in enumerate(raw_lines, start=1):
        tab_row, tab_hint = _parse_tabular_statement_line(raw_line, default_year=default_year)
        if tab_row is not None:
            if tab_row.get("skip"):
                warnings.append(f"line {idx}: {tab_row.get('warning')}")
                continue
            if tab_row:
                if tab_hint:
                    warnings.append(
                        f"line {idx}: 年がない日付 {tab_hint} のため、今日 {tab_row.get('date')} を仮入力しました。プレビューで編集してください"
                    )
                rows.append(tab_row)
                continue

        line = normalize_text_line(raw_line)
        if HEADER_FOOTER_RE.search(line):
            continue

        d, d_span, d_hint = _extract_date_from_line(line, default_year=default_year)

        if pending_date is not None:
            amount, a_span = _extract_amount_from_line(line)
            if amount is not None and a_span is not None:
                line_kind = detect_payment_kind(line)
                if pending_kind is None:
                    pending_kind = line_kind
                if pending_kind == "リボ":
                    warnings.append(f"line {idx}: リボ明細をスキップしました")
                    pending_date = None
                    pending_date_hint = None
                    pending_kind = None
                    pending_title_parts = []
                    continue
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
                if detect_payment_kind(line) == "リボ":
                    warnings.append(f"line {idx}: リボ明細をスキップしました")
                    continue
                errors.append(f"line {idx}: 金額を検出しましたが日付が見つかりません")
            continue

        amount, a_span = _extract_amount_from_line(line, skip_span=d_span)
        kind = detect_payment_kind(line)

        if amount is None or a_span is None:
            pending_date = d
            pending_date_hint = d_hint
            pending_kind = kind
            if d_hint:
                warnings.append(
                    f"line {idx}: 年がない日付 {d_hint} のため、今日 {d} を仮入力しました。プレビューで編集してください"
                )
            title_wo_date = normalize_text_line((line[: d_span[0]] + " " + line[d_span[1] :]).strip()) if d_span else ""
            pending_title_parts = [title_wo_date] if title_wo_date else []
            continue

        if kind == "リボ":
            warnings.append(f"line {idx}: リボ明細をスキップしました")
            continue

        title = _clean_title_candidate(_remove_spans(line, [d_span, a_span]))
        title = _append_kind(title, kind)

        item = {"date": d, "title": title, "price": amount}
        if d_hint:
            item["date_hint"] = d_hint
            warnings.append(
                f"line {idx}: 年がない日付 {d_hint} のため、今日 {d} を仮入力しました。プレビューで編集してください"
            )
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
