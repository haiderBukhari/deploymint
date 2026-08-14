"""Audited command runner. Every shell invocation the Execution Engine makes
goes through this. See docs/08-phase-4-execution.md §4.3."""

import asyncio
from dataclasses import dataclass


@dataclass
class CommandResult:
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def combined(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()


async def run_command(
    argv: list[str], *, cwd: str | None = None, timeout: int = 300,
    recorder=None, audit=None, agent: str = "execution",
    on_line=None,
) -> CommandResult:
    """Execute argv (shell=False, always — a repo path with a shell metacharacter
    must stay harmless), streaming lines to on_line, recording to the tmux log
    and the audit chain."""
    if recorder:
        recorder.log_command(argv)

    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )

    out_lines, err_lines = [], []

    async def pump(stream, sink, name):
        while True:
            raw = await stream.readline()
            if not raw:
                break
            line = raw.decode(errors="replace").rstrip()
            sink.append(line)
            if recorder:
                recorder.log_output(line)
            if on_line:
                await on_line(line, name)

    try:
        await asyncio.wait_for(
            asyncio.gather(pump(proc.stdout, out_lines, "stdout"),
                           pump(proc.stderr, err_lines, "stderr")),
            timeout=timeout,
        )
        await proc.wait()
    except asyncio.TimeoutError:
        proc.kill()
        err_lines.append(f"TIMEOUT after {timeout}s")

    result = CommandResult(argv, proc.returncode if proc.returncode is not None else -1,
                           "\n".join(out_lines), "\n".join(err_lines))
    if audit:
        await audit.record(agent=agent, action="shell_exec",
                           command=" ".join(argv), output=result.combined[:64_000],
                           exit_code=result.exit_code)
    return result
