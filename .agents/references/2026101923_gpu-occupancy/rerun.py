"""Run one 60 s load and sample each card locally at 250 ms.

The first attempt (`run_row`, `run_upload`) inferred card occupancy from the containers'
``/health`` polled across the tailnet, ~1.8 samples/s, which cannot resolve a 100-300 ms lock.
This script brackets the same load with a persistent ``nvidia-smi -lms 250`` on each of the three
cards and leaves the series beside the old ones.

Every line is stamped on the machine that produced it with that machine's own epoch milliseconds,
so nothing depends on when the local ``ssh`` call returned — which matters here, because ssh
connection setup to the two remote hosts takes about seven seconds. The three machines are
NTP-synced; the analysis checks that by comparing where the load's end appears in each series.

``-lms 250`` reports a new line every 250 ms but the driver refreshes ``utilization.gpu`` and
``power.draw`` every ~500 ms, so consecutive pairs are not independent samples. The analysis counts
both the lines and the distinct driver updates.

Run from the harness directory as::

    python3 rerun.py --mode row --out run_row_r2
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).parent
QUERY = "utilization.gpu,memory.used,power.draw"
NVIDIA = f"nvidia-smi --query-gpu={QUERY} --format=csv,noheader -lms 250"
LEAD_IN = 3.0
LINES = 460  # about 115 s at 250 ms


def commands(lines: int) -> dict[str, list[str]]:
    """Return how to run the sampler on each machine, from this machine."""
    reader = (
        "import sys,time\n"
        "n=0\n"
        "for line in sys.stdin:\n"
        "    print(int(time.time()*1000), line.strip(), flush=True)\n"
        "    n+=1\n"
        f"    if n>={lines}: break\n"
    )
    powershell = (
        f"{NVIDIA} | Select-Object -First {lines} | "
        "ForEach-Object { \\\"$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()),$_\\\" }"
    )
    return {
        "lab-3090": ["bash", "-c", f"{NVIDIA} | python3 -u -c {shlex.quote(reader)}"],
        "homepc-4090": [
            "ssh", "-o", "BatchMode=yes", "gwhs-homepc",
            f"{NVIDIA} | python3 -u -c {shlex.quote(reader)}",
        ],
        "lab-5090": [
            "ssh", "-o", "BatchMode=yes", "lab-5090",
            f'powershell -NoProfile -Command "{powershell}"',
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("row", "upload"), required=True)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()

    out = ROOT / arguments.out
    out.mkdir(parents=True, exist_ok=True)

    running: list[dict[str, object]] = []
    for machine, argv in commands(LINES).items():
        path = out / f"gpu_{machine}.csv"
        handle = open(path, "w", encoding="utf-8")
        process = subprocess.Popen(argv, stdout=handle, stderr=subprocess.PIPE, text=True)
        running.append(
            {
                "machine": machine,
                "path": path.name,
                "launched_epoch_ms": int(time.time() * 1000),
                "process": process,
                "handle": handle,
            }
        )
        print(f"sampler launched: {machine} at {time.time():.0f}", flush=True)

    time.sleep(LEAD_IN)

    completed = subprocess.run(
        [
            "python3", str(ROOT / "load.py"),
            "--mode", arguments.mode,
            "--seconds", str(arguments.seconds),
            "--workers", str(arguments.workers),
            "--out", arguments.out,
        ]
    )

    ends: dict[str, object] = {}
    for entry in running:
        process = entry["process"]
        _, errors = process.communicate(timeout=300)
        entry["handle"].close()
        ends[str(entry["machine"])] = {
            "launched_epoch_ms": entry["launched_epoch_ms"],
            "returncode": process.returncode,
            "stderr": (errors or "").strip()[:400],
        }
    (out / "gpu_launch.json").write_text(json.dumps(ends, indent=1), encoding="utf-8")
    print(json.dumps(ends, indent=1))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
