(async function () {
  const canvas = document.getElementById("freeChart");
  if (!canvas) return;

  const r = await fetch("/api/forecast/free");
  const j = await r.json();
  const series = Array.isArray(j.series) ? j.series : [];

  if (!series.length) return;

  const labels = series.map((p) => String(p.date || ""));
  const values = series.map((p) => Number(p.balance_yen || 0));

  const ctx = canvas.getContext("2d");

  if (canvas._chartInstance) canvas._chartInstance.destroy();

  const chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "自由に使えるお金（円）",
          data: values,
          tension: 0.25,
          pointRadius: 2.5,
          pointHoverRadius: 4,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          callbacks: {
            label: (c) => ` ${Number(c.parsed.y || 0).toLocaleString()} 円`,
          },
        },
      },
      scales: {
        x: { ticks: { autoSkip: true, maxTicksLimit: 8 } },
        y: { ticks: { callback: (v) => Number(v).toLocaleString() } },
      },
    },
  });

  canvas._chartInstance = chart;
})();
