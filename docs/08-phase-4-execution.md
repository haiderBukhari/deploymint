# 08 — Phase 4: Execution Engine (Days 8–9)

**Goal:** the generated Dockerfile builds a real image **on the host's own Docker
daemon** (the app container never runs its own nested Docker), the manifests apply to
whatever cluster is reachable through the mounted kubeconfig — or, if none is, the built
image runs directly with `docker run` — a pod (or container) reaches `Running`, and every
single command is recorded in a replayable tmux session plus a hash-chained audit log.

This is the phase where the project stops being a code generator and becomes a
deployment tool. It's also the phase where the Docker Compose architecture decision from
`01-architecture.md` becomes concrete rather than conceptual — read §4.1a before
anything else.

---

## Step 4.1a — Docker-outside-of-Docker: the one concept this whole phase depends on

The app runs **inside a container**. It still needs to run `docker build`. The naive
approach — installing a Docker daemon *inside* the app container and running a nested
build — is slow, wasteful, and means the images you build are trapped inside a container
that will be destroyed, invisible to the host's own Docker and to whatever cluster the
host has.

The actual answer, and the one `docker-compose.yml` already implements
(`02-repo-layout.md` §2.4):

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```

This mounts the **host's** Docker socket into the app container. The `docker` Python SDK
and the `docker` CLI, running inside the container, talk to that socket exactly as if
they were running directly on the host. `docker.from_env()` needs **zero code changes**
to pick this up — it already defaults to `unix:///var/run/docker.sock`, and that path now
resolves to the host's real daemon instead of nothing. Images built this way are
immediately visible to `docker images` **on the host**, and to any Kubernetes cluster the
host's Docker also backs (kind, Docker Desktop's Kubernetes) — there is no separate
"load the image into the cluster" step for those, unlike the fully-isolated dev-cluster
setup described in §4.1b.

This is the same pattern every CI system that builds Docker images uses — Jenkins agents,
GitLab Runner, and CircleCI's Docker executor all mount the host socket into the build
container rather than running Docker-in-Docker. It is well-understood and it is exactly
why `01-architecture.md` §1.7 calls the socket mount "root-equivalent host access" and
asks you to say that plainly rather than hand-wave it.

**One consequence worth internalizing now:** the build *context* (the directory
`docker build` reads from) must be a path the **host's** daemon can see — which, because
of the socket mount, means it must be a path that exists on the host's filesystem, not
just inside the app container. In practice this is automatically true here: the user's
project lives under `./projects` on the host, bind-mounted into the app container at
`/workspace`. When the app container passes `/workspace/my-app` as the build context to
the host daemon, the **host** daemon does not know about `/workspace` — it needs the
*host* path. Handle this by having the app read `DEPLOYMINT_PROJECTS_DIR` (the same env
var used in `docker-compose.yml`) and translate `/workspace/my-app` back to
`{DEPLOYMINT_PROJECTS_DIR}/my-app` before calling `docker build`:

```python
# deploymint/core/docker_engine.py (addition)
import os
from pathlib import Path


def to_host_path(container_path: str) -> str:
    """The Docker SDK talks to the HOST daemon via the socket mount, so any path
    passed as a build context must be a path the host can resolve — not the
    container's own /workspace view of it."""
    workspace_root = Path("/workspace")
    host_root = Path(os.environ["DEPLOYMINT_PROJECTS_DIR_HOST"])  # see below
    rel = Path(container_path).relative_to(workspace_root)
    return str(host_root / rel)
```

`DEPLOYMINT_PROJECTS_DIR_HOST` is a second env var, distinct from
`DEPLOYMINT_PROJECTS_DIR` — the *host's* absolute path to the projects directory, which
Compose can't infer on the container's behalf. Add it to `.env.example`
(`02-repo-layout.md` §2.5):

```bash
# The ABSOLUTE path on your host machine that DEPLOYMINT_PROJECTS_DIR resolves to.
# Required because the app builds images via your host's Docker daemon (see
# 08-phase-4-execution.md §4.1a) and must pass it a path the host can see.
DEPLOYMINT_PROJECTS_DIR_HOST=/Users/you/deploymint/projects
```

This is a real, slightly unusual wrinkle of the Docker-outside-of-Docker pattern —
document it clearly rather than letting a future contributor discover it via a confusing
`docker build` "no such file or directory" error where the file very much exists, just
not where the host daemon is looking.

---

## Step 4.1b — Verify the deploy path by hand first

