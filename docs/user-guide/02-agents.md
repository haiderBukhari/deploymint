# The agent pipeline

Every deploy runs through seven specialized agents, in order. Each one does a
distinct, real piece of work — none of it is "one LLM call pretending to be
seven agents."

## Architect

Scans your repo with `tree-sitter` (a real parser, not a text search),
detects the language/framework/package manager, and builds a graph of which
files import which others. It ranks files by PageRank over that graph, so
you can see which files the rest of your codebase depends on most — the ones
worth reviewing carefully before you trust generated infrastructure around
them.

## Artifact Smith

Generates the actual deployment files: a Dockerfile and Kubernetes manifests,
written by an LLM with a few verified example pairs for context, tailored to
what Architect found (your entrypoint, exposed port, dependencies). If no LLM
key is configured, or the LLM is unreachable, it falls back to a deterministic
template — you always get a working, secure-by-default Dockerfile either way.
The six other IaC formats (Terraform, Ansible, ArgoCD, GitHub Actions,
Prometheus, Grafana) are always generated deterministically — see
[Generated Artifacts](/guide/iac-formats).

## Security Warden

Runs Checkov (550+ built-in security rules covering Dockerfiles, Kubernetes,
and Terraform) plus custom Open Policy Agent (OPA) rules against everything
Smith generated. This is the actual gate: a critical or high-severity finding
blocks the deploy outright. Every finding you see comes with a plain-language
explanation of why it matters, not just a rule ID.

## Red Team

Adversarially probes the generated artifacts for the kind of thing a
straightforward scanner wouldn't catch — an unpinned base image, a
prompt-injection-shaped comment, a supply-chain risk in a dependency. It can
also block a deploy on its own if it finds something serious.

## Execution

Builds the Docker image on your host's own Docker daemon (via a mounted
socket — no separate build service) and deploys it: to a real Kubernetes
cluster if `kubectl` can reach one, or a plain `docker run` otherwise. Every
command it runs is recorded twice — once in a replayable terminal session,
once in a hash-chained audit log you can verify wasn't tampered with after
the fact.

## Oracle

Watches the fresh deployment for a short window — CPU, memory, restart
count, pod readiness — and uses an anomaly-detection model (Isolation
Forest) to catch problems a simple threshold would miss. If it finds a real
problem, it automatically rolls the deployment back and explains what
happened in plain language.

## FinOps

Estimates the deployment's monthly cost from the actual CPU/memory requests
in the generated manifest — deterministic arithmetic, never an LLM guess —
and flags obvious waste (over-provisioned limits, no autoscaling, a single
replica with no redundancy). If you've connected AWS credentials, it uses
your real Cost Explorer data instead of an estimate. See
[Cost Tracking](/guide/cost-tracking).
