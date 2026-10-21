"""Documentation figures must reuse the client's palette decoder and declaration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageFont

import global_wheat


@pytest.fixture
def figures(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load figure tooling without writing images or requiring system fonts."""
    path = Path(__file__).resolve().parents[3] / "docs/scripts/make_figures.py"
    spec = importlib.util.spec_from_file_location("make_figures", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "save", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "font", lambda *args, **kwargs: ImageFont.load_default())
    return module


def test_palette_figures_use_the_client_and_supplied_colours(
    figures: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both figure paths decode a changed palette through the existing client function."""
    classes = [
        {"name": "background", "color": "#ffffff"},
        {"name": "head", "color": "#010203"},
        {"name": "stem", "color": "#040506"},
        {"name": "leaf", "color": "#070809"},
    ]
    calls: list[Any] = []
    decode = global_wheat.label_masks

    def counted(pixels: np.ndarray, palette: Any) -> dict[str, np.ndarray]:
        """Record the declaration passed to the public decoder."""
        calls.append(palette)
        return decode(pixels, palette)

    monkeypatch.setattr(global_wheat, "label_masks", counted)
    image = Image.new("RGB", (2, 2), "white")
    mask = Image.new("RGB", (2, 2), "#010203")
    monkeypatch.setattr(figures, "rle_mask", lambda *args: np.ones((2, 2), dtype=bool))

    figures.draw_gwfss(image, mask, classes)
    figures.draw_prediction(image, mask, classes)

    assert calls == [classes, classes]
