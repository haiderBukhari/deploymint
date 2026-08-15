"""Docker build via the (Docker-outside-of-Docker) mounted host socket.
See docs/08-phase-4-execution.md §4.1a and §4.5.

No host-path translation is needed here, despite earlier drafts of this
design assuming otherwise. `docker build` — whether via the CLI or this SDK
call — always builds its tar context from the CLIENT's own local filesystem
(here, the app container's view of the bind-mounted /workspace) and streams
those bytes over the socket; the daemon on the other end never resolves
`path` against its own filesystem. Verified directly: `docker build -f
.deploymint/<run_id>/Dockerfile .` run from inside the app container against
the mounted host socket succeeds using the container-local path as-is, and
the resulting image is visible in `docker images` on the HOST — proving the
build isn't trapped in a nested daemon either."""

import asyncio

import docker
from docker.errors import BuildError, DockerException


def get_client():
    try:
        c = docker.from_env()   # unix:///var/run/docker.sock — the HOST's daemon
        c.ping()
        return c
    except DockerException as e:
        raise RuntimeError(
            "Cannot reach the Docker daemon via the mounted socket. Is "
            f"/var/run/docker.sock mounted in docker-compose.yml? ({e})"
        ) from e


def _build_sync(context: str, dockerfile: str, tag: str, line_cb):
    from pathlib import Path

    client = get_client()
    dockerfile_rel = str(Path(dockerfile).relative_to(Path(context)))
    stream = client.api.build(
        path=context, dockerfile=dockerfile_rel, tag=tag,
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
        except TimeoutError:
            continue
    return await task
