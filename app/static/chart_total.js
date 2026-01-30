(function () {
  function readSeries() {
    const el = document.getElementById("totalSeriesData");
    if (!el) return [];
    try {
      const data = JSON.parse(el.textContent || "[]");
      return Array.isArray(data) ? data : [];
    } catch (e) {
      console.warn("failed to parse totalSeriesData:", e);
      return [];
    }
  }

  function toLabel(d) {
    // "YYYY-MM-DD" をそのまま使う（まずはシンプルに）
    return String(d || "");
  }

  function toNumber(x) {
    const n = Number(x);
    return Number.isFinite(n) ? n : 0;
  }

  const canvas = document.getElementById("totalChart");
  if (!canvas) return;

  const series = readSeries();
  if (!series.length) {
    // データが無い場合は何も描かない（将来: empty表示を入れてもOK）
    return;
  }

  // 期待する形式：[{date: "YYYY-MM-DD", balance_yen: 123}, ...]
  const labels = series.map((p) => toLabel(p.date));
  const values = series.map((p) => toNumber(p.balance_yen));

  const ctx = canvas.getContext("2d");

  // 既に同じCanvasで作られている場合の保険（Hot reload等）
  if (canvas._chartInstance) {
    canvas._chartInstance.destroy();
  }

  const chart = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels,
      datasets: [
        {
          label: "総資産（円）",
          data: values,
          tension: 0.25,
          pointRadius: 2.5,
          pointHoverRadius: 4,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false, // canvas height を活かす
      plugins: {
        legend: { display: true },
        tooltip: {
          callbacks: {
            label: function (ctx) {
              const v = ctx.parsed.y ?? 0;
              return ` ${v.toLocaleString()} 円`;
            },
          },
        },
      },
      scales: {
        x: {
          ticks: {
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 8,
          },
        },
        y: {
          ticks: {
            callback: function (value) {
              return Number(value).toLocaleString();
            },
          },
        },
      },
    },
  });

  canvas._chartInstance = chart;
})();
