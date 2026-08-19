# 25 — Folder picker on the dashboard

**Status: done.** Registering a project no longer requires typing an exact
`/workspace/<name>` path by hand — the dashboard offers a real folder picker.

## Why this had to be server-driven

The obvious approach — `<input type="file" webkitdirectory>`, the browser's
native folder picker — doesn't work here. Browsers deliberately never expose
the absolute filesystem path of a folder picked that way to JavaScript (only
a relative file list via `webkitRelativePath`), as a sandboxing protection.
There's no client-side workaround for that; it's not a bug to fix, it's how
browsers are built.

Since DeployMint can only ever see folders already mounted under
`./projects` (as `/workspace/*` inside the container) anyway, the actual
right answer is simpler than a real file-system picker: **list what's really
there and let the user choose from that**, server-side.

## What changed

- `core/sandbox.py`'s new `list_workspace_dirs()` lists top-level directory
  names under `Settings.workspace_root` (skipping dotfiles/dotdirs) — the
  same root `validate_repo_path()` already enforces every registration
  against.
- `web/routes.py`'s `dashboard()` and `register_project_form()`'s error paths
  pass `workspace_dirs` into the template.
- `dashboard.html`: when at least one folder exists, the repo-path field
  becomes a `<select>` of real folder names (value `/workspace/<name>`)
  instead of a free-text input. With zero folders present, it falls back to
  the original text input with a hint to copy a repo in first — never a
  broken empty dropdown.
- `app.js`'s `wireFolderPicker()` auto-fills the name field from whichever
  folder gets picked (only if the name field is still empty) — one less
  thing to type, not a required step.
- The registration form itself was also restructured into three visible
  steps (pick a folder → name it → cloud target) instead of one flat row of
  fields, as part of the same "clearer dashboard journey" pass.

## Verified

- `tests/test_sandbox.py`: `list_workspace_dirs()` against an empty
  workspace, a workspace with real subdirectories, and one with a stray file
  and a dotdir mixed in (both correctly excluded).
- `tests/test_web.py`: the dashboard renders the `<select>` populated with a
  real mounted folder when one exists, and falls back to the plain text
  input with zero folders present.
