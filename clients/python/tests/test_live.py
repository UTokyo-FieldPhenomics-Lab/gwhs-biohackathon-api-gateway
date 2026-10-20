"""One live call per method, so a client that ships has been run rather than reasoned about.

Skipped by default, because it needs the network and a token::

    GLOBAL_WHEAT_LIVE=1 GLOBAL_WHEAT_API_KEY=... \
        uv run --project clients/python pytest tests/test_live.py

The inference half is skipped when the pool is empty, which is the honest answer to "is it up": the
gateway publishes that count, and a client is not the thing that should guess.
"""

from __future__ import annotations

import os
from typing import Any

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


def test_resizing_before_upload_does_not_change_the_answer() -> None:
    """Sending the model's own side and sending the original must answer alike.

    The client resizes on the participant's machine to the side the model would have chosen
    anyway; this is the check that the shortcut is not a different question. A published row is
    upscaled so the original really is larger than the model's side, and the two answers are
    compared on object count, area and normalised boxes.
    """
    from io import BytesIO

    from PIL import Image

    with Client() as client:
        model_id = "sam3.1-meta"
        if not any(m["id"] == model_id and m["backends_up"] for m in client.models()):
            pytest.skip("sam3.1-meta has no machine answering")

        row = None
        for summary in client.iter_images("gwhs-temp", limit=20):
            found = client.predict(model_id, "gwhs-temp", summary["row"], prompt="wheat head")
            if found["objects"]:
                row = summary["row"]
                break
        if row is None:
            pytest.skip("no published row holds a wheat head this model recognises")

        with Image.open(BytesIO(client.image_bytes("gwhs-temp", row))) as handle:
            upright = handle.convert("RGB")
        # Large enough that the model's side is a real downscale, but under the container's
        # 50-megapixel guard: a row that already is large must not be upscaled into a refusal.
        factor = max(1.0, 3000 / max(upright.size))
        big = upright.resize(
            (round(upright.width * factor), round(upright.height * factor)), Image.LANCZOS
        )
        buffer = BytesIO()
        big.save(buffer, format="JPEG", quality=92)
        original = buffer.getvalue()

        sent_original = client.predict_file(
            model_id, original, prompt="wheat head", resize_before_submit=False
        )
        sent_resized = client.predict_file(model_id, original, prompt="wheat head")

        assert sent_original["resolution_used"] == sent_resized["resolution_used"]
        assert sent_original["objects"], "the upscaled row found nothing to compare"

        side = int(client.model(model_id)["resolution"] or 1008)
        scale = side / max(big.size)
        resized_size = (round(big.width * scale), round(big.height * scale))

        def boxes(
            answer: dict[str, Any], size: tuple[int, int]
        ) -> list[tuple[str, tuple[float, ...]]]:
            """Normalise each box by its own image's size, which is what compares two scales."""
            width, height = size
            return [
                (
                    obj["category"],
                    (
                        obj["bbox"][0] / width,
                        obj["bbox"][1] / height,
                        obj["bbox"][2] / width,
                        obj["bbox"][3] / height,
                    ),
                )
                for obj in answer["objects"]
            ]

        def iou(one: tuple[float, ...], other: tuple[float, ...]) -> float:
            """Intersection over union of two `(x, y, w, h)` boxes in the same units."""
            ax2, ay2 = one[0] + one[2], one[1] + one[3]
            bx2, by2 = other[0] + other[2], other[1] + other[3]
            overlap_width = min(ax2, bx2) - max(one[0], other[0])
            overlap_height = min(ay2, by2) - max(one[1], other[1])
            if overlap_width <= 0 or overlap_height <= 0:
                return 0.0
            overlap = overlap_width * overlap_height
            return overlap / (one[2] * one[3] + other[2] * other[3] - overlap)

        large = boxes(sent_original, big.size)
        small = boxes(sent_resized, resized_size)

        assert abs(len(large) - len(small)) <= 1, f"{len(large)} objects against {len(small)}"

        remaining = list(small)
        for category, box in large:
            best = max(
                (
                    (iou(box, other), position)
                    for position, (kind, other) in enumerate(remaining)
                    if kind == category
                ),
                default=(0.0, -1),
            )
            if best[1] >= 0:
                assert best[0] >= 0.85, f"a {category} box moved: IoU {best[0]:.2f}"
                remaining.pop(best[1])

        def area(objects: list[tuple[str, tuple[float, ...]]]) -> float:
            """Total box area, which is the figure stable enough to compare across scales."""
            return sum(box[2] * box[3] for _, box in objects)

        assert abs(area(large) - area(small)) <= 0.10 * max(area(large), area(small))
