"""Drive 60 s of sustained predict load through the test gateway and sample the backends.

Twelve worker threads (six per model) each send one request after another with no sleep.
One mode per process: ``row`` addresses a real ``(dataset, row)``; ``upload`` posts one of four
real images. While the workers run, the gateway host polls all six ``/health`` endpoints; the
trigger file marks the load window inside the host's own clock, so no clock comparison is made.

Run from the generator as::

    python3 load.py --mode row --seconds 60 --workers 12 --out run_row
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import random
import subprocess
import threading
import time
from pathlib import Path

BASE_HOST = "gwhs-host"
BASE_PORT = 8001
GATEWAY = f"http://{BASE_HOST}:{BASE_PORT}"
KEY = "<rotated>"
TIMEOUT = 60.0
VPS = "gwhs-host"
LOG_PATH = "$HOME/gw-test/gateway.log"
TRIGGER = "$HOME/gw-test/RUN"
SAMPLER = "$HOME/gw-test/sampler.py"

MODELS = ("sam3.1-meta", "gwfss-segformer-b4")
# Only rows whose image object answers 200; gwhs-temp has four published rows of ten.
ROWS = {
    "gwhd": list(range(10)),
    "gwfss": list(range(10)),
    "gwhs-temp": [0, 1, 6, 9],
}
ROOT = Path(__file__).parent
IMAGES = (
    (ROOT / "images" / "gwhd_images_00000003.jpg", "image/jpeg"),
    (ROOT / "images" / "gwfss_images_00000007.png", "image/png"),
    (ROOT / "images" / "gwhs-temp_images_00000000.jpg", "image/jpeg"),
    (ROOT / "images" / "gwhs-temp_images_00000009.jpg", "image/jpeg"),
)


def ssh(*arguments: str, timeout: float = 30.0) -> str:
    """Run one command on the gateway host and return its stdout."""
    result = subprocess.run(
        ["ssh", VPS, *arguments], capture_output=True, text=True, timeout=timeout
    )
    return result.stdout


def multipart(name: str, data: bytes, mime: str, boundary: str) -> bytes:
    """Build one multipart body exactly as the API's upload route expects it."""
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode()


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile of ``values``, in milliseconds."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return round(float(ordered[rank - 1]), 1)


class Worker(threading.Thread):
    """One client loop: model fixed, payload sampled per request."""

    def __init__(self, index: int, mode: str, end: float, barrier: threading.Barrier) -> None:
        super().__init__(daemon=True)
        self.index = index
        self.model = MODELS[index % len(MODELS)]
        self.mode = mode
        self.end = end
        self.barrier = barrier
        self.records: list[dict[str, object]] = []
        self.rng = random.Random(20261019 + index)
        self.images = [(path.read_bytes(), mime, path.name) for path, mime in IMAGES]

    def _payload(self) -> tuple[str, bytes, dict[str, str], str]:
        """Return path, body, headers and a short label for one request."""
        headers = {"X-API-Key": KEY}
        if self.mode == "row":
            dataset = self.rng.choice(list(ROWS))
            row = self.rng.choice(ROWS[dataset])
            body = json.dumps({"dataset": dataset, "row": row}).encode()
            headers["Content-Type"] = "application/json"
            return f"/models/{self.model}/predict", body, headers, f"{dataset}:{row}"
        data, mime, name = self.rng.choice(self.images)
        boundary = f"----gwhsmeasure{self.index}"
        body = multipart(name, data, mime, boundary)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        return f"/models/{self.model}/predict/upload", body, headers, name

    def run(self) -> None:
        """Send requests back to back until the shared deadline."""
        connection = http.client.HTTPConnection(BASE_HOST, BASE_PORT, timeout=TIMEOUT)
        self.barrier.wait()
        while time.time() < self.end:
            path, body, headers, label = self._payload()
            started = time.time()
            status: object = "transport_error"
            detail = ""
            try:
                connection.request("POST", path, body=body, headers=headers)
                response = connection.getresponse()
                response.read()
                status = response.status
                detail = response.getheader("content-type") or ""
                if response.will_close:
                    connection.close()
                    connection = http.client.HTTPConnection(
                        BASE_HOST, BASE_PORT, timeout=TIMEOUT
                    )
            except Exception as error:  # noqa: BLE001 - the outcome is the measurement
                detail = f"{type(error).__name__}: {error}"
                connection.close()
                connection = http.client.HTTPConnection(BASE_HOST, BASE_PORT, timeout=TIMEOUT)
            ended = time.time()
            self.records.append(
                {
                    "start_ms": int(started * 1000),
                    "end_ms": int(ended * 1000),
                    "status": status,
                    "model": self.model,
                    "mode": self.mode,
                    "payload": label,
                    "bytes_sent": len(body),
                    "detail": str(detail).replace(",", ";"),
                }
            )
        connection.close()


