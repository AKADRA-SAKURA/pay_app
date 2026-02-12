(function () {
  function toNumber(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }

  async function fetchMerchantPie(cardId, month) {
    const q = new URLSearchParams({
      card_id: String(cardId),
      withdraw_month: String(month),
    });
    const r = await fetch(`/api/cards/merchant-pie?${q.toString()}`);
    if (!r.ok) {
      const t = await r.text();
      throw new Error(`request failed: ${r.status} ${t}`);
    }
    return r.json();
  }

  function buildMetaText(data) {
    const total = toNumber(data.total_yen).toLocaleString();
    let base = `${data.card_name} / withdraw ${data.withdraw_date} / period ${data.analyzed_start} - ${data.analyzed_end} / total ${total} JPY`;
    if (data.analyzed_start !== data.period_start || data.analyzed_end !== data.period_end) {
      base += " (filtered by effective range)";
    }
    return base;
  }

  function renderTable(tbody, items) {
    if (!tbody) return;
    tbody.innerHTML = "";
    items.forEach((x) => {
      const tr = document.createElement("tr");
      const label = String(x.label || "-");
      const value = toNumber(x.value).toLocaleString();
      const ratio = `${toNumber(x.ratio).toFixed(2)}%`;
      tr.innerHTML = `<td>${label}</td><td class="money">${value}</td><td>${ratio}</td>`;
      tbody.appendChild(tr);
    });
  }

  function renderChart(canvas, items) {
    if (!canvas) return;
    const labels = items.map((x) => String(x.label || "-"));
    const values = items.map((x) => toNumber(x.value));
    const colors = [
      "#2fb7a7",
      "#6fd3c2",
      "#9be2d5",
      "#f6c453",
      "#f28b82",
      "#60a5fa",
      "#34d399",
      "#f472b6",
      "#a78bfa",
      "#f59e0b",
    ];

    const ctx = canvas.getContext("2d");
    if (canvas._chartInstance) {
      canvas._chartInstance.destroy();
    }

    canvas._chartInstance = new Chart(ctx, {
      type: "pie",
      data: {
        labels,
        datasets: [
          {
            data: values,
            backgroundColor: labels.map((_, i) => colors[i % colors.length]),
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              label: (c) => ` ${toNumber(c.parsed).toLocaleString()} JPY`,
            },
          },
        },
      },
    });
  }

  function initCardMerchantPie() {
    const cardSel = document.getElementById("cardMerchantPieCard");
    const monthInp = document.getElementById("cardMerchantPieMonth");
    const loadBtn = document.getElementById("cardMerchantPieLoad");
    const meta = document.getElementById("cardMerchantPieMeta");
    const empty = document.getElementById("cardMerchantPieEmpty");
    const chartWrap = document.getElementById("cardMerchantPieChartWrap");
    const canvas = document.getElementById("cardMerchantPieCanvas");
    const table = document.getElementById("cardMerchantPieTable");

    if (!cardSel || !monthInp || !loadBtn || !meta || !canvas || !table || !empty || !chartWrap) {
      return;
    }

    async function load() {
      const cardId = cardSel.value;
      const month = monthInp.value;
      if (!cardId || !month) return;

      meta.textContent = "loading...";
      loadBtn.disabled = true;

      try {
        const data = await fetchMerchantPie(cardId, month);
        const items = Array.isArray(data.items) ? data.items : [];
        meta.textContent = buildMetaText(data);
        renderTable(table, items);

        if (!items.length) {
          empty.hidden = false;
          chartWrap.style.display = "none";
          if (canvas._chartInstance) {
            canvas._chartInstance.destroy();
            canvas._chartInstance = null;
          }
        } else {
          empty.hidden = true;
          chartWrap.style.display = "";
          renderChart(canvas, items);
        }
      } catch (e) {
        meta.textContent = `load failed: ${e.message || e}`;
        table.innerHTML = "";
        empty.hidden = false;
        chartWrap.style.display = "none";
      } finally {
        loadBtn.disabled = false;
      }
    }

    loadBtn.addEventListener("click", load);
    cardSel.addEventListener("change", load);
    monthInp.addEventListener("change", load);
    load();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initCardMerchantPie);
  } else {
    initCardMerchantPie();
  }
})();
