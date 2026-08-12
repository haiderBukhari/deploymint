# 08 — Phase 4: Execution Engine (Days 8–9)

**Goal:** the generated Dockerfile builds a real image, the image loads into the kind
cluster, the manifests apply, a pod reaches `Running`, and every single command is
recorded in a replayable tmux session plus a hash-chained audit log.

This is the phase where the project stops being a code generator and becomes a
deployment tool.

---

## Step 4.1 — Verify the deploy path by hand first

Do this manually, end to end, before writing a line of Python. When the automated
version breaks, you will know exactly which step differs.

```bash
docker build -f ~/.deploymint/artifacts/<run_id>/Dockerfile -t deploymint/sample-api:manual ./tests/fixtures/sample_fastapi
```

```bash
kind load docker-image deploymint/sample-api:manual --name deploymint
```

```bash
kubectl apply -f ~/.deploymint/artifacts/<run_id>/k8s-deployment.yaml -f ~/.deploymint/artifacts/<run_id>/k8s-service.yaml
```

```bash
kubectl rollout status deployment/sample-api --timeout=120s
```

```bash
kubectl get pods -l app=sample-api && kubectl port-forward svc/sample-api-svc 8081:8000 &
```

```bash
sleep 2 && curl -s localhost:8081/health
```

**You must see `{"status":"ok"}`.** If you do not, fix it manually now. Automating a
broken sequence just hides the bug behind a layer of Python.

Common failures at this step:

| Symptom | Cause | Fix |
|---|---|---|
| `ErrImagePull` / `ImagePullBackOff` | image not loaded into kind, or `imagePullPolicy: Always` | `kind load docker-image`, set `IfNotPresent` |
| `CrashLoopBackOff` | app exits immediately — wrong CMD, missing dep | `kubectl logs <pod>` |
| Readiness never passes | `/health` doesn't exist or wrong port | check probe path/port vs app |
| `CreateContainerConfigError` | `readOnlyRootFilesystem` + app writes to disk | add an `emptyDir` at `/tmp` |
| Pod `Pending` forever | resource requests exceed node capacity | lower requests |

---

## Step 4.2 — tmux recorder

```python
# deploymint/core/tmux_recorder.py
import asyncio, shutil
from pathlib import Path
from datetime import datetime, timezone

from deploymint.config import get_settings


class TmuxRecorder:
    """Records a shell session to a replayable log file.

    Falls back to plain subprocess capture when tmux is unavailable — the deploy
    still works, only the replay fidelity is reduced.
    """

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.available = shutil.which("tmux") is not None
        self.session_name = f"deploymint-{run_id}"
        self.log_path: Path = get_settings().sessions_dir / f"{run_id}.log"
        self._server = None
        self._session = None
        self._pane = None

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_header()
        if not self.available:
            return
        import libtmux
        self._server = libtmux.Server()
        self._session = self._server.new_session(
            session_name=self.session_name, kill_session=True, attach=False,
        )
        self._pane = self._session.active_window.active_pane
        self._pane.cmd("pipe-pane", "-o", f"cat >> {self.log_path}")

    def _write_header(self) -> None:
        with self.log_path.open("a") as f:
            f.write(f"# DeployMint session {self.run_id}\n")
            f.write(f"# started {datetime.now(timezone.utc).isoformat()}\n")
            f.write(f"# tmux: {'yes' if self.available else 'no (degraded capture)'}\n\n")

    def log_command(self, argv: list[str]) -> None:
        with self.log_path.open("a") as f:
            f.write(f"\n$ {' '.join(argv)}\n")

    def log_output(self, text: str) -> None:
        with self.log_path.open("a") as f:
            f.write(text if text.endswith("\n") else text + "\n")

    def stop(self) -> str:
        if self._session:
            try:
                self._pane.cmd("pipe-pane")     # stop piping
                self._session.kill()
            except Exception:
                pass
        with self.log_path.open("a") as f:
            f.write(f"\n# ended {datetime.now(timezone.utc).isoformat()}\n")
        return str(self.log_path)
```

