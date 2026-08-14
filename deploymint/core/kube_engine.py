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
