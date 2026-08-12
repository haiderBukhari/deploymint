# 06 — Phase 2: Artifact Generation (Days 3–4)

**Goal:** the LLM writes a Dockerfile and Kubernetes manifests specific to the analyzed
repo — validated, repaired on failure, and with a deterministic template fallback that
guarantees the run always produces artifacts.

---

## Step 2.1 — Verify Ollama before writing any code

```bash
curl -s localhost:11434/api/tags | python -m json.tool | head -30
```

```bash
ollama run llama3.1:8b "Return only JSON: {\"ok\": true}"
```

If the second command returns prose around the JSON, that is **expected** and exactly why
`extract_json()` exists. Do not try to prompt-engineer it away entirely.

### Measure your latency now

```bash
time ollama run llama3.1:8b "Write a Dockerfile for a Python FastAPI app. Output only the Dockerfile."
```

Note the wall time. On an M-series Mac this is typically 8–25 s. Your whole run will be
roughly `smith_time + build_time + rollout_time`. If generation takes 40 s, set
`llm_timeout` accordingly and consider `llama3.2` (2 GB, much faster) for the dev loop —
you already have it pulled.

**Dev tip that will save you hours:** add `DEPLOYMINT_MODEL=llama3.2` for iteration, and
switch to `llama3.1:8b` for quality runs and the demo.

---

## Step 2.2 — The LLM layer

```python
# deploymint/core/llm.py
import asyncio
import json
import re
import httpx

from deploymint.config import get_settings

FENCE_RE = re.compile(r"```(?:json|yaml|dockerfile)?\s*(.*?)```", re.DOTALL)


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    pass


async def health() -> tuple[bool, str]:
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{s.ollama_base_url}/api/tags")
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
    except Exception as e:
        return False, f"Ollama unreachable at {s.ollama_base_url}: {e}"

    if not any(m.split(":")[0] == s.model.split(":")[0] for m in models):
        return False, f"model '{s.model}' not pulled. Run: ollama pull {s.model}"
    return True, f"{s.model} ready"


async def complete(
    system: str,
    user: str,
    *,
    temperature: float | None = None,
    json_mode: bool = False,
    timeout: int | None = None,
) -> str:
    """Single completion against Ollama's /api/chat."""
    s = get_settings()
    payload = {
        "model": s.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": temperature if temperature is not None else s.llm_temperature},
    }
    if json_mode:
        payload["format"] = "json"

    try:
        async with httpx.AsyncClient(timeout=timeout or s.llm_timeout) as c:
            r = await c.post(f"{s.ollama_base_url}/api/chat", json=payload)
            r.raise_for_status()
            return r.json()["message"]["content"]
    except httpx.ConnectError as e:
        raise LLMUnavailable(f"cannot reach Ollama at {s.ollama_base_url}") from e
    except httpx.TimeoutException as e:
        raise LLMError(f"model timed out after {timeout or s.llm_timeout}s") from e


def extract_json(text: str) -> dict:
    text = text.strip()
    m = FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(text[start : end + 1])


async def complete_json(system: str, user: str, **kw) -> dict:
    raw = await complete(system, user, json_mode=True, **kw)
    return extract_json(raw)
```

### Why raw `httpx` instead of `langchain-ollama`

- `format: "json"` is an Ollama-native flag that dramatically improves structured output.
  Going direct means it is one line, not a wrapper hunt.
- One fewer abstraction between you and the error message. When generation fails on
  Day 4, you want the actual HTTP response.
- LangChain is still used for LangGraph in Phase 5 — you are not avoiding the dependency,
  just not routing your critical path through it.

**LiteLLM swap (stretch goal):** add `provider: str = "ollama"` to settings and branch
inside `complete()`. Because everything calls `complete()`, that one branch is the entire
"zero-code model swap" feature from the proposal. Build the seam now, the branch later.

---

## Step 2.3 — Prompts (one file, versioned)

Create `deploymint/core/prompts.py` with `SMITH_SYSTEM`, `SMITH_USER`, `SMITH_REPAIR`,
`HARD_REQUIREMENTS`, `REDTEAM_SYSTEM`, `INTENT_SYSTEM`, `FINOPS_ANSWER` — full text in
`04-agents-spec.md`.

Add a version constant and store it on each run:

