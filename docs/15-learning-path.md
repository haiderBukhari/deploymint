# 15 — Learning Path

You asked what you need to know before starting. Here it is, ordered by when you need it,
with time budgets and — importantly — **what you can skip**.

The rule: learn *just enough to unblock the next phase*, then build. Do not study
LangGraph on Day 1; you don't touch it until Day 10.

---

## Tier 1 — Learn before writing any code (~4 hours)

### 1. Python `asyncio` — the parts you actually need (90 min)

This is the highest-leverage thing on the list. FastAPI, LangGraph, and your event bus
are all async. Not understanding it produces bugs that look like crashes.

You need exactly five concepts:

| Concept | Why it matters here |
|---|---|
| `async def` / `await` | every agent method |
| `asyncio.create_task()` | fire-and-forget a run so the HTTP request returns immediately |
| `asyncio.Queue` | the event bus |
| `asyncio.to_thread()` | **the critical one** — wraps blocking calls (Docker SDK, subprocess) so they don't freeze the server |
| `asyncio.gather()` | run the stdout and stderr pumps concurrently |

**The one mistake that will bite you:** calling a blocking function directly in an async
handler. `docker_client.build(...)` blocks the entire event loop — every request hangs,
the WebSocket stops, and it looks like the server crashed. It didn't; it's just busy.
Wrap it: `await asyncio.to_thread(docker_client.build, ...)`.

Prove you understand it with this exercise:

```python
import asyncio, time

def blocking(n):          # simulates docker build
    time.sleep(n)
    return f"done in {n}s"

async def main():
    t0 = time.perf_counter()
    results = await asyncio.gather(
        asyncio.to_thread(blocking, 2),
        asyncio.to_thread(blocking, 2),
        asyncio.to_thread(blocking, 2),
    )
    print(results, f"{time.perf_counter()-t0:.1f}s")

asyncio.run(main())
```

If it prints ~2s, you get it. If you predicted 6s, re-read `to_thread`.

**Skip:** event loop internals, custom executors, `asyncio.Protocol`, async context
manager authoring.

### 2. FastAPI — routing, dependencies, WebSockets (60 min)

| Need | Don't need |
|---|---|
| `APIRouter`, path/query/body params | background task queues (Celery et al.) |
| `Depends()` for the DB session | OAuth, JWT, auth middleware |
| Pydantic request/response models | custom middleware |
| `@app.websocket` | GraphQL, SSE |
| `lifespan` for startup/shutdown | response caching |

Build one toy app: a router, a Pydantic body model, a `Depends` dependency, and a
WebSocket that echoes. That's the entire surface you use.

**The `/docs` page is free.** FastAPI generates interactive OpenAPI docs from your type
hints. Use it as your API testing tool instead of writing curl commands.

### 3. Pydantic v2 (45 min)

| Need | Don't need |
|---|---|
| `BaseModel`, field types, defaults | custom serializers |
| `Field(min_length=…, ge=…)` | generic models |
| `@field_validator` — this is your artifact validation | `RootModel`, discriminated unions |
| `ValidationError` handling | `model_config` beyond `from_attributes` |
| `pydantic-settings` for config | |

**v1 vs v2 matters.** Most tutorials online are v1. The differences that will confuse you:
`@validator` → `@field_validator` (and it needs `@classmethod`), `.dict()` → `.model_dump()`,
`Config` class → `model_config` dict. If a snippet uses `@validator`, it's v1.

### 4. SQLAlchemy 2.0 ORM (45 min)

| Need | Don't need |
|---|---|
| `DeclarativeBase`, `Mapped[]`, `mapped_column()` | Alembic (you're using `create_all`) |
| `sessionmaker`, session lifecycle | async SQLAlchemy |
| `relationship()` with `back_populates` | complex joins, subqueries, CTEs |
| `db.query(X).filter_by().first()` | connection pool tuning |
| `JSON` column type | |

**2.0 style vs legacy matters as much as Pydantic v1/v2.** If a tutorial uses
`Column(Integer, primary_key=True)` instead of `Mapped[int] = mapped_column(...)`, it's
pre-2.0 style. Both work, but mixing them in one file is confusing. Pick 2.0 and stay
consistent — the models in `03-data-model.md` are all 2.0 style.

---

## Tier 2 — Learn during Phase 1–2 (~5 hours)

### 5. tree-sitter (2 hours — the hardest thing in Tier 2)

This is where beginners lose a day. Two things make it manageable:

**a) Use `tree-sitter-language-pack`.** Prebuilt grammars for ~100 languages, one API.
Do not follow tutorials that have you clone grammar repos and compile `.so` files — that
path is real and it will consume your afternoon.

```python
from tree_sitter_language_pack import get_parser
parser = get_parser("python")
tree = parser.parse(b"import os\n")
```

