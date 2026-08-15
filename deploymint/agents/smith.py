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
    "python_version", "has_tests", "file_count",
)


class ArtifactSmithAgent(BaseAgent):
    name = "smith"

    async def run(self, state: DeployState) -> dict:
        s = get_settings()
        analysis = state.get("analysis") or {}
        project_name = state["project_name"]
        image = f"deploymint/{project_name}:{state['run_id']}"

        await self.emit("smith.thinking", model=s.model)

        artifacts, how, err = None, "template", None
        try:
            artifacts = await self._generate_llm(analysis, project_name, image)
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
            artifacts = templates.render(analysis, project_name, image)
            how = "template"
        else:
            artifacts = _inject_image(artifacts, image, project_name)

        result: dict = {
            "artifacts": {
                "dockerfile": artifacts.dockerfile,
                "dockerignore": artifacts.dockerignore or _default_dockerignore(analysis),
                "k8s_deployment": artifacts.k8s_deployment,
                "k8s_service": artifacts.k8s_service,
                "generated_by": how,
                "model_used": s.model if how == "llm" else "none",
                "reasoning": artifacts.reasoning,
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
        self, analysis: dict, project_name: str, image: str
    ) -> GeneratedArtifacts:
        trimmed = {k: analysis.get(k) for k in TRIM_KEYS}
        trimmed["dependencies"] = (analysis.get("dependencies") or [])[:30]
        trimmed["critical_files"] = (analysis.get("critical_files") or [])[:5]

        fewshot = format_fewshot(analysis.get("language", ""), analysis.get("framework", ""))
        user = prompts.SMITH_USER.format(
            analysis_json=json.dumps(trimmed, indent=2),
            fewshot=fewshot,
            project_name=project_name,
            exposed_port=analysis.get("exposed_port", 8000),
            entrypoint=analysis.get("entrypoint", ""),
        )
        system = prompts.SMITH_SYSTEM.format(requirements=prompts.HARD_REQUIREMENTS)

        self._last_raw = await llm.complete(system, user, json_mode=True)
        data = llm.extract_json(self._last_raw)
        return GeneratedArtifacts(**data)

    async def _repair(
        self, analysis: dict, project_name: str, image: str, error: str
    ) -> GeneratedArtifacts:
        user = prompts.SMITH_REPAIR.format(error=error, previous=self._last_raw[:3000])
        raw = await llm.complete(
            prompts.SMITH_SYSTEM.format(requirements=prompts.HARD_REQUIREMENTS),
            user, json_mode=True,
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


def _default_dockerignore(analysis: dict) -> str:
    language = analysis.get("language")
    if language == "python":
        return "__pycache__/\n*.pyc\n.venv/\nvenv/\n.git/\n.deploymint/\n"
    if language == "javascript":
        return "node_modules/\n.git/\n.deploymint/\n"
    return ".git/\n.deploymint/\n"
