"""Record the four paid pictures the Nano Banana model page shows, once.

The model page draws one generation, one edit, one segmentation mask and one detection card. None
is reproducible: the model samples a new picture every time, and every call spends a team credit. So
the answers are recorded here, committed as fixtures, and composed by `make_figures.py` without
another call. Pass `--force` to spend again; without it the recorder refuses to overwrite a fixture
it already has.

    set -a; . ./.env; set +a
    uv run --project docs --with requests --with numpy --with pillow \
        python docs/scripts/record_nano_banana.py [--force]

The key comes from `GLOBAL_WHEAT_API_KEY`, the same variable the client and the figure tooling read.
Nothing here prints it.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLIENT_PYTHON = REPO / "clients/python"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "nano-banana"

sys.path.insert(0, str(CLIENT_PYTHON))
import global_wheat  # noqa: E402

MODEL = "nano-banana-2.1"

GENERATE = {
    "prompt": "a wheat field at golden hour",
    "aspect_ratio": "16:9",
    "image_size": "1K",
}
"""The page's own generate example, at the size the figure lays out."""

EDIT = {
    "prompt": "the same crop under colder evening light",
    "image_size": "1K",
}
"""The page's own edit example on `gwfss` image 4."""

MASK = {
    "prompt": "generate segmentation masks for wheat head in the proposed image",
    "image_size": "1K",
}
"""The live demo's mask example on `gwfss` image 3."""

DETECT = {
    "prompt": "draw a red bounding box around every wheat head in the proposed image",
    "image_size": "1K",
}
"""A detection prompt on the same image, to show boxes drawn as pixels."""


def record(
    client: global_wheat.Client,
    name: str,
    call: dict[str, object],
    *,
    row: int | None,
) -> dict[str, object]:
    """Run one call and write its picture and the facts the figure needs.

    Parameters
    ----------
    client : global_wheat.Client
        An authenticated client.
    name : str
        The fixture's short name for this call, which also names its picture.
    call : dict
        The prompt and the model's own parameters.
    row : int or None
        The dataset row to edit, or ``None`` for a generation.

    Returns
    -------
    dict
        The recorded facts, with `picture` the file name the figure reads.
    """
    if row is None:
        answer = client.generate(MODEL, **call)
        picture = f"{name}.jpg"
    else:
        answer = client.predict(MODEL, "gwfss", row, **call)
        picture = f"{name}.jpg"
    image = answer.get("image")
    if not isinstance(image, dict) or image.get("picture") is None:
        raise RuntimeError(f"{MODEL} answered without a picture")
    encoded = io.BytesIO()
    image["picture"].convert("RGB").save(encoded, format="JPEG", quality=92)
    FIXTURE.mkdir(parents=True, exist_ok=True)
    (FIXTURE / picture).write_bytes(encoded.getvalue())
    return {
        "name": name,
        "model": answer.get("model"),
        "route": "generate" if row is None else "predict",
        "dataset": None if row is None else "gwfss",
        "row": row,
        "params": call,
        "picture": picture,
        "media_type": "image/jpeg",
        "provider_media_type": image.get("media_type"),
        "width": image.get("width"),
        "height": image.get("height"),
        "frame": image.get("frame"),
        "answer": answer.get("answer"),
    }


def main() -> int:
    """Record the four pictures, unless the fixture already exists.

    Returns
    -------
    int
        Zero on success, one when the key is missing.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="spend again even when the fixture already exists"
    )
    args = parser.parse_args()
    if (FIXTURE / "calls.json").exists() and not args.force:
        print(f"{FIXTURE.relative_to(REPO)} exists; pass --force to spend again")
        return 0
    if not os.environ.get(global_wheat.KEY_ENV):
        print(f"set {global_wheat.KEY_ENV} first", file=sys.stderr)
        return 1
    client = global_wheat.Client(timeout=180)
    calls = [
        record(client, "generate", GENERATE, row=None),
        record(client, "edit", EDIT, row=4),
        record(client, "mask", MASK, row=3),
        record(client, "detect", DETECT, row=3),
    ]
    FIXTURE.mkdir(parents=True, exist_ok=True)
    (FIXTURE / "calls.json").write_text(json.dumps(calls, indent=2) + "\n", encoding="utf-8")
    for entry in calls:
        print(
            f"{entry['name']}: {entry['width']}x{entry['height']} "
            f"{entry['picture']} ({entry['media_type']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
