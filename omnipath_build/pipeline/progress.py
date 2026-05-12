from __future__ import annotations

import time
import atexit
from typing import Any, Iterator
import threading
from contextlib import contextmanager
from dataclasses import dataclass

@dataclass
class ActivePhase:
    label: str
    detail: str
    started_at: float
    updated_at: float


_lock = threading.Lock()
_active: dict[int, ActivePhase] = {}
_stop_event = threading.Event()
_heartbeat_thread: threading.Thread | None = None
_heartbeat_interval = 30.0


def _format_active(now: float) -> str:
    with _lock:
        phases = list(_active.values())
    if not phases:
        return '[active] idle'

    parts = []
    for phase in sorted(phases, key=lambda item: item.started_at):
        elapsed = now - phase.started_at
        stale = now - phase.updated_at
        stale_suffix = f' stale={stale:.0f}s' if stale >= _heartbeat_interval else ''
        parts.append(
            f'{phase.label}({elapsed:.0f}s{stale_suffix}; {phase.detail})'
        )
    return '[active] ' + ' | '.join(parts)


def active_phase_snapshot() -> list[dict[str, Any]]:
    """Return active phase labels in a JSON-serializable form."""
    now = time.perf_counter()
    with _lock:
        phases = list(_active.values())
    return [
        {
            'label': phase.label,
            'detail': phase.detail,
            'elapsed_seconds': now - phase.started_at,
            'stale_seconds': now - phase.updated_at,
        }
        for phase in sorted(phases, key=lambda item: item.started_at)
    ]


def _heartbeat() -> None:
    while not _stop_event.wait(_heartbeat_interval):
        print(_format_active(time.perf_counter()), flush=True)


def start_heartbeat(interval_seconds: float = 30.0) -> None:
    global _heartbeat_thread, _heartbeat_interval
    _heartbeat_interval = max(5.0, interval_seconds)
    if _heartbeat_thread and _heartbeat_thread.is_alive():
        return
    _stop_event.clear()
    _heartbeat_thread = threading.Thread(
        target=_heartbeat,
        name='omnipath-progress-heartbeat',
        daemon=True,
    )
    _heartbeat_thread.start()


def stop_heartbeat() -> None:
    _stop_event.set()
    thread = _heartbeat_thread
    if thread and thread.is_alive():
        thread.join(timeout=1.0)


def set_phase(label: str, detail: str = '') -> None:
    thread_id = threading.get_ident()
    now = time.perf_counter()
    with _lock:
        previous = _active.get(thread_id)
        started_at = previous.started_at if previous and previous.label == label else now
        _active[thread_id] = ActivePhase(
            label=label,
            detail=detail,
            started_at=started_at,
            updated_at=now,
        )


def update_phase(detail: str = '') -> None:
    thread_id = threading.get_ident()
    now = time.perf_counter()
    with _lock:
        previous = _active.get(thread_id)
        if previous is None:
            return
        previous.detail = detail or previous.detail
        previous.updated_at = now


def clear_phase() -> None:
    with _lock:
        _active.pop(threading.get_ident(), None)


@contextmanager
def phase(label: str, detail: str = '') -> Iterator[None]:
    set_phase(label, detail)
    try:
        yield
    finally:
        clear_phase()


atexit.register(stop_heartbeat)
