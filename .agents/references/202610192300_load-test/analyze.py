"""Summarise one run: client-side outcomes and the gateway's own record per level."""

from __future__ import annotations

import csv
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

INFERENCE = re.compile(
    r"inference model=(?P<model>\S+) backend=(?P<backend>\S+) mode=(?P<mode>\S+) "
    r"attempt=(?P<attempt>\d+) ahead=(?P<ahead>\d+) status=(?P<status>\d+) ms=(?P<ms>[\d.]+) "
    r"bytes_in=(?P<bytes_in>\d+) bytes_out=(?P<bytes_out>\d+)"
)
ROOT = Path(__file__).parent


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile, matching run.py."""
    if not values:
        return None
    ordered = sorted(values)
    return round(float(ordered[max(1, math.ceil(pct / 100 * len(ordered))) - 1]), 1)


def summarise(folder: Path) -> dict:
    """Read one level's client CSV, gateway slice and /models snapshot."""
    with (folder / "client.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    durations = [int(row["end_ms"]) - int(row["start_ms"]) for row in rows]
    statuses = Counter(row["status"] for row in rows)

    lines = (folder / "gateway.log").read_text(encoding="utf-8").splitlines()
    attempts = [match.groupdict() for match in map(INFERENCE.search, lines) if match]
    backend_split = Counter(item["backend"] for item in attempts)
    model_backend = Counter((item["model"], item["backend"]) for item in attempts)
    max_ahead = max((int(item["ahead"]) for item in attempts), default=None)
    ahead_by_status = Counter(
        (item["status"], int(item["ahead"]) >= 1) for item in attempts
    )

    models = json.loads((folder / "models.json").read_text(encoding="utf-8"))
    published = {
        entry["id"]: {
            "backends_up": entry.get("backends_up"),
            "max_in_flight": entry.get("max_in_flight"),
            "checked_seconds_ago": entry.get("checked_seconds_ago"),
        }
        for entry in models["models"]
    }

    warnings = Counter()
    for line in lines:
        for marker in (
            "stopped answering",
            "is answering",
            "failed a",
            "unreachable",
            "refused the gateway's token",
            "did not declare itself",
            "answered a body that is not this contract",
        ):
            if marker in line:
                warnings[marker] += 1
                break

    refusals = Counter(
        line.split("refused:")[-1].strip() for line in lines if "refused:" in line
    )
    return {
        "level": folder.name,
        "requests": len(rows),
        "statuses": dict(sorted(statuses.items())),
        "p50_ms": percentile(durations, 50),
        "p95_ms": percentile(durations, 95),
        "max_ms": max(durations) if durations else None,
        "by_model_mode": dict(Counter(f"{row['model']}/{row['mode']}" for row in rows)),
        "log_lines": len(lines),
        "inference_lines": len(attempts),
        "inference_status": dict(Counter(item["status"] for item in attempts)),
        "max_ahead": max_ahead,
        "attempts_with_queue": {f"{s} queued={q}": n for (s, q), n in ahead_by_status.items()},
        "backend_split": dict(backend_split),
        "model_backend": {f"{m} {b}": n for (m, b), n in sorted(model_backend.items())},
        "mean_ms_by_attempt": {
            status: round(
                sum(float(i["ms"]) for i in attempts if i["status"] == status)
                / max(1, sum(1 for i in attempts if i["status"] == status)),
                1,
            )
            for status in sorted({i["status"] for i in attempts})
        },
        "published": published,
        "warnings": dict(warnings),
        "lane_refusals": dict(refusals),
    }


def main() -> int:
    """Print one block per level, and a run total."""
    folders = sorted(
        (path for path in ROOT.glob("level_*") if path.is_dir()),
        key=lambda path: int(path.name.split("_")[1]),
    )
    out = []
    for folder in folders:
        summary = summarise(folder)
        out.append(summary)
        print(json.dumps(summary, indent=1), flush=True)
    (ROOT / "summary.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

    print("\nlevel | n | statuses | p50 | p95 | max | inf | inf status | max ahead")
    for summary in out:
        print(
            f"{summary['level']} | {summary['requests']} | {summary['statuses']} | "
            f"{summary['p50_ms']} | {summary['p95_ms']} | {summary['max_ms']} | "
            f"{summary['inference_lines']} | {summary['inference_status']} | "
            f"{summary['max_ahead']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
