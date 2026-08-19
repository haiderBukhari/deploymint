# What DeployMint does

DeployMint turns a codebase into a secure, running deployment — automatically.
Point it at a repo and it reads your code, generates the deployment configs
you'd otherwise hand-write, checks them for security problems before anything
runs, deploys the result, watches it for trouble, and tells you what it costs.
All of it runs on your own machine — your source code never leaves your
Docker Compose stack.

## The one-command pitch

```bash
deploymint up ./my-project
```

That single command:

1. **Reads your repo** — detects the language, framework, and dependencies,
   and maps how your files import each other.
2. **Generates a full deployment package** — a Dockerfile, Kubernetes
   manifests, Terraform, Ansible, ArgoCD, and a GitHub Actions workflow,
   tailored to what it actually found in your code.
3. **Scans everything it generated** — with Checkov (550+ built-in security
   rules) and custom policy checks. A failing scan blocks the deploy; it
   doesn't just print a warning and continue.
4. **Adversarially tests the result** — probing for the kind of subtle
   backdoors or bypasses a straightforward scan wouldn't catch.
5. **Builds and deploys it** — to a real Kubernetes cluster if one is
   reachable, or a plain `docker run` if not — recording every command it
   runs in a tamper-evident audit log.
6. **Watches the fresh deployment** — for crash loops or repeated restarts,
   rolling back automatically if something's wrong.
7. **Estimates what it costs** — from the actual resource requests in the
   generated manifest, or from your live AWS Cost Explorer data if you've
   connected it.

You can watch all seven steps happen live — from the CLI, the web dashboard,
or by asking a chat assistant to do it for you in plain English.

## Why this exists

Writing and maintaining deployment configs across five different formats
(Dockerfile, Kubernetes YAML, Terraform HCL, Ansible, CI workflows) is real,
recurring work — and it's easy to get the security-sensitive parts wrong
(root containers, exposed ports, missing resource limits) in ways that only
show up after something's already running. DeployMint handles the generation
and the security review together, so the two never drift apart.

## Where to go next

- **[Getting Started](/guide/getting-started)** — register a project and run
  your first deploy.
- **[The Agent Pipeline](/guide/agents)** — what each of the seven steps
  above actually does.
- **[Generated Artifacts](/guide/iac-formats)** — the six formats you get and
  when you'd use each one.