Do this manually, end to end, before writing a line of Python. When the automated
version breaks, you will know exactly which step differs. This uses a disposable `kind`
cluster for **your own dev/testing** — see `00-prerequisites.md` §0.2 — which is a
different thing from whatever cluster (if any) an end user's own `~/.kube/config` points
at in production.

```bash
kind create cluster --name deploymint-dev
```

```bash
docker build -f ./tests/fixtures/sample_fastapi/.deploymint/manual-test/Dockerfile -t deploymint/sample-api:manual ./tests/fixtures/sample_fastapi
```

```bash
kind load docker-image deploymint/sample-api:manual --name deploymint-dev
```

```bash
kubectl apply -f ./tests/fixtures/sample_fastapi/.deploymint/manual-test/k8s-deployment.yaml -f ./tests/fixtures/sample_fastapi/.deploymint/manual-test/k8s-service.yaml
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
| `ErrImagePull` / `ImagePullBackOff` | image not loaded into a kind cluster, or `imagePullPolicy: Always` | `kind load docker-image`, set `IfNotPresent` |
| `CrashLoopBackOff` | app exits immediately — wrong CMD, missing dep | `kubectl logs <pod>` |
| Readiness never passes | `/health` doesn't exist or wrong port | check probe path/port vs app |
| `CreateContainerConfigError` | `readOnlyRootFilesystem` + app writes to disk | add an `emptyDir` at `/tmp` |
| Pod `Pending` forever | resource requests exceed node capacity | lower requests |

`kind load docker-image` is **only needed for kind specifically** — it exists because
kind runs its own containerd, separate from the host Docker daemon your build just used.
A real cloud cluster, or Docker Desktop's built-in Kubernetes, doesn't need this step at
all; the image is already visible to it via the shared daemon. The Execution Engine
detects which situation it's in — see §4.6.

---

## Step 4.2 — tmux recorder

```python
# deploymint/core/tmux_recorder.py
import asyncio, shutil
from pathlib import Path
from datetime import datetime, timezone


class TmuxRecorder:
    """Records a shell session to a replayable log file, written next to the
    project's own generated artifacts — see 01-architecture.md §1.8.

    Falls back to plain subprocess capture when tmux is unavailable — the deploy
    still works, only the replay fidelity is reduced.
    """

    def __init__(self, run_id: str, repo_path: str):
        self.run_id = run_id
        self.available = shutil.which("tmux") is not None
        self.session_name = f"deploymint-{run_id}"
        self.log_path: Path = Path(repo_path) / ".deploymint" / run_id / "session.log"
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
it gives you a real terminal you can `docker compose exec app tmux attach -t
deploymint-<run_id>` into and watch live, and a session file that replays.

Do not pretend the tmux pane is the execution path when it is not — a reviewer reading
the code will spot it. What you *can* say truthfully and impressively:

> "Every command is executed through a recorded session with full argv, stdout, stderr,
> exit code, and a hash-chained audit entry. You can attach to the live session, or
> replay the log afterwards."

That is a real, verifiable claim. Make `tmux attach` actually work — it is a great live
demo moment. Since tmux now runs *inside* the app container, attaching to it means
`docker compose exec app tmux attach -t deploymint-<run_id>` rather than a bare `tmux
attach` on the host — document this exact command in `14-command-reference.md`.

---

## Step 4.3 — Audited command runner

Unchanged from a purely local design — this layer doesn't know or care whether it's
running inside a container. Every shell invocation goes through this. Nothing bypasses it.

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

Unchanged in design — Postgres via the same `get_session_factory()` from
`03-data-model.md` §3.2, just a different backing store than the original SQLite plan.

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

Demo it: run a deploy, then tamper with one row directly in the compose Postgres and
re-verify:

```bash
docker compose exec db psql -U deploymint -c "UPDATE audit_logs SET output='clean' WHERE run_id='<run_id>' AND seq=3;"
```

```bash
curl -s localhost:8000/api/runs/<run_id>/audit/verify
```

Watch it report `broken_at_seq: 3`. **This takes 15 seconds and is genuinely convincing.**

---

## Step 4.5 — Docker build with streaming

Use the Docker SDK's low-level API so you get log lines as they happen. **The only
change from a purely local design is the host-path translation from §4.1a** — the
build/streaming mechanics themselves are identical whether `docker.from_env()` is
talking to a local daemon directly or to one via a mounted socket.

