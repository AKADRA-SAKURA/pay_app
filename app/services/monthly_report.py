from __future__ import annotations

import calendar
import math
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models import Account, Card, CardTransaction, CashflowEvent

SOURCE_LABELS = {
    "plan": "予定",
    "oneoff": "単発",
    "transfer": "振替",
    "card_tx": "カード利用",
}

METHOD_LABELS = {
    "card": "カード",
    "bank": "口座",
    "cash": "現金",
    "barcode": "バーコード",
    "emoney": "電子マネー",
    "nisa": "NISA",
    "transfer": "振替",
}


def parse_report_month(month: str) -> date:
    s = (month or "").strip()
    try:
        d = datetime.strptime(s, "%Y-%m").date()
    except ValueError:
        raise ValueError("month must be YYYY-MM")
    return date(d.year, d.month, 1)


def month_bounds(month_first: date) -> tuple[date, date]:
    last = date(month_first.year, month_first.month, calendar.monthrange(month_first.year, month_first.month)[1])
    return month_first, last


def _top_items(totals: dict[str, int], top_n: int = 8) -> list[dict]:
    pairs = sorted(((k, int(v)) for k, v in totals.items() if int(v) > 0), key=lambda x: x[1], reverse=True)
    if len(pairs) > top_n:
        head = pairs[: top_n - 1]
        rest = sum(v for _, v in pairs[top_n - 1 :])
        pairs = head + [("その他", rest)]
    total = sum(v for _, v in pairs) or 1
    return [{"label": k, "value": int(v), "ratio": round((v / total) * 100.0, 2)} for k, v in pairs]


def _clean_title(s: str | None) -> str:
    t = (s or "").strip()
    return t if t else "-"


def _is_account_active_on(account: Account, d: date) -> bool:
    start_d = getattr(account, "effective_start_date", None)
    end_d = getattr(account, "effective_end_date", None)
    if start_d and d < start_d:
        return False
    if end_d and d > end_d:
        return False
    return True


