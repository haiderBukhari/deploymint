# 20 — In-App Docs Viewer

**Status: done.** A GitBook-style sidebar + page view, live inside the dashboard at
`/guide`, over documentation that already existed — no new writing, just a new way to
browse it.

---

## What it is

Every file in `docs/` (00 through 20) plus `docs/PROJECT.md` is rendered as a browsable
page with a sticky sidebar for navigation — click a page, it opens, exactly like this
one. Nothing here is newly written content: it's the same build log, architecture doc,
and decisions log a contributor would read in the repo, just reachable from inside the
running app instead of requiring a checkout.

## Why not fresh, simplified end-user pages instead?

That was the other option on the table (a curated quickstart/reference section written
for someone just using the product, separate from the contributor-facing build docs).
Rendering what already exists was chosen instead — it's zero new writing, and the
existing docs are already extensive enough (architecture decisions, the full phase-by-
phase build log, a risk register, a decisions log recording every real bug found along
the way) that duplicating a simplified version alongside them would mean keeping two
sources of truth in sync. If a genuinely different, simpler end-user-only doc set is
wanted later, it's a separate decision, not a natural evolution of this one.

## How it works

- `deploymint/web/docs_content.py`: a static `NAV` list of `(slug, title, path)` —
  order matches the README's own doc index. `render(page)` reads the file and converts
  it with the `markdown` library (`fenced_code`, `tables`, `toc`, `sane_lists`
  extensions) — pure Python, no npm build step, no CDN.
- `GET /guide` redirects to the first page (`docs/PROJECT.md`, the zero-to-full-picture
  overview); `GET /guide/{slug}` renders any other page from the nav list.
- `docs.html`: a two-column layout (sticky sidebar, content pane) reusing the same
  card/typography system as the rest of the dashboard — see the `.markdown-body` rules
  in `style.css` for how headings, code blocks, tables, and blockquotes are styled to
  match.

## Two real bugs found while building this — both from actually testing, not review

**The route can't be `/docs`.** FastAPI serves its own interactive Swagger UI at `/docs`
by default (`server.py` never overrides `docs_url`). A route registered at that same
path doesn't error — it just silently never gets hit, because FastAPI's own built-in
route already owns it. This was caught by a test that actually issued `GET /docs` and
asserted on the *content* (checking for `docs-nav`, not just a 200 status) rather than
only checking the route responded — a weaker test would have passed while the page
served was Swagger UI, not this viewer. Renamed to `/guide` to sidestep the collision
entirely rather than trying to override FastAPI's default.

**`docs/PROJECT.md`, not `PROJECT.md` at the repo root.** The initial version assumed
the project overview doc lived at the repo root (`REPO_ROOT / "PROJECT.md"`) — it
doesn't; it's `docs/PROJECT.md`, alongside every other numbered doc. `Path.is_file()`
on the wrong path doesn't raise, it just returns `False`, so this silently fell through
to the "not available in the running container" placeholder rather than an error.
Caught by a test that checks `.is_file()` on every `NAV` entry directly, independent of
the HTTP layer.

## Getting the files into the container at all

`docs/` lives at the repo root, not inside the `deploymint/` Python package — and the
Dockerfile only ever `COPY`s the package itself. Without a change, the docs viewer
would work in local dev (running from a checkout, where the files are just there) and
then silently show "not available" for every page inside the actual shipped container.
Fixed by adding `COPY docs/ ./docs/` to the Dockerfile, right after the package copy —
same relative layout in both places (`docs_content.py` resolves paths from its own file
location, two directories up, not from the process's current working directory), so no
path translation is needed between a dev checkout and the container.

## Verified

- Every page in `NAV` resolves to a real file and renders actual HTML (not raw
  markdown text, not the "unavailable" placeholder) — checked both directly
  (`Path.is_file()` on every nav entry) and over HTTP (every slug, parametrized)
- A dedicated regression test confirms `GET /docs` still serves Swagger UI, not this
  viewer — protects against a future edit accidentally reintroducing the collision
- Confirmed the Dockerfile change matters: without it, every page under `/guide` would
  render "not available in the running container" the moment the image ships — the
  kind of bug that's invisible in local dev and only shows up in what's actually
  shipped, matching the pattern the Phase 7 correction log describes for a different
  bug entirely