```python
PROMPT_VERSION = "2026.08.12-1"
```

When output quality changes, you will want to know which prompt produced which artifacts.
This costs one line and saves real confusion later.

---

## Step 2.4 — Few-shot examples

`deploymint/data/fewshot.jsonl` — one JSON object per line:

```json
{"language":"python","framework":"fastapi","package_manager":"pip","dockerfile":"...","k8s_deployment":"...","k8s_service":"...","notes":"multi-stage, non-root, healthcheck"}
```

**15–25 examples is enough.** The proposal says 150–200; that is a data-collection
project, not a two-week MVP feature. With retrieval by `(language, framework)` and
2 examples injected per prompt, more examples add nothing to an 8B model's context budget.

Selection:

```python
def select_fewshot(language: str, framework: str, k: int = 2) -> list[dict]:
    rows = load_fewshot()
    exact = [r for r in rows if r["language"] == language and r["framework"] == framework]
    same_lang = [r for r in rows if r["language"] == language and r not in exact]
    generic = [r for r in rows if r["language"] != language]
    return (exact + same_lang + generic)[:k]
```

Write your own examples — they must satisfy all 12 hard requirements, because the model
will copy their structure. A sloppy few-shot example produces sloppy output; this is the
highest-leverage 90 minutes in Phase 2.

**Keep them small.** Two full artifact sets is ~2000 tokens. Three is pushing an 8B
model's useful attention. Prefer 2.

---

## Step 2.5 — Templates (write these BEFORE the LLM path)

`deploymint/agents/templates.py`. One function per `(language, framework)`, plus a
generic per-language fallback.

```python
from deploymint.schemas.artifacts import GeneratedArtifacts
from deploymint.agents.state import RepoAnalysis


def render(analysis: RepoAnalysis, project_name: str, image: str) -> GeneratedArtifacts:
    key = (analysis["language"], analysis["framework"])
    fn = REGISTRY.get(key) or REGISTRY.get((analysis["language"], "*")) or _generic
    return fn(analysis, project_name, image)


REGISTRY = {
    ("python", "fastapi"): _python_fastapi,
    ("python", "flask"):   _python_flask,
    ("python", "django"):  _python_django,
    ("python", "*"):       _python_generic,
    ("javascript", "express"): _node_express,
    ("javascript", "*"):   _node_generic,
    ("go", "*"):           _go_generic,
    ("java", "*"):         _java_generic,
}
```

### Build templates first. Three reasons.

1. They define what "correct output" looks like — and they become your few-shot examples.
2. They give you a working end-to-end pipeline **today**, without waiting on model quality.
   You can move to Phase 3 and 4 while generation quality is still rough.
3. They are the safety net. If the model is unavailable during your demo, nobody knows.

The FastAPI template (full text in `04-agents-spec.md` §4.2) must produce a Dockerfile
that actually builds. **Verify manually before moving on:**

```bash
python -c "
from deploymint.agents.templates import render
a = {'language':'python','framework':'fastapi','python_version':'3.11','entrypoint':'main.py','exposed_port':8000,'package_manager':'pip'}
art = render(a, 'sample-api', 'deploymint/sample-api:test')
open('/tmp/Dockerfile.test','w').write(art.dockerfile)
print(art.dockerfile)
"
```

```bash
docker build -f /tmp/Dockerfile.test -t dm-template-test ./tests/fixtures/sample_fastapi
```

```bash
docker run --rm -d -p 8099:8000 --name dmtest dm-template-test && sleep 3 && curl -s localhost:8099/health; docker rm -f dmtest
```

**If this does not print `{"status":"ok"}`, stop and fix the template.** Every downstream
phase assumes a buildable, runnable image.

---

## Step 2.6 — The Artifact Smith

