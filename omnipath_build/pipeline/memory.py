from __future__ import annotations

import json
import time
from typing import Any
from pathlib import Path
from datetime import UTC, datetime
import threading

try:
    import psutil
except ImportError:  # pragma: no cover - exercised only in stripped envs
    psutil = None  # type: ignore[assignment]

from omnipath_build.pipeline.progress import active_phase_snapshot

def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def _bytes_to_mib(value: int) -> float:
    return value / 1024 / 1024


class MemoryMonitor:
    """Sample process RSS and write phase-aware samples as NDJSON."""

    def __init__(
        self,
        *,
        output_path: Path,
        interval_seconds: float = 5.0,
    ) -> None:
        self.output_path = output_path
        self.interval_seconds = max(0.5, interval_seconds)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._started_at = time.perf_counter()
        self._sample_count = 0
        self._peak_sample: dict[str, Any] | None = None
        self._peak_by_phase: dict[str, dict[str, Any]] = {}
        self._process = psutil.Process() if psutil is not None else None

    @property
    def enabled(self) -> bool:
        """Whether RSS sampling can run in this environment."""
        return self._process is not None

    def start(self) -> None:
        """Start the background sampler."""
        if not self.enabled:
            print('[memory] psutil unavailable; memory sampling disabled', flush=True)
            return
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text('', encoding='utf-8')
        self._record_sample()
        self._thread = threading.Thread(
            target=self._run,
            name='omnipath-memory-monitor',
            daemon=True,
        )
        self._thread.start()
        print(
            '[memory] sampling rss '
            f'every {self.interval_seconds:g}s -> {self.output_path}',
            flush=True,
        )

    def stop(self) -> dict[str, Any]:
        """Stop sampling and return the final summary."""
        if not self.enabled:
            return {
                'enabled': False,
                'reason': 'psutil_unavailable',
            }
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=self.interval_seconds + 1.0)
        self._record_sample()
        summary = self.summary()
        print(
            '[memory] peak_rss='
            f'{summary["peak_rss_mebibytes"]:.1f} MiB '
            f'samples={summary["sample_count"]} log={self.output_path}',
            flush=True,
        )
        return summary

    def summary(self) -> dict[str, Any]:
        """Return the current peak and per-phase summary."""
        with self._lock:
            peak_sample = dict(self._peak_sample or {})
            peak_by_phase = {
                key: dict(value)
                for key, value in sorted(self._peak_by_phase.items())
            }
            sample_count = self._sample_count
        peak_rss_bytes = int(peak_sample.get('rss_bytes') or 0)
        return {
            'enabled': True,
            'log_path': str(self.output_path),
            'sample_interval_seconds': self.interval_seconds,
            'sample_count': sample_count,
            'peak_rss_bytes': peak_rss_bytes,
            'peak_rss_mebibytes': _bytes_to_mib(peak_rss_bytes),
            'peak_sample': peak_sample,
            'peak_by_phase': peak_by_phase,
        }

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            self._record_sample()

    def _record_sample(self) -> None:
        sample = self._sample()
        with self._lock:
            self._sample_count += 1
            if (
                self._peak_sample is None
                or sample['rss_bytes'] > self._peak_sample['rss_bytes']
            ):
                self._peak_sample = sample
            phase_labels = [
                phase['label']
                for phase in sample['active_phases']
                if phase.get('label')
            ] or ['idle']
            for label in phase_labels:
                current = self._peak_by_phase.get(label)
                if current is None or sample['rss_bytes'] > current['rss_bytes']:
                    self._peak_by_phase[label] = {
                        'rss_bytes': sample['rss_bytes'],
                        'rss_mebibytes': sample['rss_mebibytes'],
                        'elapsed_seconds': sample['elapsed_seconds'],
                        'timestamp': sample['timestamp'],
                    }
        with self.output_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(sample, sort_keys=True) + '\n')

    def _sample(self) -> dict[str, Any]:
        assert self._process is not None
        process_rss, children_rss, child_count = self._rss_with_children()
        rss = process_rss + children_rss
        return {
            'timestamp': _iso_now(),
            'elapsed_seconds': time.perf_counter() - self._started_at,
            'rss_bytes': rss,
            'rss_mebibytes': _bytes_to_mib(rss),
            'process_rss_bytes': process_rss,
            'children_rss_bytes': children_rss,
            'descendant_process_count': child_count,
            'active_phases': active_phase_snapshot(),
        }

    def _rss_with_children(self) -> tuple[int, int, int]:
        assert self._process is not None
        process_rss = 0
        children_rss = 0
        child_count = 0
        try:
            process_rss = self._process.memory_info().rss
        except psutil.Error:  # type: ignore[union-attr]
            process_rss = 0
        try:
            children = self._process.children(recursive=True)
        except psutil.Error:  # type: ignore[union-attr]
            children = []
        for child in children:
            try:
                children_rss += child.memory_info().rss
                child_count += 1
            except psutil.Error:  # type: ignore[union-attr]
                continue
        return process_rss, children_rss, child_count


def start_memory_monitor(
    *,
    output_path: Path,
    interval_seconds: float = 5.0,
) -> MemoryMonitor:
    """Create and start a phase-aware RSS memory monitor."""
    monitor = MemoryMonitor(
        output_path=output_path,
        interval_seconds=interval_seconds,
    )
    monitor.start()
    return monitor
