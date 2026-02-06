(function () {
  const MAX_ROWS = 5;

  function getRows(tbody) {
    return Array.from(tbody.rows).filter(
      (r) => !r.classList.contains('empty') && r.dataset.filtered !== '1'
    );
  }

  function removeToggle(table) {
    const anchor = table.closest('.table-wrap') || table;
    const next = anchor?.nextElementSibling;
    if (next && next.classList.contains('table-toggle')) {
      next.remove();
    }
  }

  function resetCollapsed(tbody) {
    Array.from(tbody.rows).forEach((r) => {
      if (r.dataset.collapsed === '1') {
        delete r.dataset.collapsed;
        r.hidden = r.dataset.filtered === '1';
      }
    });
  }

  function setupTable(table, force) {
    if (!force && table.dataset.collapseReady === '1') return;
    const tbody = table.tBodies && table.tBodies[0];
    if (!tbody) return;

    if (force) {
      removeToggle(table);
      resetCollapsed(tbody);
    }

    const rows = getRows(tbody);
    if (rows.length <= MAX_ROWS) {
      table.dataset.collapseReady = '1';
      return;
    }

    rows.slice(MAX_ROWS).forEach((r) => {
      r.dataset.collapsed = '1';
      r.hidden = true;
    });

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-ghost table-toggle-btn';
    btn.setAttribute('aria-expanded', 'false');
    btn.innerHTML = '<span class="arrow"></span><span class="label">展開</span>';

    const wrap = document.createElement('div');
    wrap.className = 'table-toggle';
    wrap.appendChild(btn);

    const anchor = table.closest('.table-wrap') || table;
    if (anchor.parentNode) {
      anchor.parentNode.insertBefore(wrap, anchor.nextSibling);
    }

    btn.addEventListener('click', () => {
      const expanded = btn.getAttribute('aria-expanded') === 'true';
      rows.slice(MAX_ROWS).forEach((r) => {
        if (r.dataset.filtered === '1') return;
        r.hidden = expanded;
      });
      btn.setAttribute('aria-expanded', expanded ? 'false' : 'true');
      btn.querySelector('.label').textContent = expanded ? '展開' : '折りたたむ';
    });

    table.dataset.collapseReady = '1';
  }

  window.refreshTableCollapse = function (table) {
    setupTable(table, true);
  };

  function init() {
    document.querySelectorAll('table').forEach((table) => setupTable(table, false));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
