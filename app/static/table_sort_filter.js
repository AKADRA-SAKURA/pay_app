(function () {
  function getFieldValue(field) {
    if (!field) return '';
    if (field.tagName === 'SELECT') {
      const opt = field.selectedOptions && field.selectedOptions[0];
      return (opt && opt.textContent) || field.value || '';
    }
    return field.value || '';
  }

  function getCellValue(cell) {
    if (!cell) return '';
    const data = cell.getAttribute('data-sort-value');
    if (data !== null) return data;

    const numberInput = cell.querySelector('input[type="number"]');
    if (numberInput && numberInput.value !== '') {
      let num = numberInput.value;
      const dir = cell.querySelector('select[name="direction"]');
      if (dir && dir.value === 'expense') {
        num = '-' + num;
      }
      return num;
    }

    const fields = cell.querySelectorAll('input, select');
    if (fields.length) {
      return Array.from(fields)
        .map((f) => getFieldValue(f))
        .join(' ')
        .trim();
    }

    return cell.textContent.trim();
  }

  function parseValue(value) {
    const v = (value || '').trim();
    if (!v) return { type: 'empty', value: '' };

    if (/^\d{4}[/-]\d{2}[/-]\d{2}$/.test(v)) {
      const d = new Date(v.replace(/\//g, '-'));
      if (!Number.isNaN(d.getTime())) {
        return { type: 'date', value: d.getTime() };
      }
    }

    const normalized = v.replace(/[\s,円]/g, '');
    if (/^-?\d+(\.\d+)?$/.test(normalized)) {
      return { type: 'number', value: Number(normalized) };
    }

    return { type: 'string', value: v.toLowerCase() };
  }

  function compareValues(a, b) {
    if (a.type === 'empty' && b.type !== 'empty') return 1;
    if (b.type === 'empty' && a.type !== 'empty') return -1;

    if (a.type === 'number' && b.type === 'number') return a.value - b.value;
    if (a.type === 'date' && b.type === 'date') return a.value - b.value;

    return String(a.value).localeCompare(String(b.value), 'ja', {
      numeric: true,
      sensitivity: 'base',
    });
  }

  function updateSortIndicators(table, activeTh, dir) {
    const headers = table.tHead ? Array.from(table.tHead.querySelectorAll('th')) : [];
    headers.forEach((th) => {
      th.classList.remove('sort-asc', 'sort-desc');
    });
    if (activeTh) {
      activeTh.classList.add(dir === 'asc' ? 'sort-asc' : 'sort-desc');
    }
  }

  function sortTable(table, colIndex, dir) {
    const tbody = table.tBodies && table.tBodies[0];
    if (!tbody) return;

    const rows = Array.from(tbody.rows).filter((r) => !r.classList.contains('empty'));
    const emptyRows = Array.from(tbody.rows).filter((r) => r.classList.contains('empty'));

    rows.sort((rowA, rowB) => {
      const cellA = rowA.cells[colIndex];
      const cellB = rowB.cells[colIndex];
      const valueA = parseValue(getCellValue(cellA));
      const valueB = parseValue(getCellValue(cellB));
      const result = compareValues(valueA, valueB);
      return dir === 'desc' ? -result : result;
    });

    rows.forEach((row) => tbody.appendChild(row));
    emptyRows.forEach((row) => tbody.appendChild(row));

    if (typeof window.refreshTableCollapse === 'function') {
      window.refreshTableCollapse(table);
    }
  }

  function makeSortable(table) {
    if (table.dataset.sortReady === '1') return;
    const headerRow = table.tHead && table.tHead.rows[0];
    if (!headerRow) return;

    const headers = Array.from(headerRow.cells);
    headers.forEach((th, index) => {
      const label = (th.textContent || '').trim();
      if (!label || label === '操作' || th.classList.contains('sticky-col')) return;

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'sort-btn';
      btn.innerHTML = `<span class="label">${label}</span><span class="sort-indicator"></span>`;

      th.textContent = '';
      th.appendChild(btn);
      th.classList.add('is-sortable');

      btn.addEventListener('click', () => {
        const current = table.dataset.sortDir || 'asc';
        const activeIndex = Number(table.dataset.sortCol || -1);
        const nextDir = activeIndex === index && current === 'asc' ? 'desc' : 'asc';
        table.dataset.sortCol = String(index);
        table.dataset.sortDir = nextDir;
        updateSortIndicators(table, th, nextDir);
        sortTable(table, index, nextDir);
      });
    });

    table.dataset.sortReady = '1';
  }

  function getRowText(row) {
    return Array.from(row.cells)
      .filter((cell) => !cell.classList.contains('sticky-col'))
      .map((cell) => getCellValue(cell))
      .join(' ')
      .toLowerCase();
  }

  function addFilter(table) {
    if (table.dataset.filterReady === '1') return;
    const wrap = table.closest('.table-wrap');
    if (!wrap || !wrap.parentNode) return;

    const toolbar = document.createElement('div');
    toolbar.className = 'table-toolbar';
    toolbar.innerHTML = `
      <input class="table-filter-input" type="search" placeholder="フィルタ（キーワード）" aria-label="フィルタ" />
      <button class="btn btn-ghost table-filter-clear" type="button">クリア</button>
      <span class="table-filter-count"></span>
    `;

    wrap.parentNode.insertBefore(toolbar, wrap);

    const input = toolbar.querySelector('.table-filter-input');
    const clearBtn = toolbar.querySelector('.table-filter-clear');
    const count = toolbar.querySelector('.table-filter-count');

    const tbody = table.tBodies && table.tBodies[0];
    const baseRows = tbody
      ? Array.from(tbody.rows).filter((r) => !r.classList.contains('empty'))
      : [];

    function updateCount(shown) {
      if (!count) return;
      const total = baseRows.length;
      count.textContent = total ? `${shown}/${total}` : '';
    }

    function applyFilter() {
      const term = (input.value || '').trim().toLowerCase();
      let shown = 0;

      baseRows.forEach((row) => {
        const match = term === '' || getRowText(row).includes(term);
        if (match) {
          delete row.dataset.filtered;
          row.hidden = false;
          shown += 1;
        } else {
          row.dataset.filtered = '1';
          row.hidden = true;
        }
      });

      updateCount(shown);

      if (typeof window.refreshTableCollapse === 'function') {
        window.refreshTableCollapse(table);
      }
    }

    input.addEventListener('input', applyFilter);
    clearBtn.addEventListener('click', () => {
      input.value = '';
      applyFilter();
      input.focus();
    });

    updateCount(baseRows.length);
    table.dataset.filterReady = '1';
  }

  function init() {
    document.querySelectorAll('.table-wrap table').forEach((table) => {
      makeSortable(table);
      addFilter(table);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
