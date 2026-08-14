# 12 — Testing Strategy

## 12.1 The pyramid, sized for two weeks

```
        ╱ 3 e2e ╲            slow, needs docker+cluster, run before demo
      ╱  12 integration ╲    API + DB, fast, run on every commit
   ╱     ~45 unit          ╲ pure functions, milliseconds
```

You are not aiming for coverage percentage. You are aiming for **five specific
properties** that, if broken, silently ruin the demo:

1. The pipeline always produces artifacts, whatever the LLM does.
2. Bad artifacts are always blocked.
3. Path traversal is always rejected.
4. Generated artifacts always build and always apply.
5. The audit chain always verifies, and always fails on tamper.

Write tests for those five first. Everything else is optional.

---

## 12.2 `conftest.py`

```python
# tests/conftest.py
import os, shutil
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Isolate the sandbox root AND the database so tests never touch the real
    Postgres data. Point DATABASE_URL at a throwaway database on the SAME Postgres
    instance the dev container already runs — never auto-provision a second
    database server per test run."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("DEPLOYMINT_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("DATABASE_URL",
        os.environ.get("TEST_DATABASE_URL",
                       "postgresql+psycopg://deploymint:deploymint@localhost:5432/deploymint_test"))

    from deploymint.config import get_settings
    get_settings.cache_clear()

    import deploymint.db.database as dbmod
    from deploymint.db.models import Base
    dbmod._engine = None
    dbmod._SessionLocal = None
    engine = dbmod.get_engine()
    # Full isolation per test, not just per test-run: drop and recreate every
    # table so rows from an earlier test in the same session can't leak in.
    # Discovered as a real failure during Phase 1 implementation — pointing at
    # an isolated database is necessary but not sufficient; create_all() alone
    # doesn't clear rows a prior test already committed.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    yield ws

    get_settings.cache_clear()
    dbmod._engine = None
    dbmod._SessionLocal = None


@pytest.fixture
def client(workspace):
    from deploymint.server import create_app
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def sample_repo(workspace):
    """A copy of the FastAPI fixture, placed UNDER the isolated workspace root —
    the sandbox (05-phase-1-foundation.md §1.3) rejects anything else."""
    dst = workspace / "sample_fastapi"
    shutil.copytree(FIXTURES / "sample_fastapi", dst)
    return dst


@pytest.fixture
def registered_project(client, sample_repo):
    r = client.post("/api/projects",
                    json={"name": "test-app", "repo_path": str(sample_repo)})
    assert r.status_code == 201
    return r.json()


@pytest.fixture
def fake_llm(monkeypatch):
    """Deterministic LLM. Set .response before the call under test."""
    class Fake:
        response = '{"dockerfile":"FROM python:3.11-slim\\nUSER 10001\\nCMD [\\"python\\"]"}'
        calls = []

        async def complete(self, system, user, **kw):
            self.calls.append((system, user))
            return self.response

    fake = Fake()
    monkeypatch.setattr("deploymint.core.llm.complete", fake.complete)
    return fake
```

The `workspace` fixture resetting `dbmod._engine` is the crux of test isolation. Without
it, the first test builds an engine pointed at your real Postgres database and every
subsequent test writes there. **Verify this works before writing other tests** — run the
suite twice against a real `deploymint` database and confirm its `runs`/`projects` tables
are untouched; everything should land in `deploymint_test` instead.

---

## 12.3 What to test at each layer

### Unit — pure functions, no I/O

| Module | Tests |
|---|---|
| `core/sandbox` | `/` rejected · missing path rejected · `../` traversal rejected · valid dir accepted · symlink escaping root rejected |
| `core/llm.extract_json` | bare JSON · fenced ` ```json ` · fenced plain · prose before · prose after · nested braces · malformed → raises |
| `agents/architect` detectors | each language fixture · manifest beats extension count · framework priority (fastapi over starlette) · port from code · port from framework default |
| `agents/templates` | every stack renders · output passes `GeneratedArtifacts` validation · contains `USER` · contains resource limits |
| `schemas/artifacts` | fenced Dockerfile rejected · no `FROM` rejected · invalid YAML rejected · YAML without `kind` rejected |
| `agents/finops.parse_quantity` | `500m`→0.5 · `2`→2.0 · `512Mi`→0.5 · `1Gi`→1.0 · `None`→0.0 |
| `core/audit` | chain verifies · tampered output fails at the right seq · tampered `prev_hash` fails |

### Integration — API + DB, with `TestClient`

```python
def test_register_analyze_flow(client, sample_repo):
    r = client.post("/api/projects",
                    json={"name": "flow", "repo_path": str(sample_repo)})
    assert r.status_code == 201
    pid = r.json()["id"]

    a = client.post(f"/api/projects/{pid}/analyze")
    assert a.status_code == 200
    data = a.json()
    assert data["language"] == "python"
    assert data["framework"] == "fastapi"
    assert data["exposed_port"] == 8000
    assert len(data["graph"]["nodes"]) >= 4


