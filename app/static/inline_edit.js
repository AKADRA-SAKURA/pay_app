const toViewText = (el) => {
  if (!el) return '';
  if (el.tagName === 'SELECT') {
    const opt = el.options[el.selectedIndex];
    return opt ? opt.textContent.trim() : '';
  }
  if (el.type === 'date') {
    return el.value || '-';
  }
  const raw = (el.value ?? '').toString();
  if (el.closest('td') && el.closest('td').classList.contains('money')) {
    const n = Number(raw);
    if (!Number.isNaN(n)) {
      return n.toLocaleString('ja-JP');
    }
  }
  return raw;
};

const getInputValue = (el) => (el?.value ?? '').toString();

const setOriginals = (row, force = false) => {
  row.querySelectorAll('.edit-input').forEach((el) => {
    if (force || el.dataset.original === undefined) {
      el.dataset.original = getInputValue(el);
    }
  });
};

const setEditing = (row, editing) => {
  row.dataset.editing = editing ? '1' : '0';
  row.querySelectorAll('.edit-input').forEach((el) => {
    el.hidden = !editing;
  });
  row.querySelectorAll('.edit-view').forEach((el) => {
    el.hidden = editing;
  });
  row.querySelectorAll('.save-btn, .cancel-btn').forEach((el) => {
    el.hidden = !editing;
  });
  row.querySelectorAll('.edit-btn').forEach((el) => {
    el.hidden = editing;
  });
};

const isRowDirty = (row) => {
  const inputs = Array.from(row.querySelectorAll('.edit-input'));
  if (!inputs.length) return false;
  return inputs.some((el) => getInputValue(el) !== (el.dataset.original ?? ''));
};

const resetRow = (row) => {
  row.querySelectorAll('.edit-input').forEach((el) => {
    if (el.dataset.original !== undefined) {
      el.value = el.dataset.original;
    }
    const view = el.previousElementSibling;
    if (view && view.classList.contains('edit-view')) {
      view.textContent = toViewText(el);
    }
  });
};

const updateViews = (row) => {
  row.querySelectorAll('.edit-input').forEach((el) => {
    let view = el.previousElementSibling;
    if (!view || !view.classList.contains('edit-view')) {
      view = document.createElement('span');
      view.className = 'edit-view';
      el.parentNode.insertBefore(view, el);
    }
    view.textContent = toViewText(el);
  });
};

const getRowsInTable = (table) => Array.from(table.querySelectorAll('tbody tr[data-edit-row]'));

const getToolbarByTable = (table) => {
  if (!table || !table.id) return null;
  return document.querySelector(`.bulk-edit-toolbar[data-target-table-id="${table.id}"]`);
};

const updateToolbarState = (table) => {
  const toolbar = getToolbarByTable(table);
  if (!toolbar) return;

  const rows = getRowsInTable(table);
  const editingRows = rows.filter((r) => r.dataset.editing === '1');
  const dirtyRows = editingRows.filter((r) => isRowDirty(r));
  const saving = toolbar.dataset.saving === '1';

  const startBtn = toolbar.querySelector('[data-bulk-edit="start"]');
  const saveBtn = toolbar.querySelector('[data-bulk-edit="save"]');
  const cancelBtn = toolbar.querySelector('[data-bulk-edit="cancel"]');
  const meta = toolbar.querySelector('.bulk-edit-meta');

  if (startBtn) startBtn.disabled = saving || rows.length === 0;
  if (saveBtn) saveBtn.disabled = saving || editingRows.length === 0 || dirtyRows.length === 0;
  if (cancelBtn) cancelBtn.disabled = saving || editingRows.length === 0;

  if (meta) {
    if (!rows.length) {
      meta.textContent = '対象行なし';
    } else if (saving) {
      meta.textContent = '保存中...';
    } else {
      meta.textContent = `編集中 ${editingRows.length} 行 / 変更あり ${dirtyRows.length} 行`;
    }
  }
};

