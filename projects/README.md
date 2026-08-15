# Example projects

This directory is what `docker-compose.yml` mounts to `/workspace` inside the
app container (`DEPLOYMINT_PROJECTS_DIR`, see `.env`). Anything you put here
becomes visible to DeployMint.

- `fastapi-app/` — a small FastAPI service with a `/health` endpoint and a
  multi-file dependency graph (routes → models/db). Pick this one first:

```bash
deploymint up ./projects/fastapi-app
```

or register and deploy it from the web dashboard at `http://localhost:8000`.
