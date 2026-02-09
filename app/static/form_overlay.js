(function () {
  const overlay = document.getElementById('form-overlay');
  const body = document.getElementById('form-overlay-body');
  const closeBtn = document.getElementById('form-overlay-close');

  if (!overlay || !body) return;

  let currentTemplate = null;

  function open(templateId) {
    const template = document.getElementById(templateId);
    if (!template) return;
    const content = template.firstElementChild;
    if (!content) return;

    currentTemplate = template;
    body.innerHTML = '';
    body.appendChild(content);
    overlay.style.display = 'block';
  }

  function close() {
    const content = body.firstElementChild;
    if (currentTemplate && content) {
      currentTemplate.appendChild(content);
    }
    overlay.style.display = 'none';
  }

  function isOverlayOpen() {
    return overlay.style.display === 'block';
  }

  function isEditableTarget(target) {
    if (!(target instanceof HTMLElement)) return false;
    if (target.isContentEditable) return true;
    if (target.tagName === 'TEXTAREA') return true;
    if (target.tagName !== 'INPUT') return false;

    const input = target;
    const type = (input.getAttribute('type') || 'text').toLowerCase();
    return !['button', 'submit', 'reset', 'checkbox', 'radio'].includes(type);
  }

  document.querySelectorAll('[data-overlay-open]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.overlayOpen;
      if (target) open(target);
    });
  });

  closeBtn?.addEventListener('click', close);
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) close();
  });

  overlay.addEventListener('submit', (e) => {
    if (e.defaultPrevented) return;
    close();
  });

  document.addEventListener('keydown', (e) => {
    if (!isOverlayOpen()) return;
    if (e.key === 'Escape') {
      close();
      return;
    }
    // Prevent browser back navigation while editing overlay forms.
    if (e.key === 'Backspace' && !isEditableTarget(e.target)) {
      e.preventDefault();
    }
  });
})();