def test_duplicate_name_conflicts(client, sample_repo):
    body = {"name": "dup", "repo_path": str(sample_repo)}
    assert client.post("/api/projects", json=body).status_code == 201
    assert client.post("/api/projects", json=body).status_code == 409


def test_system_path_rejected(client):
    r = client.post("/api/projects", json={"name": "evil", "repo_path": "/"})
    assert r.status_code == 400


def test_run_without_deploy(client, registered_project, fake_llm):
    pid = registered_project["id"]
    r = client.post(f"/api/projects/{pid}/runs", json={"skip_deploy": True})
    assert r.status_code == 202
    run_id = r.json()["run_id"]

    for _ in range(60):
        got = client.get(f"/api/runs/{run_id}").json()
        if got["status"] in {"success", "failed", "blocked"}:
            break
        time.sleep(0.5)
    assert got["status"] == "success"
    assert got["artifacts"]["dockerfile"]
```

### End-to-end — marked slow, needs docker + cluster

```python
@pytest.mark.slow
@pytest.mark.asyncio
async def test_full_deploy_reaches_running_pod(client, registered_project):
    """The demo path. If this passes, the product works."""
    pid = registered_project["id"]
    run_id = client.post(f"/api/projects/{pid}/runs", json={}).json()["run_id"]

    for _ in range(240):                       # up to 4 minutes
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in {"success", "failed", "blocked"}:
            break
        await asyncio.sleep(1)

    assert run["status"] == "success", run["errors"]
    assert run["deployment"]["status"] == "running"
    assert run["deployment"]["pod_name"]

    verify = client.get(f"/api/runs/{run_id}/audit/verify").json()
    assert verify["valid"] is True
    assert verify["entries"] >= 4


@pytest.mark.slow
def test_poisoned_repo_is_blocked(client, tmp_path):
    dst = tmp_path / "poisoned"
    shutil.copytree(FIXTURES / "poisoned_repo", dst)
    pid = client.post("/api/projects",
                      json={"name": "poison", "repo_path": str(dst)}).json()["id"]
    run_id = client.post(f"/api/projects/{pid}/runs", json={}).json()["run_id"]
    run = _wait(client, run_id)
    assert run["status"] == "blocked"
    assert run["security"]["blocked_reason"]
    assert run["deployment"] is None or run["deployment"]["status"] != "running"
```

---

## 12.4 Fixture repos

| Fixture | Purpose | Must have |
|---|---|---|
| `sample_fastapi/` | primary demo target | `/health`, 4+ modules, one shared module imported twice |
| `sample_flask/` | second Python framework | `/health`, `requirements.txt` |
| `sample_express/` | JS path | `/health`, `package.json` with `start` script |
| `sample_go/` | Go path | `/health`, `go.mod`, `cmd/server/main.go` |
| `poisoned_repo/` | security demo | README with prompt injection |
| `crashloop_app/` | rollback demo | app that exits 1 after 2 seconds |
| `empty_repo/` | degradation | one `.txt` file, nothing else |
| `monorepo/` | microservice detection | 3 subdirs each with own manifest + `docker-compose.yml` |

`crashloop_app` — deliberately broken:

```python
# main.py
import sys, time
print("starting...", flush=True)
time.sleep(2)
print("fatal: cannot connect to database", file=sys.stderr, flush=True)
sys.exit(1)
```

This is the only reliable way to demo the Oracle → Remediator path. Build it in Phase 6,
not on demo morning.

---

## 12.5 Testing LLM-dependent code

**Never** let an LLM call into a unit test. Three approaches, in order of preference:

**1. Mock `llm.complete` (default).** Fast, deterministic, and lets you inject exactly
the failure modes you care about: fenced output, prose, timeout, invalid JSON.

**2. Record/replay for prompt-quality work.** Save real responses to
`tests/data/llm_responses/{hash}.json` keyed by prompt hash. When iterating on prompts,
replay them so you can refactor parsing without re-running inference.

**3. Live LLM tests, marked and excluded by default.**

```python
@pytest.mark.llm
@pytest.mark.asyncio
async def test_model_produces_valid_artifacts_most_of_the_time():
    """Quality signal, not a gate. Runs only with -m llm."""
    successes = 0
    for _ in range(5):
        out = await ArtifactSmithAgent().run(dict(BASE))
        if out["artifacts"]["generated_by"] == "llm":
            successes += 1
    assert successes >= 4, f"only {successes}/5 — check prompts; Claude should clear this easily"