### A note on what tmux actually buys you

Honest framing: DeployMint runs its commands via `asyncio.create_subprocess_exec` and
captures stdout/stderr directly. The tmux session is a **parallel recording surface** —
it gives you a real terminal the user can `tmux attach` to and watch live, and a session
file that replays.

Do not pretend the tmux pane is the execution path when it is not — a reviewer reading
the code will spot it. What you *can* say truthfully and impressively:

> "Every command is executed through a recorded session with full argv, stdout, stderr,
> exit code, and a hash-chained audit entry. You can attach to the live session with
> `tmux attach -t deploymint-<run_id>`, or replay the log afterwards."

That is a real, verifiable claim. Make `tmux attach` actually work — it is a great live
demo moment.

---

## Step 4.3 — Audited command runner

Every shell invocation goes through this. Nothing bypasses it.

```python
# deploymint/core/runner.py
import asyncio
from dataclasses import dataclass


@dataclass
class CommandResult:
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def combined(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()


async def run_command(
    argv: list[str], *, cwd: str | None = None, timeout: int = 300,
    recorder=None, audit=None, agent: str = "execution",
    on_line=None,
) -> CommandResult:
    """Execute argv, streaming lines to on_line, recording to tmux log + audit chain."""
    if recorder:
        recorder.log_command(argv)

    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )

    out_lines, err_lines = [], []

    async def pump(stream, sink, name):
        while True:
            raw = await stream.readline()
            if not raw:
                break
            line = raw.decode(errors="replace").rstrip()
            sink.append(line)
            if recorder:
                recorder.log_output(line)
            if on_line:
                await on_line(line, name)

    try:
        await asyncio.wait_for(
            asyncio.gather(pump(proc.stdout, out_lines, "stdout"),
                           pump(proc.stderr, err_lines, "stderr")),
            timeout=timeout,
        )
        await proc.wait()
    except asyncio.TimeoutError:
        proc.kill()
        err_lines.append(f"TIMEOUT after {timeout}s")

    result = CommandResult(argv, proc.returncode if proc.returncode is not None else -1,
                           "\n".join(out_lines), "\n".join(err_lines))
    if audit:
        await audit.record(agent=agent, action="shell_exec",
                           command=" ".join(argv), output=result.combined[:64_000],
                           exit_code=result.exit_code)
    return result
```

`shell=False` with a list argv, always. A repo directory named
`my project; rm -rf ~` is harmless here and catastrophic with `shell=True`.

---

## Step 4.4 — Audit chain writer

```python
# deploymint/core/audit.py
import hashlib, json
from datetime import datetime, timezone

from deploymint.db.database import get_session_factory
from deploymint.db.models import AuditLog

GENESIS = "0" * 64


class AuditChain:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.seq = 0
        self.prev_hash = GENESIS

    async def record(self, *, agent: str, action: str, command: str,
                     output: str = "", exit_code: int | None = None) -> str:
        self.seq += 1
        ts = datetime.now(timezone.utc)
        payload = json.dumps({
            "prev": self.prev_hash, "run_id": self.run_id, "seq": self.seq,
            "agent": agent, "action": action, "command": command,
            "output": output, "exit_code": exit_code, "ts": ts.isoformat(),
        }, sort_keys=True, separators=(",", ":"))
        h = hashlib.sha256(payload.encode()).hexdigest()

        Session = get_session_factory()
        with Session() as db:
            db.add(AuditLog(run_id=self.run_id, seq=self.seq, agent=agent,
                            action=action, command=command, output=output,
                            exit_code=exit_code, prev_hash=self.prev_hash,
                            hash=h, ts=ts))
            db.commit()

        self.prev_hash = h
        return h


def verify_chain(run_id: str) -> dict:
    Session = get_session_factory()
    with Session() as db:
        rows = db.query(AuditLog).filter_by(run_id=run_id).order_by(AuditLog.seq).all()

    prev = GENESIS
    for r in rows:
        payload = json.dumps({
            "prev": prev, "run_id": r.run_id, "seq": r.seq, "agent": r.agent,
            "action": r.action, "command": r.command, "output": r.output,
            "exit_code": r.exit_code, "ts": r.ts.isoformat(),
        }, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(payload.encode()).hexdigest() != r.hash or r.prev_hash != prev:
            return {"valid": False, "broken_at_seq": r.seq, "entries": len(rows)}
        prev = r.hash
    return {"valid": True, "broken_at_seq": None, "entries": len(rows)}
```