const ensureBulkToolbars = () => {
  const tables = Array.from(document.querySelectorAll('table')).filter((table) => {
    return table.querySelector('tr[data-edit-row]');
  });

  tables.forEach((table, index) => {
    if (table.dataset.bulkToolbarBound === '1') {
      updateToolbarState(table);
      return;
    }

    if (!table.id) {
      table.id = `bulk-edit-table-${index + 1}`;
    }

    const wrap = table.parentElement;
    if (!wrap) return;

    const toolbar = document.createElement('div');
    toolbar.className = 'bulk-edit-toolbar';
    toolbar.dataset.targetTableId = table.id;
    toolbar.innerHTML = `
      <button type="button" class="btn btn-ghost" data-bulk-edit="start">一括編集</button>
      <button type="button" class="btn" data-bulk-edit="save" disabled>一括保存</button>
      <button type="button" class="btn btn-ghost" data-bulk-edit="cancel" disabled>一括キャンセル</button>
      <span class="bulk-edit-meta"></span>
    `;

    wrap.insertBefore(toolbar, table);
    table.dataset.bulkToolbarBound = '1';
    updateToolbarState(table);
  });
};

const getRowUpdateForms = (row) => {
  const ids = Array.from(new Set(
    Array.from(row.querySelectorAll('.edit-input[form]'))
      .map((el) => el.getAttribute('form'))
      .filter(Boolean)
  ));

  return ids
    .map((id) => document.getElementById(id))
    .filter((form) => form instanceof HTMLFormElement);
};

const toFormBody = (form) => {
  const fd = new FormData(form);
  const params = new URLSearchParams();
  for (const [k, v] of fd.entries()) {
    params.append(k, String(v));
  }
  return params.toString();
};

const saveRow = async (row) => {
  const forms = getRowUpdateForms(row);
  for (const form of forms) {
    const method = (form.method || 'POST').toUpperCase();
    const body = toFormBody(form);

    const res = await fetch(form.action, {
      method,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' },
      body,
      redirect: 'follow',
    });

    if (!res.ok) {
      throw new Error(`保存に失敗しました (HTTP ${res.status})`);
    }
  }

  setOriginals(row, true);
  updateViews(row);
  setEditing(row, false);
};

const bulkSaveTable = async (table) => {
  const toolbar = getToolbarByTable(table);
  if (!toolbar || toolbar.dataset.saving === '1') return;

  const targetRows = getRowsInTable(table).filter((row) => row.dataset.editing === '1' && isRowDirty(row));
  if (!targetRows.length) {
    alert('保存対象の変更がありません');
    return;
  }

  toolbar.dataset.saving = '1';
  updateToolbarState(table);

  try {
    const confirmed = await confirmBulkChanges(table, targetRows);
    if (!confirmed) return;

    for (const row of targetRows) {
      await saveRow(row);
    }
    window.location.reload();
  } catch (e) {
    alert(String(e.message || e));
  } finally {
    delete toolbar.dataset.saving;
    updateToolbarState(table);
  }
};

const getHeaderTextForInput = (row, input) => {
  const td = input.closest('td');
  const tr = td?.parentElement;
  const table = row.closest('table');
  const headRow = table?.querySelector('thead tr');
  if (!td || !tr || !headRow) return input.name || '項目';

  const cells = Array.from(tr.children);
  const idx = cells.indexOf(td);
  if (idx < 0) return input.name || '項目';
  const th = headRow.children[idx];
  const txt = th ? (th.textContent || '').trim() : '';
  return txt || input.name || '項目';
};

const summarizeRowChanges = (row) => {
  const changes = [];
  row.querySelectorAll('.edit-input').forEach((input) => {
    const before = input.dataset.original ?? '';
    const after = getInputValue(input);
    if (before === after) return;
    const label = getHeaderTextForInput(row, input);
    changes.push({
      label,
      before: before || '-',
      after: after || '-',
    });
  });
  return changes;
};

const getRowDisplayId = (row, fallbackIndex) => {
  const candidates = [2, 3, 4];
  for (const n of candidates) {
    const cell = row.querySelector(`td:nth-child(${n})`);
    const txt = (cell?.textContent || '').trim();
    if (txt) return txt;
  }
  return String(fallbackIndex + 1);
};

