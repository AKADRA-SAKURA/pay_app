(async function () {
  function readSeries() {
    const el = document.getElementById('totalSeriesData');
    if (!el) return [];
    try {
      const data = JSON.parse(el.textContent || '[]');
      return Array.isArray(data) ? data : [];
    } catch (e) {
      console.warn('failed to parse totalSeriesData:', e);
      return [];
    }
  }

  function toNumber(x) {
    const n = Number(x);
    return Number.isFinite(n) ? n : 0;
  }

  const canvas = document.getElementById('combinedChart');
  if (!canvas) return;

  const totalSeries = readSeries();
  let freeSeries = [];
  try {
    const r = await fetch('/api/forecast/free');
    const j = await r.json();
    freeSeries = Array.isArray(j.series) ? j.series : [];
  } catch (e) {
    console.warn('failed to fetch free series:', e);
  }

  if (!totalSeries.length && !freeSeries.length) return;

  const labelSet = new Set();
  totalSeries.forEach((p) => labelSet.add(String(p.date || '')));
  freeSeries.forEach((p) => labelSet.add(String(p.date || '')));
  const labels = Array.from(labelSet).filter(Boolean).sort();

  const totalMap = new Map(totalSeries.map((p) => [String(p.date || ''), toNumber(p.balance_yen)]));
  const freeMap = new Map(freeSeries.map((p) => [String(p.date || ''), toNumber(p.balance_yen)]));

  const totalValues = labels.map((d) => totalMap.get(d) ?? null);
  const freeValues = labels.map((d) => freeMap.get(d) ?? null);

  const ctx = canvas.getContext('2d');
  if (canvas._chartInstance) canvas._chartInstance.destroy();

  const chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: '総資産の推移（予測）',
          data: totalValues,
          tension: 0.25,
          borderColor: '#2fb7a7',
          backgroundColor: 'rgba(47, 183, 167, 0.2)',
          pointRadius: 2.5,
          pointHoverRadius: 4,
          spanGaps: true,
        },
        {
          label: '自由に使えるお金の推移（予測）',
          data: freeValues,
          tension: 0.25,
          borderColor: '#f59e0b',
          backgroundColor: 'rgba(245, 158, 11, 0.2)',
          pointRadius: 2.5,
          pointHoverRadius: 4,
          spanGaps: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom' },
        tooltip: {
          callbacks: {
            label: function (ctx) {
              const v = ctx.parsed.y ?? 0;
              return ` ${Number(v).toLocaleString()} 円`;
            },
          },
        },
      },
      scales: {
        x: {
          ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
        },
        y: {
          ticks: { callback: (v) => Number(v).toLocaleString() },
        },
      },
    },
  });

  canvas._chartInstance = chart;
})();
