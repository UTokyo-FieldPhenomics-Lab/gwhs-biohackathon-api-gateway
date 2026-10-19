"""Put the r2 baseline and the r3 re-run side by side, with the arithmetic shown.

Reads ``summary_r2.json`` and ``summary_r3.json`` (both produced by ``analyze_r2.py`` and its
byte-identical twin ``analyze_r3.py``, which differs only in the run directory it opens) plus each
run's ``meta.json``, and prints one JSON document: per mode the two tables, their deltas, and the
two cross-run aggregates an operator reads first — throughput and card-time per served request.

Run from the harness directory as::

    python3 compare_r3.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent
MACHINES = ("lab-3090", "homepc-4090", "lab-5090")


def delta(new: float | None, old: float | None) -> dict[str, float | None]:
    """Return the absolute and relative change, the latter as a fraction of the old value."""
    if new is None or old is None:
        return {"abs": None, "rel": None}
    return {"abs": round(new - old, 2), "rel": round((new - old) / old, 3) if old else None}


def main() -> int:
    old = json.loads((ROOT / "summary_r2.json").read_text(encoding="utf-8"))
    new = json.loads((ROOT / "summary_r3.json").read_text(encoding="utf-8"))
    document: dict[str, object] = {}
    for mode in ("row", "upload"):
        a, b = old[mode], new[mode]
        old_meta = json.loads((ROOT / f"run_{mode}_r2" / "meta.json").read_text(encoding="utf-8"))
        new_meta = json.loads((ROOT / f"run_{mode}_r3" / "meta.json").read_text(encoding="utf-8"))
        machines: dict[str, object] = {}
        for machine in MACHINES:
            before, after = a["machines"][machine], b["machines"][machine]
            machines[machine] = {
                "mean_util_r2": before["mean_util"],
                "mean_util_r3": after["mean_util"],
                "mean_util_delta": delta(after["mean_util"], before["mean_util"]),
                "gpu_ms_per_request_r2": before["gpu_ms_per_request"],
                "gpu_ms_per_request_r3": after["gpu_ms_per_request"],
                "gpu_ms_per_request_delta": delta(
                    after["gpu_ms_per_request"], before["gpu_ms_per_request"]
                ),
                "requests_r2": before["requests"],
                "requests_r3": after["requests"],
                "health_in_flight_1_r2": a["health"].get(f"{machine}:12711", {}).get("busy"),
                "health_in_flight_1_r3": b["health"].get(f"{machine}:12711", {}).get("busy"),
            }
        busy_before = sum(m["mean_util"] / 100 * m["window_ms"] for m in a["machines"].values())
        busy_after = sum(m["mean_util"] / 100 * m["window_ms"] for m in b["machines"].values())
        document[mode] = {
            "window_s_r2": round(a["window_ms"] / 1000, 2),
            "window_s_r3": round(b["window_ms"] / 1000, 2),
            "requests_r2": old_meta["requests"],
            "requests_r3": new_meta["requests"],
            "requests_delta": delta(new_meta["requests"], old_meta["requests"]),
            "throughput_rps_r2": round(old_meta["requests"] / (a["window_ms"] / 1000), 2),
            "throughput_rps_r3": round(new_meta["requests"] / (b["window_ms"] / 1000), 2),
            "p50_r2": old_meta["p50_ms"],
            "p50_r3": new_meta["p50_ms"],
            "p50_delta": delta(new_meta["p50_ms"], old_meta["p50_ms"]),
            "p95_r2": old_meta["p95_ms"],
            "p95_r3": new_meta["p95_ms"],
            "p95_delta": delta(new_meta["p95_ms"], old_meta["p95_ms"]),
            "mean_util_mean_r2": round(
                sum(m["mean_util"] for m in a["machines"].values()) / len(a["machines"]), 2
            ),
            "mean_util_mean_r3": round(
                sum(m["mean_util"] for m in b["machines"].values()) / len(b["machines"]), 2
            ),
            "card_ms_total_r2": round(busy_before),
            "card_ms_total_r3": round(busy_after),
            "card_ms_per_request_r2": round(busy_before / old_meta["requests"], 1),
            "card_ms_per_request_r3": round(busy_after / new_meta["requests"], 1),
            "machines": machines,
        }
    print(json.dumps(document, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
