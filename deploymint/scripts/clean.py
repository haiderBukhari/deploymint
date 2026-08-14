"""Cleans up resources DeployMint created — deployments, services, and
(optionally) images, all identified by the managed-by=deploymint label set on
every generated manifest and every `docker run` container. Runs inside the
app container, since it needs the same mounted Docker socket and kubeconfig.
See docs/08-phase-4-execution.md §4.8."""

import argparse
import asyncio

from deploymint.core.runner import run_command


async def main(wipe_images: bool):
    await run_command(["kubectl", "delete", "deployment,service",
                       "-l", "managed-by=deploymint", "--ignore-not-found"])
    await run_command(["docker", "ps", "-aq", "--filter", "label=managed-by=deploymint"])
    if wipe_images:
        await run_command(["docker", "image", "prune", "-f",
                           "--filter", "label=managed-by=deploymint"])


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--all", action="store_true", dest="wipe_images")
    asyncio.run(main(**vars(p.parse_args())))
