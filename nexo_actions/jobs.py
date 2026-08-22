"""Controlled execution for long-running Nexo tools."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple


LOG = logging.getLogger("nexo.jobs")
ACTIVE_STATES = {"pending", "running"}
TERMINAL_STATES = {"completed", "failed", "cancelled"}


class JobCancelled(RuntimeError):
    """Raised by cooperative jobs when cancellation was requested."""


@dataclass(frozen=True)
class JobSubmission:
    job_id: str
    created: bool
    state: str


@dataclass
class _JobRecord:
    job_id: str
    name: str
    dedupe_key: Optional[str]
    timeout: Optional[float]
    state: str = "pending"
    progress: Optional[float] = None
    progress_message: Optional[str] = None
    progress_data: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    future: Optional[Future] = None
    timer: Optional[threading.Timer] = None
    completion_notified: bool = False


class JobContext:
    """Cooperative cancellation and progress API passed to capable tools."""

    def __init__(self, manager: "JobManager", job_id: str):
        self._manager = manager
        self.job_id = job_id

    @property
    def cancelled(self) -> bool:
        return self._manager.is_cancel_requested(self.job_id)

    @property
    def cancel_event(self) -> threading.Event:
        return self._manager.cancel_event(self.job_id)

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelled(f"Job {self.job_id} foi cancelado")

    def report_progress(
        self,
        progress: Optional[float] = None,
        message: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._manager._report_progress(
            self.job_id,
            progress=progress,
            message=message,
            data=data or {},
        )


class JobManager:
    """Thread-pool based job manager with timeout, cancellation and dedupe."""

    def __init__(self, max_workers: int = 3, retention_seconds: float = 600):
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="nexo-job",
        )
        self._retention_seconds = max(0.0, float(retention_seconds))
        self._jobs: Dict[str, _JobRecord] = {}
        self._active_dedupe: Dict[str, str] = {}
        self._callbacks: Dict[str, Tuple[Optional[Callable], Optional[Callable]]] = {}
        self._lock = threading.RLock()
        self._closed = False

    def submit(
        self,
        name: str,
        function: Callable[..., Any],
        *args: Any,
        timeout: Optional[float] = None,
        dedupe_key: Optional[str] = None,
        on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        pass_context: bool = False,
        **kwargs: Any,
    ) -> JobSubmission:
        """Submit work and return the existing active job when deduplicated."""
        with self._lock:
            if self._closed:
                raise RuntimeError("JobManager já foi encerrado")

            self.cleanup()
            if dedupe_key:
                existing_id = self._active_dedupe.get(dedupe_key)
                existing = self._jobs.get(existing_id) if existing_id else None
                if existing and existing.state in ACTIVE_STATES:
                    return JobSubmission(existing.job_id, False, existing.state)

            job_id = uuid.uuid4().hex
            record = _JobRecord(
                job_id=job_id,
                name=str(name),
                dedupe_key=dedupe_key,
                timeout=(None if timeout is None else max(0.01, float(timeout))),
            )
            self._jobs[job_id] = record
            self._callbacks[job_id] = (on_complete, on_progress)
            if dedupe_key:
                self._active_dedupe[dedupe_key] = job_id

            future = self._executor.submit(
                self._run,
                job_id,
                function,
                args,
                kwargs,
                pass_context,
            )
            record.future = future

            if record.timeout is not None:
                timer = threading.Timer(
                    record.timeout,
                    self._timeout,
                    args=(job_id,),
                )
                timer.daemon = True
                record.timer = timer
                timer.start()

            LOG.info(
                "JOB_SUBMITTED job_id=%s name=%s timeout=%s",
                job_id,
                record.name,
                record.timeout,
            )
            return JobSubmission(job_id, True, record.state)

    def _run(
        self,
        job_id: str,
        function: Callable[..., Any],
        args: tuple,
        kwargs: dict,
        pass_context: bool,
    ) -> None:
        with self._lock:
            record = self._jobs[job_id]
            if record.state != "pending":
                return
            record.state = "running"
            record.started_at = time.time()

        LOG.info("JOB_STARTED job_id=%s name=%s", job_id, record.name)
        context = JobContext(self, job_id)

        try:
            if pass_context:
                result = function(*args, job_context=context, **kwargs)
            else:
                result = function(*args, **kwargs)
        except JobCancelled as exc:
            self._finish(job_id, "cancelled", error=str(exc), error_type=type(exc).__name__)
        except Exception as exc:
            LOG.exception("JOB_FAILED job_id=%s name=%s", job_id, record.name)
            self._finish(job_id, "failed", error=str(exc), error_type=type(exc).__name__)
        else:
            self._finish(job_id, "completed", result=result)

    def _finish(
        self,
        job_id: str,
        state: str,
        result: Any = None,
        error: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> None:
        callback = None
        snapshot = None
        with self._lock:
            record = self._jobs.get(job_id)
            if not record or record.state in TERMINAL_STATES:
                return
            record.state = state
            record.result = result
            record.error = error
            record.error_type = error_type
            record.completed_at = time.time()
            if state == "completed":
                record.progress = 100.0
            if record.timer:
                record.timer.cancel()
            self._release_dedupe(record)
            callback = self._callbacks.get(job_id, (None, None))[0]
            if not record.completion_notified:
                record.completion_notified = True
                snapshot = self._snapshot(record)

        LOG.info(
            "JOB_FINISHED job_id=%s name=%s state=%s",
            job_id,
            record.name,
            state,
        )
        if callback and snapshot:
            self._safe_callback(callback, snapshot, "completion")

    def _timeout(self, job_id: str) -> None:
        callback = None
        snapshot = None
        with self._lock:
            record = self._jobs.get(job_id)
            if not record or record.state not in ACTIVE_STATES:
                return
            record.cancel_event.set()
            if record.future:
                record.future.cancel()
            record.state = "failed"
            record.error = f"Job excedeu o timeout de {record.timeout:g} segundos"
            record.error_type = "TimeoutError"
            record.completed_at = time.time()
            self._release_dedupe(record)
            callback = self._callbacks.get(job_id, (None, None))[0]
            if not record.completion_notified:
                record.completion_notified = True
                snapshot = self._snapshot(record)

        LOG.warning("JOB_TIMEOUT job_id=%s name=%s", job_id, record.name)
        if callback and snapshot:
            self._safe_callback(callback, snapshot, "timeout")

    def cancel(self, job_id: str, *, notify: bool = True) -> bool:
        callback = None
        snapshot = None
        with self._lock:
            record = self._jobs.get(job_id)
            if not record or record.state not in ACTIVE_STATES:
                return False
            record.cancel_event.set()
            if record.future:
                record.future.cancel()
            record.state = "cancelled"
            record.error = "Cancelado pelo usuário"
            record.error_type = "JobCancelled"
            record.completed_at = time.time()
            if record.timer:
                record.timer.cancel()
            self._release_dedupe(record)
            callback = self._callbacks.get(job_id, (None, None))[0] if notify else None
            if not record.completion_notified:
                record.completion_notified = True
                snapshot = self._snapshot(record)

        LOG.info("JOB_CANCELLED job_id=%s name=%s", job_id, record.name)
        if callback and snapshot:
            self._safe_callback(callback, snapshot, "cancellation")
        return True

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            record = self._jobs.get(job_id)
            return bool(record and record.cancel_event.is_set())

    def cancel_event(self, job_id: str) -> threading.Event:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                event = threading.Event()
                event.set()
                return event
            return record.cancel_event

    def _report_progress(
        self,
        job_id: str,
        progress: Optional[float],
        message: Optional[str],
        data: Dict[str, Any],
    ) -> None:
        callback = None
        snapshot = None
        with self._lock:
            record = self._jobs.get(job_id)
            if not record or record.state not in ACTIVE_STATES:
                return
            if progress is not None:
                record.progress = max(0.0, min(100.0, float(progress)))
            record.progress_message = message
            record.progress_data = dict(data)
            callback = self._callbacks.get(job_id, (None, None))[1]
            snapshot = self._snapshot(record)

        LOG.info(
            "JOB_PROGRESS job_id=%s name=%s progress=%s message=%s",
            job_id,
            record.name,
            record.progress,
            message,
        )
        if callback:
            self._safe_callback(callback, snapshot, "progress")

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._jobs.get(job_id)
            return self._snapshot(record) if record else None

    def list_jobs(self) -> list[Dict[str, Any]]:
        with self._lock:
            return [self._snapshot(record) for record in self._jobs.values()]

    def cleanup(self) -> int:
        now = time.time()
        removed = []
        with self._lock:
            for job_id, record in self._jobs.items():
                if (
                    record.state in TERMINAL_STATES
                    and record.completed_at is not None
                    and now - record.completed_at >= self._retention_seconds
                ):
                    removed.append(job_id)
            for job_id in removed:
                record = self._jobs.pop(job_id)
                self._callbacks.pop(job_id, None)
                self._release_dedupe(record)
        return len(removed)

    def shutdown(self, wait: bool = False) -> None:
        with self._lock:
            self._closed = True
            active_ids = [
                job_id for job_id, record in self._jobs.items()
                if record.state in ACTIVE_STATES
            ]
        for job_id in active_ids:
            self.cancel(job_id, notify=False)
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _release_dedupe(self, record: _JobRecord) -> None:
        if (
            record.dedupe_key
            and self._active_dedupe.get(record.dedupe_key) == record.job_id
        ):
            self._active_dedupe.pop(record.dedupe_key, None)

    @staticmethod
    def _snapshot(record: _JobRecord) -> Dict[str, Any]:
        return {
            "job_id": record.job_id,
            "name": record.name,
            "state": record.state,
            "timeout": record.timeout,
            "progress": record.progress,
            "progress_message": record.progress_message,
            "progress_data": dict(record.progress_data),
            "result": record.result,
            "error": record.error,
            "error_type": record.error_type,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "completed_at": record.completed_at,
        }

    @staticmethod
    def _safe_callback(callback: Callable, snapshot: Dict[str, Any], kind: str) -> None:
        try:
            callback(snapshot)
        except Exception:
            LOG.exception("Erro em callback de %s do JobManager", kind)
