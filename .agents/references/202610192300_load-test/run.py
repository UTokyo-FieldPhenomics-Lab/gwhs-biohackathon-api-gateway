"""Load-test the Global Wheat gateway from this laptop.

One thread per simulated team; every thread holds one connection and is released by a barrier at
the same instant. Per request the model and the mode come from one seeded shuffle, and the payload
from another, sliced per level so no two levels send the same sequence.

The gateway log on the host is sliced by line number around each level, so the server-side record of
one level is exactly the lines that level produced.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import sys
import threading
import time

from pathlib import Path
from typing import Any

import http.client

BASE_HOST = "gwhs-host"
BASE_PORT = 8001
CLIENT_TIMEOUT = 60.0
LOG_PATH = "$HOME/gw-test/gateway.log"
ROOT = Path(__file__).parent

MODELS = ("sam3.1-meta", "gwfss-segformer-b4")
MODES = ("predict", "predict/upload")
DATASETS = ("gwhd", "gwfss", "gwhs-temp")
ROWS = range(10)
IMAGES = {
    "gwhd_0003.jpg": (ROOT / "images" / "gwhd_0003.jpg", "image/jpeg"),
    "gwfss_0007.png": (ROOT / "images" / "gwfss_0007.png", "image/png"),
    "gwhs-temp_0004.png": (ROOT / "images" / "gwhs-temp_0004.png", "image/png"),
    "gwhs-temp_0009.jpg": (ROOT / "images" / "gwhs-temp_0009.jpg", "image/jpeg"),
}


def ssh(*args: str) -> str:
    """Run one command on the gateway host and return its stdout."""
    out = subprocess.run(
        ["ssh", "gwhs-host", *args], check=True, capture_output=True, text=True
    )
    return out.stdout


def log_lines() -> int:
    """Count the gateway log's lines on the host."""
    return int(ssh("wc", "-l", LOG_PATH).split()[0])


def log_slice(first: int) -> str:
    """Return the gateway's log from line `first` (1-based) to the end."""
    return ssh("tail", "-n", f"+{first}", LOG_PATH)


def multipart(name: str, data: bytes, mime: str, boundary: str) -> bytes:
    """Build the exact multipart body a browser-style upload sends."""
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode()


