"""Kubernetes engine — used when the mounted kubeconfig reaches a real cluster.
See docs/08-phase-4-execution.md §4.6."""

from deploymint.config import get_settings
from deploymint.core.runner import run_command


def _ctx() -> list[str]:
    ctx = get_settings().kube_context
    return ["--context", ctx] if ctx else []


async def cluster_reachable(**kw) -> bool:
    """The single gate that decides Kubernetes vs. plain docker run."""
    r = await run_command(["kubectl", *_ctx(), "cluster-info",
                           "--request-timeout=5s"], timeout=10, **kw)
    return r.ok


async def is_kind_context() -> bool:
    r = await run_command(["kubectl", *_ctx(), "config", "current-context"], timeout=5)
    return r.ok and r.stdout.strip().startswith("kind-")


async def kind_cluster_name() -> str | None:
    """`kind load` needs the kind CLUSTER name, not the current kubectl context's
    project/app name — a `kind-<cluster>` context maps to cluster `<cluster>`."""
    r = await run_command(["kubectl", *_ctx(), "config", "current-context"], timeout=5)
    ctx = r.stdout.strip()
    return ctx.removeprefix("kind-") if r.ok and ctx.startswith("kind-") else None


async def kind_load(image: str, cluster_name: str, **kw):
    """Only relevant if the reachable cluster IS a kind cluster — kind runs its
    own containerd, separate from the host Docker daemon the build just used."""
    return await run_command(
        ["kind", "load", "docker-image", image, "--name", cluster_name],
        timeout=180, **kw)


async def kind_cluster_exists(name: str, **kw) -> bool:
    r = await run_command(["kind", "get", "clusters"], timeout=15, **kw)
    return r.ok and name in r.stdout.split()


async def ensure_kind_cluster(name: str = "deploymint", **kw) -> bool:
    """Auto-provisions a real local Kubernetes cluster instead of the plain
    `docker run` fallback — opt-in via `enable_auto_kind_cluster` (see
    execution.py). Never raises, same never-block-the-pipeline principle
    every other function in this file follows: returns True iff a cluster
    named `name` is confirmed to exist afterward (pre-existing or freshly
    created), False on any failure. `kind create cluster` sets the
    kubeconfig's current-context to `kind-{name}` automatically — no extra
    step needed for cluster_reachable()/kind_cluster_name() to immediately
    see it. See docs/32-architect-thread-offload.md's sibling doc for the
    Docker-networking caveat this depends on (macOS/Windows Docker Desktop
    may not make the created cluster reachable from inside this container —
    this degrades to a no-op False in that case, never a hang or a raise)."""
    try:
        if await kind_cluster_exists(name, **kw):
            return True
        r = await run_command(["kind", "create", "cluster", "--name", name],
                              timeout=180, **kw)
        return r.ok
    except Exception:
        return False


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
