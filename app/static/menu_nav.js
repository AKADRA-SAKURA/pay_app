(function () {
  const toggleBtn = document.getElementById('menu-toggle');
  const closeBtn = document.getElementById('menu-close');
  const drawer = document.getElementById('menu-drawer');
  const backdrop = document.getElementById('menu-backdrop');
  const list = document.getElementById('menu-list');

  if (!toggleBtn || !drawer || !backdrop || !list) return;

  function slugify(text) {
    return (text || '')
      .toLowerCase()
      .replace(/[^a-z0-9\u3040-\u30ff\u4e00-\u9faf]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'card';
  }

  function openMenu() {
    drawer.classList.add('open');
    backdrop.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
    toggleBtn.setAttribute('aria-expanded', 'true');
  }

  function closeMenu() {
    drawer.classList.remove('open');
    backdrop.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
    toggleBtn.setAttribute('aria-expanded', 'false');
  }

  function buildMenu() {
    const cards = Array.from(document.querySelectorAll('.card h2'));
    const used = new Set();
    list.innerHTML = '';

    cards.forEach((h2, idx) => {
      const title = (h2.textContent || '').trim().replace(/\s+/g, ' ');
      if (!title) return;

      const card = h2.closest('.card');
      if (!card) return;
      if (card.closest('.overlay-form-template')) return;

      let id = card.id;
      if (!id) {
        const base = slugify(title);
        id = base;
        let n = 1;
        while (used.has(id) || document.getElementById(id)) {
          id = `${base}-${n}`;
          n += 1;
        }
        card.id = id;
      }
      used.add(id);

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'menu-item';
      btn.textContent = title;
      btn.addEventListener('click', () => {
        card.scrollIntoView({ behavior: 'smooth', block: 'start' });
        closeMenu();
      });
      list.appendChild(btn);
    });
  }

  toggleBtn.addEventListener('click', () => {
    const isOpen = drawer.classList.contains('open');
    if (isOpen) {
      closeMenu();
    } else {
      openMenu();
    }
  });

  closeBtn?.addEventListener('click', closeMenu);
  backdrop.addEventListener('click', closeMenu);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeMenu();
  });

  buildMenu();
})();
