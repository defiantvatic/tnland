"""Live progress for long-running reports.

A parcel report fans out to half a dozen public services and can take tens
of seconds when caches are cold. This registry lets the frontend show which
source is running, done or failed in real time: the browser sends a client-
generated job id with its request and polls /api/progress for snapshots.
Every update is also logged, so the terminal running the server narrates
the same activity.

In-memory and single-process on purpose -- the app is a local tool bound to
127.0.0.1. Jobs expire after a few minutes so abandoned polls cannot leak.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger("tnland")

LABELS = {
    "parcel": "Parcel lookup",
    "flood": "FEMA flood",
    "wetlands": "Wetlands",
    "terrain": "Slope / terrain",
    "access": "Road access",
    "soils": "Soils (SSURGO)",
    "drivetimes": "Drive times",
    "soilanalysis": "Soil analysis",
    "elevation": "Elevation",
}

_TTL_S = 300.0
_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def update(job: str | None, source: str, status: str, detail: str = "") -> None:
    """Record (and log) a source's state. job may be None: log-only."""
    label = LABELS.get(source, source)
    log.info("%s: %s%s", label, status, f" ({detail})" if detail else "")
    if not job:
        return
    with _lock:
        _prune()
        entry = _jobs.setdefault(job, {"touched": 0.0, "order": [], "events": {}})
        if source not in entry["events"]:
            entry["order"].append(source)
        entry["events"][source] = {"status": status, "detail": detail}
        entry["touched"] = time.time()


def snapshot(job: str) -> dict[str, Any]:
    with _lock:
        entry = _jobs.get(job)
        if not entry:
            return {"sources": []}
        return {"sources": [
            {"source": s, "label": LABELS.get(s, s), **entry["events"][s]}
            for s in entry["order"]
        ]}


def _prune() -> None:
    cutoff = time.time() - _TTL_S
    for key in [k for k, v in _jobs.items() if v["touched"] < cutoff]:
        del _jobs[key]
