# 14 — Command Reference

One page. Bookmark it.

## 14.1 Environment

```bash
source /Users/haiderbukhari/Public/DeployMint/venv/bin/activate
```

Always. Your system `python3` is 3.15-alpha and will not install this project's
dependencies.

---

## 14.2 DeployMint CLI

| Command | Description |
|---|---|
| `deploymint doctor` | Check every prerequisite. Exit 1 if any required check fails. |
| `deploymint server` | Start the local server on `127.0.0.1:8000`. |
| `deploymint server --reload` | Dev mode, auto-restart on file change. |
| `deploymint server --port 8010` | Alternate port. |
| `deploymint up ./repo` | Analyze → generate → scan → deploy, streaming live. |
| `deploymint up ./repo --name myapi` | Explicit project name (default: directory name). |
| `deploymint up ./repo --no-deploy` | Generate and scan only. **Use this constantly during dev.** |
| `deploymint up ./repo --force` | Deploy even if security checks fail. Recorded in the audit log. |
| `deploymint clean` | Remove DeployMint-managed deployments from the cluster. |
| `deploymint clean --all` | Also prune built `deploymint/*` images. |
| `deploymint export <run_id> ./repo` | Write generated artifacts into the repo (post-MVP). |
| `deploymint --version` | Version. |

### Exit codes for `up`

| Code | Meaning |
|---|---|
| 0 | success — pod running |
| 1 | failure — build or deploy error |
| 2 | blocked — security gate |
| 3 | server unreachable |

Distinct codes make DeployMint usable in CI. Mention that when someone asks how it fits
an existing workflow.

---

## 14.3 Environment variables

All prefixed `DEPLOYMINT_`.

| Variable | Default | Purpose |
|---|---|---|
| `DEPLOYMINT_HOME` | `~/.deploymint` | DB, artifacts, sessions. Set to a tmpdir in tests. |
| `DEPLOYMINT_HOST` | `127.0.0.1` | Server bind address. |
| `DEPLOYMINT_PORT` | `8000` | Server port. |
| `DEPLOYMINT_MODEL` | `llama3.1:8b` | Ollama model. Use `llama3.2` for a faster dev loop. |
| `DEPLOYMINT_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint. |
| `DEPLOYMINT_LLM_TIMEOUT` | `180` | Seconds before giving up on generation. |
| `DEPLOYMINT_KUBE_CONTEXT` | `kind-deploymint` | kubectl context. |
| `DEPLOYMINT_KIND_CLUSTER` | `deploymint` | kind cluster name for `kind load`. |
| `DEPLOYMINT_ROLLOUT_TIMEOUT` | `120` | Seconds to wait for a rollout. |
| `DEPLOYMINT_BLOCK_SEVERITY` | `critical` | Minimum severity that blocks. `critical`\|`high`\|`medium`. |
| `DEPLOYMINT_ENABLE_REDTEAM` | `true` | Toggle the Red Team node. |
| `DEPLOYMINT_MAX_CONCURRENT_RUNS` | `2` | Semaphore limit. |
| `DEPLOYMINT_SQL_ECHO` | `false` | Log every SQL statement. |

---

## 14.4 API

### Health

```bash
curl -s localhost:8000/health
```

```bash
curl -s localhost:8000/api/doctor | python -m json.tool
```

### Projects

```bash
curl -s -X POST localhost:8000/api/projects -H 'content-type: application/json' -d '{"name":"myapi","repo_path":"/abs/path/to/repo"}'
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
curl -s localhost:8000/api/runs/run_abc123def456/session
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

### WebSocket (from the CLI)

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

FastAPI generates these for free:

```bash
open http://localhost:8000/docs
```

---

## 14.5 Kubernetes

```bash
kind create cluster --name deploymint
```

```bash
kind delete cluster --name deploymint
```

```bash
kubectl config use-context kind-deploymint
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

```bash
kind load docker-image deploymint/myapi:run_abc --name deploymint
```

Install metrics-server (needed for `kubectl top`):

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

```bash
kubectl patch deployment metrics-server -n kube-system --type=json -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

---

## 14.6 Docker

```bash
docker images | grep deploymint
```

```bash
docker build -f ~/.deploymint/artifacts/<run_id>/Dockerfile -t test:local ./path/to/repo
```

```bash
docker run --rm -p 8099:8000 test:local
```

