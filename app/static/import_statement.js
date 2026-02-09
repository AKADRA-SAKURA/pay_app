(function () {
  let lastDuplicateCandidates = [];
  let lastPreviewErrors = [];

  function qs(id) {
    return document.getElementById(id);
  }

  function toInt(v) {
    const n = Number(String(v || '').replace(/,/g, '').trim());
    return Number.isFinite(n) ? Math.trunc(n) : NaN;
  }

  function esc(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  async function parseJson(res) {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data.detail;
      const msg = Array.isArray(detail) ? detail.join(', ') : (detail || 'request failed');
      throw new Error(msg);
    }
    return data;
  }

  function normalizeDateInput(value) {
    const s = String(value || '').trim();
    if (!s) return '';
    return s.replace(/\//g, '-');
  }

  function normalizeDateOutput(value) {
    const s = String(value || '').trim();
    if (!s) return '';
    return s.replace(/-/g, '/');
  }

  function getPreviewBodyRows() {
    const body = qs('import-preview-body');
    return body ? Array.from(body.querySelectorAll('tr')) : [];
  }

  function collectRowsWithValidation() {
    const rows = [];
    const invalidMessages = [];

    getPreviewBodyRows().forEach((tr, idx) => {
      const d = tr.querySelector('[data-field="date"]')?.value || '';
      const t = tr.querySelector('[data-field="title"]')?.value || '';
      const p = tr.querySelector('[data-field="price"]')?.value || '';
      const price = toInt(p);

      if (!d) invalidMessages.push(`行${idx + 1}: 日付を入力してください`);
      if (!String(t).trim()) invalidMessages.push(`行${idx + 1}: タイトルを入力してください`);
      if (Number.isNaN(price)) invalidMessages.push(`行${idx + 1}: 金額は整数で入力してください`);
      if (!d || !String(t).trim() || Number.isNaN(price)) return;

      rows.push({ date: normalizeDateOutput(d), title: String(t).trim(), price: price });
    });

    return { rows, invalidMessages };
  }

  function updateCommitState() {
    const btn = qs('import-commit-btn');
    if (!btn) return;
    const hasPreviewRows = getPreviewBodyRows().length > 0;
    const { invalidMessages } = collectRowsWithValidation();
    const hasBlockingErrors = Array.isArray(lastPreviewErrors) && lastPreviewErrors.length > 0;
    btn.disabled = !hasPreviewRows || hasBlockingErrors || invalidMessages.length > 0;
  }

  function renderAlerts(warnings, errors, duplicateCandidates) {
    const warn = qs('import-warnings');
    const err = qs('import-errors');
    if (!warn || !err) return;

    const dup = Array.isArray(duplicateCandidates) ? duplicateCandidates : [];
    lastPreviewErrors = Array.isArray(errors) ? errors : [];
    lastDuplicateCandidates = dup;

    const warnList = (warnings || []).map((x) => `<li>${esc(x)}</li>`).join('');
    const dupList = dup
      .slice(0, 15)
      .map((d) => `<li>${esc(d.date)} / ${esc(d.title)} / ${esc(d.price)}円 (${esc(d.reason)})</li>`)
      .join('');
    const dupMore = dup.length > 15 ? `<li>...他 ${dup.length - 15} 件</li>` : '';
    const hasWarn = !!warnList || dup.length > 0;

    if (hasWarn) {
      const dupHtml = dup.length
        ? `<p class="mt-8"><strong>重複候補詳細</strong></p><ul>${dupList}${dupMore}</ul>`
        : '';
      warn.hidden = false;
      warn.innerHTML = `<strong>警告</strong>${warnList ? `<ul>${warnList}</ul>` : ''}${dupHtml}`;
    } else {
      warn.hidden = true;
      warn.innerHTML = '';
    }

    if (errors && errors.length) {
      err.hidden = false;
      err.innerHTML = `<strong>エラー</strong><ul>${errors.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
    } else {
      err.hidden = true;
      err.innerHTML = '';
    }
    updateCommitState();
  }

  function renderRows(rows) {
    const wrap = qs('import-preview-wrap');
    const body = qs('import-preview-body');
    if (!wrap || !body) return;

    body.innerHTML = '';
    rows.forEach((r, i) => {
      const dateHint = r.date_hint
        ? `<div class="import-date-hint">元日付: ${esc(r.date_hint)}（年不明のため今日を仮入力。必要なら編集してください）</div>`
        : '';
      const tr = document.createElement('tr');
      tr.dataset.index = String(i);
      tr.innerHTML = `
        <td><input data-field="date" type="date" value="${esc(normalizeDateInput(r.date))}" />${dateHint}</td>
        <td><input data-field="title" type="text" value="${esc(r.title)}" /></td>
        <td><input data-field="price" type="number" value="${esc(r.price)}" /></td>
        <td><button class="btn btn-danger" type="button" data-remove-row="1">削除</button></td>
      `;
      body.appendChild(tr);
    });

    wrap.hidden = rows.length === 0;
    updateCommitState();
  }

  function collectRows() {
    return collectRowsWithValidation();
  }

  async function previewFromText() {
    const card = qs('import-card-select')?.value;
    const text = qs('import-textarea')?.value || '';
    if (!card) {
      alert('カードを選択してください');
      return;
    }
    if (!text.trim()) {
      alert('テキストを入力してください');
      return;
    }

    const res = await fetch('/import/preview_text', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text, card: Number(card) }),
    });
    const data = await parseJson(res);
    renderAlerts(data.warnings || [], data.errors || [], data.duplicate_candidates || []);
    renderRows(data.rows || []);
  }

  async function previewFromCsv() {
    const card = qs('import-card-select')?.value;
    const file = qs('import-csv-file')?.files?.[0];
    if (!card) {
      alert('カードを選択してください');
      return;
    }
    if (!file) {
      alert('CSVファイルを選択してください');
      return;
    }

    const fd = new FormData();
    fd.append('card', card);
    fd.append('file', file);

    const res = await fetch('/import/preview_csv', {
      method: 'POST',
      body: fd,
    });
    const data = await parseJson(res);
    renderAlerts(data.warnings || [], data.errors || [], data.duplicate_candidates || []);
    renderRows(data.rows || []);
  }

  async function commitRows() {
    const card = qs('import-card-select')?.value;
    const duplicateMode = qs('import-duplicate-mode')?.value || 'skip';
    const allowDuplicates = duplicateMode === 'allow';
    const { rows, invalidMessages } = collectRows();

    if (!card) {
      alert('カードを選択してください');
      return;
    }
    if (lastPreviewErrors.length > 0) {
      alert('抽出エラーがあるため登録できません。入力を修正して再プレビューしてください。');
      return;
    }
    if (!rows.length) {
      alert('登録対象の行がありません');
      return;
    }
    if (invalidMessages.length > 0) {
      alert(`未入力項目があります:\n${invalidMessages.slice(0, 8).join('\n')}`);
      return;
    }

    const res = await fetch('/import/commit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card: Number(card), rows: rows, allow_duplicates: allowDuplicates }),
    });
    const data = await parseJson(res);

    const skipped = Number(data.skipped_duplicates || 0);
    const dupDetail = Array.isArray(data.duplicates_detail) ? data.duplicates_detail : [];
    const dupMsg = dupDetail.length
      ? `\n重複詳細:\n${dupDetail
          .slice(0, 10)
          .map((d) => `${d.date} / ${d.title} / ${d.price}円 (${d.reason})`)
          .join('\n')}${dupDetail.length > 10 ? `\n...他 ${dupDetail.length - 10} 件` : ''}`
      : '';
    const msg = `登録: ${data.inserted}件 / 重複スキップ: ${skipped}件${dupMsg}`;
    alert(msg);

    const close = qs('form-overlay-close');
    if (close) close.click();
    window.location.reload();
  }

  function clearPreview() {
    lastDuplicateCandidates = [];
    lastPreviewErrors = [];
    renderAlerts([], [], []);
    renderRows([]);
    const ta = qs('import-textarea');
    if (ta) ta.value = '';
    const fi = qs('import-csv-file');
    if (fi) fi.value = '';
    const mode = qs('import-duplicate-mode');
    if (mode) mode.value = 'skip';
    updateCommitState();
  }

  function bindOnce() {
    const panel = qs('card-import-panel');
    if (!panel || panel.dataset.bound === '1') return;
    panel.dataset.bound = '1';

    qs('import-preview-text-btn')?.addEventListener('click', async () => {
      try {
        await previewFromText();
      } catch (e) {
        alert(String(e.message || e));
      }
    });

    qs('import-preview-csv-btn')?.addEventListener('click', async () => {
      try {
        await previewFromCsv();
      } catch (e) {
        alert(String(e.message || e));
      }
    });

    qs('import-commit-btn')?.addEventListener('click', async () => {
      try {
        await commitRows();
      } catch (e) {
        alert(String(e.message || e));
      }
    });

    qs('import-clear-btn')?.addEventListener('click', clearPreview);

    qs('import-preview-body')?.addEventListener('click', (ev) => {
      const t = ev.target;
      if (!(t instanceof HTMLElement)) return;
      if (t.matches('[data-remove-row="1"]')) {
        t.closest('tr')?.remove();
        updateCommitState();
      }
    });

    qs('import-preview-body')?.addEventListener('input', () => updateCommitState());
    qs('import-preview-body')?.addEventListener('change', () => updateCommitState());
  }

  function onOpenImportOverlay() {
    setTimeout(() => {
      bindOnce();
      clearPreview();
    }, 0);
  }

  document.querySelectorAll('[data-overlay-open="card-import-form-template"]').forEach((btn) => {
    btn.addEventListener('click', onOpenImportOverlay);
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindOnce);
  } else {
    bindOnce();
  }
})();
