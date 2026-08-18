"""In-memory registry of live "terraform against a real cloud" jobs, one per
run at a time. See docs/21-cloud-deploy.md.

Deliberately separate from core/events.py's EventBus: that bus is created at
run start and dropped the moment the run finishes (docs/09-phase-5-
orchestration.md §5.3), but a cloud deploy happens later, on demand, possibly
long after the run and its bus are gone. A CloudJob has its own lifecycle
tied to the deploy action itself, not the run.
"""

import asyncio
from dataclasses import dataclass, field


@dataclass
class CloudJob:
    run_id: str
    action: str
    status: str = "running"  # running | success | failed
    lines: list[str] = field(default_factory=list)
    _subscribers: list[asyncio.Queue] = field(default_factory=list)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=10_000)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def emit(self, line: str) -> None:
        self.lines.append(line)
        for q in list(self._subscribers):
            try:
                q.put_nowait(line)
            except asyncio.QueueFull:
                pass

    async def finish(self, status: str) -> None:
        self.status = status
        for q in list(self._subscribers):
            try:
                q.put_nowait("__end__")
            except asyncio.QueueFull:
                pass


class CloudJobRegistry:
    def __init__(self):
        self._jobs: dict[str, CloudJob] = {}

    def get(self, run_id: str) -> CloudJob | None:
        return self._jobs.get(run_id)

    def is_running(self, run_id: str) -> bool:
        job = self._jobs.get(run_id)
        return job is not None and job.status == "running"

    def start(self, run_id: str, action: str) -> CloudJob:
        job = CloudJob(run_id=run_id, action=action)
        self._jobs[run_id] = job
        return job


registry = CloudJobRegistry()
