# 14 — Command Reference

One page. Bookmark it. Split into **end-user commands** (everything you need if you're
just running DeployMint) and **dev commands** (building it).

---

## 14.1 End-user quickstart

```bash
git clone <this-repo> deploymint && cd deploymint
```

```bash
cp .env.example .env
```

Edit `.env`: set `ANTHROPIC_API_KEY`.

```bash
docker compose up -d
```

```bash
open http://localhost:8000
```

That's the whole install. Everything below assumes this is already running.

---

## 14.2 The thin CLI

A separate, minimal client — see `09-phase-5-orchestration.md` §5.5. It talks to the
already-running container over HTTP/WebSocket; it never starts anything itself.

| Command | Description |
|---|---|
| `deploymint up <path>` | Register `<path>` (must be under `DEPLOYMINT_PROJECTS_DIR`) and deploy, streaming live. |
| `deploymint up <path> --name myapi` | Explicit project name (default: directory name). |
| `deploymint up <path> --no-deploy` | Generate and scan only. **Use this constantly during dev** — skips the docker build/deploy, ~20s instead of ~60-90s. |
| `deploymint up <path> --force` | Deploy even if security checks fail. Recorded in the audit log. |
| `deploymint up <path> --server http://host:8000` | Point at a non-default server (or set `DEPLOYMINT_SERVER`). |

No `deploymint server` command exists — the server is `docker compose up -d`, full stop.
No `deploymint doctor` CLI command either — that check is `GET /api/doctor`, since it
needs to run inside the container where the mounts and the LLM client actually live:

```bash
curl -s localhost:8000/api/doctor | python -m json.tool
```

### Exit codes for `up`

| Code | Meaning |
|---|---|
| 0 | success — pod (or docker-run container) running |
| 1 | failure — build or deploy error |
| 2 | blocked — security gate |
| 3 | server unreachable |

Distinct codes make DeployMint usable in CI.

---

## 14.3 Environment variables (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | Claude access. Unset → falls back to templates, resilience not offline mode. |
| `DEPLOYMINT_PROJECTS_DIR` | `./projects` | Host directory bind-mounted to `/workspace` in the app container. |
| `DEPLOYMINT_PORT` | `8000` | Host port the dashboard is published on. |
| `DEPLOYMINT_MODEL` | `claude-opus-5` | Override the model. |
| `KUBE_CONTEXT` | *(blank = current context)* | Which kubeconfig context to deploy into. Blank and unreachable → falls back to `docker run`. |
| `DEPLOYMINT_BLOCK_SEVERITY` | `critical` | Minimum severity that blocks. `critical`\|`high`\|`medium`. |
| `DEPLOYMINT_ENABLE_REDTEAM` | `true` | Toggle the Red Team LLM critique layer (the deterministic probes always run regardless). |
| `DEPLOYMINT_MAX_CONCURRENT_RUNS` | `2` | Semaphore limit. |
| `DEPLOYMINT_SQL_ECHO` | `false` | Log every SQL statement. |

`DATABASE_URL` is **not** in `.env` — it's set directly in `docker-compose.yml`,
pointing at the bundled `db` service. Only override it (via a real env var, not `.env`)
if you're running against an external Postgres — see `01-architecture.md` §1.4
decision 4.

---

## 14.4 Docker Compose — the whole app lifecycle

```bash
docker compose up -d
```

```bash
docker compose down
```

```bash
docker compose down -v
```

`-v` also removes the Postgres volume — a genuine full reset, not just a restart. This
is the `make reset` target.

```bash
docker compose logs -f app
```

```bash
docker compose ps
```

```bash
docker compose build
```

```bash
docker compose up -d --build
```

```bash
docker compose exec app bash
```

Get a shell **inside** the app container — this is where the docker socket, kubeconfig,
and workspace mounts all actually live, so it's the right place to poke around when
something's confusing.

```bash
docker compose restart app
```

```bash
docker compose restart db
```

Use this to test `pool_pre_ping` resilience deliberately — restart `db` mid-session and
confirm the next request still succeeds (`03-data-model.md` §3.3).

---

## 14.5 API

### Health & doctor

```bash
curl -s localhost:8000/health
```

```bash
curl -s localhost:8000/api/doctor | python -m json.tool
```

### Projects

Paths must resolve under `DEPLOYMINT_PROJECTS_DIR` — as seen from inside the container,
that's `/workspace/<name>`.