def snapshot(path: Path) -> None:
    """Capture one nvidia-smi sample per Linux machine and try the Windows 5090."""
    command = "nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader"
    result: dict[str, object] = {}
    for label, command_line in (
        ("lab-3090 (local)", command.split()),
        ("homepc-4090", ["ssh", "-o", "BatchMode=yes", "gwhs-homepc", command]),
        ("lab-5090", ["ssh", "-o", "BatchMode=yes", "lab-5090", command]),
    ):
        try:
            done = subprocess.run(
                command_line, capture_output=True, text=True, timeout=40
            )
            result[label] = {
                "returncode": done.returncode,
                "stdout": done.stdout.strip(),
                "stderr": done.stderr.strip()[:300],
            }
        except Exception as error:  # noqa: BLE001 - a skipped machine is a result
            result[label] = {"error": f"{type(error).__name__}: {error}"}
    path.write_text(json.dumps(result, indent=1), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("row", "upload"), required=True)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()

    out = ROOT / arguments.out
    out.mkdir(parents=True, exist_ok=True)
    health = out / "health.csv"
    host_health = f"$HOME/gw-test/health_{arguments.mode}.csv"

    ssh("pkill", "-f", "sampler.py", timeout=10)
    subprocess.run(
        ["ssh", VPS, f"cat > {SAMPLER}"],
        input=(ROOT / "sampler.py").read_text(encoding="utf-8"),
        text=True,
        check=True,
        timeout=20,
    )
    subprocess.run(
        [
            "ssh",
            VPS,
            "setsid nohup python3 $HOME/gw-test/sampler.py"
            f" --out {host_health} --seconds {arguments.seconds + 14:.0f}"
            f" --trigger {TRIGGER} > $HOME/gw-test/sampler.log 2>&1 < /dev/null &",
        ],
        check=False,
        timeout=15,
    )

    for _ in range(40):
        if "ready" in ssh("cat $HOME/gw-test/sampler.log", timeout=10):
            break
        time.sleep(0.25)
    else:
        raise SystemExit("sampler did not start")

    first_line = int(ssh("wc", "-l", LOG_PATH).split()[0]) + 1
    ssh("touch", TRIGGER)

    barrier = threading.Barrier(arguments.workers)
    end = time.time() + arguments.seconds
    workers = [
        Worker(index, arguments.mode, end, barrier) for index in range(arguments.workers)
    ]
    started = time.time()
    for worker in workers:
        worker.start()

    siren = threading.Timer(arguments.seconds / 2, snapshot, args=(out / "nvidia-smi.json",))
    siren.start()
    for worker in workers:
        worker.join()
    ended = time.time()
    # Close the sampling window before the (possibly slow) nvidia-smi capture, or idle
    # samples after the load would be counted as part of it.
    ssh("rm", "-f", TRIGGER)
    siren.join()

    records = [record for worker in workers for record in worker.records]
    records.sort(key=lambda record: int(record["start_ms"]))
    columns = [
        "start_ms", "end_ms", "status", "model", "mode", "payload", "bytes_sent", "detail",
    ]
    lines = [",".join(columns)]
    lines += [",".join(str(record[column]) for column in columns) for record in records]
    (out / "client.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for _ in range(80):
        if "done" in ssh("cat $HOME/gw-test/sampler.log", timeout=10):
            break
        time.sleep(0.25)
    health.write_text(ssh("cat", host_health, timeout=30), encoding="utf-8")

    log_text = ssh("tail", "-n", f"+{first_line}", LOG_PATH, timeout=60)
    (out / "gateway.log").write_text(log_text, encoding="utf-8")
    inferences = [line for line in log_text.splitlines() if "inference model=" in line]
    statuses = [line for line in inferences if "status=0 " in line or "status=504 " in line]
    durations = [int(record["end_ms"]) - int(record["start_ms"]) for record in records]
    counts: dict[str, int] = {}
    for record in records:
        key = str(record["status"])
        counts[key] = counts.get(key, 0) + 1
    per_model: dict[str, int] = {}
    for record in records:
        key = str(record["model"])
        per_model[key] = per_model.get(key, 0) + 1

    meta = {
        "mode": arguments.mode,
        "workers": arguments.workers,
        "requested_seconds": arguments.seconds,
        "wall_seconds": round(ended - started, 2),
        "requests": len(records),
        "statuses": counts,
        "per_model": per_model,
        "p50_ms": percentile(durations, 50),
        "p95_ms": percentile(durations, 95),
        "max_ms": max(durations) if durations else None,
        "gateway_inference_lines": len(inferences),
        "gateway_status_0_or_504": len(statuses),
        "log_first_line": first_line,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(json.dumps(meta, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
