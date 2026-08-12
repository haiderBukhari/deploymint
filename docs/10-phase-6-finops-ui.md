# 10 — Phase 6: FinOps, Observability & Web UI (Days 12–13)

**Goal:** the Observability Oracle watches the new deployment and rolls back on failure,
the FinOps Agent answers *"which service costs the most?"*, and the whole thing is
visible in a browser at `http://localhost:8000`.

---

## Step 6.1 — Observability Oracle

```python
# deploymint/agents/oracle.py
import asyncio, json
import numpy as np

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState
from deploymint.core import kube_engine
from deploymint.core.runner import run_command
from deploymint.config import get_settings

SAMPLES = 12
INTERVAL = 5
FATAL_REASONS = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
                 "CreateContainerConfigError", "OOMKilled"}


class ObservabilityOracleAgent(BaseAgent):
    name = "oracle"

    async def run(self, state: DeployState) -> dict:
        dep = dict(state.get("deployment") or {})
        name = state["project_name"]
        if dep.get("status") != "running":
            return {}

        samples, anomaly_reason = [], None

        for i in range(SAMPLES):
            snap = await self._sample(name)
            samples.append(snap)
            await self.emit("oracle.metric", **snap)

            if snap["reason"] in FATAL_REASONS:
                anomaly_reason = f"pod entered {snap['reason']}"
                break
            if snap["restarts"] > 2:
                anomaly_reason = f"pod restarted {snap['restarts']} times"
                break
            if i >= 6 and snap["ready"] == 0:
                anomaly_reason = "no ready replicas after 35s"
                break

            await asyncio.sleep(INTERVAL)

        if anomaly_reason is None and len(samples) >= 8:
            anomaly_reason = self._isolation_forest(samples)

        dep["metrics"] = samples
        if anomaly_reason:
            await self.emit("oracle.anomaly", reason=anomaly_reason, score=1.0)
            from deploymint.agents.remediator import RemediatorAgent
            rem = await RemediatorAgent(self.bus).run({**state, "deployment": dep,
                                                      "anomaly_reason": anomaly_reason})
            dep.update(rem.get("deployment", {}))
            return {"deployment": dep,
                    "errors": state.get("errors", []) + [f"oracle: {anomaly_reason}"]}

        await self.emit("oracle.done", healthy=True, samples=len(samples))
        return {"deployment": dep}

    async def _sample(self, app: str) -> dict:
        r = await run_command(
            ["kubectl", "--context", get_settings().kube_context,
             "get", "pods", "-l", f"app={app}", "-o", "json"], timeout=15)
        cpu = mem = 0.0
        restarts = ready = 0
        reason = ""
        try:
            items = json.loads(r.stdout or "{}").get("items", [])
            if items:
                st = items[0].get("status", {})
                cs = (st.get("containerStatuses") or [{}])[0]
                restarts = cs.get("restartCount", 0)
                ready = 1 if cs.get("ready") else 0
                waiting = (cs.get("state", {}).get("waiting") or {})
                terminated = (cs.get("lastState", {}).get("terminated") or {})
                reason = waiting.get("reason") or terminated.get("reason") or ""
        except (json.JSONDecodeError, IndexError, KeyError):
            pass

        top = await run_command(
            ["kubectl", "--context", get_settings().kube_context,
             "top", "pod", "-l", f"app={app}", "--no-headers"], timeout=10)
        if top.ok and top.stdout.strip():
            parts = top.stdout.split()
            if len(parts) >= 3:
                cpu = float(parts[1].rstrip("m") or 0)
                mem = float(parts[2].rstrip("Mi") or 0)

        return {"cpu": cpu, "memory": mem, "restarts": restarts,
                "ready": ready, "reason": reason}

    def _isolation_forest(self, samples: list[dict]) -> str | None:
        from sklearn.ensemble import IsolationForest
        X = np.array([[s["cpu"], s["memory"], s["restarts"]] for s in samples])
        if X.std(axis=0).sum() == 0:      # perfectly flat — nothing to detect
            return None
        labels = IsolationForest(contamination=0.15, random_state=42).fit_predict(X)
        n_anom = int((labels == -1).sum())
        if n_anom >= 3:
            return f"IsolationForest flagged {n_anom}/{len(samples)} metric samples as anomalous"
        return None
```

### `kubectl top` requires metrics-server, which kind does not ship

Two options:

**Option A — install it** (adds ~30s to cluster setup, gives you real CPU/memory):

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