Demo it: run a deploy, then `sqlite3 ~/.deploymint/deploymint.db "UPDATE audit_logs SET
output='clean' WHERE seq=3;"`, then hit `/api/runs/{id}/audit/verify` and watch it report
`broken_at_seq: 3`. **This takes 15 seconds and is genuinely convincing.**

---

## Step 4.5 — Docker build with streaming

Use the Docker SDK's low-level API so you get log lines as they happen.

```python
# deploymint/core/docker_engine.py
import asyncio, json
import docker
from docker.errors import BuildError, APIError, DockerException


def get_client():
    try:
        c = docker.from_env()
        c.ping()
        return c
    except DockerException as e:
        raise RuntimeError(f"Docker unreachable — is Docker Desktop running? ({e})") from e


def _build_sync(context: str, dockerfile: str, tag: str, line_cb):
    client = get_client()
    stream = client.api.build(
        path=context, dockerfile=dockerfile, tag=tag,
        rm=True, forcerm=True, decode=True, nocache=False, pull=False,
    )
    error = None
    for chunk in stream:
        if "stream" in chunk:
            for line in chunk["stream"].splitlines():
                if line.strip():
                    line_cb(line.rstrip())
        elif "errorDetail" in chunk:
            error = chunk["errorDetail"].get("message", "unknown build error")
            line_cb(f"ERROR: {error}")
    if error:
        raise BuildError(error, build_log=None)
    return tag


async def build_image(context: str, dockerfile: str, tag: str, on_line) -> str:
    """Run the blocking build in a thread, forwarding lines to the async event bus."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def cb(line: str):
        loop.call_soon_threadsafe(queue.put_nowait, line)

    task = asyncio.create_task(asyncio.to_thread(_build_sync, context, dockerfile, tag, cb))

    while not task.done() or not queue.empty():
        try:
            line = await asyncio.wait_for(queue.get(), timeout=0.2)
            await on_line(line)
        except asyncio.TimeoutError:
            continue
    return await task
```

The `loop.call_soon_threadsafe` bridge is the crux. The Docker SDK is synchronous and
runs in a thread; your event bus is async on the main loop. Calling `await` from the
thread would fail. This pattern — thread pushes to a threadsafe queue, main loop drains
it — is the correct way and worth internalizing.

**Alternative if this fights you:** shell out to `docker build` via `run_command()` with
`on_line`. You lose nothing meaningful and it is 5 lines instead of 40. Take this option
if you are behind schedule.

---

## Step 4.6 — Kubernetes engine

