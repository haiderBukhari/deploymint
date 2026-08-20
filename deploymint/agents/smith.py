"""Artifact Smith: Claude-backed Dockerfile + K8s generation with a
deterministic template fallback. See docs/06-phase-2-generation.md §2.6."""

import json

import yaml
from pydantic import ValidationError

from deploymint.agents import templates
from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState
from deploymint.config import get_settings
from deploymint.core import llm, prompts
from deploymint.core.fewshot import format_fewshot
from deploymint.schemas.artifacts import GeneratedArtifacts

TRIM_KEYS = (
    "language", "framework", "package_manager", "entrypoint", "exposed_port",
    "python_version", "has_tests", "file_count", "dockerfile_exists",
)

# Shared 4000-token ceiling in llm.complete()'s default covered the Dockerfile
# + both YAML docs + reasoning all at once — richer reasoning needs real room.
# See docs/29-richer-reasoning.md.
SMITH_MAX_TOKENS = 8000

# Sending the raw import graph (every file, every edge) would blow the token
# budget on a large repo for no benefit — Smith only needs enough of it to
# reason about layer-caching order and which files are load-bearing. This
# caps it to counts plus the edges that actually touch a critical file.
_MAX_GRAPH_EDGES = 40


def _graph_summary(analysis: dict, critical_files: list[str]) -> dict:
    graph = analysis.get("graph") or {}
    nodes = graph.get("nodes") or []
    links = graph.get("links") or []
    critical = set(critical_files)
    touching = [
        link for link in links
        if link.get("source") in critical or link.get("target") in critical
    ][:_MAX_GRAPH_EDGES]
    return {
        "total_files": len(nodes),
        "total_import_edges": len(links),
        "edges_touching_critical_files": touching,
    }


class ArtifactSmithAgent(BaseAgent):
    name = "smith"

    async def run(self, state: DeployState) -> dict:
        s = get_settings()
        analysis = state.get("analysis") or {}
        project_name = state["project_name"]
        image = f"deploymint/{project_name}:{state['run_id']}"
        # Knobs from the architecture approval gate (docs/33), if this run
        # went through it — None on the ungated path, leaving every existing
        # behavior untouched.
        approved_plan = state.get("approved_plan")

        await self.emit("smith.thinking", model=s.model)

        artifacts, how, err = None, "template", None
        try:
            artifacts = await self._generate_llm(analysis, project_name, image, approved_plan)
            how = "llm"
        except llm.LLMError as e:
            # The API call itself failed (network, auth, rate limit, timeout —
            # anything raised from inside llm.complete()) — there is no raw
            # output to repair, self._last_raw was never set. Go straight to
            # the template fallback rather than crashing _repair() on a
            # missing attribute.
            err = str(e)[:300]
        except (ValidationError, ValueError, KeyError) as e:
            # The API call succeeded and DID produce output, but it failed our
            # own schema validation or JSON extraction — this is the case
            # repair is actually for.
            err = str(e)[:300]
            try:
                artifacts = await self._repair(analysis, project_name, image, err)
                how = "llm"
            except Exception as e2:
                err = f"{err} | repair failed: {str(e2)[:200]}"

        if artifacts is None:
            artifacts = templates.render(analysis, project_name, image, approved_plan)
            how = "template"
        else:
            artifacts = _inject_image(artifacts, image, project_name)
            if approved_plan:
                # The LLM path doesn't go through templates.render(), so it
                # needs its own binding step — same knobs, applied by
                # rewriting the model's YAML directly rather than
                # regenerating it. See docs/33-deploy-lock-and-findings.md.
                artifacts = _apply_approved_plan(artifacts, approved_plan)

        result: dict = {
            "artifacts": {
                "dockerfile": artifacts.dockerfile,
                "dockerignore": artifacts.dockerignore or _default_dockerignore(analysis),
                "k8s_deployment": artifacts.k8s_deployment,
                "k8s_service": artifacts.k8s_service,
                "generated_by": how,
                "model_used": s.model if how == "llm" else "none",
                "reasoning": artifacts.reasoning,
                "reasoning_detail": artifacts.reasoning_detail,
                **templates.render_extra_artifacts(
                    analysis, project_name, image, state["run_id"],
                    state.get("cloud_provider", "aws"), approved_plan),
            }
        }
        if err and how == "template":
            result["errors"] = state.get("errors", []) + [f"smith: fell back to template ({err})"]

        await self.emit(
            "smith.done", generated_by=how,
            files=["Dockerfile", ".dockerignore", "k8s-deployment.yaml", "k8s-service.yaml"],
        )
        return result

    async def _generate_llm(
        self, analysis: dict, project_name: str, image: str, approved_plan: dict | None = None,
    ) -> GeneratedArtifacts:
        # More critical files, and a compact summary of the import graph
        # around them — previously Smith never saw the graph/services at
        # all, so per-file rationale in `reasoning_detail` was impossible to
        # ask for honestly. See docs/29-richer-reasoning.md.
        critical_files = (analysis.get("critical_files") or [])[:10]
        trimmed = {k: analysis.get(k) for k in TRIM_KEYS}
        trimmed["dependencies"] = (analysis.get("dependencies") or [])[:30]
        trimmed["critical_files"] = critical_files
        trimmed["services"] = (analysis.get("services") or [])[:10]
        trimmed["import_graph_summary"] = _graph_summary(analysis, critical_files)

        fewshot = format_fewshot(analysis.get("language", ""), analysis.get("framework", ""))
        # The approved knobs override analysis's raw port — and get their own
        # paragraph in the prompt below so the LLM path honors replicas/
        # resources too, not just the deterministic template path (which
        # applies them post-hoc in templates.render()). See docs/33.
        exposed_port = (approved_plan or {}).get("port") or analysis.get("exposed_port", 8000)
        user = prompts.SMITH_USER.format(
            analysis_json=json.dumps(trimmed, indent=2),
            fewshot=fewshot,
            project_name=project_name,
            exposed_port=exposed_port,
            entrypoint=analysis.get("entrypoint", ""),
        )
        if approved_plan:
            user += (
                "\n\nThe user has approved this deployment plan — the Kubernetes "
                "Deployment you generate MUST use these exact values, not your own "
                f"defaults: replicas={approved_plan.get('replicas', 1)}, "
                f"cpu request={approved_plan.get('cpu_request', '100m')}, "
                f"cpu limit={approved_plan.get('cpu_limit', '500m')}, "
                f"memory request={approved_plan.get('memory_request', '128Mi')}, "
                f"memory limit={approved_plan.get('memory_limit', '512Mi')}, "
                f"container port={exposed_port}."
            )
        system = prompts.SMITH_SYSTEM.format(requirements=prompts.HARD_REQUIREMENTS)

        self._last_raw = await llm.complete(
            system, user, json_mode=True, max_tokens=SMITH_MAX_TOKENS)
        data = llm.extract_json(self._last_raw)
        return GeneratedArtifacts(**data)

    async def _repair(
        self, analysis: dict, project_name: str, image: str, error: str
    ) -> GeneratedArtifacts:
        user = prompts.SMITH_REPAIR.format(error=error, previous=self._last_raw[:3000])
        raw = await llm.complete(
            prompts.SMITH_SYSTEM.format(requirements=prompts.HARD_REQUIREMENTS),
            user, json_mode=True, max_tokens=SMITH_MAX_TOKENS,
        )
        self._last_raw = raw
        return GeneratedArtifacts(**llm.extract_json(raw))