```python
# deploymint/agents/smith.py
import asyncio
from pydantic import ValidationError

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState
from deploymint.agents import templates
from deploymint.core import llm, prompts
from deploymint.config import get_settings
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
        except (llm.LLMError, ValidationError, ValueError, KeyError) as e:
            err = str(e)[:300]
            try:
                artifacts = await self._repair(analysis, project_name, image, err)
                how = "llm+repair"
            except Exception as e2:
                err = f"{err} | repair failed: {str(e2)[:200]}"

        if artifacts is None:
            artifacts = templates.render(analysis, project_name, image)
            how = "template"

        result = {
            "artifacts": {
                "dockerfile": artifacts.dockerfile,
                "dockerignore": artifacts.dockerignore or _default_dockerignore(analysis),
                "k8s_deployment": artifacts.k8s_deployment,
                "k8s_service": artifacts.k8s_service,
                "generated_by": how,
                "model_used": s.model if how != "template" else "none",
            }
        }
        if err and how == "template":
            result["errors"] = state.get("errors", []) + [f"smith: fell back to template ({err})"]

        await self.emit("smith.done", generated_by=how,
                        files=["Dockerfile", ".dockerignore", "k8s-deployment.yaml", "k8s-service.yaml"])
        return result

    async def _generate_llm(self, analysis, project_name, image) -> GeneratedArtifacts:
        import json
        trimmed = {k: analysis.get(k) for k in TRIM_KEYS}
        trimmed["dependencies"] = (analysis.get("dependencies") or [])[:30]
        trimmed["critical_files"] = (analysis.get("critical_files") or [])[:5]

        fewshot = _format_fewshot(analysis)
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
        art = GeneratedArtifacts(**data)
        return _inject_image(art, image, project_name)

    async def _repair(self, analysis, project_name, image, error) -> GeneratedArtifacts:
        user = prompts.SMITH_REPAIR.format(error=error, previous=self._last_raw[:3000])
        raw = await llm.complete(prompts.SMITH_SYSTEM.format(
            requirements=prompts.HARD_REQUIREMENTS), user, json_mode=True)
        art = GeneratedArtifacts(**llm.extract_json(raw))
        return _inject_image(art, image, project_name)
```

### `_inject_image` — do not trust the model with the image tag

```python
import yaml

def _inject_image(art: GeneratedArtifacts, image: str, name: str) -> GeneratedArtifacts:
    """Force the correct image tag, name, and imagePullPolicy into the Deployment."""
    doc = yaml.safe_load(art.k8s_deployment)
    doc["metadata"]["name"] = name
    for c in doc["spec"]["template"]["spec"]["containers"]:
        c["image"] = image
        c["imagePullPolicy"] = "IfNotPresent"
    art.k8s_deployment = yaml.safe_dump(doc, sort_keys=False)

    svc = yaml.safe_load(art.k8s_service)
    svc["metadata"]["name"] = f"{name}-svc"
    svc["spec"].setdefault("type", "ClusterIP")
    art.k8s_service = yaml.safe_dump(svc, sort_keys=False)
    return art
```

The model will happily write `image: my-app:latest` — which does not exist in your kind
cluster. Post-processing the image tag deterministically removes an entire class of
"the pod won't start" bugs. **Do this. It is the single highest-value 20 lines in Phase 2.**

Also make sure label selectors match between Deployment and Service — if the model
generates `app: myapp` in one and `app: my-app` in the other, the Service routes to
nothing. Normalize both to `app: {name}` in the injection step.

---

## Step 2.7 — Wire generation into a run (temporary linear driver)

LangGraph comes in Phase 5. For now, a plain function so you can exercise the pipeline:

```python
# deploymint/runner/manager.py  (Phase 2 version)
async def execute_run_linear(run_id: str, project, bus) -> dict:
    from deploymint.agents.architect import ArchitectAgent
    from deploymint.agents.smith import ArtifactSmithAgent

    state = {
        "run_id": run_id, "project_id": project.id, "project_name": project.name,
        "repo_path": project.repo_path, "force": False, "errors": [], "current_node": "",
    }
    for agent in (ArchitectAgent(bus), ArtifactSmithAgent(bus)):
        state["current_node"] = agent.name
        state.update(await agent.run(state))
    return state
```

Add `POST /api/projects/{id}/runs` returning `202 {"run_id": ...}` and spawning this via
`asyncio.create_task`. Add `GET /api/runs/{run_id}` and
`GET /api/runs/{run_id}/artifacts`.

---

## Step 2.8 — Tests

