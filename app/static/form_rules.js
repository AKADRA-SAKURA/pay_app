(function () {
  function lockField(el, locked) {
    if (!el) return;

    if (locked) {
      if (el.dataset.prev === undefined) {
        el.dataset.prev = el.value ?? '';
      }
      el.value = '';
    } else {
      if ((el.value ?? '') === '' && el.dataset.prev !== undefined) {
        el.value = el.dataset.prev;
      }
      delete el.dataset.prev;
    }

    el.classList.toggle('is-locked', locked);
    el.setAttribute('aria-disabled', locked ? 'true' : 'false');
    if (locked) {
      el.dataset.locked = '1';
      el.tabIndex = -1;
    } else {
      delete el.dataset.locked;
      el.removeAttribute('tabindex');
    }

    const label = el.closest('label');
    if (label) {
      label.classList.toggle('field-locked', locked);
    }
  }

  function applyPlan(container) {
    const freq = container.querySelector('[name="freq"]')?.value || 'monthly';
    const interval = container.querySelector('[name="interval_months"]');
    const month = container.querySelector('[name="month"]');

    if (freq === 'monthly') {
      lockField(interval, true);
      lockField(month, true);
    } else if (freq === 'yearly') {
      lockField(interval, true);
      lockField(month, false);
    } else if (freq === 'monthly_interval') {
      lockField(interval, false);
      lockField(month, true);
    }

    const pay = container.querySelector('[name="payment_method"]')?.value || 'bank';
    const account = container.querySelector('[name="account_id"]');
    const card = container.querySelector('[name="card_id"]');

    if (pay === 'bank') {
      lockField(card, true);
      lockField(account, false);
    } else if (pay === 'card') {
      lockField(card, false);
      lockField(account, true);
    }
  }

  function applySub(container) {
    const freq = container.querySelector('[name="freq"]')?.value || 'monthly';
    const interval = container.querySelector('[name="interval_months"]');
    const month = container.querySelector('[name="billing_month"]');

    if (freq === 'monthly') {
      lockField(interval, true);
      lockField(month, true);
    } else if (freq === 'yearly') {
      lockField(interval, true);
      lockField(month, false);
    } else if (freq === 'monthly_interval') {
      lockField(interval, false);
      lockField(month, true);
    }

    const pay = container.querySelector('[name="payment_method"]')?.value || 'bank';
    const account = container.querySelector('[name="account_id"]');
    const card = container.querySelector('[name="card_id"]');

    if (pay === 'bank') {
      lockField(card, true);
      lockField(account, false);
    } else if (pay === 'card') {
      lockField(card, false);
      lockField(account, true);
    }
  }

  function bind(container, kind) {
    const apply = kind === 'plan' ? applyPlan : applySub;
    apply(container);

    container.addEventListener('change', (e) => {
      const name = e.target?.name;
      if (!name) return;
      if (name === 'freq' || name === 'payment_method') {
        apply(container);
      }
    });
  }

  function init() {
    document.querySelectorAll('form[action="/plans"]').forEach((form) => bind(form, 'plan'));
    document.querySelectorAll('form[action="/subscriptions"]').forEach((form) => bind(form, 'sub'));

    document.querySelectorAll('tr[data-edit-row]').forEach((row) => {
      if (row.querySelector('form[id^="plan-"]')) bind(row, 'plan');
      if (row.querySelector('form[id^="sub-"]')) bind(row, 'sub');
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