```bash
kubectl patch deployment metrics-server -n kube-system --type=json -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

**Option B — degrade gracefully.** `_sample()` already returns `cpu=0, mem=0` when `top`
fails. The deterministic restart/ready checks still work, and those are what actually
catch failures.

**Recommendation: do Option A in `scripts/reset.sh`.** Real numbers in the metrics chart
make the dashboard look substantially more credible, for one command. But make sure
Option B works too, so a user without metrics-server gets a working tool.

### The honest framing for IsolationForest

Say this in your writeup: *"IsolationForest runs over the deployment's metric window and
demonstrates the anomaly-detection hook. With 12 samples it is illustrative, not
statistically meaningful — the deterministic restart/crashloop/OOM checks are what
actually protect a deployment today. The ML path is where a real metrics backend plugs in."*

Reviewers respect calibrated claims far more than inflated ones, and this costs you
nothing.

---

## Step 6.2 — Remediator

```python
# deploymint/agents/remediator.py
class RemediatorAgent(BaseAgent):
    name = "remediator"

    async def run(self, state: DeployState) -> dict:
        name = state["project_name"]
        dep = dict(state.get("deployment") or {})
        reason = state.get("anomaly_reason", "anomaly detected")

        await self.emit("remediator.start", reason=reason)

        r = await kube_engine.rollout_undo(name)
        if r.ok:
            await kube_engine.rollout_status(name)
            dep["status"] = "rolled_back"
            dep["remediation"] = f"rolled back to previous revision ({reason})"
        else:
            # first-ever deploy has no previous revision — remove it instead
            d = await kube_engine.delete_deployment(name)
            dep["status"] = "rolled_back"
            dep["remediation"] = (
                f"no previous revision; deployment removed ({reason}). "
                f"{'' if d.ok else 'Manual cleanup may be required.'}"
            )

        await self.emit("remediator.done", status=dep["status"],
                        detail=dep["remediation"])
        return {"deployment": dep}
```

The no-previous-revision path is the **common** case in a demo (first deploy of a fresh
project). If you only handle `rollout undo`, your remediation will fail exactly when you
show it off.

---

## Step 6.3 — FinOps Agent

```python
# deploymint/agents/finops.py
import json, yaml
from importlib.resources import files

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState

HOURS_PER_MONTH = 730


def load_rate_card(cloud: str = "aws") -> dict:
    data = json.loads((files("deploymint") / "data/rate_card.json").read_text())
    return data.get(cloud, data["aws"])


def parse_quantity(q: str | None, kind: str) -> float:
    """K8s quantities: '500m' cpu -> 0.5 vCPU; '512Mi' memory -> 0.5 GB."""
    if not q:
        return 0.0
    q = str(q)
    if kind == "cpu":
        return float(q[:-1]) / 1000 if q.endswith("m") else float(q)
    for suffix, mult in (("Gi", 1.0), ("Mi", 1 / 1024), ("Ki", 1 / 1024**2),
                         ("G", 1e9 / 1024**3), ("M", 1e6 / 1024**3)):
        if q.endswith(suffix):
            return float(q[: -len(suffix)]) * mult
    return float(q) / 1024**3


class FinOpsAgent(BaseAgent):
    name = "finops"

    async def run(self, state: DeployState) -> dict:
        artifacts = state.get("artifacts") or {}
        report = self._estimate(artifacts, state["project_name"])
        await self.emit("finops.done", **report)
        return {"cost": report}

    def _estimate(self, artifacts: dict, name: str) -> dict:
        rates = load_rate_card("aws")
        try:
            dep = yaml.safe_load(artifacts.get("k8s_deployment", "")) or {}
            replicas = dep.get("spec", {}).get("replicas", 1)
            containers = dep["spec"]["template"]["spec"]["containers"]
        except Exception:
            return {"source": "estimate", "monthly_usd": 0.0, "breakdown": {},
                    "recommendations": ["Could not parse the Deployment manifest."]}

        total, breakdown, recs = 0.0, {}, []
        for c in containers:
            res = c.get("resources", {}) or {}
            req, lim = res.get("requests", {}) or {}, res.get("limits", {}) or {}
            cpu = parse_quantity(req.get("cpu"), "cpu")
            mem = parse_quantity(req.get("memory"), "mem")
            cost = (cpu * rates["vcpu_hour"] + mem * rates["gb_hour"]) * HOURS_PER_MONTH * replicas
            breakdown[c.get("name", "container")] = round(cost, 2)
            total += cost

            lim_cpu = parse_quantity(lim.get("cpu"), "cpu")
            if cpu and lim_cpu and lim_cpu / cpu > 4:
                recs.append(f"'{c['name']}': CPU limit is {lim_cpu/cpu:.0f}× the request — "
                            "likely over-provisioned or under-requested.")
            if not lim:
                recs.append(f"'{c['name']}': no resource limits — cost is unbounded.")
            if mem > 1.0:
                recs.append(f"'{c['name']}': {mem:.1f} GB memory request. "
                            f"Halving it saves ~${mem/2 * rates['gb_hour'] * HOURS_PER_MONTH:.2f}/mo.")

        if replicas == 1:
            recs.append(f"Single replica — no high availability. "
                        f"A second replica costs ~${total:.2f}/mo more.")
        recs.append("No HorizontalPodAutoscaler configured — "
                    "scaling to zero off-peak could cut this substantially.")

        return {"source": "estimate", "monthly_usd": round(total, 2),
                "breakdown": breakdown, "recommendations": recs[:5]}
