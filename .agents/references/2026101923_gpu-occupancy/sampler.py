"""Poll every backend's /health from the gateway host and record GPU occupancy.

Each backend is polled by its own thread at a start-to-start interval of 0.5 s, so a
``/health`` that takes ~0.5 s over the tailnet still yields about two samples per second.
``during_run`` is 1 exactly while the load generator's trigger file exists, which makes the
run window independent of any clock difference between this host and the generator.

Run on the host as::

    python3 sampler.py --out health_row.csv --seconds 72 --trigger $HOME/gw-test/RUN
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone

BACKENDS = {
    "lab-5090:12711": "http://<address>:12711",
    "lab-5090:12712": "http://<address>:12712",
    "lab-3090:12711": "http://<address>:12711",
    "lab-3090:12712": "http://<address>:12712",
    "homepc-4090:12711": "http://<address>:12711",
    "homepc-4090:12712": "http://<address>:12712",
}

INTERVAL = 0.5


def poll(name: str, deadline: float, trigger: str) -> list[dict[str, object]]:
    """Sample one backend until ``deadline``, returning one row per attempt."""
    rows: list[dict[str, object]] = []
    url = f"{BACKENDS[name]}/health"
    while time.monotonic() < deadline:
        started = time.monotonic()
        epoch = time.time()
        during = 1 if os.path.exists(trigger) else 0
        in_flight: object = ""
        free: object = ""
        status = "ok"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                document = json.loads(response.read())
            in_flight = document.get("in_flight", "")
            free = document.get("gpu_free_mib", "")
        except Exception as error:  # noqa: BLE001 - the failure is part of the record
            status = f"{type(error).__name__}: {error}"[:120]
        rows.append(
            {
                "epoch_ms": int(epoch * 1000),
                "timestamp": datetime.fromtimestamp(epoch, timezone.utc).isoformat(
                    timespec="milliseconds"
                ),
                "backend": name,
                "in_flight": in_flight,
                "gpu_free_mib": free,
                "during_run": during,
                "status": status.replace(",", ";"),
            }
        )
        elapsed = time.monotonic() - started
        time.sleep(max(0.0, INTERVAL - elapsed))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--seconds", type=float, default=72.0)
    parser.add_argument("--trigger", required=True)
    arguments = parser.parse_args()

    deadline = time.monotonic() + arguments.seconds
    results: list[dict[str, object]] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        rows = poll(name, deadline, arguments.trigger)
        with lock:
            results.extend(rows)

    threads = [threading.Thread(target=worker, args=(name,)) for name in BACKENDS]
    print("ready", flush=True)
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    results.sort(key=lambda row: int(row["epoch_ms"]))
    columns = [
        "epoch_ms", "timestamp", "backend", "in_flight", "gpu_free_mib", "during_run", "status",
    ]
    with open(arguments.out, "w", encoding="utf-8") as handle:
        handle.write(",".join(columns) + "\n")
        for row in results:
            handle.write(",".join(str(row[column]) for column in columns) + "\n")
    print(f"done {len(results)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
