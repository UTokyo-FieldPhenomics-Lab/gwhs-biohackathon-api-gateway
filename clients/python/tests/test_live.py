"""One live call per method, so a client that ships has been run rather than reasoned about.

Skipped by default, because it needs the network and a token::

    GLOBAL_WHEAT_LIVE=1 GLOBAL_WHEAT_API_KEY=... \
        uv run --project clients/python pytest tests/test_live.py

The inference half is skipped when the pool is empty, which is the honest answer to "is it up": the
gateway publishes that count, and a client is not the thing that should guess.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from global_wheat import Client

pytestmark = pytest.mark.skipif(
    os.environ.get("GLOBAL_WHEAT_LIVE") != "1",
    reason="set GLOBAL_WHEAT_LIVE=1 and GLOBAL_WHEAT_API_KEY to call the API",
)


def test_every_method_answers() -> None:
    """The one test that would catch a route this client names wrongly."""
    with Client() as client:
        directory = client.datasets()
        assert directory, "the API published no datasets"

        gwhd = client.describe("gwhd")
        assert gwhd["dataset"] == "gwhd"

        page = client.images("gwhd", limit=2)
        assert len(page["images"]) <= 2

        rows = [row for row in client.iter_images("gwhd", limit=50)][:3]
        assert rows, "no rows selected"

        detail = client.image("gwhs-temp", 0)  # the dataset whose geometry is RLE
        assert detail["objects"], "the row published no objects"
        assert all(obj["mask"].dtype == np.bool_ for obj in detail["objects"])

        photo = client.image_bytes("gwhd", rows[0]["row"])
        assert photo[:2] in (b"\xff\xd8", b"\x89P")

        mask = client.mask_bytes("gwfss", 0)
        assert mask.startswith(b"\x89PNG")

        models = client.models()
        assert models, "the API published no models"
        model_id = models[0]["id"]
        assert client.model(model_id)["id"] == model_id

        if not models[0]["backends_up"]:
            pytest.skip("no machine is answering, so the inference half was not exercised")

        prediction = client.predict(model_id, "gwhd", rows[0]["row"], prompt="wheat head")
        assert prediction["model"] == model_id
        for obj in prediction["objects"]:
            assert obj["mask"].ndim == 2
            assert int(obj["mask"].sum()) == int(obj["area"])

        uploaded = client.predict_file(model_id, photo, prompt="wheat head")
        assert uploaded["model"] == model_id
