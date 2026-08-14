"""Serves /health successfully so the initial Kubernetes rollout succeeds,
then deliberately crashes ~12s after each start — simulating a runtime bug
that only shows up after a healthy-looking deploy. This is what the
Observability Oracle (docs/10-phase-6-finops-ui.md §6.1) is designed to
catch, as opposed to a Dockerfile/entrypoint bug that fails the rollout
itself before the Oracle ever runs."""

import os
import threading
import time

from flask import Flask

app = Flask(__name__)


@app.get("/health")
def health():
    return {"status": "ok"}


def _timebomb():
    time.sleep(12)
    os._exit(1)


threading.Thread(target=_timebomb, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