```python
# deploymint/core/docker_engine.py
import asyncio, json, os
from pathlib import Path
import docker
from docker.errors import BuildError, APIError, DockerException


def get_client():
    try:
        c = docker.from_env()   # unix:///var/run/docker.sock — the HOST's daemon,
        c.ping()                # reachable because of the mount in docker-compose.yml
        return c
    except DockerException as e:
        raise RuntimeError(
            f"Cannot reach the Docker daemon via the mounted socket. Is "
            f"/var/run/docker.sock mounted in docker-compose.yml? ({e})"
        ) from e


def to_host_path(container_path: str) -> str:
    """See 08-phase-4-execution.md §4.1a — build contexts must be host-visible paths."""
    workspace_root = Path("/workspace")
    host_root = Path(os.environ["DEPLOYMINT_PROJECTS_DIR_HOST"])
    rel = Path(container_path).relative_to(workspace_root)
    return str(host_root / rel)


def _build_sync(context: str, dockerfile: str, tag: str, line_cb):
    client = get_client()
    stream = client.api.build(
        path=to_host_path(context), dockerfile=dockerfile, tag=tag,
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

**`dockerfile` is still a container-local path** (e.g.
`/workspace/my-app/.deploymint/{run_id}/Dockerfile`) passed as the `dockerfile=` build
arg, which the Docker API resolves *relative to the build context* — since both
`context` and `dockerfile` get the same host-path translation applied by virtue of both
living under the workspace mount, this works out correctly as long as `dockerfile` is
computed as a path relative to `context` before translation, not translated
independently. Test this explicitly; it's the one place a subtle path bug hides.

The `loop.call_soon_threadsafe` bridge is the crux of the streaming design and is
unchanged from a local build: the Docker SDK is synchronous and runs in a thread; your
event bus is async on the main loop. This pattern — thread pushes to a threadsafe queue,
main loop drains it — is worth internalizing regardless of where the daemon lives.

**Alternative if the host-path translation fights you:** shell out to `docker build` via
`run_command()` with `on_line`, and pass the host path directly as `cwd`/argument rather
than going through the SDK's `path=` parameter. Both routes go through the same mounted
socket either way (the CLI's default connection is also
`unix:///var/run/docker.sock`) — this only changes how you invoke the build, not the
DooD pattern underneath it.

---

## Step 4.6 — Kubernetes engine — with the `docker run` fallback

