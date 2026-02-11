(function () {
  const groups = [
    {
      formId: 'card-tx-bulk-delete-form',
      btnId: 'card-tx-bulk-delete-btn',
      hiddenId: 'card-tx-bulk-delete-ids',
      tableId: 'card-transactions-table',
      selectAllId: 'card-tx-select-all',
      rowSelector: '.card-tx-select',
      rowIdAttr: 'data-tx-id',
      label: 'カード取引',
    },
    {
      formId: 'oneoff-bulk-delete-form',
      btnId: 'oneoff-bulk-delete-btn',
      hiddenId: 'oneoff-bulk-delete-ids',
      tableId: 'oneoff-table',
      selectAllId: 'oneoff-select-all',
      rowSelector: '.oneoff-select',
      rowIdAttr: 'data-oneoff-id',
      label: '単発支払い',
    },
    {
      formId: 'card-revolving-bulk-delete-form',
      btnId: 'card-revolving-bulk-delete-btn',
      hiddenId: 'card-revolving-bulk-delete-ids',
      tableId: 'card-revolving-table',
      selectAllId: 'card-revolving-select-all',
      rowSelector: '.card-revolving-select',
      rowIdAttr: 'data-card-revolving-id',
      label: 'リボ',
    },
    {
      formId: 'card-installment-bulk-delete-form',
      btnId: 'card-installment-bulk-delete-btn',
      hiddenId: 'card-installment-bulk-delete-ids',
      tableId: 'card-installment-table',
      selectAllId: 'card-installment-select-all',
      rowSelector: '.card-installment-select',
      rowIdAttr: 'data-card-installment-id',
      label: '分割',
    },
  ];

  function initBulkDelete(config) {
    const form = document.getElementById(config.formId);
    const btn = document.getElementById(config.btnId);
    const hiddenIds = document.getElementById(config.hiddenId);
    const table = document.getElementById(config.tableId);
    const selectAll = document.getElementById(config.selectAllId);
    if (!form || !btn || !hiddenIds || !table) return;

    function getRowChecks() {
      return Array.from(table.querySelectorAll(config.rowSelector));
    }

    function getSelectedIds() {
      return getRowChecks()
        .filter((el) => el instanceof HTMLInputElement && el.checked)
        .map((el) => String(el.getAttribute(config.rowIdAttr) || '').trim())
        .filter((id) => /^\d+$/.test(id));
    }

    function syncBulkState() {
      const checks = getRowChecks();
      const selectedIds = getSelectedIds();
      hiddenIds.value = selectedIds.join(',');
      btn.disabled = selectedIds.length === 0;

      if (!selectAll) return;
      if (checks.length === 0) {
        selectAll.checked = false;
        selectAll.indeterminate = false;
        selectAll.disabled = true;
        return;
      }

      const checkedCount = checks.filter((el) => el instanceof HTMLInputElement && el.checked).length;
      selectAll.disabled = false;
      selectAll.checked = checkedCount === checks.length;
      selectAll.indeterminate = checkedCount > 0 && checkedCount < checks.length;
    }

    selectAll?.addEventListener('change', () => {
      const checks = getRowChecks();
      checks.forEach((el) => {
        if (el instanceof HTMLInputElement) {
          el.checked = !!selectAll.checked;
        }
      });
      syncBulkState();
    });

    table.addEventListener('change', (ev) => {
      const t = ev.target;
      if (!(t instanceof HTMLElement)) return;
      if (!t.matches(config.rowSelector)) return;
      syncBulkState();
    });

    btn.addEventListener('click', () => {
      const selectedIds = getSelectedIds();
      if (!selectedIds.length) return;
      if (!window.confirm(`選択した${config.label} ${selectedIds.length} 件を削除します。よろしいですか？`)) return;
      hiddenIds.value = selectedIds.join(',');
      form.submit();
    });

    syncBulkState();
  }

  groups.forEach((cfg) => initBulkDelete(cfg));
})();
