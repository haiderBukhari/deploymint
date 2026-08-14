# 06 — Phase 2: Artifact Generation (Days 3–4)

**Goal:** Claude writes a Dockerfile and Kubernetes manifests specific to the analyzed
repo — validated, repaired on failure, and with a deterministic template fallback that
guarantees the run always produces artifacts even if the API call itself fails.

---

## Step 2.1 — Verify the Anthropic API before writing any code

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

```bash
source venv/bin/activate && python -c "
import anthropic
client = anthropic.Anthropic()
r = client.messages.create(model='claude-opus-5', max_tokens=32,
    messages=[{'role':'user','content':'Return only JSON: {\"ok\": true}'}])
print(r.content[0].text)
"
```

Expected: something very close to `{"ok": true}`, possibly with minor formatting around
it — which is exactly why `extract_json()` in §2.2 still exists even with a strong model.

### Measure your latency now

```bash
time python -c "
import anthropic
anthropic.Anthropic().messages.create(model='claude-opus-5', max_tokens=1024,
    messages=[{'role':'user','content':'Write a Dockerfile for a Python FastAPI app. Output only the Dockerfile.'}])
"
```

Note the wall time — typically 3–8s for a response this size, dramatically faster and
more reliable than an 8B local model. Your whole run is roughly
`smith_time + build_time + rollout_time`; budget `llm_timeout` accordingly (120s is
generous headroom, not an expectation).

---

## Step 2.2 — The LLM layer

```python
# deploymint/core/llm.py
import json
import re
import anthropic

from deploymint.config import get_settings

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    pass


_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        s = get_settings()
        if not s.anthropic_api_key:
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
        _client = anthropic.Anthropic(api_key=s.anthropic_api_key)
    return _client


async def health() -> tuple[bool, str]:
    import asyncio
    try:
        client = get_client()
    except LLMUnavailable as e:
        return False, str(e)
    try:
        await asyncio.to_thread(
            client.messages.create,
            model=get_settings().model, max_tokens=8,
            messages=[{"role": "user", "content": "ok"}],
        )
        return True, f"{get_settings().model} reachable"
    except anthropic.AuthenticationError:
        return False, "ANTHROPIC_API_KEY is set but invalid"
    except anthropic.APIConnectionError as e:
        return False, f"cannot reach the Anthropic API: {e}"
    except Exception as e:
        return False, str(e)[:200]


async def complete(system: str, user: str, *, max_tokens: int = 4000,
                    temperature: float = 0.1, json_mode: bool = False) -> str:
    """Single completion. Runs the blocking SDK call in a thread — see
    01-architecture.md §1.6 on why every blocking call must be wrapped this way."""
    import asyncio
    s = get_settings()
    client = get_client()

    sys_prompt = system
    if json_mode:
        sys_prompt += "\n\nReturn ONLY a JSON object. No markdown fences, no prose."

    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.messages.create,
                model=s.model, max_tokens=max_tokens, temperature=temperature,
                system=sys_prompt,
                messages=[{"role": "user", "content": user}],
            ),
            timeout=s.llm_timeout,
        )
    except anthropic.RateLimitError as e:
        raise LLMUnavailable(f"rate limited: {e}") from e
    except anthropic.APIConnectionError as e:
        raise LLMUnavailable(f"cannot reach the API: {e}") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"api error {e.status_code}: {e.message}") from e
    except TimeoutError as e:
        raise LLMError(f"timed out after {s.llm_timeout}s") from e

    return response.content[0].text


def extract_json(text: str) -> dict:
    """Even a strong model occasionally wraps JSON in fences or a sentence of prose.
    Dig it out rather than trying to prompt-engineer this away entirely."""
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

### Why the official `anthropic` SDK, not raw HTTP

There's no reason to hand-roll HTTP calls when a maintained, typed SDK exists — it
handles retries on 429/5xx, timeouts, and error typing for you. The only custom pieces
are `extract_json()` (a strong model still occasionally wraps output) and the
`asyncio.to_thread` wrapper (the SDK's sync client would otherwise block the event loop —
see `01-architecture.md` §1.6).

**One credential, one place it lives:** `ANTHROPIC_API_KEY` is read from the environment
by `get_settings()`, which in the running container comes from `.env` via
`docker-compose.yml` (`02-repo-layout.md` §2.4). It is never passed by the end user
per-request, never stored in the database, and never logged.

---

## Step 2.3 — Prompts (one file, versioned)

Create `deploymint/core/prompts.py` with `SMITH_SYSTEM`, `SMITH_USER`, `SMITH_REPAIR`,
`HARD_REQUIREMENTS`, `REDTEAM_SYSTEM`, `INTENT_SYSTEM`, `ARCHITECT_SUMMARY_PROMPT`,
`FINDING_EXPLANATION_PROMPT`, `ANOMALY_EXPLANATION_PROMPT` — full text across
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

**15–25 examples is enough.** With a strong model and retrieval by `(language,
framework)`, 2 examples injected per prompt already anchor the output shape reliably —
more examples in the library don't mean more get used per call, they just improve the
chance of an exact-stack match.

```python
def select_fewshot(language: str, framework: str, k: int = 2) -> list[dict]:
    rows = load_fewshot()
    exact = [r for r in rows if r["language"] == language and r["framework"] == framework]
    same_lang = [r for r in rows if r["language"] == language and r not in exact]
    generic = [r for r in rows if r["language"] != language]
    return (exact + same_lang + generic)[:k]
