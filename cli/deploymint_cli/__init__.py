"""A genuinely separate package from deploymint itself — it never imports
agents/core/db, because it never runs any of that code; the container
already running via `docker compose up -d` does. See
docs/09-phase-5-orchestration.md §5.5."""

import asyncio
import json
import sys

import click
import httpx
import websockets
from rich.console import Console
from rich.live import Live
from rich.table import Table

console = Console()
NODES = ["architect", "smith", "warden", "redteam", "execution"]
EXIT_CODES = {"success": 0, "blocked": 2}


@click.group()
def cli():
    """DeployMint CLI — talks to an already-running `docker compose up -d` container."""


@cli.command()
@click.argument("path")
@click.option("--name", default=None)
@click.option("--force", is_flag=True, help="deploy even if security checks fail")
@click.option("--no-deploy", is_flag=True, help="generate + scan only")
@click.option("--server", "server_url", default="http://localhost:8000",
             envvar="DEPLOYMINT_SERVER")
def up(path, name, force, no_deploy, server_url):
    """Register PATH with a running DeployMint container and deploy it."""
    try:
        httpx.get(f"{server_url}/health", timeout=3).raise_for_status()
    except httpx.HTTPError:
        console.print(f"[red]Cannot reach DeployMint at {server_url}.[/] "
                      "Is it running? Try: [cyan]docker compose up -d[/]")
        sys.exit(3)

    project_name = name or path.strip("/").split("/")[-1]
    r = httpx.post(f"{server_url}/api/projects",
                   json={"name": project_name, "repo_path": path})
    if r.status_code == 409:
        r = httpx.get(f"{server_url}/api/projects", params={"name": project_name})
        project_id = next(p["id"] for p in r.json() if p["name"] == project_name)
    else:
        r.raise_for_status()
        project_id = r.json()["id"]

    r = httpx.post(f"{server_url}/api/projects/{project_id}/runs",
                   json={"force": force, "skip_deploy": no_deploy, "trigger": "cli"})
    r.raise_for_status()
    run_id = r.json()["run_id"]

    exit_code = asyncio.run(_stream(server_url, run_id))
    sys.exit(exit_code)


async def _stream(server_url: str, run_id: str) -> int:
    ws_url = server_url.replace("http", "ws", 1) + f"/ws/runs/{run_id}"
    status_by_node: dict[str, str] = {}
    log_tail: list[str] = []
    final_status = "running"
    blocked_reason = None

    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"since": 0}))
        with Live(_render(status_by_node, log_tail), console=console,
                 refresh_per_second=4) as live:
            async for raw in ws:
                evt = json.loads(raw)
                etype, payload = evt["type"], evt.get("payload", {})
                if etype == "node.enter":
                    status_by_node[payload["node"]] = "running"
                elif etype == "node.exit":
                    status_by_node[payload["node"]] = "done"
                elif etype == "execution.log":
                    log_tail.append(payload["line"])
                    log_tail[:] = log_tail[-15:]
                elif etype == "run.end":
                    final_status = payload["status"]
                    blocked_reason = payload.get("reason")
                live.update(_render(status_by_node, log_tail))

    console.print(f"\n[bold]Result:[/] {final_status}")
    if final_status == "blocked" and blocked_reason:
        console.print(f"[red]{blocked_reason}[/]")
    return EXIT_CODES.get(final_status, 1)


def _render(status_by_node: dict, log_tail: list):
    table = Table(show_header=False)
    for n in NODES:
        icon = {"running": "◐", "done": "✓"}.get(status_by_node.get(n), "○")
        table.add_row(icon, n)
    for line in log_tail[-5:]:
        table.add_row("", f"[dim]{line}[/]")
    return table


if __name__ == "__main__":
    cli()
