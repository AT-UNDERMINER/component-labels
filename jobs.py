"""
jobs.py — tiny in-memory background-job registry for the web UI.

The Flask dev server runs threaded (see app.main()), so a long-running add or
batch import can execute on a daemon worker thread while the browser polls
GET /api/job/<id> for progress. Jobs live in memory only — fine for a
single-process LAN tool — and finished jobs are pruned after they age out so
the registry does not grow without bound.

A Job exposes:
    status   "running" | "done" | "error"
    log      list of human-readable progress lines (appended live)
    result   arbitrary dict the worker fills (e.g. resolved MPN, batch counts)
    error    one-line message when status == "error"

Each Job guards its own mutable state with a lock, so the worker thread can
append to it while request threads read it via to_dict().
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any


class Job:
    """A single tracked unit of background work."""

    def __init__(self, kind: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind                       # "lookup" | "import"
        self.status = "running"                # running | done | error
        self.created = time.time()
        self.finished: float | None = None
        self.log: list[str] = []
        self.result: dict[str, Any] = {}
        self.error: str | None = None
        self._lock = threading.Lock()

    def add_log(self, line: str) -> None:
        """Append a progress line (whitespace-trimmed; blanks ignored)."""
        line = (line or "").strip()
        if not line:
            return
        with self._lock:
            self.log.append(line)

    def set_result(self, **fields: Any) -> None:
        with self._lock:
            self.result.update(fields)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe snapshot for the /api/job/<id> response."""
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "log": list(self.log),
                "result": dict(self.result),
                "error": self.error,
            }


class JobManager:
    """Creates jobs, runs them on daemon threads, and prunes old ones."""

    def __init__(self, ttl_seconds: float = 900.0) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def create(self, kind: str) -> Job:
        self._prune()
        job = Job(kind)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def start(self, job: Job, target: Callable[[Job], None]) -> None:
        """Run `target(job)` on a daemon thread. Any exception becomes the
        job's error; a target that does not set status leaves it "done"."""

        def _runner() -> None:
            try:
                target(job)
                if job.status == "running":
                    job.status = "done"
            except Exception as exc:           # surface as a clean job error
                job.error = str(exc) or exc.__class__.__name__
                job.status = "error"
            finally:
                job.finished = time.time()

        threading.Thread(target=_runner, name=f"job-{job.id}", daemon=True).start()

    def _prune(self) -> None:
        now = time.time()
        with self._lock:
            stale = [
                jid for jid, job in self._jobs.items()
                if job.finished is not None and (now - job.finished) > self._ttl
            ]
            for jid in stale:
                del self._jobs[jid]