This is where `01-architecture.md` §1.4 decision 12 ("the host's Kubernetes if reachable,
else plain `docker run`") becomes real code. **This fallback did not exist in earlier
drafts of this design** — it is new, and it is what keeps the product working for a user
who has never touched Kubernetes.

```python
# deploymint/core/kube_engine.py
import json
from deploymint.core.runner import run_command
from deploymint.config import get_settings


def _ctx() -> list[str]:
    ctx = get_settings().kube_context
    return ["--context", ctx] if ctx else []


async def cluster_reachable(**kw) -> bool:
    """The single gate that decides Kubernetes vs. plain docker run. If the mounted
    ~/.kube/config doesn't exist, or exists but has no reachable cluster, this
    returns False and the Execution Engine takes the docker-run path instead."""
    r = await run_command(["kubectl", *_ctx(), "cluster-info",
                           "--request-timeout=5s"], timeout=10, **kw)
    return r.ok


async def is_kind_context() -> bool:
    r = await run_command(["kubectl", *_ctx(), "config", "current-context"], timeout=5)
    return r.ok and r.stdout.strip().startswith("kind-")


async def kind_load(image: str, cluster_name: str, **kw):
    """Only relevant if the reachable cluster IS a kind cluster — see §4.1b for why."""
    return await run_command(
        ["kind", "load", "docker-image", image, "--name", cluster_name],
        timeout=180, **kw)


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

```python
# deploymint/core/docker_run.py — the fallback path
from deploymint.core.runner import run_command


async def run_container(name: str, image: str, port: int, **kw):
    """No cluster reachable: run the built image directly via the same mounted
    Docker socket. This is what proves the pipeline end-to-end for a user who has
    Docker but no Kubernetes at all."""
    await run_command(["docker", "rm", "-f", name], timeout=15)  # clean up a prior run
    return await run_command(
        ["docker", "run", "-d", "--name", name, "-p", f"{port}:{port}",
         "--label", "managed-by=deploymint", image],
        timeout=30, **kw)


async def container_healthy(name: str, port: int, path: str = "/health", **kw) -> bool:
    r = await run_command(
        ["docker", "exec", name, "curl", "-sf", f"http://localhost:{port}{path}"],
        timeout=10, **kw)
    return r.ok


async def container_logs(name: str, tail: int = 100, **kw):
    return await run_command(["docker", "logs", "--tail", str(tail), name], timeout=15, **kw)
```

---

## Step 4.7 — The Execution Engine agent

```python
# deploymint/agents/execution.py
from pathlib import Path
import asyncio

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState
from deploymint.core import docker_engine, kube_engine, docker_run
from deploymint.core.tmux_recorder import TmuxRecorder
from deploymint.core.audit import AuditChain


class ExecutionEngineAgent(BaseAgent):
    name = "execution"

    async def run(self, state: DeployState) -> dict:
        run_id, name = state["run_id"], state["project_name"]
        repo_path = state["repo_path"]
        art_dir = Path(repo_path) / ".deploymint" / run_id
        image = f"deploymint/{name}:{run_id}"
        port = (state.get("analysis") or {}).get("exposed_port", 8000)

        rec = TmuxRecorder(run_id, repo_path)
        rec.start()
        audit = AuditChain(run_id)
        kw = {"recorder": rec, "audit": audit, "on_line": self._line}

        dep: dict = {"image_tag": image, "build_log": "", "session_file": str(rec.log_path),
                     "kubectl_output": "", "status": "building"}

        try:
            # 1. build — via the mounted host socket, see §4.1a
            await self.emit("execution.stage", stage="build")
            build_lines: list[str] = []

            async def collect(line: str):
                build_lines.append(line)
                await self.emit("execution.log", line=line, stream="stdout")

            rec.log_command(["docker", "build", "-t", image, "-f",
                             str(art_dir / "Dockerfile"), repo_path])
            await docker_engine.build_image(
                context=repo_path, dockerfile=str(art_dir / "Dockerfile"),
                tag=image, on_line=collect,
            )
            dep["build_log"] = "\n".join(build_lines)[-64_000:]
            await audit.record(agent=self.name, action="docker_build",
                               command=f"docker build -t {image}",
                               output=dep["build_log"][-8000:], exit_code=0)

            # 2. deploy — Kubernetes if reachable, else plain docker run
            dep["status"] = "deploying"
            if await kube_engine.cluster_reachable(**kw):
                dep["mode"] = "kubernetes"
                if await kube_engine.is_kind_context():
                    await self.emit("execution.stage", stage="load")
                    r = await kube_engine.kind_load(image, name, **kw)
                    if not r.ok:
                        raise RuntimeError(f"kind load failed: {r.combined[:400]}")

                await self.emit("execution.stage", stage="apply")
                r = await kube_engine.apply(
                    [str(art_dir / "k8s-deployment.yaml"), str(art_dir / "k8s-service.yaml")], **kw)
                dep["kubectl_output"] = r.combined
                if not r.ok:
                    raise RuntimeError(f"kubectl apply failed: {r.combined[:400]}")

                await self.emit("execution.stage", stage="rollout")
                r = await kube_engine.rollout_status(name, **kw)
                if not r.ok:
                    diag = await self._diagnose(name, **kw)
                    raise RuntimeError(f"rollout did not complete.\n{r.combined[:400]}\n\n{diag}")

                dep["pod_name"] = await kube_engine.get_pod_name(name, **kw)
            else:
                # No reachable cluster — run the built image directly. This is the
                # path that makes the product work with only Docker installed.
                dep["mode"] = "docker"
                await self.emit("execution.stage", stage="docker_run")
                r = await docker_run.run_container(name, image, port, **kw)
                if not r.ok:
                    raise RuntimeError(f"docker run failed: {r.combined[:400]}")
                dep["container_id"] = r.stdout.strip()[:12]
                dep["local_url"] = f"http://localhost:{port}"

                for _ in range(15):
                    if await docker_run.container_healthy(name, port, **kw):
                        break
                    await asyncio.sleep(2)
                else:
                    logs = await docker_run.container_logs(name, **kw)
                    raise RuntimeError(f"container never became healthy.\n{logs.combined[-1500:]}")

            dep["status"] = "running"
            await self.emit("execution.done", image_tag=image, mode=dep["mode"],
                            pod_name=dep.get("pod_name"), local_url=dep.get("local_url"),
                            status="running")
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

**`dep["mode"]`, `dep["container_id"]`, `dep["local_url"]` are additions to the
`Deployment` TypedDict** from `01-architecture.md` §1.5 — all `NotRequired`, so nothing
that already worked breaks. The web UI's run page checks `mode` to decide whether to show
a `kubectl port-forward` hint or a direct clickable `local_url`.

The `_diagnose` method is what separates a usable tool from a frustrating one, and it's
unchanged — when a Kubernetes rollout fails, the user gets pod events and container logs
in the same response, not "deployment failed." The `docker run` path gets the equivalent
treatment via `container_logs()` on a health-check timeout.

---

## Step 4.8 — Cleanup

Repeated runs accumulate images and deployments. Because the cleanup logic needs the
same mounted Docker socket and kubeconfig the app itself uses, it runs **inside the app
container** — there is no separate CLI tool that could do this from outside, since a
thin external client (`09-phase-5-orchestration.md`) has no access to those mounts.

```makefile
# Makefile addition
clean-deploys:
	docker compose exec app python -m deploymint.scripts.clean

clean-deploys-all:
	docker compose exec app python -m deploymint.scripts.clean --all
```

```python
# deploymint/scripts/clean.py
import argparse
from deploymint.core.runner import run_command
import asyncio


async def main(wipe_images: bool):
    await run_command(["kubectl", "delete", "deployment,service",
                       "-l", "managed-by=deploymint", "--ignore-not-found"])
    await run_command(["docker", "ps", "-aq", "--filter", "label=managed-by=deploymint"])
    if wipe_images:
        await run_command(["docker", "image", "prune", "-f",
                           "--filter", "label=managed-by=deploymint"])


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--all", action="store_true", dest="wipe_images")
    asyncio.run(main(**vars(p.parse_args())))
```

The `managed-by: deploymint` label on every generated manifest **and** on every
`docker run` container (`docker_run.py` in §4.6) is what makes this possible. Make sure
the templates, `_inject_image`, and `docker_run.run_container` all set it.

---

## Step 4.9 — Phase 4 acceptance test

```bash
curl -s -X POST localhost:8000/api/projects/1/runs -H 'content-type: application/json' -d '{}'
```

Then, while it runs:

```bash
docker compose exec app tmux ls
```

```bash
docker compose exec app tmux attach -t deploymint-<run_id>
```

After it completes, **with a reachable cluster** (kubeconfig mounted, per §0.1):

```bash
kubectl get pods -l managed-by=deploymint
```

```bash
kubectl port-forward svc/sample-api-svc 8081:8000 > /dev/null 2>&1 & sleep 2; curl -s localhost:8081/health; kill %1
```

**Or with no cluster reachable at all** (comment out the kubeconfig mount in
`docker-compose.yml` and restart to test this path):

```bash
docker ps --filter label=managed-by=deploymint
```

```bash
curl -s localhost:8000/api/runs/<run_id> | python -c "import json,sys; print(json.load(sys.stdin)['deployment']['local_url'])"
```

Both paths, then:

```bash
curl -s localhost:8000/api/runs/<run_id>/audit/verify
```

```bash
cat ./projects/sample-api/.deploymint/<run_id>/session.log | head -50
```

**Pass criteria:**

- With a reachable cluster: `deployment.mode="kubernetes"`, `kubectl get pods` shows
  `1/1 Running`, `curl localhost:8081/health` returns `{"status":"ok"}`
- With no cluster reachable: `deployment.mode="docker"`, `docker ps` shows the running
  container, and `curl {local_url}/health` returns `{"status":"ok"}` — **this is the
  path that must work for a user with only Docker installed**
- Session log contains the full `docker build` output and every subsequent command
- `audit/verify` returns `{"valid": true}` with ≥ 5 entries
- Tampering with one audit row makes verify return `valid: false` at the right seq
- A deliberately broken Dockerfile produces a failure that includes pod events + logs
  (Kubernetes path) or container logs (docker run path)

Tick **Phase 4**. Next: `09-phase-5-orchestration.md`.

---

## Time budget

| Task | Hours |
|---|---|
| Manual deploy verification (both paths) | 1.5 |
| Docker-outside-of-Docker host-path translation | 1.5 |
| tmux recorder | 2.0 |
| Audited command runner | 2.0 |
| Audit chain + verify endpoint | 2.0 |
| Docker build streaming (thread bridge) | 3.0 |
| kube_engine + docker_run fallback | 3.0 |
| Execution agent + diagnose path (both modes) | 3.5 |
| Cleanup script | 1.0 |
| Debugging the deploy loop (budget generously) | 4.0 |
| **Total** | **~23.5 h (2.5 days)** |

**The deploy loop will fight you, and the docker-run fallback adds a genuinely new
failure surface.** Wrong port, missing `/health`, read-only filesystem, the host-path
translation being off by one directory — each of these costs 20–40 minutes the first
time. This is normal. The manual verification in §4.1b, and testing both the
Kubernetes and docker-run paths explicitly, is what keeps it to a day and a half instead
of three.
