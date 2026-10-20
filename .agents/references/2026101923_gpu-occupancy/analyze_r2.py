"""Summarise the re-run's local GPU series, its gateway log, and the old /health series.

Reads ``run_<mode>_r2/{gpu_<machine>.csv,client.csv,gateway.log,health.csv}``, where each GPU line is
``epoch_ms utilization %, memory MiB, power W``, and prints one JSON document: per machine and mode
the occupancy of the card at 250 ms, the requests the gateway routed to that machine, the secondary
``/health`` series, and where the load's end appears in each GPU series (a clock-sync check).

Run from the harness directory as::

    python3 analyze_r2.py > summary_r2.json
"""

from __future__ import annotations

import csv
import json
import os
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).parent
MACHINES_ENV = "GW_ADDRESSES"
MACHINES: dict[str, str] = {}
LINE = re.compile(r"^(\d+)[, ]\s*(\d+) %,\s*(\d+) MiB,\s*([\d.]+) W")


def samples(path: Path) -> list[tuple[int, int, float]]:
    """Return ``(epoch_ms, utilization_percent, power_w)`` for one sampler's file, first line dropped."""
    rows: list[tuple[int, int, float]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        match = LINE.match(line.strip())
        if match:
            if index == 0:
                continue  # -lms reports once immediately; that sample has no interval behind it
            rows.append((int(match.group(1)), int(match.group(2)), float(match.group(4))))
    return rows


def summarise(
    series: list[tuple[int, int, float]], window_start: int, window_end: int
) -> dict[str, object]:
    """Return the occupancy summary of one machine's in-window samples."""
    inside = [row for row in series if window_start <= row[0] <= window_end]
    values = [row[1] for row in inside]
    times = [row[0] for row in inside]
    window_ms = window_end - window_start
    gaps = [later - earlier for earlier, later in zip(times, times[1:])]
    # The driver refreshes utilization and power about every 500 ms, so two 250 ms lines can carry
    # the same value. Collapsing equal neighbours gives the number of independent updates.
    distinct = [
        previous
        for previous, row in zip([None, *inside], inside)
        if previous is None or (row[1], row[2]) != (previous[1], previous[2])
    ]
    zero_runs: list[int] = []
    run = 0
    for value in values:
        if value == 0:
            run += 1
        elif run:
            zero_runs.append(run)
            run = 0
    if run:
        zero_runs.append(run)
    return {
        "lines": len(values),
        "distinct_updates": len(distinct),
        "mean_util": round(statistics.fmean(values), 2) if values else None,
        "median_util": statistics.median(values) if values else None,
        "share_above_50": round(sum(v > 50 for v in values) / len(values), 3) if values else None,
        "share_zero": round(sum(v == 0 for v in values) / len(values), 3) if values else None,
        "interval_median_ms": statistics.median(gaps) if gaps else None,
        "interval_p95_ms": sorted(gaps)[max(0, int(0.95 * len(gaps)) - 1)] if gaps else None,
        "interval_max_ms": max(gaps) if gaps else None,
        "window_ms": window_ms,
        "coverage": round(len(values) * statistics.median(gaps) / window_ms, 3)
        if gaps
        else None,
        "zero_runs": len(zero_runs),
        "longest_zero_run_ms": max(zero_runs) * statistics.median(gaps) if zero_runs and gaps else 0,
        "last_busy_in_window_ms": max(
            (row[0] - window_start for row in inside if row[1] > 0), default=None
        ),
    }


def load_machines() -> None:
    """Fill ``MACHINES`` from ``GW_ADDRESSES``, alias to the address its gateway lines carry.

    Raises
    ------
    SystemExit
        When the variable is unset or is not JSON: a summary that guessed which machine a line came
        from would produce a table that looks right and is not. The values are in
        ``.agents/local/pool.md``.
    """
    raw = os.environ.get(MACHINES_ENV, "")
    if not raw:
        raise SystemExit(
            f"{MACHINES_ENV} is unset: alias-to-address is in .agents/local/pool.md, "
            "and this summary will not guess which machine a line came from"
        )
    MACHINES.clear()
    MACHINES.update(json.loads(raw))


def requests_per_machine(log_text: str) -> dict[str, int]:
    """Count the gateway's per-attempt lines by the machine behind each backend URL."""
    counts = dict.fromkeys(MACHINES, 0)
    for line in log_text.splitlines():
        if "inference model=" not in line:
            continue
        for machine, address in MACHINES.items():
            if f"backend=http://{address}:" in line:
                counts[machine] += 1
                break
    return counts


def health_series(path: Path) -> dict[str, dict[str, object]]:
    """Return the secondary ``/health`` occupancy per backend during the load."""
    rows: dict[str, list[dict[str, str]]] = {}
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["during_run"] == "1":
                rows.setdefault(row["backend"], []).append(row)
    result: dict[str, dict[str, object]] = {}
    for backend, items in sorted(rows.items()):
        ok = [item for item in items if item["status"] == "ok"]
        stamps = sorted(int(item["epoch_ms"]) for item in ok)
        gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
        busy = [item for item in ok if item["in_flight"] == "1"]
        result[backend] = {
            "samples": len(ok),
            "busy": round(len(busy) / len(ok), 3) if ok else None,
            "interval_median_ms": statistics.median(gaps) if gaps else None,
        }
    return result


def main() -> int:
    load_machines()
    document: dict[str, object] = {}
    for mode in ("row", "upload"):
        out = ROOT / f"run_{mode}_r2"
        if not out.exists():
            continue
        with (out / "client.csv").open(encoding="utf-8") as handle:
            records = list(csv.DictReader(handle))
        window_start = min(int(record["start_ms"]) for record in records)
        window_end = max(int(record["end_ms"]) for record in records)
        served = requests_per_machine((out / "gateway.log").read_text(encoding="utf-8"))

        machines: dict[str, object] = {}
        for machine in MACHINES:
            entry = summarise(samples(out / f"gpu_{machine}.csv"), window_start, window_end)
            count = served.get(machine, 0)
            busy_ms = (entry["mean_util"] or 0) / 100 * entry["window_ms"]
            entry["requests"] = count
            entry["gpu_ms_per_request"] = round(busy_ms / count, 1) if count else None
            machines[machine] = entry

        document[mode] = {
            "window_start_ms": window_start,
            "window_ms": window_end - window_start,
            "requests_total": sum(served.values()),
            "machines": machines,
            "health": health_series(out / "health.csv"),
        }
    print(json.dumps(document, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