```

### Cost Q&A

```python
# deploymint/api/costs.py
COST_INTENTS = {
    "most_expensive": ["most expensive", "costs the most", "biggest cost", "highest"],
    "total_spend":    ["total", "how much", "overall", "altogether"],
    "by_service":     ["breakdown", "by service", "per service", "each"],
    "optimize":       ["save", "reduce", "optimize", "cheaper", "cut"],
}


@router.post("/query")
async def query_costs(body: dict, db: Session = Depends(get_db)):
    question = (body.get("question") or "").strip()
    data = load_cost_data(db)          # sample JSON, AWS CE, or local estimates
    intent = classify_cost_intent(question)

    if intent == "most_expensive":
        svc, amount = max(data["by_service"].items(), key=lambda kv: kv[1])
        share = amount / sum(data["by_service"].values()) * 100
        facts = {"service": svc, "amount": amount, "share_pct": round(share, 1),
                 "period": data["period"]}
        answer = (f"**{svc}** is your largest cost at **${amount:,.2f}** "
                  f"for {data['period']} — {share:.0f}% of total spend.")
    ...
    # Optional: pass `facts` to the LLM ONLY to phrase the sentence.
    # The numbers always come from `data`, never from the model.
    return {"answer": answer, "intent": intent, "data": facts}
```

**Rule: the LLM never computes a number.** It classifies intent and phrases a sentence
using facts you computed. A cost tool that hallucinates a dollar figure is worse than no
cost tool. Ship the deterministic string; make the LLM phrasing an optional enhancement.

### Sample cost data

`deploymint/data/sample_cost_export.json` — real AWS Cost Explorer response shape:

```json
{
  "ResultsByTime": [{
    "TimePeriod": {"Start": "2026-07-01", "End": "2026-08-01"},
    "Total": {},
    "Groups": [
      {"Keys": ["Amazon Elastic Kubernetes Service"],
       "Metrics": {"UnblendedCost": {"Amount": "218.40", "Unit": "USD"}}},
      {"Keys": ["Amazon Elastic Compute Cloud - Compute"],
       "Metrics": {"UnblendedCost": {"Amount": "487.12", "Unit": "USD"}}},
      {"Keys": ["Amazon Relational Database Service"],
       "Metrics": {"UnblendedCost": {"Amount": "312.55", "Unit": "USD"}}},
      {"Keys": ["Amazon Simple Storage Service"],
       "Metrics": {"UnblendedCost": {"Amount": "43.09", "Unit": "USD"}}},
      {"Keys": ["AWS Lambda"],
       "Metrics": {"UnblendedCost": {"Amount": "12.88", "Unit": "USD"}}},
      {"Keys": ["Amazon CloudWatch"],
       "Metrics": {"UnblendedCost": {"Amount": "67.31", "Unit": "USD"}}}
    ]
  }],
  "GroupDefinitions": [{"Type": "DIMENSION", "Key": "SERVICE"}]
}
```

Using the real API shape means switching to a live AWS connection is a source swap, not
a rewrite. That is the stretch goal from the proposal, and this makes it a one-hour job.

---

## Step 6.4 — Web UI

Server-rendered Jinja2 + HTMX + a vendored WebSocket client. No npm, no build step,
ships inside the wheel.

```python
# deploymint/server.py additions
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

WEB = Path(__file__).parent / "web"
templates = Jinja2Templates(directory=str(WEB / "templates"))
app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    return templates.TemplateResponse("index.html",
                                      {"request": request, "projects": projects})
