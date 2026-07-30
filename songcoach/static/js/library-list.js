// Client-side library search + pagination over the server-rendered rows.
// Self-contained: filters/paginates by showing/hiding existing <li> rows.
(() => {
  const PAGE_SIZE = 10;
  const list = document.querySelector(".rec-list");
  const search = document.getElementById("lib-search");
  const pager = document.getElementById("lib-pager");
  const noResults = document.getElementById("lib-noresults");
  const countLabel = document.querySelector(".library .tape__label");
  if (!list || !search || !pager) return; // empty library / not on this page

  const rows = [...list.querySelectorAll("li")];
  const total = rows.length;
  const baseCount = countLabel ? countLabel.textContent : "";
  let query = "";
  let page = 1;

  const tokens = () => query.split(/\s+/).filter(Boolean);

  function matches() {
    const ts = tokens();
    if (!ts.length) return rows;
    return rows.filter((li) => {
      const key = li.dataset.search || "";
      return ts.every((t) => key.includes(t));
    });
  }

  // Page numbers to show, always including 1 and last, with "…" for gaps.
  function windowPages(current, totalPages) {
    const wanted = new Set([1, totalPages, current, current - 1, current + 1]);
    const seq = [...wanted].filter((n) => n >= 1 && n <= totalPages).sort((a, b) => a - b);
    const out = [];
    let prev = 0;
    for (const n of seq) {
      if (n - prev > 1) out.push("…");
      out.push(n);
      prev = n;
    }
    return out;
  }

  function pagerItem(label, target, opts = {}) {
    const clickable = !opts.disabled && !opts.current && !opts.gap;
    const el = document.createElement(clickable ? "a" : "span");
    el.className = "pager__link";
    el.textContent = label;
    if (opts.gap) el.classList.add("pager__gap");
    if (opts.disabled) el.classList.add("pager__link--disabled");
    if (opts.current) {
      el.classList.add("pager__link--current");
      el.setAttribute("aria-current", "page");
    }
    if (clickable) {
      el.href = "#";
      el.dataset.page = String(target);
    }
    return el;
  }

  function renderPager(totalPages) {
    pager.replaceChildren();
    if (totalPages <= 1) {
      pager.hidden = true;
      return;
    }
    pager.hidden = false;
    pager.append(pagerItem("‹ Prev", page - 1, { disabled: page === 1 }));
    for (const p of windowPages(page, totalPages)) {
      pager.append(p === "…" ? pagerItem("…", null, { gap: true })
                             : pagerItem(String(p), p, { current: p === page }));
    }
    pager.append(pagerItem("Next ›", page + 1, { disabled: page === totalPages }));
  }

  function render() {
    const matched = matches();
    const totalPages = Math.max(1, Math.ceil(matched.length / PAGE_SIZE));
    page = Math.min(Math.max(page, 1), totalPages);

    const start = (page - 1) * PAGE_SIZE;
    const shown = new Set(matched.slice(start, start + PAGE_SIZE));
    rows.forEach((li) => { li.hidden = !shown.has(li); });

    if (noResults) {
      noResults.hidden = matched.length !== 0;
      if (matched.length === 0) noResults.textContent = `No recordings match “${query}”.`;
    }
    renderPager(totalPages);
    if (countLabel) {
      countLabel.textContent = query ? `Library · ${matched.length} of ${total}` : baseCount;
    }
  }

  let debounce;
  search.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      query = search.value.trim().toLowerCase();
      page = 1;
      render();
    }, 200);
  });

  pager.addEventListener("click", (e) => {
    const link = e.target.closest("a.pager__link");
    if (!link) return;
    e.preventDefault();
    const target = parseInt(link.dataset.page, 10);
    if (!Number.isNaN(target)) {
      page = target;
      render();
    }
  });

  render();
})();