def _inject_image(art: GeneratedArtifacts, image: str, name: str) -> GeneratedArtifacts:
    """Force the correct image tag, name, and imagePullPolicy — the model does
    not know the run-specific tag the Execution Engine is about to build."""
    try:
        doc = yaml.safe_load(art.k8s_deployment)
        doc["metadata"]["name"] = name
        for c in doc["spec"]["template"]["spec"]["containers"]:
            c["image"] = image
            c["imagePullPolicy"] = "IfNotPresent"
        for label_holder in (doc["metadata"], doc["spec"]["template"]["metadata"]):
            label_holder.setdefault("labels", {})["app"] = name
            label_holder["labels"]["managed-by"] = "deploymint"
        doc["spec"]["selector"]["matchLabels"]["app"] = name
        art.k8s_deployment = yaml.safe_dump(doc, sort_keys=False)

        svc = yaml.safe_load(art.k8s_service)
        svc["metadata"]["name"] = f"{name}-svc"
        svc["spec"].setdefault("type", "ClusterIP")
        svc["spec"].setdefault("selector", {})["app"] = name
        art.k8s_service = yaml.safe_dump(svc, sort_keys=False)
    except Exception:
        # If the model's YAML is too malformed to safely rewrite, let the
        # caller's outer validation/fallback handle it rather than crashing here.
        pass
    return art


def _apply_approved_plan(art: GeneratedArtifacts, approved_plan: dict) -> GeneratedArtifacts:
    """Rewrites the LLM-generated k8s_deployment/k8s_service YAML in place
    with the approved knobs — same binding requirement templates.render()
    satisfies for the deterministic path, applied here by patching the
    model's own YAML (same style as _inject_image above) rather than
    regenerating it from scratch. Never raises: malformed YAML from the
    model is the outer caller's problem (validation/fallback), not this
    best-effort patch's. See docs/33-deploy-lock-and-findings.md."""
    try:
        port = approved_plan.get("port")
        doc = yaml.safe_load(art.k8s_deployment)
        doc["spec"]["replicas"] = approved_plan.get("replicas", 1)
        containers = doc["spec"]["template"]["spec"]["containers"]
        for c in containers:
            c["resources"] = {
                "requests": {
                    "cpu": approved_plan.get("cpu_request", "100m"),
                    "memory": approved_plan.get("memory_request", "128Mi"),
                },
                "limits": {
                    "cpu": approved_plan.get("cpu_limit", "500m"),
                    "memory": approved_plan.get("memory_limit", "512Mi"),
                },
            }
            if port:
                for p in c.get("ports", []):
                    p["containerPort"] = port
        art.k8s_deployment = yaml.safe_dump(doc, sort_keys=False)

        if port:
            svc = yaml.safe_load(art.k8s_service)
            for p in svc.get("spec", {}).get("ports", []):
                p["port"] = port
                p["targetPort"] = port
            art.k8s_service = yaml.safe_dump(svc, sort_keys=False)
    except Exception:
        pass
    return art


def _default_dockerignore(analysis: dict) -> str:
    language = analysis.get("language")
    if language == "python":
        return "__pycache__/\n*.pyc\n.venv/\nvenv/\n.git/\n.deploymint/\n"
    if language == "javascript":
        return "node_modules/\n.git/\n.deploymint/\n"
    return ".git/\n.deploymint/\n"
