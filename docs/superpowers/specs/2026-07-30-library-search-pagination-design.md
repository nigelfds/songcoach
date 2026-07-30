# Library search + pagination — design

**Date:** 2026-07-30
**Status:** Approved, ready for plan

## Goal

As the recordings library grows, make songs quick to find. Two client-side
usability features on the landing-page library:

1. **Pagination** — 10 rows per page, windowed page-number links (`‹ Prev  1 … 4
   [5] 6 … 25  Next ›`).
2. **Quick search** — a filter box over the library; matches the keyword against
   the song **title** and **artist**; debounced so it only filters after a short
   pause in typing.

Everything is **client-side** (no server round-trips) so it's instant. It
operates on the already server-rendered `<li>` rows — no re-fetch, no re-render
of markup (thumbnails, links, badges are preserved).

## Non-goals

- No server-side pagination/search API. No infinite scroll.
- No fuzzy/ranked search — plain case-insensitive substring, tokenized.
- No URL/query-string state or persistence across reloads (in-memory only).
- The player page and other views are unchanged.

## Data source

The library is server-rendered in `index.html`: `<section class="library">` →
`<ul class="rec-list">` → one `<li>` per job (each an `<a class="rec">` with
`.rec__name` = title, `.rec__artist` = artist · duration, a thumbnail, and a
status/Practice badge). All jobs are rendered.

The JS filters/paginates by showing/hiding these existing `<li>` rows.

## Markup additions (`songcoach/templates/index.html`, inside `{% if jobs %}`)

- **Per-row search key** — each `<li>` gets:
  ```html
  <li data-search="{{ (job.title ~ ' ' ~ (job.artist or '')) | lower }}">
  ```
  So matching uses a clean lowercased "title artist" string (no scraping the
  duration out of `.rec__artist`).
- **Search box** — between `.library__head` and the `<ul>`:
  ```html
  <div class="library__search">
    <input id="lib-search" type="search" class="meta__in"
           placeholder="Search by song or artist…" autocomplete="off"
           spellcheck="false" aria-label="Search recordings" />
  </div>
  ```
- **Pager + empty-match message** — after the `<ul class="rec-list">`:
  ```html
  <nav id="lib-pager" class="pager" aria-label="Library pages" hidden></nav>
  <p id="lib-noresults" class="library__empty" hidden></p>
  ```

These render only when `{% if jobs %}` (a non-empty library); the empty-library
`library__empty` case is unchanged.

## Behaviour (`songcoach/static/js/library-list.js`, new; loaded like `apple-music.js`)

Module state: `PAGE_SIZE = 10`, `query = ""`, `page = 1`, and a cached
`rows = [...ul.querySelectorAll("li")]`.

- **`matches()`** → the rows to show. Empty query → all rows. Otherwise split the
  query on whitespace into tokens; a row matches iff **every** token is a
  substring of its `data-search` (AND semantics). Case-insensitive (query and
  key are both lowercased).
- **`render()`**:
  1. `matched = matches()`; `totalPages = max(1, ceil(matched.length / 10))`;
     clamp `page` into `[1, totalPages]`.
  2. Hide every row (`hidden = true`); show only
     `matched.slice((page-1)*10, page*10)`.
  3. Rebuild the pager (see below) into `#lib-pager`; hide it when
     `totalPages <= 1`.
  4. Toggle `#lib-noresults` (text: `No recordings match "<query>".`) when
     `matched.length === 0`; the `<ul>` is effectively empty then.
  5. Update the header count: default `Library · N recording(s)`; while a query
     is active, `Library · <matched> of <total>`.
- **Search input** — `input` event, **debounced ~200 ms**: set
  `query = value.trim().toLowerCase()`, `page = 1`, `render()`.
- **Pager** — click handler (event-delegated) reads `data-page` on the clicked
  link → set `page`, `render()`. Prev/Next map to `page-1`/`page+1` (disabled/no
  `data-page` at the ends).
- Initial `render()` on load.

### Pager windowing

Given `page` and `totalPages`, build the sequence: always page 1 and
`totalPages`; the current page and its neighbours (`page-1 … page+1`); insert an
ellipsis (`…`, non-clickable) where the numeric sequence skips. Prepend
`‹ Prev` and append `Next ›`; both disabled (rendered as non-links) at the first
/ last page. The current page is marked (`aria-current="page"` + a class) and is
not a link.

Example (`page=5, totalPages=25`): `‹ Prev  1 … 4 [5] 6 … 25  Next ›`.

## CSS (`songcoach/static/css/styles.css`)

- `.library__search` + its input in the app's existing input idiom (reuse
  `.meta__in`; add margin so it sits between the head and the list).
- `.pager` (flex row, centered, gap), `.pager__link` (chip-like), the current
  page (`.pager__link[aria-current]` highlighted, non-interactive), disabled
  Prev/Next (dimmed), and the ellipsis (muted, non-interactive).

## Edge cases

- **Query matches nothing** → no rows shown, pager hidden, "No recordings
  match …" shown, count reads `· 0 of N`.
- **≤ 1 page** (e.g. ≤10 matches) → pager hidden.
- **Clearing the query** → all rows return, page resets to 1, count back to plain.
- **New rows** (e.g. after an Apple Music session appears on next load): rows are
  re-collected on page load, so a normal reload picks them up — consistent with
  today's server-rendered library. (No live DOM-mutation handling needed.)

## Testing

No JS unit runner exists in the repo, so verify via **Playwright** (as
`library.js` / `apple-music.js` were): seed a library with enough recordings to
page (either the real `./data`, or an isolated data dir with >10 fixture jobs),
then:
- Type a query matching a subset → only matching rows visible, page reset to 1,
  count shows `X of N`, non-matches hidden.
- A two-token query (`"artist word"`) matches rows containing both tokens.
- Clear the query → all rows back.
- With >10 rows and no query → page 2 link + Prev/Next present; clicking page 2
  shows rows 11–20; Prev returns to page 1; Prev disabled on page 1.
- A no-match query → "No recordings match" message, pager hidden.

The `data-search` template change is exercised by the above.

## Files touched

- **New**: `songcoach/static/js/library-list.js`.
- **Edit**: `songcoach/templates/index.html` (search box, `data-search`, pager +
  no-results nodes, load the new script), `songcoach/static/css/styles.css`
  (search + pager styles).