```python
# deploymint/core/kube_engine.py
import json
from deploymint.core.runner import run_command
from deploymint.config import get_settings


def _ctx() -> list[str]:
    return ["--context", get_settings().kube_context]


async def cluster_reachable(**kw) -> bool:
    r = await run_command(["kubectl", *_ctx(), "cluster-info",
                           "--request-timeout=5s"], timeout=10, **kw)
    return r.ok


async def is_kind_context() -> bool:
    return get_settings().kube_context.startswith("kind-")


async def kind_load(image: str, **kw):
    return await run_command(
        ["kind", "load", "docker-image", image,
         "--name", get_settings().kind_cluster], timeout=180, **kw)


async def apply(paths: list[str], **kw):
    argv = ["kubectl", *_ctx(), "apply"]
    for p in paths:
        argv += ["-f", p]
    return await run_command(argv, timeout=60, **kw)


async def rollout_status(name: str, **kw):
    s = get_settings()
    return await run_command(
        ["kubectl", *_ctx(), "rollout", "status", f"deployment/{name}",
         f"--timeout={s.rollout_timeout}s"], timeout=s.rollout_timeout + 30, **kw)


async def get_pod_name(app_label: str, **kw) -> str | None:
    r = await run_command(
        ["kubectl", *_ctx(), "get", "pods", "-l", f"app={app_label}",
         "-o", "jsonpath={.items[0].metadata.name}"], timeout=20, **kw)
    return r.stdout.strip() or None


async def describe_pod(pod: str, **kw):
    return await run_command(["kubectl", *_ctx(), "describe", "pod", pod], timeout=30, **kw)


async def pod_logs(pod: str, tail: int = 100, **kw):
    return await run_command(
        ["kubectl", *_ctx(), "logs", pod, f"--tail={tail}"], timeout=30, **kw)


async def rollout_undo(name: str, **kw):
    return await run_command(
        ["kubectl", *_ctx(), "rollout", "undo", f"deployment/{name}"], timeout=60, **kw)


async def delete_deployment(name: str, **kw):
    return await run_command(
        ["kubectl", *_ctx(), "delete", "deployment", name,
         "--ignore-not-found"], timeout=60, **kw)
```

---

## Step 4.7 — The Execution Engine agent

```python
# deploymint/agents/execution.py
from pathlib import Path

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState
from deploymint.core import docker_engine, kube_engine
from deploymint.core.tmux_recorder import TmuxRecorder
from deploymint.core.audit import AuditChain
from deploymint.config import get_settings


class ExecutionEngineAgent(BaseAgent):
    name = "execution"

    async def run(self, state: DeployState) -> dict:
        s = get_settings()
        run_id, name = state["run_id"], state["project_name"]
        art_dir = s.artifacts_dir / run_id
        image = f"deploymint/{name}:{run_id}"

        rec = TmuxRecorder(run_id)
        rec.start()
        audit = AuditChain(run_id)
        kw = {"recorder": rec, "audit": audit, "on_line": self._line}

        dep: dict = {"image_tag": image, "build_log": "", "session_file": str(rec.log_path),
                     "kubectl_output": "", "status": "building"}

        try:
            # 1. build
            await self.emit("execution.stage", stage="build")
            build_lines: list[str] = []

            async def collect(line: str):
                build_lines.append(line)
                await self.emit("execution.log", line=line, stream="stdout")

            rec.log_command(["docker", "build", "-t", image, "-f",
                             str(art_dir / "Dockerfile"), state["repo_path"]])
            await docker_engine.build_image(
                context=state["repo_path"],
                dockerfile=str(art_dir / "Dockerfile"),
                tag=image, on_line=collect,
            )
            dep["build_log"] = "\n".join(build_lines)[-64_000:]
            await audit.record(agent=self.name, action="docker_build",
                               command=f"docker build -t {image}",
                               output=dep["build_log"][-8000:], exit_code=0)

            # 2. load into kind
            if await kube_engine.is_kind_context():
                await self.emit("execution.stage", stage="load")
                r = await kube_engine.kind_load(image, **kw)
                if not r.ok:
                    raise RuntimeError(f"kind load failed: {r.combined[:400]}")

            # 3. apply
            dep["status"] = "deploying"
            await self.emit("execution.stage", stage="apply")
            r = await kube_engine.apply(
                [str(art_dir / "k8s-deployment.yaml"), str(art_dir / "k8s-service.yaml")], **kw)
            dep["kubectl_output"] = r.combined
            if not r.ok:
                raise RuntimeError(f"kubectl apply failed: {r.combined[:400]}")

            # 4. rollout
            await self.emit("execution.stage", stage="rollout")
            r = await kube_engine.rollout_status(name, **kw)
            if not r.ok:
                diag = await self._diagnose(name, **kw)
                raise RuntimeError(f"rollout did not complete.\n{r.combined[:400]}\n\n{diag}")

            dep["pod_name"] = await kube_engine.get_pod_name(name, **kw)
            dep["status"] = "running"
            await self.emit("execution.done", image_tag=image,
                            pod_name=dep.get("pod_name"), status="running")
            return {"deployment": dep}

        except Exception as e:
            dep["status"] = "failed"
            await self.emit("error", node=self.name, message=str(e)[:500])
            return {"deployment": dep,
                    "errors": state.get("errors", []) + [f"execution: {str(e)[:500]}"]}
        finally:
            rec.stop()

    async def _diagnose(self, name: str, **kw) -> str:
        pod = await kube_engine.get_pod_name(name, **kw)
        if not pod:
            return "No pod found for this deployment."
        desc = await kube_engine.describe_pod(pod, **kw)
        logs = await kube_engine.pod_logs(pod, **kw)
        events = desc.stdout.split("Events:")[-1][:1500] if "Events:" in desc.stdout else ""
        return f"--- pod events ---\n{events}\n--- pod logs (tail) ---\n{logs.combined[-1500:]}"

    async def _line(self, line: str, stream: str):
        await self.emit("execution.log", line=line, stream=stream)
```

