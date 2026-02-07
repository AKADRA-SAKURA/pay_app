(function () {
  const overlay = document.getElementById("overlay");
  const closeBtn = document.getElementById("overlay-close");
  const titleEl = document.getElementById("overlay-title");
  const bodyEl = document.getElementById("overlay-body");

  if (!overlay || !closeBtn || !titleEl || !bodyEl) return;

  function openOverlay(name, series) {
    titleEl.textContent = `${name} の残高推移（イベント集計）`;
    bodyEl.innerHTML = "";

    for (const p of series) {
      const tr = document.createElement("tr");

      const dateTd = document.createElement("td");
      dateTd.textContent = p.date ?? "";

      const deltaTd = document.createElement("td");
      const delta = Number(p.delta_yen ?? 0);
      deltaTd.textContent = `${delta >= 0 ? "+" : ""}${delta.toLocaleString()}`;

      const balTd = document.createElement("td");
      const bal = Number(p.balance_yen ?? 0);
      balTd.textContent = bal.toLocaleString();

      tr.appendChild(dateTd);
      tr.appendChild(deltaTd);
      tr.appendChild(balTd);
      bodyEl.appendChild(tr);
    }

    overlay.style.display = "block";
  }

  function closeOverlay() {
    overlay.style.display = "none";
  }

  document.querySelectorAll(".detail-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const name = btn.dataset.accountName || "口座";
      const series = JSON.parse(btn.dataset.series || "[]");
      openOverlay(name, series);
    });
  });

  closeBtn.addEventListener("click", closeOverlay);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeOverlay();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeOverlay();
  });
})();
