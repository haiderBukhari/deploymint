# CLI reference

`deploymint` is a thin client — it never runs any agent code itself, it
talks HTTP/WebSocket to a DeployMint container you already started with
`docker compose up -d`.

## Install

```bash
cd cli
pip install -e .
```

## `deploymint up`

```bash
deploymint up PATH [OPTIONS]
```

Registers `PATH` as a project (if it isn't already) and triggers a deploy,
streaming the full pipeline live in your terminal — the same seven steps you'd
watch on the run page, rendered as a live-updating table.

| Option | Effect |
|---|---|
| `--name TEXT` | Project name (defaults to the last path segment). Gets sanitized the same way the web form does. |
| `--force` | Deploy even if the security gate would otherwise block it. |
| `--no-deploy` | Generate and scan only — skip the actual build/deploy step. |
| `--server URL` | Where to reach DeployMint (default `http://localhost:8000`, or set `DEPLOYMINT_SERVER`). |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `2` | Blocked by the security gate. |
| `1` | Any other failure. |
| `3` | The DeployMint server itself is unreachable. |

Exit codes make it straightforward to wire `deploymint up` into your own CI
pipeline as a gate — a non-zero exit means don't proceed.