```bash
curl -s -X POST localhost:8000/api/projects -H 'content-type: application/json' -d '{"name":"myapi","repo_path":"/workspace/myapi"}'
```

```bash
curl -s localhost:8000/api/projects | python -m json.tool
```

```bash
curl -s -X POST localhost:8000/api/projects/1/analyze | python -m json.tool
```

```bash
curl -s localhost:8000/api/projects/1/graph | python -m json.tool
```

```bash
curl -s -X DELETE localhost:8000/api/projects/1 -o /dev/null -w '%{http_code}\n'
```

### Runs

```bash
curl -s -X POST localhost:8000/api/projects/1/runs -H 'content-type: application/json' -d '{"skip_deploy":true}'
```

```bash
curl -s localhost:8000/api/runs/run_abc123def456 | python -m json.tool
```

```bash
curl -s "localhost:8000/api/runs?project_id=1&limit=10" | python -m json.tool
```

```bash
curl -s localhost:8000/api/runs/run_abc123def456/artifacts | python -m json.tool
```

```bash
curl -s localhost:8000/api/runs/run_abc123def456/artifacts/Dockerfile
```

```bash
curl -s localhost:8000/api/runs/run_abc123def456/audit | python -m json.tool
```

```bash
curl -s localhost:8000/api/runs/run_abc123def456/audit/verify
```

```bash
curl -s -X POST localhost:8000/api/runs/run_abc123def456/cancel
```

### Chat & costs

```bash
curl -s -X POST localhost:8000/api/chat -H 'content-type: application/json' -d '{"message":"deploy my myapi project"}'
```

```bash
curl -s -X POST localhost:8000/api/costs/query -H 'content-type: application/json' -d '{"question":"which service costs the most?"}'
```

### WebSocket (bypassing the CLI)

```bash
python -c "
import asyncio, json, websockets
async def main():
    async with websockets.connect('ws://localhost:8000/ws/runs/RUN_ID') as ws:
        await ws.send(json.dumps({'since': 0}))
        async for m in ws:
            e = json.loads(m)
            print(e['seq'], e['type'], str(e['payload'])[:100])
asyncio.run(main())
"
```

### Interactive docs

```bash
open http://localhost:8000/docs
```

---

## 14.6 Kubernetes (only relevant if you have a cluster)

Not required — see `01-architecture.md` §1.4 decision 12. The app deploys to whatever
`~/.kube/config` mounts, or falls back to `docker run` if nothing is reachable.

```bash
kubectl config current-context
```

```bash
kubectl get pods -l managed-by=deploymint
```

```bash
kubectl describe pod <pod-name>
```

```bash
kubectl logs <pod-name> --tail=100 -f
```

```bash
kubectl port-forward svc/myapi-svc 8081:8000
```

```bash
kubectl delete deployment,service -l managed-by=deploymint
```

Or, from inside the app container, the same cleanup as a single command:

```bash
docker compose exec app python -m deploymint.scripts.clean
```

Install metrics-server (only needed for the Observability Oracle's real CPU/memory
numbers — the deterministic restart/crashloop checks work without it):

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

