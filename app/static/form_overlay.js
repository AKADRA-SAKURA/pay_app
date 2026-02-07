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

  overlay.addEventListener('submit', () => {
    close();
  });
})();
