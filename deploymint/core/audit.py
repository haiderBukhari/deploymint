"""Hash-chained audit log — every command execution is chained to the previous
one so tampering with a row is detectable. See docs/08-phase-4-execution.md §4.4."""

import hashlib
import json
from datetime import datetime, timezone

from deploymint.db.database import get_session_factory
from deploymint.db.models import AuditLog

GENESIS = "0" * 64


def _payload(prev: str, run_id: str, seq: int, agent: str, action: str,
             command: str, output: str, exit_code: int | None, ts: str) -> str:
    return json.dumps({
        "prev": prev, "run_id": run_id, "seq": seq, "agent": agent, "action": action,
        "command": command, "output": output, "exit_code": exit_code, "ts": ts,
    }, sort_keys=True, separators=(",", ":"))


class AuditChain:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.seq = 0
        self.prev_hash = GENESIS

    async def record(self, *, agent: str, action: str, command: str,
                     output: str = "", exit_code: int | None = None) -> str:
        self.seq += 1
        ts = datetime.now(timezone.utc)
        payload = _payload(self.prev_hash, self.run_id, self.seq, agent, action,
                           command, output, exit_code, ts.isoformat())
        h = hashlib.sha256(payload.encode()).hexdigest()

        Session = get_session_factory()
        with Session() as db:
            db.add(AuditLog(run_id=self.run_id, seq=self.seq, agent=agent,
                            action=action, command=command, output=output,
                            exit_code=exit_code, prev_hash=self.prev_hash,
                            hash=h, ts=ts))
            db.commit()

        self.prev_hash = h
        return h


def verify_chain(run_id: str) -> dict:
    Session = get_session_factory()
    with Session() as db:
        rows = db.query(AuditLog).filter_by(run_id=run_id).order_by(AuditLog.seq).all()

    prev = GENESIS
    for r in rows:
        payload = _payload(prev, r.run_id, r.seq, r.agent, r.action, r.command,
                           r.output, r.exit_code, r.ts.isoformat())
        if hashlib.sha256(payload.encode()).hexdigest() != r.hash or r.prev_hash != prev:
            return {"valid": False, "broken_at_seq": r.seq, "entries": len(rows)}
        prev = r.hash
    return {"valid": True, "broken_at_seq": None, "entries": len(rows)}