The `_diagnose` method is what separates a usable tool from a frustrating one. When a
rollout fails, the user gets pod events and container logs in the same response — not
"deployment failed." Build it now, not later.

---

## Step 4.8 — Cleanup

Repeated runs accumulate images and deployments. Add:

```python
# deploymint/cli.py
@main.command()
@click.option("--all", "wipe_all", is_flag=True, help="also remove built images")
def clean(wipe_all):
    """Remove DeployMint deployments (and optionally images) from the cluster."""
```

- `kubectl delete deployment,service -l managed-by=deploymint`
- if `--all`: `docker image prune` filtered to `deploymint/*`

That `managed-by: deploymint` label in every generated manifest is what makes this
possible. Make sure the templates and `_inject_image` both set it.

---

## Step 4.9 — Phase 4 acceptance test

```bash
curl -s -X POST localhost:8000/api/projects/1/runs -H 'content-type: application/json' -d '{}'
```

Then, while it runs:

```bash
tmux ls
```

```bash
tmux attach -t deploymint-<run_id>
```

After it completes:

```bash
kubectl get pods -l managed-by=deploymint
```

```bash
kubectl port-forward svc/sample-api-svc 8081:8000 > /dev/null 2>&1 & sleep 2; curl -s localhost:8081/health; kill %1
```

```bash
curl -s localhost:8000/api/runs/<run_id>/audit/verify
```

```bash
cat ~/.deploymint/sessions/<run_id>.log | head -50
```

**Pass criteria:**

- Run reaches `status=success`, `deployment.status="running"`
- `kubectl get pods` shows `1/1 Running`
- `curl localhost:8081/health` returns `{"status":"ok"}` — **the pod is real**
- Session log contains the full `docker build` output and every kubectl command
- `audit/verify` returns `{"valid": true}` with ≥ 5 entries
- Tampering with one audit row makes verify return `valid: false` at the right seq
- A deliberately broken Dockerfile produces a failure that includes pod events + logs

Tick **Phase 4**. Next: `09-phase-5-orchestration.md`.

---

## Time budget

| Task | Hours |
|---|---|
| Manual deploy verification | 1.0 |
| tmux recorder | 2.0 |
| Audited command runner | 2.0 |
| Audit chain + verify endpoint | 2.0 |
| Docker build streaming (thread bridge) | 3.0 |
| kube_engine | 2.0 |
| Execution agent + diagnose path | 3.0 |
| Cleanup command | 1.0 |
| Debugging the deploy loop (budget generously) | 4.0 |
| **Total** | **~20 h (2 days)** |

**The deploy loop will fight you.** Wrong port, missing `/health`, read-only filesystem,
image not loaded — each of these costs 20–40 minutes the first time. This is normal.
The manual verification in §4.1 is what keeps it to hours instead of a full day.