**b) You only need three things:** parse to a tree, walk the nodes, read `node.type` and
`src[node.start_byte:node.end_byte]`. You are extracting import statements, not building a
compiler.

**Skip entirely:** the query language (`.scm` files), incremental parsing, tree editing,
custom grammars. A recursive `walk()` checking `node.type` is 15 lines and enough.

**Time-box this to 2 hours.** If import extraction isn't working by then, ship a regex
version (`^\s*(?:from\s+(\S+)|import\s+(\S+))`) and revisit later. The graph is a feature,
not the product. Do not let it eat Day 2.

### 6. Docker Compose + Docker SDK (90 min — required now, not optional)

This project's distribution mechanism *is* Docker Compose (`01-architecture.md` §1.1),
so this isn't a nice-to-have the way it might be elsewhere.

**Compose basics (45 min):** a `services:` block per container, `volumes:` for bind
mounts and named volumes, `depends_on: condition: service_healthy`, and `environment:`
for injecting config. Write one two-service `docker-compose.yml` by hand (an app + a
database) before copying `02-repo-layout.md` §2.4 — the act of writing one from scratch
is what makes `depends_on`'s healthcheck gotcha stick.

**The one concept that matters most: Docker-outside-of-Docker (30 min).** Mounting
`/var/run/docker.sock` into a container lets code *inside* that container talk to the
Docker daemon *outside* it — the SDK and CLI both default to that socket path, so no
code change is needed, only an understanding of what you just granted access to (root-
equivalent host control — `01-architecture.md` §1.7). This is standard practice in CI
runners (Jenkins, GitLab Runner); it is not exotic, but it is unfamiliar if you haven't
built a Docker-building tool before.

```python
import docker
client = docker.from_env()   # unix:///var/run/docker.sock — the HOST's daemon,
client.ping()                # once that socket is mounted in
```

You need: `client.api.build(...)` with `decode=True` for streaming logs, and image
tagging. That's it on the SDK side.

**Skip:** container networks beyond what Compose gives you for free, swarm, multi-stage
Compose profiles.

**Escape hatch:** if the SDK's streaming API fights you, `subprocess` to the `docker` CLI
via your `run_command()` helper — same mounted socket either way. You lose nothing
meaningful and gain 40 lines of your life back. This is a legitimate choice, not a
compromise.

### 7. Prompt engineering for structured output (45 min)

With a strong hosted model, this is a smaller lift than it would be with a small local
one, but the discipline is the same:

1. **State the exact JSON schema in the system prompt**, with 2 concrete few-shot
   examples. Examples anchor output shape more reliably than prose instructions alone.
2. **Say what NOT to do explicitly.** "Return ONLY the JSON object. No markdown fences,
   no prose before or after." Even a strong model will occasionally wrap its answer in a
   sentence unless told not to.
3. **Validate, don't just trust.** A Pydantic validator + one repair attempt + template
   fallback (`04-agents-spec.md` §4.2) catches the rare cases prompting alone can't —
   expect this to fire far less often than it would with a small local model, but design
   for it regardless, because "far less often" is not "never."

### 8. Rich for terminal UI (30 min)

`Console`, `Table`, `Panel`, `Live`, `Progress`. `Live` is the one that matters — it
redraws a region in place, which is how you get the CLI's live agent timeline.

---

## Tier 3 — Learn during Phase 3–5 (~5 hours)

### 9. OPA / Rego (2.5 hours — genuinely unfamiliar syntax)

Rego is a declarative query language and it does not think like Python. Budget real time.

Core mental model: **a rule produces a set of values, and unbound variables are
implicitly universally quantified.**

```rego
package example

# "deny contains msg if" = add msg to the deny set when the body holds
deny contains msg if {
    some line in input.lines           # iterate
    startswith(lower(line), "user root")
    msg := "container runs as root"    # := is assignment
}
```

Key gotchas:

- **`:=` is assignment, `=` is unification.** Use `:=` unless you know why you want `=`.
- **Rego v1 (OPA 1.0+, Jan 2025) requires `if` and `contains`.** Older tutorials use
  `deny[msg] { ... }`. Check `opa version` and pick one dialect for all three policies.
- **There are no loops.** `some x in collection` iterates by generating bindings.
- **`opa eval` output nesting is deep.** Print the raw JSON once by hand before writing
  your parser: `result[0].expressions[0].value.<package>.deny`.

