FROM python:3.11-slim AS base

# System deps baked in ONCE — see docs/00-prerequisites.md §0.3 for why each is here
RUN apt-get update && apt-get install -y --no-install-recommends \
        git tmux curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# kubectl — pinned version, not "latest"
ARG KUBECTL_VERSION=v1.31.0
RUN curl -Lo /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod +x /usr/local/bin/kubectl

# opa — pinned version
ARG OPA_VERSION=v1.1.0
RUN curl -Lo /usr/local/bin/opa \
        "https://openpolicyagent.org/downloads/${OPA_VERSION}/opa_linux_amd64_static" \
    && chmod +x /usr/local/bin/opa

# docker CLI only (client) — talks to the mounted host socket, no daemon needed here
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz \
        | tar xz -C /usr/local/bin --strip-components=1 docker/docker

WORKDIR /app

# checkov's stale `networkx<2.7` pin is unresolvable together with the app's own
# networkx>=3.3 requirement in one pip resolve — install it FIRST, alone, then
# restore networkx afterward. See docs/00-prerequisites.md §0.6 and
# requirements.txt for the full explanation. Checkov itself only ever runs as a
# subprocess (never imported), so the stale pin has no runtime effect.
RUN pip install --no-cache-dir "checkov>=3.2.0"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade "networkx>=3.3"

COPY deploymint/ ./deploymint/
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

EXPOSE 8000
CMD ["uvicorn", "deploymint.server:app", "--host", "0.0.0.0", "--port", "8000"]
