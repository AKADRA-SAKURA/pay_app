(function () {
  function readData(id) {
    const el = document.getElementById(id);
    if (!el) return [];
    try {
      const data = JSON.parse(el.textContent || '[]');
      return Array.isArray(data) ? data : [];
    } catch (e) {
      console.warn('pay pie parse error', e);
      return [];
    }
  }

  function makeChart(canvasId, dataId) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const items = readData(dataId);
    if (!items.length) return;

    const labels = items.map((x) => String(x.label || '-'));
    const values = items.map((x) => Number(x.value || 0));

    const colors = [
      '#2fb7a7',
      '#6fd3c2',
      '#9be2d5',
      '#f6c453',
      '#f28b82',
      '#a78bfa',
      '#60a5fa',
      '#34d399',
      '#f472b6',
    ];

    const ctx = canvas.getContext('2d');
    if (canvas._chartInstance) canvas._chartInstance.destroy();

    const chart = new Chart(ctx, {
      type: 'pie',
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
          legend: { position: 'bottom' },
          tooltip: {
            callbacks: {
              label: (c) => {
                const v = Number(c.parsed || 0);
                return ` ${v.toLocaleString()} 円`;
              },
            },
          },
        },
      },
    });

    canvas._chartInstance = chart;
  }

  makeChart('payPieThis', 'payPieThisData');
  makeChart('payPieNext', 'payPieNextData');
})();
