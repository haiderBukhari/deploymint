function setNodeState(node, state) {
  const li = document.querySelector(`#timeline li[data-node="${node}"]`);
  if (!li) return;
  li.classList.remove("running", "done");
  li.classList.add(state);
  li.querySelector(".icon").textContent = state === "done" ? "✓" : "◐";
}

function addFinding(f) {
  const list = document.getElementById("findings");
  if (!list) return;
  const li = document.createElement("li");
  li.className = `sev-${f.severity}`;
  li.textContent = `${f.severity} · ${f.id} · ${f.message}`;
  list.appendChild(li);
}

function setStatus(status) {
  const badge = document.getElementById("run-status");
  if (!badge) return;
  badge.textContent = status;
  badge.className = `status status-${status}`;
}

function connectRun(runId, since) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/runs/${runId}`);

  ws.onopen = () => ws.send(JSON.stringify({ since }));

  const term = document.getElementById("terminal");
  let buffer = [];
  const flush = setInterval(() => {
    if (!term || !buffer.length) return;
    term.insertAdjacentText("beforeend", buffer.join("\n") + "\n");
    term.scrollTop = term.scrollHeight;
    buffer = [];
  }, 100);

  function isTerminal(s) {
    return ["success", "failed", "blocked", "cancelled"].includes(s);
  }

  ws.onmessage = (e) => {
    const evt = JSON.parse(e.data);
    since = evt.seq;
    const payload = evt.payload || {};
    switch (evt.type) {
      case "node.enter":
        setNodeState(payload.node, "running");
        break;
      case "node.exit":
        setNodeState(payload.node, "done");
        break;
      case "execution.log":
        buffer.push(payload.line);
        break;
      case "warden.finding":
      case "redteam.probe":
        if (payload.id) addFinding(payload);
        break;
      case "run.end":
        setStatus(payload.status);
        clearInterval(flush);
        ws.close();
        // The Artifacts/Security/Cost panels are server-rendered from the DB
        // at page load and are not patched incrementally over the socket —
        // reload once so they pick up the run's final artifacts/security/cost,
        // instead of staying stuck on whatever was true when the page loaded.
        setTimeout(() => location.reload(), 400);
        break;
    }
  };

  ws.onclose = () => {
    // Reconnect unless the page already knows the run finished.
    const badge = document.getElementById("run-status");
    if (badge && !isTerminal(badge.textContent.trim())) {
      setTimeout(() => connectRun(runId, since), 2000);
    }
  };
}

function renderDepGraph() {
  const el = document.getElementById("depgraph");
  if (!el || typeof cytoscape === "undefined") return;
  let graph;
  try {
    graph = JSON.parse(el.dataset.graph || "{}");
  } catch {
    return;
  }
  const nodes = (graph.nodes || []).map((n) => ({ data: { id: n.id } }));
  const edges = (graph.links || []).map((l, i) => ({
    data: { id: `e${i}`, source: l.source, target: l.target },
  }));
  cytoscape({
    container: el,
    elements: [...nodes, ...edges],
    style: [
      { selector: "node", style: { label: "data(id)", "font-size": 10,
        "background-color": "#2ee6a6", color: "#e6edf3" } },
      { selector: "edge", style: { "line-color": "#2b3138",
        "target-arrow-color": "#2b3138", "target-arrow-shape": "triangle",
        "curve-style": "bezier" } },
    ],
    layout: { name: "cose" },
  });
}

function wireCostForm() {
  const form = document.getElementById("cost-form");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const question = document.getElementById("cost-question").value;
    const r = await fetch("/api/costs/query", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const body = await r.json();
    document.getElementById("cost-answer").innerHTML = body.answer.replace(
      /\*\*(.+?)\*\*/g, "<strong>$1</strong>"
    );
  });
}
