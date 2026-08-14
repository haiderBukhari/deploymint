"""The no-cluster-reachable fallback: run the built image directly via the same
mounted Docker socket. This is what proves the pipeline end-to-end for a user
who has Docker but no Kubernetes at all. See docs/08-phase-4-execution.md §4.6."""

from deploymint.core.runner import run_command


async def run_container(name: str, image: str, port: int, **kw):
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
