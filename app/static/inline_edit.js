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

const setOriginals = (row) => {
  row.querySelectorAll('.edit-input').forEach((el) => {
    if (!el.dataset.original) {
      el.dataset.original = el.value;
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

const init = () => {
  document.querySelectorAll('[data-edit-row]').forEach((row) => {
    setOriginals(row);
    updateViews(row);
    setEditing(row, false);
  });

  document.addEventListener('change', (e) => {
    const el = e.target;
    if (!el.classList || !el.classList.contains('edit-input')) return;
    const view = el.previousElementSibling;
    if (view && view.classList.contains('edit-view')) {
      view.textContent = toViewText(el);
    }
  });

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-edit]');
    if (!btn) return;
    const row = btn.closest('[data-edit-row]');
    if (!row) return;

    if (btn.dataset.edit === 'start') {
      setEditing(row, true);
      return;
    }
    if (btn.dataset.edit === 'cancel') {
      resetRow(row);
      setEditing(row, false);
      return;
    }
  });
};

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
