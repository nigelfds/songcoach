# Library Search + Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add client-side quick-search (debounced, title+artist) and windowed pagination (10/page) to the landing-page library, operating on the existing server-rendered `<li>` rows.

**Architecture:** One server touch — a `data-search` attribute per row. All behaviour lives in a new self-contained `library-list.js` (an IIFE) that filters/paginates by showing/hiding the existing rows. No re-fetch, no API changes.

**Tech Stack:** Jinja2 template, vanilla JS, CSS. Verified via Playwright (no JS unit runner in this repo).

## Global Constraints

- **Client-side only** — no server round-trips, no new endpoints. Operate on the existing `<ul class="rec-list"> <li>` rows.
- **Search**: debounced **~200 ms**; query split on whitespace into tokens; a row matches iff **every** token is a substring of its lowercased `data-search`; typing resets to **page 1**.
- **Pagination**: `PAGE_SIZE = 10` over the *filtered* set; windowed pager `‹ Prev  1 … 4 [5] 6 … 25  Next ›` (first & last always shown, current ±1, `…` for gaps, Prev/Next disabled at ends); pager hidden when `totalPages <= 1`.
- **Guard the empty-library case**: the new nodes render only inside `{% if jobs %}`; `library-list.js` early-returns if they're absent.
- Reuse existing CSS vars: `--ink`, `--ink-dim`, `--bg`, `--line-soft`.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 1: Library search + pagination

**Files:**
- Create: `songcoach/static/js/library-list.js`
- Modify: `songcoach/templates/index.html` (`data-search`, search box, pager + no-results nodes, load script)
- Modify: `songcoach/static/css/styles.css` (search + pager styles)

**Interfaces:**
- Consumes: the server-rendered `.rec-list > li` rows; the library header label `.library .tape__label`.
- Produces: no code interface; a working UI. New DOM ids: `#lib-search`, `#lib-pager`, `#lib-noresults`.

- [ ] **Step 1: Add `data-search` to each row**

In `songcoach/templates/index.html`, the library loop currently opens each row with a bare `<li>`. Replace that opening tag (inside `{% for job in jobs %}`) — change:

```html
      <li>
        <a class="rec" href="/jobs/{{ job.id }}">
```

to:

```html
      <li data-search="{{ ((job.title or 'Untitled') ~ ' ' ~ (job.artist or '')) | lower }}">
        <a class="rec" href="/jobs/{{ job.id }}">
```

- [ ] **Step 2: Add the search box, pager, and no-results nodes**

Still in `index.html`, the library section is:

```html
    {% if jobs %}
    <ul class="rec-list">
      ...
    </ul>
    {% else %}
    <p class="library__empty">No recordings yet. Capture your first one above.</p>
    {% endif %}
```

Insert the search box between `{% if jobs %}` and `<ul class="rec-list">`:

```html
    {% if jobs %}
    <div class="library__search">
      <input id="lib-search" type="search" class="meta__in"
             placeholder="Search by song or artist…" autocomplete="off"
             spellcheck="false" aria-label="Search recordings" />
    </div>
    <ul class="rec-list">
```

Insert the pager + no-results node between the closing `</ul>` and `{% else %}`:

```html
    </ul>
    <nav id="lib-pager" class="pager" aria-label="Library pages" hidden></nav>
    <p id="lib-noresults" class="library__empty" hidden></p>
    {% else %}
```

- [ ] **Step 3: Load the script**

Change the scripts block from:

```html
{% block scripts %}<script src="/static/js/app.js"></script><script src="/static/js/library.js"></script><script src="/static/js/apple-music.js"></script>{% endblock %}
```

to (append `library-list.js`):

```html
{% block scripts %}<script src="/static/js/app.js"></script><script src="/static/js/library.js"></script><script src="/static/js/apple-music.js"></script><script src="/static/js/library-list.js"></script>{% endblock %}
```

- [ ] **Step 4: Create `library-list.js`**

Create `songcoach/static/js/library-list.js`:

```javascript
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
```

- [ ] **Step 5: Add styles**

Append to `songcoach/static/css/styles.css`:

```css
/* Library search + pagination */
.library__search { margin: .9rem 0 .1rem; }
.library__search input { width: 100%; }

.pager { display: flex; flex-wrap: wrap; align-items: center; justify-content: center;
  gap: .35rem; margin: 1rem 0 .1rem; }
.pager__link { min-width: 2rem; text-align: center; padding: .3rem .55rem; border-radius: 8px;
  font-size: .9rem; color: var(--ink-dim); text-decoration: none; border: 1px solid transparent; }
a.pager__link:hover { color: var(--ink); border-color: var(--line-soft); }
.pager__link--current { color: var(--ink); font-weight: 700; background: var(--bg);
  border-color: var(--line-soft); }
.pager__link--disabled { opacity: .4; }
.pager__gap { color: var(--ink-dim); }
```

- [ ] **Step 6: Verify (implementer: suite + render smoke-check; controller: Playwright)**

Do NOT run Playwright — the controller runs the browser acceptance. Implementer verification:

1. Backend suite unaffected but confirm green: `.venv/bin/python -m pytest -q` → 73 passed.
2. Render smoke-check (uses the real `./data`, which has >10 recordings → 2 pages):
```
.venv/bin/python -m uvicorn songcoach.main:app --port 8146 >/tmp/ll.log 2>&1 &
SRV=$!; sleep 4
curl -s http://127.0.0.1:8146/ | grep -o -E 'id="lib-(search|pager|noresults)"|data-search=' | sort | uniq -c
curl -s http://127.0.0.1:8146/static/js/library-list.js | head -2
kill $SRV
```
Expect: `id="lib-search"`, `id="lib-pager"`, `id="lib-noresults"` each present; multiple `data-search=` occurrences (one per row); `library-list.js` served (not 404).

**Controller Playwright acceptance** (against the real `./data`, read-only):
- Load `/`; assert the pager shows page 1 + 2 + `Next ›`, with `‹ Prev` disabled; assert exactly 10 rows visible.
- Click page `2` → rows 11–N visible (≤10); `‹ Prev` now enabled. Click `‹ Prev` → back to page 1.
- Type a known unique substring (e.g. `billie`) in `#lib-search` → after the debounce, only matching rows visible, count shows `· 1 of N`, pager hidden (≤1 page).
- Type a two-token query that matches one row's title+artist → that row shows.
- Type gibberish (`zzzzz`) → `#lib-noresults` visible ("No recordings match …"), no rows.
- Clear the box → all rows back, page 1, count restored.

- [ ] **Step 7: Commit**

```bash
git add songcoach/static/js/library-list.js songcoach/templates/index.html songcoach/static/css/styles.css
git commit -m "feat(library): client-side search + windowed pagination"
```

---

## Self-Review

**Spec coverage:**
- `data-search` per row (lowercased title+artist, Untitled fallback) → Step 1. ✓
- Search box, pager, no-results nodes inside `{% if jobs %}` → Step 2. ✓
- Script loaded alongside the others → Step 3. ✓
- Debounced (200ms) tokenized-AND search, reset to page 1 → Step 4 (`search` listener, `matches`). ✓
- 10/page over filtered set, windowed pager, hidden at ≤1 page → Step 4 (`render`, `renderPager`, `windowPages`). ✓
- No-match message + count `X of N` + restore on clear → Step 4 (`render`). ✓
- Empty-library guard → `if (!list || !search || !pager) return`. ✓
- Styles → Step 5. ✓
- Playwright verification → Step 6. ✓

**Placeholder scan:** No TBD/TODO; complete code in every step.

**Type/name consistency:** ids `#lib-search`, `#lib-pager`, `#lib-noresults`, class `.rec-list`, `.library .tape__label`, `data-search` used identically in the template and JS. `windowPages`/`renderPager`/`pagerItem`/`matches`/`render` are internally consistent. `PAGE_SIZE = 10` matches the spec.

**Note for the implementer:** rows are hidden via the `hidden` DOM property (`li.hidden = true`), which visually removes them regardless of the `.rec-list` grid layout — no CSS needed for hiding. The `pager` click handler only fires for `a.pager__link` (real links), so `…`/current/disabled `<span>`s are inert.
