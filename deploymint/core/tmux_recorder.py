"""Records a deploy to a replayable session log, next to the project's own
generated artifacts. Falls back to plain file capture when tmux is
unavailable — the deploy still works, only replay fidelity is reduced.
See docs/08-phase-4-execution.md §4.2."""

import shutil
from datetime import datetime, timezone
from pathlib import Path


class TmuxRecorder:
    def __init__(self, run_id: str, repo_path: str):
        self.run_id = run_id
        self.available = shutil.which("tmux") is not None
        self.session_name = f"deploymint-{run_id}"
        self.log_path: Path = Path(repo_path) / ".deploymint" / run_id / "session.log"
        self._server = None
        self._session = None
        self._pane = None

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_header()
        if not self.available:
            return
        try:
            import libtmux

            self._server = libtmux.Server()
            self._session = self._server.new_session(
                session_name=self.session_name, kill_session=True, attach=False,
            )
            self._pane = self._session.active_window.active_pane
            self._pane.cmd("pipe-pane", "-o", f"cat >> {self.log_path}")
        except Exception:
            # A tmux session is a recording surface, not the execution path —
            # if it can't be set up, degrade to plain file capture instead of
            # failing the deploy over it.
            self._session = None
            self._pane = None

    def _write_header(self) -> None:
        with self.log_path.open("a") as f:
            f.write(f"# DeployMint session {self.run_id}\n")
            f.write(f"# started {datetime.now(timezone.utc).isoformat()}\n")
            f.write(f"# tmux: {'yes' if self.available else 'no (degraded capture)'}\n\n")

    def log_command(self, argv: list[str]) -> None:
        with self.log_path.open("a") as f:
            f.write(f"\n$ {' '.join(argv)}\n")

    def log_output(self, text: str) -> None:
        with self.log_path.open("a") as f:
            f.write(text if text.endswith("\n") else text + "\n")

    def stop(self) -> str:
        if self._session:
            try:
                self._pane.cmd("pipe-pane")
                self._session.kill()
            except Exception:
                pass
        with self.log_path.open("a") as f:
            f.write(f"\n# ended {datetime.now(timezone.utc).isoformat()}\n")
        return str(self.log_path)
