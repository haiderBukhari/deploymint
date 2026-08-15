"""The no-cluster-reachable fallback: run the built image directly via the same
mounted Docker socket. This is what proves the pipeline end-to-end for a user
who has Docker but no Kubernetes at all. See docs/08-phase-4-execution.md §4.6.

Port publishing must be delegated to the daemon, not chosen by this process:
`docker run` here goes through the mounted host socket, so the port is bound
on the real HOST's network, not this container's own network namespace. A
free-port check done locally (e.g. via `socket.bind`) would check the wrong
network entirely. Bind to port 0 (daemon picks a free host port) and read the
result back with `docker port` — this also avoids colliding with the
DeployMint dashboard's own published port, which a hardcoded same-numbered
mapping does whenever the deployed app's exposed port matches it (commonly
8000, DeployMint's own default)."""

from deploymint.core.runner import run_command


async def run_container(name: str, image: str, port: int, **kw):
    await run_command(["docker", "rm", "-f", name], timeout=15)  # clean up a prior run
    return await run_command(
        ["docker", "run", "-d", "--name", name, "-p", f"127.0.0.1::{port}",
         "--label", "managed-by=deploymint", image],
        timeout=30, **kw)


async def published_port(name: str, container_port: int, **kw) -> int | None:
    """Asks the daemon what host port it actually assigned — see module note above."""
    r = await run_command(["docker", "port", name, str(container_port)], timeout=10, **kw)
    if not r.ok or not r.stdout.strip():
        return None
    try:
        return int(r.stdout.strip().splitlines()[0].rsplit(":", 1)[-1])
    except ValueError:
        return None


async def container_healthy(name: str, **kw) -> bool:
    """Reads the CONTAINER's OWN `HEALTHCHECK` status via the daemon, rather
    than `docker exec`-ing an HTTP client into the deployed image. The prior
    approach execed `curl`, which the generated runtime images never install
    (see HARD_REQUIREMENTS — the Dockerfile's own HEALTHCHECK uses a
    language-native check, e.g. `python -c "import urllib.request..."`, for
    exactly this reason) — so the exec always failed with "curl: not found"
    regardless of whether the app was actually healthy. Not every template
    defines a HEALTHCHECK (the Go template doesn't), so fall back to "is the
    container still running" when there's no health status to read."""
    r = await run_command(
        ["docker", "inspect", "-f", "{{json .State.Health}}", name],
        timeout=10, **kw)
    if r.ok and r.stdout.strip() not in ("", "null"):
        import json

        try:
            return json.loads(r.stdout)["Status"] == "healthy"
        except (json.JSONDecodeError, KeyError, TypeError):
            return False

    r = await run_command(
        ["docker", "inspect", "-f", "{{.State.Running}}", name], timeout=10, **kw)
    return r.ok and r.stdout.strip() == "true"


async def container_logs(name: str, tail: int = 100, **kw):
    return await run_command(["docker", "logs", "--tail", str(tail), name], timeout=15, **kw)
