# 24 — Landing/dashboard split + user-facing docs

**Status: done.** `/` is now a pure marketing page, the register-a-project
functionality moved to `/dashboard`, and the in-app docs viewer (`/guide`)
was repointed from this project's own build log to genuine end-user
documentation.

## Why

Two separate but related pieces of feedback drove this:

1. The landing page needed to actually sell the product — a real hero
   (heading → description → button, in that order), with generated artwork,
   and a legitimate marketing page below it — not a register form as the
   first thing anyone sees.
2. The in-app docs viewer (`/guide`, built in [20-in-app-docs.md](20-in-app-docs.md))
   rendered `docs/PROJECT.md` plus the numbered build-log docs 00-23 —
   "Phase 1: Foundation", the decisions log, the risk register. That's
   contributor/build history. Someone deploying their own repo through
   DeployMint doesn't need to know how DeployMint itself was built; they
   need to know what it does and how to use it. Confirmed with the user:
   replace the nav entirely rather than adding a second tab — the old docs
   stay on disk under `docs/*.md`, they just aren't reachable from `/guide`
   anymore.

## What changed

- **`web/routes.py`**: `index()` at `GET /` is now a static render with no DB
  query at all — pure marketing. The old `index()` (project query, register
  form handling) moved to a new `dashboard()` at `GET /dashboard`, rendering
  `dashboard.html` (this is the old `index.html` content, unchanged
  functionally). `register_project_form`'s error/success paths point at
  `dashboard.html` / `/dashboard` instead of `index.html` / `/`.
- **`base.html`**: nav's "Projects" link is now "Dashboard" → `/dashboard`.
  Brand link stays `/`.
- **`index.html`**: rewritten as a real landing page — a white-themed hero
  (see below), a stats strip, the seven-agent pipeline visualized, and a
  feature-highlight grid. Nothing functional lives here anymore.
- **The hero went from a dark gradient to white/light-themed**, consistent
  with the rest of the app's palette rather than being the one loud
  exception to it — an eyebrow tag, heading, description, and two buttons
  (`Enter Dashboard →`, `Read the docs`), then a small original inline SVG
  illustration (code → shield → cloud, connected by dashed lines) instead of
  a stock photo. This app is explicitly self-contained with no CDN/npm and
  runs entirely on the user's machine — a "found" stock image doesn't fit
  that (licensing, and it wouldn't look like it belongs), so the artwork is
  hand-authored SVG markup, matching the same emoji-glyph agent icons used
  on the run page for visual continuity.
- **`web/docs_content.py`**: `NAV` now points at 10 new files under
  `docs/user-guide/` instead of `docs/PROJECT.md` + the 00-23 build log.
  `render()`/`get_page()` logic is unchanged — it was already generic over
  any `DocPage`.
- **`docs/user-guide/00-overview.md` through `09-faq.md`**: new content,
  framed entirely around using DeployMint — what it does, getting started,
  what each of the seven agents does *for you*, the six generated artifact
  formats and when to use each, the security gate, cost tracking, Cloud
  Deploy, the chat assistant (documented honestly — `status`/`cost`/
  `rollback` intents are noted as not yet wired up, matching what's actually
  shipped today, not what's planned), the CLI, and an FAQ.
- **`docs.html`/`style.css`**: the docs layout now uses the same design
  tokens (`--sp-*`, `--text-*`, `--shadow-*`) as the rest of the redesigned
  app instead of predating that pass.

## Verified

- `tests/test_web.py`: `/` renders as pure marketing (no register form in
  the HTML) and links to `/dashboard`; `/dashboard` has the register form
  and lists registered projects.
- `tests/test_docs_viewer.py`: the new NAV excludes stale build-log titles
  ("Phase 1", "Decisions Log", etc.) and includes the new user-facing ones;
  every internal cross-link between user-guide pages resolves to a real NAV
  slug (a link written as a relative `.md` filename instead of `/guide/<slug>`
  would 404 silently for a reader clicking through — caught by rewriting
  every internal link in the new docs to the actual route paths before this
  test was added).
- Manually verified against the real containerized stack: `/` shows the
  white hero with working SVG artwork and both CTA buttons, `/dashboard`
  has the exact same register-project functionality as before the split,
  and `/guide` opens on the new Overview page with the 10-page nav — no
  "Phase 1" or "Decisions Log" anywhere in the sidebar.