Practice: write one rule, test it with `opa eval` in **both** directions (input that
should trigger, and input that shouldn't). A rule that never fires and one that always
fires look identical until you check both.

**Skip:** partial evaluation, the bundle API, OPA as a server, the Gatekeeper integration.
You use `opa eval` as a CLI, nothing more.

### 10. LangGraph (2 hours — but only when you reach Phase 5)

**Do not learn this early.** You don't touch it until Day 10, and by then your agents
already work — which makes LangGraph trivial instead of confusing.

Five concepts:

| Concept | What it is here |
|---|---|
| `StateGraph(StateSchema)` | the graph, typed by `DeployState` |
| node | `async def(state) -> partial_dict` |
| edge | `g.add_edge("a", "b")` |
| conditional edge | `g.add_conditional_edges("warden", fn, {"execute": "execution", "blocked": "blocked"})` |
| `.astream(state)` | async iteration over state after each node — your streaming source |

**The thing that will confuse you:** nodes return a **partial** dict that gets merged into
state. Returning `{"errors": [...]}` **replaces** `errors` — it does not append. That's
why every agent in this plan writes `state.get("errors", []) + [new]` explicitly.

**Skip:** checkpointers/persistence (you persist to Postgres yourself), human-in-the-loop
interrupts, subgraphs, `Send` API, LangSmith tracing, cyclic graphs. Your graph is a chain
with one branch.

### 11. WebSockets (30 min)

Server: `@app.websocket`, `await ws.accept()`, `send_json`, `receive_json`, catch
`WebSocketDisconnect`.

Client: `new WebSocket(url)`, `onopen`/`onmessage`/`onclose`, and reconnect-with-resume.

The only non-obvious part is the resume protocol: client sends `{"since": lastSeq}` on
connect, server replays persisted events past that seq, then tails live. That's the whole
design.

---

## Tier 4 — Learn only if you get there (~3 hours)

| Topic | When | Time |
|---|---|---|
| NetworkX PageRank | Phase 1, if you want criticality ranking | 30 min — it's `nx.pagerank(g)`, one line. Do NOT reverse the graph first — see `04-agents-spec.md` §4.1 for why that's backwards for an importer→imported edge convention |
| scikit-learn IsolationForest | Phase 6 | 30 min — `fit_predict(X)`, `-1` means anomaly |
| boto3 Cost Explorer | stretch goal | 1 h |
| Cytoscape.js | Phase 6 graph viz | 45 min — or skip and render an adjacency list |
| HTMX | Phase 6 | 30 min — `hx-get`, `hx-post`, `hx-target`, `hx-swap`. That's genuinely all of it. |
| The Anthropic Python SDK's error types | Phase 2 | 20 min — `AuthenticationError`, `RateLimitError`, `APIConnectionError`; you'll branch on these in `core/llm.py` |

---

## The single most important thing

If you learn one thing deeply, make it **`asyncio.to_thread` and why blocking calls
freeze an async server.**

That one concept prevents the worst class of bug in this project: a server that appears
to hang, with no traceback, no error, and no obvious cause — because a synchronous
`docker build` is holding the event loop hostage while your WebSocket clients time out
and your HTTP requests queue up behind it.

Every other mistake in this project produces an error message. That one produces silence.

---

## What NOT to learn (yet)

| Topic | Why not |
|---|---|
| Kubernetes operators / CRDs | you use `kubectl apply`. That's it. |
| Helm | not in scope; templates are your own |
| Terraform / HCL | post-MVP |
| Ansible | post-MVP |
| React / Vue / npm | Jinja2 + HTMX, no build step |
| Alembic migrations | `create_all()` until v1.0 |
| Fine-tuning / LoRA | not this quarter |
| Vector databases | 20 few-shot examples in a JSONL file beat a vector DB at this scale |
| Docker Swarm / Kubernetes operators | plain Compose for distribution, plain `kubectl apply` for the user's deploy target |
| gRPC / message queues | one process, one Postgres database |

Every hour spent here is an hour not spent on the deploy loop, which is where the real
difficulty lives.

---

## Suggested pre-start schedule

If you have a day before Day 1:

| Block | Topic | Deliverable |
|---|---|---|
| Morning, 2 h | asyncio (the 5 concepts + the `to_thread` exercise) | you can predict the output of the gather exercise |
| Morning, 1 h | FastAPI toy app | router + Depends + WebSocket echo, running |
| Afternoon, 1 h | Pydantic v2 + `pydantic-settings` | a model with a `@field_validator` that rejects bad input |
| Afternoon, 1 h | SQLAlchemy 2.0 against Postgres | two related models, create, query, JSONB column |
| Afternoon, 1 h | Docker Compose + the socket-mount concept | a two-service compose file, and you can explain in one sentence why mounting `/var/run/docker.sock` is root-equivalent access |
| Evening, 2 h | tree-sitter | prints the import statements from a real Python file |

That's 8 hours and covers everything you need through Phase 2. Learn Rego on Day 5 and
LangGraph on Day 10 — right before you need them, when the context makes them click.