def build_monthly_payment_report(db: Session, user_id: int, month_first: date) -> dict:
    period_start, period_end = month_bounds(month_first)

    accounts = db.query(Account).filter(Account.user_id == int(user_id)).all()
    account_by_id = {int(a.id): a for a in accounts}
    start_balance_yen = sum(
        int(a.balance_yen or 0)
        for a in accounts
        if _is_account_active_on(a, period_start)
    )

    card_by_id = {int(c.id): c for c in db.query(Card).all()}

    rows: list[dict] = []

    # Add both expense and income events to list view.
    events = (
        db.query(CashflowEvent)
        .filter(CashflowEvent.user_id == int(user_id))
        .filter(CashflowEvent.date >= period_start, CashflowEvent.date <= period_end)
        .filter(CashflowEvent.amount_yen != 0)
        .filter(CashflowEvent.source.in_(["plan", "oneoff", "transfer"]))
        .order_by(CashflowEvent.date.asc(), CashflowEvent.id.asc())
        .all()
    )
    for e in events:
        amount = int(e.amount_yen or 0)
        source = str(e.source or "")
        acc = account_by_id.get(int(e.account_id)) if e.account_id is not None else None

        if source == "transfer":
            method_key = "transfer"
        else:
            method_key = str(getattr(acc, "kind", "bank") or "bank")
        method_label = METHOD_LABELS.get(method_key, method_key)

        title = _clean_title(e.description or SOURCE_LABELS.get(source, source))
        rows.append(
            {
                "date": e.date.isoformat(),
                "source": source,
                "source_label": SOURCE_LABELS.get(source, source or "-"),
                "title": title,
                "store": title,
                "payment_method": method_key,
                "payment_method_label": method_label,
                "amount_yen": amount,
            }
        )

    # Card usage details by usage date.
    tx_rows = (
        db.query(CardTransaction)
        .filter(CardTransaction.date >= period_start, CardTransaction.date <= period_end)
        .filter(CardTransaction.amount_yen != 0)
        .order_by(CardTransaction.date.asc(), CardTransaction.id.asc())
        .all()
    )
    for t in tx_rows:
        raw = int(t.amount_yen or 0)
        if raw == 0:
            continue
        # card tx positive means expense, negative means refund/income
        signed_amount = -abs(raw) if raw > 0 else abs(raw)

        card_name = _clean_title(getattr(card_by_id.get(int(t.card_id)), "name", None))
        merchant = _clean_title(t.merchant)
        rows.append(
            {
                "date": t.date.isoformat(),
                "source": "card_tx",
                "source_label": SOURCE_LABELS["card_tx"],
                "title": f"{card_name} / {merchant}",
                "store": merchant,
                "payment_method": "card",
                "payment_method_label": METHOD_LABELS["card"],
                "amount_yen": signed_amount,
            }
        )

    rows.sort(key=lambda r: (str(r["date"]), str(r["source_label"]), str(r["title"])))

    income_total_yen = sum(int(r["amount_yen"]) for r in rows if int(r["amount_yen"]) > 0)
    expense_total_yen = sum(abs(int(r["amount_yen"])) for r in rows if int(r["amount_yen"]) < 0)
    net_cashflow_yen = int(income_total_yen - expense_total_yen)
    free_money_yen = int(start_balance_yen + net_cashflow_yen)

    # Pie charts are expense-only analysis.
    expense_rows = [r for r in rows if int(r["amount_yen"]) < 0]

    by_store: dict[str, int] = {}
    by_method: dict[str, int] = {}
    by_method_store: dict[str, dict[str, int]] = {}

    for r in expense_rows:
        val = abs(int(r["amount_yen"]))
        store = str(r.get("store") or "-")
        method = str(r.get("payment_method_label") or "-")

        by_store[store] = by_store.get(store, 0) + val
        by_method[method] = by_method.get(method, 0) + val

        m = by_method_store.setdefault(method, {})
        m[store] = m.get(store, 0) + val

    method_store_pies = []
    for method_label, totals in sorted(by_method_store.items(), key=lambda kv: sum(kv[1].values()), reverse=True):
        method_store_pies.append(
            {
                "method": method_label,
                "total_yen": int(sum(totals.values())),
                "items": _top_items(totals, top_n=8),
            }
        )

    expense_store_items = _top_items(by_store, top_n=8)
    method_items = _top_items(by_method, top_n=8)

    return {
        "month": month_first.strftime("%Y-%m"),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "start_balance_yen": int(start_balance_yen),
        "income_total_yen": int(income_total_yen),
        "expense_total_yen": int(expense_total_yen),
        "net_cashflow_yen": int(net_cashflow_yen),
        "free_money_yen": int(free_money_yen),
        "total_yen": int(expense_total_yen),
        "row_count": len(rows),
        "rows": rows,
        "expense_store_pie_items": expense_store_items,
        "method_pie_items": method_items,
        "method_store_pies": method_store_pies,
        # backward compatibility keys
        "pie_items": expense_store_items,
        "source_items": method_items,
    }


def _pdf_hex_text(s: str, limit: int = 64) -> str:
    x = (s or "").replace("\r", " ").replace("\n", " ").strip()
    if len(x) > limit:
        x = x[: limit - 3] + "..."
    return x.encode("utf-16-be").hex().upper()


def _draw_text(x: float, y: float, size: int, text: str) -> str:
    return f"BT /F1 {int(size)} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm <{_pdf_hex_text(text)}> Tj ET\n"


def _build_pdf(content_stream: str) -> bytes:
    stream_bytes = content_stream.encode("ascii")
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 6 0 R >>",
        "<< /Type /Font /Subtype /Type0 /BaseFont /HeiseiKakuGo-W5 /Encoding /UniJIS-UCS2-H /DescendantFonts [5 0 R] >>",
        "<< /Type /Font /Subtype /CIDFontType0 /BaseFont /HeiseiKakuGo-W5 /CIDSystemInfo << /Registry (Adobe) /Ordering (Japan1) /Supplement 5 >> >>",
        f"<< /Length {len(stream_bytes)} >>\nstream\n{content_stream}endstream",
    ]

    out = bytearray(b"%PDF-1.4\n%\xE2\xE3\xCF\xD3\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n{obj}\nendobj\n".encode("ascii"))

    xref_at = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))

    out.extend(
        (
            f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n"
            "%%EOF\n"
        ).encode("ascii")
    )
    return bytes(out)


