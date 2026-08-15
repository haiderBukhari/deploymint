# Contributing

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
```

Checkov's stale `networkx<2.7` pin conflicts with this package's own
`networkx>=3.3` in a single resolve. If `pip install` fails with
`ResolutionImpossible`, install in this order instead:

```bash
pip install "checkov>=3.2.0"
pip install -e ".[dev]"
pip install --upgrade "networkx>=3.3"
```

See [docs/00-prerequisites.md](docs/00-prerequisites.md) §0.6 for why this is safe
(Checkov only ever runs as a subprocess, never imported).

You'll also need `opa`, `kubectl`, and a local dev cluster (`kind` works well) to run
the full test suite — see `docs/00-prerequisites.md` and `docs/12-testing-strategy.md`.

## Running tests

```bash
pytest -m "not slow"     # fast suite, no real infra required
pytest                    # full suite, needs docker + a reachable cluster
```

## Before opening a PR

```bash
ruff check deploymint tests
pytest -m "not slow"
```

## Where things live

`docs/` is the project's build log, kept as a permanent record — including the
architecture decisions and the real bugs found along the way
(`docs/16-decisions-log.md`). Read `docs/01-architecture.md` before touching anything
in `deploymint/agents/state.py` — that schema is frozen for a reason explained there.

## License

By contributing, you agree your contributions are licensed under the Apache License,
Version 2.0 (see [LICENSE](LICENSE)).