```python
# tests/test_smith.py
import pytest
from unittest.mock import patch
from deploymint.agents.smith import ArtifactSmithAgent

ANALYSIS = {
    "language": "python", "framework": "fastapi", "package_manager": "pip",
    "entrypoint": "main.py", "exposed_port": 8000, "python_version": "3.11",
    "dependencies": ["fastapi", "uvicorn"], "critical_files": [], "has_tests": True,
    "file_count": 6,
}
BASE = {"run_id": "run_test", "project_id": 1, "project_name": "t",
        "repo_path": "/tmp", "force": False, "errors": [], "current_node": "",
        "analysis": ANALYSIS}


@pytest.mark.asyncio
async def test_falls_back_to_template_when_llm_returns_garbage():
    with patch("deploymint.core.llm.complete", return_value="I'm sorry, I can't help."):
        out = await ArtifactSmithAgent().run(dict(BASE))
    assert out["artifacts"]["generated_by"] == "template"
    assert "FROM python:3.11" in out["artifacts"]["dockerfile"]


@pytest.mark.asyncio
async def test_falls_back_when_ollama_is_down():
    from deploymint.core.llm import LLMUnavailable
    with patch("deploymint.core.llm.complete", side_effect=LLMUnavailable("down")):
        out = await ArtifactSmithAgent().run(dict(BASE))
    assert out["artifacts"]["generated_by"] == "template"
    assert out["artifacts"]["dockerfile"]


@pytest.mark.asyncio
async def test_strips_markdown_fences():
    fenced = '```json\n{"dockerfile":"FROM python:3.11-slim\\nUSER 10001\\nCMD [\\"python\\"]",' \
             '"dockerignore":"","k8s_deployment":"kind: Deployment\\nmetadata:\\n  name: t\\n' \
             'spec:\\n  template:\\n    spec:\\n      containers:\\n      - name: t\\n' \
             '        image: x","k8s_service":"kind: Service\\nmetadata:\\n  name: t\\n' \
             'spec:\\n  ports: []","reasoning":"ok"}\n```'
    with patch("deploymint.core.llm.complete", return_value=fenced):
        out = await ArtifactSmithAgent().run(dict(BASE))
    assert out["artifacts"]["generated_by"] == "llm"
    assert "```" not in out["artifacts"]["dockerfile"]
```

The first two tests are the important ones. They assert the property your demo depends
on: **the pipeline never returns empty artifacts, no matter what the model does.**

---

## Step 2.9 — Phase 2 acceptance test

```bash
curl -s -X POST localhost:8000/api/projects/1/runs -H 'content-type: application/json' -d '{}'
```

```bash
curl -s localhost:8000/api/runs/<run_id>/artifacts | python -m json.tool | head -40
```

Then prove the generated output is real:

```bash
mkdir -p /tmp/dmcheck && curl -s localhost:8000/api/runs/<run_id>/artifacts/Dockerfile > /tmp/dmcheck/Dockerfile && docker build -f /tmp/dmcheck/Dockerfile -t dm-gen-test ./tests/fixtures/sample_fastapi
```

```bash
curl -s localhost:8000/api/runs/<run_id>/artifacts/k8s-deployment.yaml | kubectl apply --dry-run=client -f -
```

**Pass criteria:**

- Run reaches `status=success` with `artifacts` populated
- `generated_by` is `llm` or `llm+repair` at least once in five attempts
- Docker build of the **LLM-generated** Dockerfile succeeds
- `kubectl apply --dry-run=client` accepts both manifests
- Killing Ollama (`pkill ollama`) and re-running still produces a complete artifact set
  with `generated_by: "template"`
- `pytest tests/test_smith.py` green

Tick **Phase 2**. Next: `07-phase-3-security.md`.

---

## Time budget

| Task | Hours |
|---|---|
| LLM layer + extract_json + health | 2.0 |
| Prompts | 1.5 |
| Templates (4 stacks) + manual build verification | 4.0 |
| Few-shot curation (15–25 examples) | 2.0 |
| Smith agent + repair loop + image injection | 3.0 |
| Runner + run API endpoints | 2.0 |
| Tests | 2.0 |
| Prompt iteration (budget for this — it is real) | 2.0 |
| **Total** | **~18.5 h (2 days)** |

**Prompt iteration is a real line item.** Your first prompt will produce output that
fails validation ~50% of the time on an 8B model. Expect 4–6 rounds. The fastest lever
is better few-shot examples, not longer instructions.