const renderBulkConfirmModal = (title, items) => {
  const wrap = document.createElement('div');
  wrap.className = 'bulk-confirm-overlay';
  wrap.innerHTML = `
    <div class="bulk-confirm-card" role="dialog" aria-modal="true" aria-label="変更確認">
      <div class="bulk-confirm-head">
        <strong>${title}</strong>
      </div>
      <div class="bulk-confirm-body"></div>
      <div class="bulk-confirm-actions">
        <button type="button" class="btn btn-ghost" data-bulk-confirm="cancel">キャンセル</button>
        <button type="button" class="btn" data-bulk-confirm="ok">保存する</button>
      </div>
    </div>
  `;

  const body = wrap.querySelector('.bulk-confirm-body');
  if (body) {
    const list = document.createElement('div');
    list.className = 'bulk-confirm-list';

    items.forEach((item) => {
      const block = document.createElement('div');
      block.className = 'bulk-confirm-item';
      const titleEl = document.createElement('div');
      titleEl.className = 'bulk-confirm-item-title';
      titleEl.textContent = `行 ${item.rowLabel}`;
      block.appendChild(titleEl);

      item.changes.forEach((ch) => {
        const line = document.createElement('div');
        line.className = 'bulk-confirm-line';
        line.textContent = `${ch.label}: ${ch.before} -> ${ch.after}`;
        block.appendChild(line);
      });

      list.appendChild(block);
    });
    body.appendChild(list);
  }

  return wrap;
};

const confirmBulkChanges = (table, rows) =>
  new Promise((resolve) => {
    const tableTitle = (table.closest('.card')?.querySelector('h2')?.textContent || '編集内容').trim();
    const items = rows
      .map((row, idx) => ({
        rowLabel: getRowDisplayId(row, idx),
        changes: summarizeRowChanges(row),
      }))
      .filter((x) => x.changes.length > 0);

    if (!items.length) {
      resolve(true);
      return;
    }

    const modal = renderBulkConfirmModal(`${tableTitle} の変更確認`, items);
    document.body.appendChild(modal);

    const close = (ok) => {
      modal.remove();
      resolve(ok);
    };

    modal.addEventListener('click', (e) => {
      const t = e.target;
      if (!(t instanceof HTMLElement)) return;
      const action = t.getAttribute('data-bulk-confirm');
      if (action === 'ok') close(true);
      if (action === 'cancel') close(false);
    });
  });

const init = () => {
  document.querySelectorAll('[data-edit-row]').forEach((row) => {
    setOriginals(row);
    updateViews(row);
    setEditing(row, false);
  });

  ensureBulkToolbars();

  document.addEventListener('change', (e) => {
    const el = e.target;
    if (!el.classList || !el.classList.contains('edit-input')) return;

    const view = el.previousElementSibling;
    if (view && view.classList.contains('edit-view')) {
      view.textContent = toViewText(el);
    }

    const row = el.closest('[data-edit-row]');
    const table = row?.closest('table');
    if (table) updateToolbarState(table);
  });

  document.addEventListener('click', (e) => {
    const bulkBtn = e.target.closest('[data-bulk-edit]');
    if (bulkBtn) {
      const action = bulkBtn.dataset.bulkEdit;
      const toolbar = bulkBtn.closest('.bulk-edit-toolbar');
      const tableId = toolbar?.dataset.targetTableId;
      const table = tableId ? document.getElementById(tableId) : null;
      if (!table) return;

      const rows = getRowsInTable(table);
      if (action === 'start') {
        rows.forEach((row) => setEditing(row, true));
        updateToolbarState(table);
        return;
      }
      if (action === 'cancel') {
        rows.forEach((row) => {
          resetRow(row);
          setEditing(row, false);
        });
        updateToolbarState(table);
        return;
      }
      if (action === 'save') {
        bulkSaveTable(table);
        return;
      }
      return;
    }

    const btn = e.target.closest('[data-edit]');
    if (!btn) return;
    const row = btn.closest('[data-edit-row]');
    if (!row) return;

    if (btn.dataset.edit === 'start') {
      setEditing(row, true);
      updateToolbarState(row.closest('table'));
      return;
    }
    if (btn.dataset.edit === 'cancel') {
      resetRow(row);
      setEditing(row, false);
      updateToolbarState(row.closest('table'));
      return;
    }
  });
};

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