```

### Four pages

| Page | Shows |
|---|---|
| `/` | project cards (language badge, framework, last run status) + register form |
| `/projects/{id}` | dependency graph (Cytoscape), analysis summary, run history table |
| `/runs/{id}` | **the money page** — live agent timeline, artifacts with syntax highlighting, security findings, terminal stream, cost panel |
| `/costs` | spend breakdown chart + the NL query box |

### The run page layout

```
┌──────────────────────────────────────────────────────────────────┐
│ run_a3f8c21b9de0 · sample-api          ● running   00:42         │
├──────────────────────────────────────────────────────────────────┤
│ AGENT TIMELINE                                                   │
│ ✓ Architect      1.2s   python / fastapi · 6 files · 5 edges     │
│ ✓ Artifact Smith 18.4s  llama3.1:8b → 4 files                    │
│ ✓ Security Warden 3.1s  0 critical · 2 medium · PASSED           │
│ ✓ Red Team       9.8s   11 probes · 0 findings                   │
│ ◐ Execution      ...    building image                           │
│ ○ Oracle                                                         │
│ ○ FinOps                                                         │
├────────────────────────────┬─────────────────────────────────────┤
│ ARTIFACTS                  │ TERMINAL                            │
│ ▸ Dockerfile               │ $ docker build -t deploymint/...    │
│ ▸ .dockerignore            │ Step 1/9 : FROM python:3.11-slim    │
│ ▸ k8s-deployment.yaml      │ Step 2/9 : WORKDIR /build           │
│ ▸ k8s-service.yaml         │ ...                                 │
├────────────────────────────┴─────────────────────────────────────┤
│ SECURITY  2 findings                                             │
│ ⚠ medium  CKV_K8S_43  Image should use a digest                  │
│ ⚠ medium  CKV_DOCKER_2 Missing HEALTHCHECK in one stage          │
├──────────────────────────────────────────────────────────────────┤
│ COST  $18.42/month estimated · 3 recommendations                 │
└──────────────────────────────────────────────────────────────────┘
```

### The WebSocket client

```javascript
// deploymint/web/static/app.js
function connectRun(runId, since = 0) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/runs/${runId}`);

  ws.onopen = () => ws.send(JSON.stringify({ since }));

  const term = document.getElementById("terminal");
  let buffer = [];
  setInterval(() => {                     // batch DOM writes at 10 fps
    if (!buffer.length) return;
    term.insertAdjacentText("beforeend", buffer.join("\n") + "\n");
    term.scrollTop = term.scrollHeight;
    buffer = [];
  }, 100);

  ws.onmessage = (e) => {
    const evt = JSON.parse(e.data);
    since = evt.seq;
    switch (evt.type) {
      case "node.enter":      setNodeState(evt.payload.node, "running"); break;
      case "node.exit":       setNodeState(evt.payload.node, "done", evt.payload.ms); break;
      case "execution.log":   buffer.push(evt.payload.line); break;
      case "warden.finding":  addFinding(evt.payload); break;
      case "finops.done":     renderCost(evt.payload); break;
      case "run.end":         finish(evt.payload.status); ws.close(); break;
    }
  };

  ws.onclose = () => {
    if (!isTerminal()) setTimeout(() => connectRun(runId, since), 2000);  // reconnect
  };
}
```

Note the reconnect with `since` — this is why events are persisted. A dropped connection
resumes without losing a single line.

### Vendor your JS

Download `htmx.min.js` and `cytoscape.min.js` into `web/static/vendor/`. **Do not use a
CDN.** DeployMint's entire pitch is local-first and offline-capable; a dashboard that
breaks without internet contradicts the product. It also means the wheel is genuinely
self-contained.

### Keep the CSS simple

A dark terminal aesthetic, system font stack, one accent color (mint green — use the
name). ~200 lines of hand-written CSS. Do not pull in Tailwind; it needs a build step,
which reintroduces npm.

---

## Step 6.5 — Phase 6 acceptance test

```bash
open http://localhost:8000
```

Register `./tests/fixtures/sample_fastapi` in the UI, click **Deploy**, and watch.

```bash
curl -s -X POST localhost:8000/api/costs/query -H 'content-type: application/json' -d '{"question":"which service costs the most?"}'
```

Then force a rollback — deploy a fixture whose app exits immediately:

```bash
deploymint up ./tests/fixtures/crashloop_app --name crashy
```

**Pass criteria:**

- The run page updates live with no manual refresh
- The terminal panel shows docker build output as it happens
- The dependency graph renders and is interactive
- Cost query returns "Amazon Elastic Compute Cloud - Compute" at $487.12 (from the sample)
- The number in the answer matches the number in the breakdown table exactly
- A crash-looping app triggers `oracle.anomaly` → rollback, visible in the timeline
- Refreshing mid-run replays the timeline with nothing lost
- The UI works with the network disconnected (vendored assets)

Tick **Phase 6**. Next: `11-phase-7-polish-demo.md`.

---

## Time budget

| Task | Hours |
|---|---|
| Oracle + metrics-server setup | 3.0 |
| Remediator + both rollback paths | 1.5 |
| FinOps estimate + rate card + recommendations | 2.5 |
| Cost Q&A + sample data | 2.0 |
| Jinja2 templates (4 pages) | 4.0 |
| CSS | 2.0 |
| WebSocket client + graph rendering | 3.0 |
| crashloop fixture + testing | 1.5 |
| **Total** | **~19.5 h (2 days)** |

**If you fall behind:** build only the run page (`/runs/{id}`). It is the one that
demos. The project list can stay CLI-only, and the cost view can be a panel on the run
page rather than its own route.
