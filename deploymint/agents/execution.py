"""Execution Engine: builds the generated Dockerfile on the host's own Docker
daemon, then deploys to whatever Kubernetes cluster is reachable — or, if
none is, runs the built image directly with `docker run`. Every command is
recorded in a replayable tmux session and a hash-chained audit log.
See docs/08-phase-4-execution.md §4.7."""

import asyncio
from pathlib import Path

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState
from deploymint.core import docker_engine, docker_run, kube_engine
from deploymint.core.audit import AuditChain
from deploymint.core.tmux_recorder import TmuxRecorder


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
            await self.emit("execution.stage", stage="build")
            await self._mark("starting docker build...")
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

            dep["status"] = "deploying"
            if await kube_engine.cluster_reachable(**kw):
                dep["mode"] = "kubernetes"
                cluster = await kube_engine.kind_cluster_name()
                if cluster:
                    await self.emit("execution.stage", stage="load")
                    await self._mark(f"loading image into kind cluster {cluster}...")
                    r = await kube_engine.kind_load(image, cluster, **kw)
                    if not r.ok:
                        raise RuntimeError(f"kind load failed: {r.combined[:400]}")

                await self.emit("execution.stage", stage="apply")
                await self._mark("applying Kubernetes manifests...")
                r = await kube_engine.apply(
                    [str(art_dir / "k8s-deployment.yaml"), str(art_dir / "k8s-service.yaml")], **kw)
                dep["kubectl_output"] = r.combined
                if not r.ok:
                    raise RuntimeError(f"kubectl apply failed: {r.combined[:400]}")

                await self.emit("execution.stage", stage="rollout")
                await self._mark("waiting for rollout to complete...")
                r = await kube_engine.rollout_status(name, **kw)
                if not r.ok:
                    diag = await self._diagnose(name, **kw)
                    raise RuntimeError(f"rollout did not complete.\n{r.combined[:400]}\n\n{diag}")

                dep["pod_name"] = await kube_engine.get_pod_name(name, **kw)
            else:
                dep["mode"] = "docker"
                await self.emit("execution.stage", stage="docker_run")
                await self._mark("no cluster reachable — starting with docker run...")
                r = await docker_run.run_container(name, image, port, **kw)
                if not r.ok:
                    raise RuntimeError(f"docker run failed: {r.combined[:400]}")
                dep["container_id"] = r.stdout.strip()[:12]
                host_port = await docker_run.published_port(name, port, **kw)
                dep["local_url"] = f"http://localhost:{host_port or port}"

                for _ in range(15):
                    if await docker_run.container_healthy(name, **kw):
                        break
                    await asyncio.sleep(2)
                else:
                    logs = await docker_run.container_logs(name, **kw)
                    raise RuntimeError(f"container never became healthy.\n{logs.combined[-1500:]}")

            dep["status"] = "running"
            await self._mark(f"build+deploy succeeded — image: {image}")
            await self.emit("execution.done", image_tag=image, mode=dep["mode"],
                            pod_name=dep.get("pod_name"), local_url=dep.get("local_url"),
                            status="running")
            return {"deployment": dep}

        except Exception as e:
            dep["status"] = "failed"
            # Emitted twice, deliberately: as the generic "error" event (which
            # app.js surfaces on the step itself) AND as an execution.log line
            # (which lands in the terminal) — a build that fails before
            # producing any real stdout would otherwise leave the terminal
            # looking blank with no explanation anywhere on the page.
            await self._mark(f"execution failed: {str(e)[:300]}")
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

    async def _mark(self, message: str) -> None:
        """A bracketed marker line into the terminal stream, distinct from
        real subprocess output — so the terminal never *feels* inert even
        when the underlying docker/kubectl command is quiet, slow to start,
        or fails before producing any output of its own."""
        await self.emit("execution.log", line=f"[deploymint] {message}", stream="stdout")
