"""A model that spends money per call: its own budget, its own capacity, its own link.

The three behaviours here are what a managed-API backend needs and what a GPU container never
asked for: a declaration that says how long one request may take, a capacity refusal that is
neither the caller's fault nor a machine failure, and a loopback address that does not cross the
link the upload queue measures.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from biohack_gateway.admission import UploadQueue
from biohack_gateway.cache import ResultCache
from biohack_gateway.limits import ConcurrencyGate
from biohack_gateway.models import (
    MAX_RESPONSE_TIMEOUT_SECONDS,
    ModelRegistry,
    Reply,
    create_model_router,
)

MODEL = "nano-banana-2.1"
BACKEND_TOKEN = "backend-secret"
LOOPBACK = "http://127.0.0.1:8010/nano-banana-2.1"
REMOTE = "http://192.0.2.10:8010/nano-banana-2.1"

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAIAAAB7QOjdAAAADUlEQVR4nGM4UWEDRAALgwL5Z6uxbgAAAABJ"
    "RU5ErkJggg=="
)

DECLARATION: dict[str, Any] = {
    "model": MODEL,
    "resolution": 1024,
    "max_in_flight": 2,
    "response_timeout_seconds": 90,
    "computed_by": {"machine": "vertex-global"},
    "parameters": [
        {
            "name": "prompt",
            "type": "text",
            "required": True,
            "max_length": 300,
            "description": "What to draw.",
        }
    ],
}

PREDICTION: dict[str, Any] = {
    "model": MODEL,
    "resolution_used": 1024,
    "answer": "Here is the picture.",
    "image": {
        "media_type": "image/png",
        "width": 2,
        "height": 1,
        "data": base64.b64encode(PNG).decode(),
    },
    "objects": [],
}


class Fake:
    """Answer declarations and requests for one managed backend, and remember the budgets."""

    def __init__(self, replies: list[Reply], declaration: dict[str, Any] | None = None) -> None:
        self.replies = list(replies)
        self.declaration = DECLARATION if declaration is None else declaration
        self.timeouts: list[float] = []
        self.asked: list[tuple[str, str, Mapping[str, str], bytes | None]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Reply:
        """Answer one health check or one prediction, recording the budget it was given."""
        self.timeouts.append(timeout)
        self.asked.append((method, url, dict(headers), body))
        if url.endswith("/health"):
            return Reply(status=200, body=json.dumps(self.declaration).encode())
        if len(self.replies) > 1:
            return self.replies.pop(0)
        return self.replies[0]


def _registry(url: str, fake: Fake, token: str = BACKEND_TOKEN) -> ModelRegistry:
    """Build a registry whose one model is served by one fake backend."""
    return ModelRegistry({MODEL: [url]}, token, transport=fake)


def test_a_declared_budget_is_handed_to_the_transport() -> None:
    """A model that says one request takes ninety seconds gets ninety, not the ten a mask needs."""
    fake = Fake([Reply(status=200, body=json.dumps(PREDICTION).encode())])
    registry = _registry(REMOTE, fake)

    reply = registry.forward(MODEL, "generate", "prompt=wheat", None, "application/json")

    assert reply.status == 200
    assert fake.timeouts[-1] > 80.0
    assert fake.asked[-1][1] == f"{REMOTE}/models/{MODEL}/generate?prompt=wheat"
    assert fake.asked[-1][3] is None


def test_a_declared_budget_is_capped() -> None:
    """A declaration is data: a wild number is clamped rather than trusted."""
    declaration = {**DECLARATION, "response_timeout_seconds": 9999}
    fake = Fake([Reply(status=200, body=json.dumps(PREDICTION).encode())], declaration)
    registry = _registry(REMOTE, fake)

    registry.forward(MODEL, "generate", "prompt=wheat", None, "application/json")

    assert fake.timeouts[-1] <= MAX_RESPONSE_TIMEOUT_SECONDS


def test_a_failure_passes_the_backend_over_for_at_least_its_own_budget() -> None:
    """The quiet window covers the longest attempt the machine could have cost the caller."""
    fake = Fake([Reply(status=502, body=b"")])
    registry = _registry(REMOTE, fake)
    backend = registry._backends[MODEL][0]

    with pytest.raises(HTTPException) as raised:
        registry.forward(MODEL, "generate", "prompt=wheat", None, "application/json")

    assert raised.value.status_code == 502
    assert backend.unhealthy_until - time.monotonic() > 80.0


def test_a_backend_at_capacity_is_passed_through_as_429() -> None:
    """A backend's own refusal is the caller's answer, and the machine stays in the pool."""
    message = "at most 2 images at once; retry in 5 s"
    fake = Fake([Reply(status=429, body=json.dumps({"error": {"message": message}}).encode())])
    registry = _registry(REMOTE, fake)
    backend = registry._backends[MODEL][0]

    with pytest.raises(HTTPException) as raised:
        registry.forward(MODEL, "generate", "prompt=wheat", None, "application/json")
    with pytest.raises(HTTPException):
        registry.forward(MODEL, "generate", "prompt=wheat", None, "application/json")

    assert raised.value.status_code == 429
    assert raised.value.detail == message
    assert backend.unhealthy_until == 0.0
    assert len([call for call in fake.asked if "/health" not in call[1]]) == 2


def test_the_team_reaches_the_backend() -> None:
    """A backend keeping a ledger reads the same team id the gateway counts lanes against."""
    fake = Fake([Reply(status=200, body=json.dumps(PREDICTION).encode())])
    registry = _registry(REMOTE, fake)

    registry.forward(MODEL, "generate", "prompt=wheat", None, "application/json", team="gw-01")

    assert fake.asked[-1][2]["X-Biohack-Team"] == "gw-01"


def test_only_a_loopback_backend_skips_the_link_budget() -> None:
    """The queue measures the pool link; a local service does not cross it, a remote one does."""
    assert _registry(LOOPBACK, Fake([Reply(status=200, body=b"")])).charges_link(MODEL) is False
    assert (
        _registry("http://[::1]:8010/x", Fake([Reply(status=200, body=b"")])).charges_link(MODEL)
        is False
    )
    assert (
        _registry("http://localhost:8010/x", Fake([Reply(status=200, body=b"")])).charges_link(
            MODEL
        )
        is False
    )
    assert _registry(REMOTE, Fake([Reply(status=200, body=b"")])).charges_link(MODEL) is True


class SpyUploads:
    """Stand in for the byte queue, recording whether a request was ever admitted to it."""

    def __init__(self) -> None:
        self.acquires: list[tuple[str, int, int]] = []
        self.releases: list[tuple[str, int, float, int]] = []

    def acquire(self, team: str, ahead: int, size: int) -> None:
        """Record one admission."""
        self.acquires.append((team, ahead, size))

    def release(self, team: str, size: int, seconds: float, completed: int) -> None:
        """Record one release."""
        self.releases.append((team, size, seconds, completed))


def _upload_client(tmp_path: Path, url: str) -> tuple[TestClient, SpyUploads]:
    """Build an upload route over one fake backend and a recording byte queue."""
    fake = Fake([Reply(status=200, body=json.dumps(PREDICTION).encode())])
    registry = _registry(url, fake)
    uploads = SpyUploads()
    router = create_model_router(
        registry,
        {},
        "https://data.example",
        ResultCache(tmp_path / "cache.sqlite", enabled=False),
        ConcurrencyGate(0),
        cast(UploadQueue, uploads),
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), uploads


@pytest.mark.parametrize(
    ("url", "charged"),
    [(LOOPBACK, False), (REMOTE, True)],
)
def test_an_upload_charges_the_link_only_where_there_is_one(
    tmp_path: Path, url: str, charged: bool
) -> None:
    """A local image edit must not hold the byte budget a ninety-second call would keep."""
    client, uploads = _upload_client(tmp_path, url)

    response = client.post(
        f"/models/{MODEL}/predict/upload",
        params={"prompt": "colder light"},
        files={"file": ("x.png", PNG, "image/png")},
    )

    assert response.status_code == 200
    assert bool(uploads.acquires) is charged
    assert bool(uploads.releases) is charged
