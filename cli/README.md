# deploymint-cli

Thin CLI client for a running DeployMint Docker Compose deployment. It never
runs any agent code itself — it talks HTTP/WebSocket to a container already
started with `docker compose up -d`.

```bash
pip install -e .
deploymint up ./my-project --name my-project
```

Exit codes: `0` success, `2` blocked by the security gate, `1` any other
failure, `3` the DeployMint server itself is unreachable.