```bash
kubectl patch deployment metrics-server -n kube-system --type=json -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

---

## 14.7 Docker (the host's daemon, via the mounted socket)

Every image DeployMint builds is visible here directly — the app container built it via
Docker-outside-of-Docker (`08-phase-4-execution.md` §4.1a), not in an isolated nested
daemon.

```bash
docker images | grep deploymint
```

```bash
docker ps --filter label=managed-by=deploymint
```

```bash
docker logs <container-name> --tail 100
```

```bash
docker image prune -f --filter "label=managed-by=deploymint"
```

Manually testing a build the app would do:

```bash
docker build -f ./projects/myapi/.deploymint/<run_id>/Dockerfile -t test:local ./projects/myapi
```

```bash
docker run --rm -p 8099:8000 test:local
```

---

## 14.8 tmux — recorded sessions (now inside the container)

```bash
docker compose exec app tmux ls
```

```bash
docker compose exec app tmux attach -t deploymint-<run_id>
```

Detach without killing: `Ctrl-b` then `d`.

```bash
docker compose exec app tmux kill-session -t deploymint-<run_id>
```

Raw session log, from the host (it lives under the mounted projects volume, so it's
directly readable without going through the container at all):

```bash
cat ./projects/myapi/.deploymint/<run_id>/session.log | head -50
```

---

## 14.9 Postgres — the bundled database

```bash
docker compose exec db psql -U deploymint
```

```bash
docker compose exec db psql -U deploymint -c "\dt"
```

```bash
docker compose exec db psql -U deploymint -c "SELECT id, name, language, framework FROM projects;"
```

```bash
docker compose exec db psql -U deploymint -c "SELECT id, status, current_node, duration_ms, model_used FROM runs ORDER BY created_at DESC LIMIT 10;"
```

```bash
docker compose exec db psql -U deploymint -c "SELECT seq, type FROM events WHERE run_id='run_abc' ORDER BY seq;"
```

```bash
docker compose exec db psql -U deploymint -c "SELECT seq, agent, action, exit_code, left(hash,12) FROM audit_logs WHERE run_id='run_abc' ORDER BY seq;"
```

Tamper demo — break the chain on purpose:

```bash
docker compose exec db psql -U deploymint -c "UPDATE audit_logs SET output='nothing happened here' WHERE run_id='run_abc' AND seq=3;"
```

Then hit `/api/runs/run_abc/audit/verify` and watch it report `broken_at_seq: 3`.

Querying inside the JSONB columns directly:

```bash
docker compose exec db psql -U deploymint -c "SELECT id, name FROM runs WHERE security @> '{\"passed\": false}';"
```

---

## 14.10 Security tooling (baked into the image — these run inside the container)

```bash
docker compose exec app checkov -f Dockerfile --framework dockerfile -o json --quiet
```

```bash
docker compose exec app opa version
```

```bash
docker compose exec app opa eval --format pretty --input /tmp/in.json --data deploymint/policies/ "data.deploymint"
```

```bash
docker compose exec app opa check deploymint/policies/
```

`opa check` validates your Rego without needing input. Run it every time you edit a
policy, before rebuilding the image.

---

## 14.11 Local development (before/without Docker Compose)

For iterating on the app's own code — see `00-prerequisites.md` §0.4 and
`05-phase-1-foundation.md` for the full setup.

```bash
source venv/bin/activate
```

```bash
docker run -d --name deploymint-dev-db -e POSTGRES_USER=deploymint -e POSTGRES_PASSWORD=deploymint -e POSTGRES_DB=deploymint -p 5432:5432 postgres:16-alpine
```

```bash
DEPLOYMINT_WORKSPACE_ROOT=$(pwd)/tests/fixtures ANTHROPIC_API_KEY=sk-ant-... uvicorn deploymint.server:app --reload
```

```bash
pytest -m "not slow" -v
```

```bash
pytest -m llm -v
```

```bash
ruff check deploymint tests
```

```bash
ruff format deploymint tests
```

Full reset of the dev database:

```bash
docker rm -f deploymint-dev-db
```

---

## 14.12 Troubleshooting quick reference

| Symptom | First thing to check |
|---|---|
| `docker compose up` fails immediately | Is Docker Desktop running? Is `ANTHROPIC_API_KEY` set in `.env`? |
| `ErrImagePull` / `ImagePullBackOff` | if using kind: `kind load docker-image` ran? `imagePullPolicy: IfNotPresent`? |
| `CrashLoopBackOff` | `kubectl logs <pod>` — usually a wrong CMD or a missing dependency |
| Pod never becomes ready | probe path/port vs. what the app actually serves |
| `CreateContainerConfigError` | `readOnlyRootFilesystem` + app writes to disk → add an `emptyDir` at `/tmp` |
| Deploy succeeds but `deployment.mode` is `"docker"` when you expected Kubernetes | is `~/.kube/config` actually mounted? Is the context in it reachable from inside the container? |
| `ConnectionDoesNotExistError` after a while | `pool_pre_ping` missing, or the `db` container actually restarted — `docker compose ps` |
| Generation always falls back to template | `/api/doctor` — is `ANTHROPIC_API_KEY` valid and the API reachable? |
| Checkov "crashes" on findings | exit code 1 means findings exist, not failure |
| OPA `rego_parse_error` | Rego v0 vs v1 — check `opa version` inside the container |
| `kubectl top` returns nothing | metrics-server not installed on the cluster |
| Package data missing after a rebuild | `[tool.setuptools.package-data]` in `pyproject.toml`, then `docker compose build --no-cache` |
| Two tabs each miss half the events | per-client queues not implemented in `EventBus` (`09-phase-5-orchestration.md` §5.3) |
| `deploymint up` says "cannot reach DeployMint" | `docker compose ps` — is the `app` service actually running? |
