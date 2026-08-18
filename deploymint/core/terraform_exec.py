"""Runs the generated Terraform module against a real cloud account. See
docs/21-cloud-deploy.md.

This is the one place in the whole app that can create or destroy real,
billable infrastructure — everything here is deliberately narrow:
- subprocess.exec with an argument list, never shell=True, so a credential
  value can't be interpreted as shell syntax.
- credentials arrive as a plain env dict for exactly one subprocess call and
  are never written to a file, logged, or persisted (GCP's service-account
  JSON is the one exception that needs a file at all — see run_terraform's
  handling below — and that file lives in a 0600 tempdir deleted in a
  `finally` the moment the process exits).
- every call is capped by a timeout; a hung `terraform apply` can't hang a
  request forever.
"""

import asyncio
import os
import shutil
import stat
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from deploymint.core.cloud_creds import CloudCredentials, build_env

OnLine = Callable[[str], Awaitable[None]]

# init's timeout is generous on purpose: `terraform init` downloads every
# remote module a config *references*, even one gated behind `count = 0`
# (e.g. the optional EKS module in the AWS Terraform target) — module
# discovery happens before any count/for_each is evaluated. Confirmed by
# actually running init against the real generated module: the
# terraform-aws-modules/eks/aws module and its own nested submodules took
# well over two minutes to download on a cold cache, with no credentials
# involved at all yet.
_TIMEOUT_SECONDS = {"init": 300, "plan": 300, "apply": 900, "destroy": 900}


def terraform_available() -> bool:
    return shutil.which("terraform") is not None


async def _stream(proc: asyncio.subprocess.Process, on_line: OnLine) -> None:
    assert proc.stdout is not None
    async for raw in proc.stdout:
        line = raw.decode(errors="replace").rstrip("\n")
        if line:
            await on_line(line)


async def _run(args: list[str], cwd: Path, env: dict[str, str], timeout: int,
                on_line: OnLine) -> int:
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        await asyncio.wait_for(_stream(proc, on_line), timeout=timeout)
        return await proc.wait()
    except TimeoutError:
        proc.kill()
        await on_line(f"[deploymint] terraform timed out after {timeout}s — killed")
        return -1


async def run_terraform(action: str, directory: Path, cloud: str,
                         creds: CloudCredentials, on_line: OnLine) -> bool:
    """action is one of "plan", "apply", "destroy". Always runs `init` first
    (idempotent, needed for a fresh .deploymint/<run_id>/terraform checkout).
    Returns True only if every step exits 0."""
    if action not in ("plan", "apply", "destroy"):
        raise ValueError(f"unknown terraform action: {action}")
    if not terraform_available():
        await on_line("[deploymint] terraform is not installed in this image")
        return False
    if not (directory / "main.tf").is_file():
        await on_line(f"[deploymint] no Terraform module found at {directory}")
        return False

    env = {**os.environ, **build_env(cloud, creds)}
    gcp_cred_dir: str | None = None
    try:
        if cloud == "gcp":
            # The provider needs the service-account JSON as a *file*, not
            # inline — write it to a private tempdir that only this process
            # can read, and remove it as soon as terraform exits either way.
            gcp_cred_dir = tempfile.mkdtemp(prefix="deploymint-gcp-")
            os.chmod(gcp_cred_dir, stat.S_IRWXU)
            cred_path = Path(gcp_cred_dir) / "sa.json"
            cred_path.write_text(creds.gcp_credentials_json)
            os.chmod(cred_path, stat.S_IRUSR | stat.S_IWUSR)
            env["GOOGLE_APPLICATION_CREDENTIALS"] = str(cred_path)

        await on_line("[deploymint] $ terraform init -input=false")
        rc = await _run(["terraform", "init", "-input=false"], directory, env,
                         _TIMEOUT_SECONDS["init"], on_line)
        if rc != 0:
            await on_line(f"[deploymint] terraform init failed (exit {rc})")
            return False

        if action == "plan":
            await on_line("[deploymint] $ terraform plan -input=false -out=tfplan")
            rc = await _run(["terraform", "plan", "-input=false", "-out=tfplan"],
                             directory, env, _TIMEOUT_SECONDS["plan"], on_line)
        elif action == "apply":
            # Applies the plan saved by a prior "plan" run if present, else
            # plans and applies in one shot — either way -auto-approve is
            # required since there's no interactive TTY on this path.
            plan_file = directory / "tfplan"
            if plan_file.is_file():
                await on_line("[deploymint] $ terraform apply -input=false tfplan")
                rc = await _run(["terraform", "apply", "-input=false", "tfplan"],
                                 directory, env, _TIMEOUT_SECONDS["apply"], on_line)
            else:
                await on_line("[deploymint] $ terraform apply -input=false -auto-approve")
                rc = await _run(["terraform", "apply", "-input=false", "-auto-approve"],
                                 directory, env, _TIMEOUT_SECONDS["apply"], on_line)
        else:  # destroy
            await on_line("[deploymint] $ terraform destroy -input=false -auto-approve")
            rc = await _run(["terraform", "destroy", "-input=false", "-auto-approve"],
                             directory, env, _TIMEOUT_SECONDS["destroy"], on_line)

        if rc != 0:
            await on_line(f"[deploymint] terraform {action} failed (exit {rc})")
            return False
        await on_line(f"[deploymint] terraform {action} completed successfully")
        return True
    finally:
        if gcp_cred_dir:
            shutil.rmtree(gcp_cred_dir, ignore_errors=True)
