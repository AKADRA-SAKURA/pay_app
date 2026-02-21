(function () {
  let charts = [];

  function qs(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function yen(v) {
    const n = Number(v || 0);
    return Number.isFinite(n) ? n.toLocaleString("ja-JP") : "0";
  }

  function signedYen(v) {
    const n = Number(v || 0);
    if (!Number.isFinite(n)) return "¥0";
    if (n > 0) return `+¥${yen(n)}`;
    if (n < 0) return `-¥${yen(Math.abs(n))}`;
    return "¥0";
  }

  function clearCharts() {
    charts.forEach((c) => {
      try {
        c.destroy();
      } catch (_) {}
    });
    charts = [];
  }

  function clearPreview() {
    const wrap = qs("monthly-report-preview");
    const body = qs("monthly-report-table-body");
    const free = qs("monthly-report-free-money");
    const methodStoreWrap = qs("monthly-report-method-store-pies");
    const dl = qs("monthly-report-download-link");

    if (wrap) wrap.hidden = true;
    if (body) body.innerHTML = "";
    if (free) free.innerHTML = "";
    if (methodStoreWrap) methodStoreWrap.innerHTML = "";
    if (dl) {
      dl.hidden = true;
      dl.setAttribute("href", "#");
    }
    clearCharts();
  }

  function createPie(canvas, items) {
    if (!canvas || typeof Chart === "undefined") return null;

    const labels = (items || []).map((x) => String(x.label || "-"));
    const values = (items || []).map((x) => Number(x.value || 0));
    const colors = [
      "#3399db",
      "#2ecc71",
      "#f39c12",
      "#e74c3c",
      "#8e44ad",
      "#1abc9c",
      "#d35400",
      "#7f8c8d",
    ];

    return new Chart(canvas.getContext("2d"), {
      type: "pie",
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: colors }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  function renderPieSeries(data) {
    const storeCanvas = qs("monthly-report-store-pie");
    const methodCanvas = qs("monthly-report-method-pie");
    const methodStoreWrap = qs("monthly-report-method-store-pies");

    const c1 = createPie(storeCanvas, data.expense_store_pie_items || []);
    const c2 = createPie(methodCanvas, data.method_pie_items || []);
    if (c1) charts.push(c1);
    if (c2) charts.push(c2);

    if (!methodStoreWrap) return;
    methodStoreWrap.innerHTML = "";

    const pies = Array.isArray(data.method_store_pies) ? data.method_store_pies : [];
    if (!pies.length) {
      methodStoreWrap.innerHTML = '<div class="hint">支払い方法別の内訳データがありません。</div>';
      return;
    }

    pies.forEach((p, idx) => {
      const block = document.createElement("div");
      block.className = "card";
      block.style.marginTop = "8px";

      const title = document.createElement("h4");
      title.style.margin = "0 0 6px";
      title.textContent = `支払い方法: ${String(p.method || "-")} (合計 ¥${yen(p.total_yen)})`;
      block.appendChild(title);

      const wrap = document.createElement("div");
      wrap.className = "chart-wrap pie-wrap";
      wrap.style.maxWidth = "460px";
      wrap.style.margin = "0 auto";

      const canvas = document.createElement("canvas");
      canvas.id = `monthly-report-method-store-pie-${idx}`;
      wrap.appendChild(canvas);
      block.appendChild(wrap);
      methodStoreWrap.appendChild(block);

      const chart = createPie(canvas, p.items || []);
      if (chart) charts.push(chart);
    });
  }

  function renderRows(rows) {
    const body = qs("monthly-report-table-body");
    if (!body) return;
    body.innerHTML = "";

    if (!rows || !rows.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty">該当データがありません。</td></tr>';
      return;
    }

    rows.forEach((r) => {
      const amt = Number(r.amount_yen || 0);
      const cls = amt > 0 ? "money pos" : amt < 0 ? "money neg" : "money";
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${esc(r.date)}</td>
        <td>${esc(r.source_label)}</td>
        <td>${esc(r.payment_method_label)}</td>
        <td>${esc(r.title)}</td>
        <td class="${cls}">${esc(signedYen(amt))}</td>
      `;
      body.appendChild(tr);
    });
  }

  function renderFreeMoney(data) {
    const free = qs("monthly-report-free-money");
    if (!free) return;
    free.innerHTML = `
      <div><strong>自由に使えるお金:</strong> ¥${yen(data.free_money_yen)}</div>
      <div>開始残高: ¥${yen(data.start_balance_yen)} / 収入合計: ¥${yen(data.income_total_yen)} / 支出合計: ¥${yen(data.expense_total_yen)} / 月間収支: ${esc(signedYen(data.net_cashflow_yen))}</div>
    `;
  }

  async function previewMonthlyReport() {
    const month = String(qs("monthly-report-month")?.value || "").trim();
    if (!month) {
      window.alert("対象月を入力してください。");
      return;
    }

    const res = await fetch(`/api/reports/monthly?month=${encodeURIComponent(month)}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      window.alert(String(data.detail || "プレビュー取得に失敗しました。"));
      return;
    }

    renderFreeMoney(data);
    renderPieSeries(data);
    renderRows(data.rows || []);

    const dl = qs("monthly-report-download-link");
    if (dl) {
      dl.hidden = false;
      dl.setAttribute("href", `/reports/monthly/pdf?month=${encodeURIComponent(month)}`);
      dl.setAttribute("download", `monthly_report_${month}.pdf`);
    }

    const wrap = qs("monthly-report-preview");
    if (wrap) wrap.hidden = false;
  }

  document.addEventListener("click", async (ev) => {
    const t = ev.target;
    if (!(t instanceof HTMLElement)) return;

    if (t.id === "monthly-report-preview-btn") {
      ev.preventDefault();
      try {
        await previewMonthlyReport();
      } catch (e) {
        window.alert(String(e?.message || e));
      }
      return;
    }

    if (t.matches('[data-overlay-open="monthly-report-form-template"]')) {
      window.setTimeout(() => {
        clearPreview();
        const monthInput = qs("monthly-report-month");
        if (monthInput && !monthInput.value) {
          const now = new Date();
          const y = now.getFullYear();
          const m = String(now.getMonth() + 1).padStart(2, "0");
          monthInput.value = `${y}-${m}`;
        }
      }, 0);
    }
  });
})();