```

Write your own examples — they must satisfy all 12 hard requirements from
`04-agents-spec.md` §4.2, because the model will copy their structure. A sloppy few-shot
example produces sloppy output; this is the highest-leverage 90 minutes in Phase 2.

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

### Build templates first. Three reasons — none of them change with a stronger model.

1. They define what "correct output" looks like — and they become your few-shot examples.
2. They give you a working end-to-end pipeline **today**, without waiting on prompt
   quality. You can move to Phase 3 and 4 while generation is still rough.
3. They are the safety net. If the Anthropic API is rate-limited or down during your
   demo, nobody watching the terminal needs to know — the pipeline still produces
   correct, deployable output.

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
phase assumes a buildable, runnable image. (This `docker build`/`docker run` pair works
identically whether you're running it from your local venv or from inside the app
container talking to the mounted host socket — see `08-phase-4-execution.md` §8.1.)

---

## Step 2.6 — The Artifact Smith

```python
# deploymint/agents/smith.py
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
                how = "llm"
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
                "model_used": s.model if how == "llm" else "none",
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

Note there is no `"llm+repair"` distinction in `generated_by` anymore — both a clean
first pass and a successful repair are just `"llm"`. The repair loop is bookkeeping for
reliability, not something worth surfacing to the user; what matters to them is whether
Claude wrote it or the template did.

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

The model will happily write `image: my-app:latest` — which does not exist on the
cluster. Post-processing the image tag deterministically removes an entire class of
"the pod won't start" bugs. **Do this. It is the single highest-value 20 lines in Phase 2,**
and it has nothing to do with model quality — even a perfect model doesn't know the
run-specific tag your Execution Engine is about to build.

Also make sure label selectors match between Deployment and Service — normalize both to
`app: {name}` in the injection step, or the Service routes to nothing.

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
        "repo_path": "/workspace/t", "force": False, "errors": [], "current_node": "",
        "analysis": ANALYSIS}


@pytest.mark.asyncio
async def test_falls_back_to_template_when_llm_returns_garbage():
    with patch("deploymint.core.llm.complete", return_value="I'm sorry, I can't help."):
        out = await ArtifactSmithAgent().run(dict(BASE))
    assert out["artifacts"]["generated_by"] == "template"
    assert "FROM python:3.11" in out["artifacts"]["dockerfile"]


@pytest.mark.asyncio
async def test_falls_back_when_api_is_unreachable():
    from deploymint.core.llm import LLMUnavailable
    with patch("deploymint.core.llm.complete", side_effect=LLMUnavailable("rate limited")):
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
on: **the pipeline never returns empty artifacts, no matter what the model does or
whether the API is reachable at all.** Mocking `llm.complete` directly (rather than
mocking the Anthropic SDK client) keeps these tests fast and independent of network
access or a real API key — they should pass in CI with no `ANTHROPIC_API_KEY` set.

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
- `generated_by` is `llm` on essentially every attempt (Claude's output quality means
  the template fallback should be rare in normal operation — unlike an 8B local model
  where it fired routinely)
- Docker build of the **Claude-generated** Dockerfile succeeds
- `kubectl apply --dry-run=client` accepts both manifests
- Unsetting `ANTHROPIC_API_KEY` and re-running still produces a complete artifact set
  with `generated_by: "template"` — resilience, not offline support, but the run must
  still succeed
- `pytest tests/test_smith.py` green with no `ANTHROPIC_API_KEY` in the test environment

Tick **Phase 2**. Next: `07-phase-3-security.md`.

---

## Time budget

| Task | Hours |
|---|---|
| LLM layer (Anthropic SDK) + extract_json + health | 1.5 |
| Prompts | 1.5 |
| Templates (4 stacks) + manual build verification | 4.0 |
| Few-shot curation (15–25 examples) | 2.0 |
| Smith agent + repair loop + image injection | 2.5 |
| Runner + run API endpoints | 2.0 |
| Tests | 2.0 |
| Prompt iteration | 1.5 |
| **Total** | **~17 h (2 days)** |

**Prompt iteration is still a real line item, just a smaller one.** Claude's first-pass
output rate against the schema is much higher than an 8B local model's, but budget at
least a couple of rounds — the highest-leverage lever remains better few-shot examples,
not longer instructions.