```bash
docker image prune -f --filter "label=managed-by=deploymint"
```

---

## 14.7 Ollama

```bash
ollama list
```

```bash
ollama serve
```

```bash
ollama pull llama3.1:8b
```

```bash
ollama run llama3.1:8b "Return only JSON: {\"ok\": true}"
```

```bash
curl -s localhost:11434/api/tags | python -m json.tool
```

Warm the model before a demo (loads 4.9 GB into RAM):

```bash
ollama run llama3.1:8b "warm up" > /dev/null
```

---

## 14.8 Security tooling

```bash
checkov -f Dockerfile --framework dockerfile -o json --quiet
```

```bash
checkov --directory ~/.deploymint/artifacts/<run_id> --framework dockerfile --framework kubernetes -o json --quiet --compact
```

```bash
opa version
```

```bash
opa eval --format pretty --input /tmp/in.json --data deploymint/policies/ "data.deploymint"
```

```bash
opa fmt --diff deploymint/policies/
```

```bash
opa check deploymint/policies/
```

`opa check` validates your Rego without needing input. Run it every time you edit a policy.

---

## 14.9 tmux

```bash
tmux ls
```

```bash
tmux attach -t deploymint-run_abc123def456
```

Detach without killing: `Ctrl-b` then `d`.

```bash
tmux kill-session -t deploymint-run_abc123def456
```

```bash
tmux kill-server
```

---

## 14.10 Database inspection

```bash
sqlite3 ~/.deploymint/deploymint.db ".tables"
```

```bash
sqlite3 ~/.deploymint/deploymint.db "SELECT id, name, language, framework FROM projects;"
```

```bash
sqlite3 ~/.deploymint/deploymint.db "SELECT id, status, current_node, duration_ms FROM runs ORDER BY created_at DESC LIMIT 10;"
```

```bash
sqlite3 ~/.deploymint/deploymint.db "SELECT seq, type FROM events WHERE run_id='run_abc' ORDER BY seq;"
```

```bash
sqlite3 ~/.deploymint/deploymint.db "SELECT seq, agent, action, exit_code, substr(hash,1,12) FROM audit_logs WHERE run_id='run_abc' ORDER BY seq;"
```

Tamper demo — break the chain on purpose:

```bash
sqlite3 ~/.deploymint/deploymint.db "UPDATE audit_logs SET output='nothing happened here' WHERE run_id='run_abc' AND seq=3;"
```

Then hit `/api/runs/run_abc/audit/verify` and watch it report `broken_at_seq: 3`.

---

## 14.11 Development

```bash
pip install -e ".[dev]"
```

```bash
pytest -m "not slow" -v
```

```bash
pytest -m llm -v
```

```bash
pytest tests/test_warden.py::test_bad_artifacts_are_blocked -vv
```

```bash
ruff check deploymint tests
```

```bash
ruff format deploymint tests
```

```bash
python -m build
```

Full reset:

```bash
rm -rf ~/.deploymint && kind delete cluster --name deploymint && kind create cluster --name deploymint
```

---

## 14.12 Troubleshooting quick reference

| Symptom | First thing to check |
|---|---|
| `ModuleNotFoundError` on a dep | venv not activated — `python -V` should say 3.11 |
| `ErrImagePull` / `ImagePullBackOff` | `kind load docker-image` ran? `imagePullPolicy: IfNotPresent`? |
| `CrashLoopBackOff` | `kubectl logs <pod>` — usually a wrong CMD or a missing dependency |
| Pod never becomes ready | probe path/port vs. what the app actually serves |
| `CreateContainerConfigError` | `readOnlyRootFilesystem` + app writes to disk → add an `emptyDir` at `/tmp` |
| `database is locked` | WAL + `busy_timeout` pragmas missing in `database.py` |
| Server hangs mid-run | a blocking call not wrapped in `asyncio.to_thread` |
| Generation always falls back to template | `curl localhost:11434/api/tags` — is Ollama up and the model pulled? |
| Checkov "crashes" on findings | exit code 1 means findings exist, not failure |
| OPA `rego_parse_error` | Rego v0 vs v1 — check `opa version`, match your policy dialect |
| `kubectl top` returns nothing | metrics-server not installed in kind |
| Package data missing after install | `[tool.setuptools.package-data]` in `pyproject.toml` |
| Two tabs each miss half the events | per-client queues not implemented in `EventBus` |
