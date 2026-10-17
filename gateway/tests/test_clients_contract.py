"""The client files and the contract name the same routes.

`clients/routes.json` is the one table the three client files are written against, and it is
checked from this side because this is the side that can answer with the contract: the gateway's own
OpenAPI document. A route added here without a line there, or a line there naming a path that has
moved, fails in CI's place — the test suite — rather than in a participant's notebook.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from biohack_gateway.app import create_app

ROUTES = Path(__file__).resolve().parents[2] / "clients/routes.json"
"""The table the three clients share, outside any of the Python environments."""

OPEN_PATHS = {"/health"}
"""The one operation that carries no data and no participant needs a client method for."""


def _declared() -> set[tuple[str, str]]:
    """Read the contract's data routes.

    Returns
    -------
    set of (str, str)
        One ``(method, path)`` per operation the document declares, liveness excluded.
    """
    document: dict[str, Any] = create_app().openapi()
    return {
        (method.upper(), path)
        for path, operations in document["paths"].items()
        if path not in OPEN_PATHS
        for method in operations
    }


def test_every_client_route_is_declared() -> None:
    """A client naming a path the API does not serve is a call that cannot work."""
    declared = _declared()
    for name, route in json.loads(ROUTES.read_text(encoding="utf-8")).items():
        assert (route["method"], route["path"]) in declared, f"{name} names a route that moved"


def test_every_declared_route_has_a_client_method() -> None:
    """The other direction: a new endpoint is a client method or it is a decision left unwritten."""
    routed = {
        (route["method"], route["path"])
        for route in json.loads(ROUTES.read_text(encoding="utf-8")).values()
    }
    assert _declared() - routed == set()