```

Run it manually after prompt changes: `pytest -m llm -v`. It needs a real
`ANTHROPIC_API_KEY` and costs real (small) money each run — never in CI, both because a
flaky network call turning your build red teaches you to ignore red builds, and because
CI shouldn't be spending API credits on every push. The bar (`>= 4/5`) is deliberately
higher than the original 8B-local-model version of this test — Claude's structured
output reliability means the template fallback should be the rare exception, not the
routine case it was with a small local model.

---

## 12.6 CI

`.github/workflows/ci.yml`:

```yaml
name: CI
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: deploymint
          POSTGRES_PASSWORD: deploymint
          POSTGRES_DB: deploymint_test
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready --health-interval 5s --health-timeout 3s --health-retries 10
    env:
      TEST_DATABASE_URL: postgresql+psycopg://deploymint:deploymint@localhost:5432/deploymint_test
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11", cache: pip }
      - run: pip install -e ".[dev]"
      - run: ruff check deploymint tests
      - run: pytest -m "not slow and not llm" -v
        # NOTE: no ANTHROPIC_API_KEY is set here, deliberately — this proves every
        # test that needs to pass in CI does so via the template/keyword fallback
        # paths, never by silently skipping when a real key happens to be absent.

  image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose build
      - name: Verify package data survives the image build
        run: |
          docker compose run --rm --no-deps app python -c "
          from importlib.resources import files
          p = files('deploymint')
          for rel in ['policies/no_root_user.rego','data/rate_card.json','web/templates/run.html']:
              assert (p/rel).is_file(), rel
          print('package data OK')"
```

The `image` job is the one that catches real bugs. Missing package data passes every
test in a source checkout and breaks for the first user who runs `docker compose up`.

---

## 12.7 Manual test matrix (run before the demo)

| Scenario | Expected |
|---|---|
| Fresh `docker compose up -d` on an empty `./projects` | boots clean, no manual DB/config step |
| `/api/doctor` with the Docker socket unmounted | `✗` on Docker with a clear fix, `ok: false` |
| `/api/doctor` with `ANTHROPIC_API_KEY` unset | `✗` on the LLM check, but overall still boots — see next row |
| Deploy with `ANTHROPIC_API_KEY` unset | succeeds via template fallback — this is the resilience path, not a failure |
| Deploy with no kubeconfig mounted / no reachable cluster | falls back to `docker run`, `deployment.mode == "docker"`, still reaches `running` |
| Deploy the same project twice | second run replaces the first cleanly |
| Two runs concurrently | both complete; semaphore serializes builds |
| Cancel mid-run | run marked `cancelled`, no orphan tmux session |
| Refresh browser mid-run | timeline replays complete |
| Two browser tabs, same run | both receive all events (or document the known single-queue limitation — see `09-phase-5-orchestration.md` §5.3) |
| Register a repo with 5000+ files | truncates, warns, completes |
| Register an empty directory | `language: unknown`, template fallback, no crash |
| Register a path with spaces | works (no shell interpolation anywhere) |
| Register a path outside `DEPLOYMINT_PROJECTS_DIR` | rejected with 400 — the sandbox is the workspace mount, nothing else |
| Restart the `db` container mid-session | the app reconnects (`pool_pre_ping`), no crash, no data loss |
| Disconnect network entirely | the static dashboard and any already-generated artifacts still work; **new** generation and Red Team's LLM layer degrade to templates/deterministic-only — this is expected, not a bug, per `01-architecture.md` §1.1 |

That last row replaces an earlier, stronger claim ("everything works offline") that no
longer applies now that the product is online by design — see `16-decisions-log.md`.
What should still be true offline is everything deterministic: the Architect, the
Warden's Checkov/OPA verdict, the Execution Engine, and FinOps's numeric estimate. Verify
that boundary explicitly rather than assuming it.
