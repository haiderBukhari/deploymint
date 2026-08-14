"""Docker build via the (Docker-outside-of-Docker) mounted host socket.
See docs/08-phase-4-execution.md §4.1a and §4.5."""

import asyncio
import os
from pathlib import Path

import docker
from docker.errors import BuildError, DockerException

from deploymint.config import get_settings


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


def to_host_path(container_path: str) -> str:
    """Build contexts must be paths the HOST daemon can see. Inside the shipped
    Docker Compose distribution the app container's /workspace is a bind mount
    of DEPLOYMINT_PROJECTS_DIR_HOST on the host, so the container-local path
    must be translated. Running natively (no socket mount, DEPLOYMINT_PROJECTS_DIR_HOST
    unset) there is nothing to translate — the app and the daemon already share
    one filesystem view."""
    host_root_env = os.environ.get("DEPLOYMINT_PROJECTS_DIR_HOST")
    if not host_root_env:
        return container_path
    workspace_root = get_settings().workspace_root
    rel = Path(container_path).relative_to(workspace_root)
    return str(Path(host_root_env) / rel)


def _build_sync(context: str, dockerfile: str, tag: str, line_cb):
    client = get_client()
    host_context = to_host_path(context)
    dockerfile_rel = str(Path(dockerfile).relative_to(Path(context)))
    stream = client.api.build(
        path=host_context, dockerfile=dockerfile_rel, tag=tag,
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