def build_plan(total: int) -> list[dict[str, Any]]:
    """Choose every request's model, mode and payload, in one reproducible order.

    Parameters
    ----------
    total : int
        How many requests this run will send, summed over all levels.

    Returns
    -------
    list of dict
        One entry per request, in the order the levels consume them. Model and mode are a balanced
        multiset shuffled with ``seed 20261019``; payloads are a separately shuffled spread over the
        three datasets and the four downloaded images.
    """
    pairs = [
        {"model": MODELS[i % 2], "mode": MODES[(i // 2) % 2]} for i in range(total)
    ]
    random.Random(20261019).shuffle(pairs)

    addresses = [(dataset, row) for dataset in DATASETS for row in ROWS]
    random.Random(20261019).shuffle(addresses)
    images = list(IMAGES)
    random.Random(20261019).shuffle(images)

    predicts = uploads = 0
    for pair in pairs:
        if pair["mode"] == "predict":
            pair["dataset"], pair["row"] = addresses[predicts % len(addresses)]
            predicts += 1
        else:
            pair["image"] = images[uploads % len(images)]
            uploads += 1
    return pairs


def send_one(plan: dict[str, Any], key: str) -> dict[str, Any]:
    """Send one request on its own connection and return what the client saw."""
    boundary = "----gwhsloadtest20261019"
    headers = {"X-API-Key": key, "Connection": "close"}
    if plan["mode"] == "predict":
        body = json.dumps({"dataset": plan["dataset"], "row": plan["row"]}).encode()
        headers["Content-Type"] = "application/json"
        path = f"/models/{plan['model']}/predict"
    else:
        name = plan["image"]
        payload, mime = IMAGES[name]
        body = multipart(name, payload.read_bytes(), mime, boundary)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        path = f"/models/{plan['model']}/predict/upload"

    started = time.time()
    status: Any
    detail = ""
    try:
        connection = http.client.HTTPConnection(BASE_HOST, BASE_PORT, timeout=CLIENT_TIMEOUT)
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        response.read()
        status = response.status
        detail = response.getheader("content-type") or ""
        connection.close()
    except Exception as error:  # noqa: BLE001 - the outcome is the measurement
        status = "transport_error"
        detail = f"{type(error).__name__}: {error}"
    ended = time.time()
    return {
        "start_ms": round(started * 1000),
        "end_ms": round(ended * 1000),
        "status": status,
        "model": plan["model"],
        "mode": plan["mode"],
        "bytes_sent": len(body),
        "payload": plan.get("dataset", plan.get("image")),
        "row": plan.get("row", ""),
        "detail": detail.replace(",", ";"),
    }


def run_level(index: int, count: int, plan: list[dict[str, Any]], key: str) -> None:
    """Release `count` requests at once and record the client and server sides."""
    folder = ROOT / f"level_{count}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")

    first = log_lines()
    models = subprocess.run(
        ["curl", "-s", "-H", f"X-API-Key: {key}", f"http://{BASE_HOST}:{BASE_PORT}/models"],
        check=True,
        capture_output=True,
    ).stdout.decode()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    (folder / "models.json").write_text(models, encoding="utf-8")

    results: list[dict[str, Any]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(count)

    def worker(item: dict[str, Any]) -> None:
        barrier.wait()
        record = send_one(item, key)
        with lock:
            results.append(record)

    threads = [threading.Thread(target=worker, args=(item,)) for item in plan]
    level_started = time.time()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    level_ended = time.time()

    slice_text = log_slice(first + 1)
    (folder / "gateway.log").write_text(slice_text, encoding="utf-8")

    columns = [
        "start_ms", "end_ms", "status", "model", "mode", "bytes_sent", "payload", "row", "detail",
    ]
    results.sort(key=lambda item: item["start_ms"])
    lines = [",".join(columns)]
    for item in results:
        lines.append(",".join(str(item[column]) for column in columns))
    (folder / "client.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    durations = [item["end_ms"] - item["start_ms"] for item in results]
    (folder / "meta.json").write_text(
        json.dumps(
            {
                "level": count,
                "index": index,
                "started": stamp,
                "wall_seconds": round(level_ended - level_started, 1),
                "log_first_line": first + 1,
                "log_lines": len(slice_text.splitlines()),
                "p50_ms": percentile(durations, 50),
                "p95_ms": percentile(durations, 95),
                "max_ms": max(durations) if durations else None,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(
        f"level {count}: {len(results)} requests, wall {level_ended - level_started:.1f}s, "
        f"p50 {percentile(durations, 50)} ms, p95 {percentile(durations, 95)} ms, "
        f"statuses {status_counts(results)}",
        flush=True,
    )


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile of `values`, in the values' own units."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return round(float(ordered[rank - 1]), 1)


def status_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    """Count outcomes as `status -> n`, with transport errors kept apart."""
    counts: dict[str, int] = {}
    for item in results:
        counts[str(item["status"])] = counts.get(str(item["status"]), 0) + 1
    return counts


def main() -> int:
    """Run every level in the order given, with the drain pause between them."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--levels", default="3,6,12,25,50")
    parser.add_argument("--gap", type=float, default=60.0)
    parser.add_argument("--warm", type=float, default=60.0)
    parser.add_argument("--label", default="")
    arguments = parser.parse_args()

    key = os.environ.get("GW_KEY", "")
    if not key:
        print("GW_KEY is not set", file=sys.stderr)
        return 2
    levels = [int(part) for part in arguments.levels.split(",")]
    plan = build_plan(sum(levels))

    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "run_meta.json").write_text(
        json.dumps(
            {
                "label": arguments.label,
                "levels": levels,
                "gap_seconds": arguments.gap,
                "seed": 20261019,
                "client_timeout_seconds": CLIENT_TIMEOUT,
                "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "host": ssh("hostname").strip(),
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"draining {arguments.warm:.0f}s before the first level", flush=True)
    time.sleep(arguments.warm)

    offset = 0
    for index, count in enumerate(levels):
        if index:
            print(f"pausing {arguments.gap:.0f}s", flush=True)
            time.sleep(arguments.gap)
        run_level(index, count, plan[offset : offset + count], key)
        offset += count
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
