# 27 — Rename modal instead of a dead-end duplicate-name error

**Status: done.** Registering a project with a name that's already taken no
longer reloads the dashboard to a bare error message, losing the folder and
cloud-target selections you'd already made.

## The old behavior

The registration form (`dashboard.html`) was a plain HTML `<form method="post">`.
A 409 (duplicate name) response re-rendered the whole dashboard page with an
error paragraph — the folder picker, name, and cloud-target selections were
gone; you had to redo the whole three-step journey from
[25-folder-picker.md](25-folder-picker.md) just to pick a different name.

## The fix

`app.js`'s new `wireRegisterForm()` submits via `fetch()` instead, marked
with an `X-Requested-With: fetch` header. `web/routes.py`'s
`register_project_form` checks for that header and, when present, returns
JSON instead of a full HTML re-render — a plain non-JS form submission
(no header) behaves exactly as before, same validation, same
redirect-on-success.

On a 409 specifically, the JS shows a small modal (plain CSS/JS, no
library) pre-filled with a suggested alternative name (`{name}-2`),
editable, with a "Use this name" button that resubmits the same `fetch()`
call with the corrected name — the folder and cloud-target fields are
untouched in the DOM the whole time, so nothing has to be re-picked. 400s
(validation errors) show inline next to the form instead of a modal;
success redirects exactly like the old form did.

## Verified

- `tests/test_web.py`: the fetch path returns `201` + a `redirect` field on
  success, `400` + an `error` field on a bad name, `409` + an `error`
  message containing "already exists" on a genuine duplicate, and a plain
  form POST with no `X-Requested-With` header still gets the original
  `303` redirect — confirming the two paths don't interfere with each
  other.