def _draw_pie(cmds: list[str], items: list[dict], cx: float, cy: float, r: float, legend_x: float, legend_y: float) -> None:
    palette = [
        (0.20, 0.60, 0.86),
        (0.18, 0.80, 0.44),
        (0.95, 0.61, 0.07),
        (0.91, 0.30, 0.24),
        (0.61, 0.35, 0.71),
        (0.11, 0.74, 0.61),
        (0.90, 0.49, 0.13),
        (0.58, 0.65, 0.65),
    ]
    total = sum(int(x.get("value", 0)) for x in items)
    if total <= 0:
        cmds.append(_draw_text(cx - r, cy, 9, "データなし"))
        return

    angle = -math.pi / 2.0
    for i, item in enumerate(items):
        v = int(item.get("value", 0))
        if v <= 0:
            continue
        frac = v / total
        next_angle = angle + (2.0 * math.pi * frac)
        seg = max(3, int(48 * frac))
        rgb = palette[i % len(palette)]
        cmds.append(f"{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} rg\n")
        cmds.append(f"{cx:.2f} {cy:.2f} m\n")
        for j in range(seg + 1):
            a = angle + (next_angle - angle) * (j / seg)
            x = cx + r * math.cos(a)
            y = cy + r * math.sin(a)
            cmds.append(f"{x:.2f} {y:.2f} l\n")
        cmds.append("h f\n")
        angle = next_angle

    for i, item in enumerate(items[:6]):
        rgb = palette[i % len(palette)]
        x0, y0 = legend_x, legend_y - (i * 12.0)
        cmds.append(f"{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} rg\n")
        cmds.append(f"{x0:.2f} {y0:.2f} 6 6 re f\n")
        cmds.append("0 0 0 rg\n")
        cmds.append(_draw_text(x0 + 10.0, y0, 7, f"{item.get('label', '-')}: {int(item.get('value', 0))}"))


def render_monthly_report_pdf(report: dict) -> bytes:
    cmds: list[str] = []
    cmds.append("0 0 0 rg\n")
    cmds.append(_draw_text(40, 810, 16, f"月次明細レポート - {report.get('month', '-')}"))
    cmds.append(_draw_text(40, 794, 9, f"期間: {report.get('period_start', '')} 〜 {report.get('period_end', '')}"))

    cmds.append(_draw_text(40, 772, 12, f"この月に自由に使えるお金: ¥{int(report.get('free_money_yen', 0))}"))
    cmds.append(_draw_text(40, 756, 9, f"開始残高: ¥{int(report.get('start_balance_yen', 0))}"))
    cmds.append(_draw_text(200, 756, 9, f"収入合計: ¥{int(report.get('income_total_yen', 0))}"))
    cmds.append(_draw_text(330, 756, 9, f"支出合計: ¥{int(report.get('expense_total_yen', 0))}"))
    cmds.append(_draw_text(460, 756, 9, f"件数: {int(report.get('row_count', 0))}"))

    cmds.append(_draw_text(40, 734, 9, "円グラフ: 店舗割合（支出）"))
    _draw_pie(cmds, list(report.get("expense_store_pie_items") or []), 130.0, 650.0, 55.0, 190.0, 700.0)

    cmds.append(_draw_text(330, 734, 9, "円グラフ: 支払い方法割合（支出）"))
    _draw_pie(cmds, list(report.get("method_pie_items") or []), 420.0, 650.0, 55.0, 480.0, 700.0)

    y = 540.0
    cmds.append(_draw_text(40, y, 10, "具体的なリスト"))
    y -= 14.0
    cmds.append(_draw_text(40, y, 8, "日付"))
    cmds.append(_draw_text(100, y, 8, "種別"))
    cmds.append(_draw_text(160, y, 8, "支払方法"))
    cmds.append(_draw_text(240, y, 8, "内容"))
    cmds.append(_draw_text(505, y, 8, "金額"))
    y -= 12.0

    rows = list(report.get("rows") or [])
    limit = 34
    for row in rows[:limit]:
        amount = int(row.get("amount_yen", 0))
        sign = "+" if amount > 0 else ""
        cmds.append(_draw_text(40, y, 8, str(row.get("date", ""))))
        cmds.append(_draw_text(100, y, 8, str(row.get("source_label", ""))))
        cmds.append(_draw_text(160, y, 8, str(row.get("payment_method_label", ""))))
        cmds.append(_draw_text(240, y, 8, str(row.get("title", ""))))
        cmds.append(_draw_text(505, y, 8, f"{sign}{amount}"))
        y -= 11.0
        if y < 42:
            break

    if len(rows) > limit:
        cmds.append(_draw_text(40, 30, 8, f"... 省略 {len(rows) - limit} 件"))

    return _build_pdf("".join(cmds))
