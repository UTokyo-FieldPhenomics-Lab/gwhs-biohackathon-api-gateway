"""Draw the documentation figures from the data the API actually serves.

    GLOBAL_WHEAT_API_KEY=... uv run --project docs --with pillow --with numpy --with requests \
        python docs/scripts/make_figures.py

Images are public downloads and carry no key; a model answer needs one. Both geometry decoders come
from the client. The figures land under `docs/content/static/images/`, where the pages reference
them by a relative path, so a page never depends on the CDN or a model being up to render.

The dataset figures use the publisher's recorded geometry in `fixtures/row-geometry.json`; the model
cards call the deployed models once per run; the SegFormer and SPROUT-L cards use recorded answers,
and the instance-dataset figure uses recorded geometry. The two Nano Banana pictures are a paid,
non-reproducible sample and come from `fixtures/nano-banana/`, recorded by
`record_nano_banana.py`; this script never calls that model. Regeneration is what keeps a figure
honest: change a fixture, a row or a model, and re-run.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.request import urlretrieve

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAGES = REPO / "docs/content"
ASSETS = PAGES / "static" / "images"
DATASETS = ASSETS / "datasets"
MODELS = ASSETS / "models"
PREDICTION = FIXTURES / "gwfss-row4-prediction.json"
SPROUT_PREDICTION = FIXTURES / "sprout-row4-prediction.json"
NANO_BANANA = FIXTURES / "nano-banana"
CLIENT_PYTHON = REPO / "clients/python"

sys.path.insert(0, str(CLIENT_PYTHON))
import global_wheat  # noqa: E402

DATA = "https://data.global-wheat.com"

FONT = "/usr/share/fonts/TTF/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"

BOX = (255, 45, 85)             # the box colour, in every figure that draws one
GRID = (120, 130, 145)
INK = (32, 38, 48)
PAPER = (255, 255, 255)

ROLE_GREY = (112, 122, 138)     # the small role tag above a panel title
CHAT_BG = (244, 246, 249)       # a chat card's canvas, a shade away from the page
CHAT_BUBBLE = (255, 255, 255)
CHAT_LINE = (206, 213, 223)

STROKES = (                     # one outline colour per object, cycled, on top of the category fills
    (255, 255, 255),
    (255, 0, 229),
    (0, 229, 255),
    (255, 140, 0),
    (255, 235, 59),
    (170, 120, 255),
)
HALO = (18, 18, 22)             # drawn under every stroke: green on green needs a dark edge to show
CATEGORY_COLOURS = {
    "spikelet_-_side1": (50, 255, 132),
    "spikelet_-_side2": (50, 132, 255),
    "spikelet_-_terminal": (214, 255, 50),
    "spikelet _-_underdeveloped_side1": (255, 138, 0),
    "peduncle": (255, 45, 85),
}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load the label font at a size."""
    return ImageFont.truetype(FONT_BOLD if bold else FONT, size)


def label(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, size: int = 20,
          fill: tuple[int, int, int] = INK, bold: bool = False, anchor: str = "la") -> None:
    """Draw one label with a white halo, so it reads over a photograph."""
    text_font = font(size, bold)
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
        draw.text((xy[0] + dx, xy[1] + dy), text, font=text_font, fill=(255, 255, 255), anchor=anchor)
    draw.text(xy, text, font=text_font, fill=fill, anchor=anchor)


def panel(image: Image.Image, width: int, height: int) -> Image.Image:
    """Resize into a fixed panel, letterboxed on white."""
    copy = image.copy()
    copy.thumbnail((width, height), Image.LANCZOS)
    canvas = Image.new("RGB", (width, height), PAPER)
    canvas.paste(copy, ((width - copy.width) // 2, (height - copy.height) // 2))
    return canvas


def figure(
    *panels: tuple[Image.Image, str] | tuple[Image.Image, str, str],
    gap: int = 16,
    pad: int = 16,
    columns: int | None = None,
) -> Image.Image:
    """Lay panels in a grid, each with an optional role tag and a title above it.

    Parameters
    ----------
    *panels : tuple
        ``(image, title)`` or ``(image, title, role)``. The role is a short uppercase tag drawn
        above the title — ``INPUT``, ``ANSWER``, ``REFERENCE``, ``DERIVED``, ``MODE``, ``MODEL`` or
        ``ONE CALL`` — so a reader can tell a multi-mode card from a request/answer/reference card
        without reading the prose.
    gap : int, optional
        Space between cells.
    pad : int, optional
        Margin around the figure.
    columns : int or None, optional
        Cells per row. ``None`` puts every panel in one row.

    Returns
    -------
    PIL.Image.Image
        The composed figure.
    """
    items: list[tuple[Image.Image, str, str | None]] = []
    for entry in panels:
        if len(entry) == 2:
            image, title = entry
            role = None
        else:
            image, title, role = entry
        items.append((image, title, role))
    if not items:
        raise ValueError("a figure needs at least one panel")
    columns = columns or len(items)
    rows = (len(items) + columns - 1) // columns
    cell_width = max(image.width for image, _, _ in items)
    cell_height = max(image.height for image, _, _ in items)
    header = 74 if any(role for _, _, role in items) else 56
    width = columns * cell_width + gap * (columns - 1) + pad * 2
    height = rows * (header + cell_height) + gap * (rows - 1) + pad * 2
    canvas = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(canvas)
    for index, (image, title, role) in enumerate(items):
        row, column = divmod(index, columns)
        x = pad + column * (cell_width + gap) + (cell_width - image.width) // 2
        y = pad + row * (header + cell_height + gap)
        if role:
            label(draw, (x + image.width // 2, y + 2), role, 15, ROLE_GREY, bold=True, anchor="ma")
            label(draw, (x + image.width // 2, y + 24), title, 22, INK, bold=True, anchor="ma")
            top = y + header
        else:
            label(draw, (x + image.width // 2, y + 2), title, 22, INK, bold=True, anchor="ma")
            top = y + header
        canvas.paste(image, (x, top))
        draw.rectangle([x, top, x + image.width - 1, top + image.height - 1], outline=GRID)
    return canvas


def save(image: Image.Image, path: Path, quality: int = 92) -> None:
    """Write a figure, choosing the format from its suffix."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix in (".jpg", ".jpeg"):
        image.save(path, quality=quality, optimize=True, progressive=True)
    else:
        image.save(path, optimize=True)
    print(f"{path.relative_to(REPO)}  {path.stat().st_size / 1024:.0f} KB  {image.size}")


def boundary(mask: np.ndarray, width: int = 2) -> np.ndarray:
    """The outline just outside a mask, widened so it survives the downscale a figure goes through.

    Each object carries its own stroke colour, so two neighbours of the same category stay tellable
    apart where their masks touch — which is the whole point of drawing the outlines at all.
    """
    grown = mask.copy()
    for _ in range(width):
        grown = (grown | np.roll(grown, 1, 0) | np.roll(grown, -1, 0)
                 | np.roll(grown, 1, 1) | np.roll(grown, -1, 1))
    grown[0, :] = grown[-1, :] = grown[:, 0] = grown[:, -1] = False     # np.roll wraps; drop the rim
    return grown & ~mask


def rle_mask(counts: str, height: int, width: int) -> np.ndarray:
    """Decode one object's geometry with the client, which is the only decoder there is."""
    return global_wheat.rle_decode(counts, (height, width))


def fit_image(image: Image.Image, max_width: int, max_height: int) -> Image.Image:
    """Shrink an image to fit a box, keeping its aspect ratio."""
    copy = image.copy()
    copy.thumbnail((max_width, max_height), Image.LANCZOS)
    return copy


def wrapped_lines(
    draw: ImageDraw.ImageDraw, text: str, text_font: ImageFont.FreeTypeFont, max_width: int
) -> list[str]:
    """Wrap one paragraph to a pixel width."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=text_font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    text_font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    max_width: int,
    line_height: int,
    max_lines: int | None = None,
) -> int:
    """Draw wrapped text and return the y below its last line."""
    lines = wrapped_lines(draw, text, text_font, max_width)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .,;:") + " …"
    for index, line in enumerate(lines):
        draw.text((xy[0], xy[1] + index * line_height), line, font=text_font, fill=fill)
    return xy[1] + len(lines) * line_height


def instance_overlay(
    image: Image.Image,
    objects: Sequence[Mapping[str, object]],
    fill: tuple[int, int, int] = (255, 45, 85),
    colours: Mapping[str, tuple[int, int, int]] | None = None,
) -> Image.Image:
    """Tint every decoded instance and outline it, so neighbours stay tellable apart.

    Parameters
    ----------
    image : PIL.Image.Image
        The photograph.
    objects : sequence of mapping
        The answer's objects, each with a decoded ``mask``.
    fill : tuple of int
        The colour for an object with no category colour.
    colours : mapping of str to tuple of int, optional
        A colour per category, where the answer has categories worth distinguishing.

    Returns
    -------
    PIL.Image.Image
        The photograph with each instance filled and outlined.
    """
    canvas = np.asarray(image).astype(np.float32)
    for order, obj in enumerate(objects):
        mask = obj.get("mask")
        if mask is None:
            continue
        colour = fill
        if colours is not None and obj.get("category") is not None:
            colour = colours.get(str(obj["category"]), fill)
        strength = 0.50 if order % 2 else 0.62
        tint = np.array(colour, dtype=np.float32)
        canvas[mask] = strength * tint + (1 - strength) * canvas[mask]
        canvas[boundary(mask, 3)] = np.array(HALO, dtype=np.float32)
        stroke = np.array(STROKES[order % len(STROKES)], dtype=np.float32)
        canvas[boundary(mask, 2)] = 0.10 * canvas[boundary(mask, 2)] + 0.90 * stroke
    return Image.fromarray(canvas.astype(np.uint8))


def density_overlay(image: Image.Image, grid: np.ndarray, boxes: Sequence[Sequence[float]]) -> Image.Image:
    """Lay a counting field over the photograph, with the exemplars that asked for it."""
    field = np.asarray(grid, dtype=np.float32)
    occupied = field[field > 0]
    scale = float(np.percentile(occupied, 99)) if occupied.size else 1.0
    density = np.clip(field / max(scale, 1e-6), 0, 1)
    heat = np.zeros((*density.shape, 4), dtype=np.uint8)
    heat[..., 0] = 255
    heat[..., 1] = (255 * (1 - density)).astype(np.uint8)
    heat[..., 3] = (255 * density).astype(np.uint8)
    layer = Image.fromarray(heat, "RGBA").resize(image.size, Image.Resampling.BILINEAR)
    over = Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")
    draw = ImageDraw.Draw(over)
    for box in boxes:
        x, y, width, height = (float(value) for value in box[:4])
        draw.rectangle([x, y, x + width, y + height], outline=(0, 110, 255), width=6)
    return over


def chat_panel(
    request_image: Image.Image | None,
    prompt: str,
    *,
    answer_text: str | None = None,
    answer_image: Image.Image | None = None,
    thought: str | None = None,
    width: int = 420,
) -> Image.Image:
    """Draw one request/answer card as a conversation, at twice its final size.

    Parameters
    ----------
    request_image : PIL.Image.Image or None
        The image the request carries, or ``None`` for a prompt-only request.
    prompt : str
        The text the request carries.
    answer_text : str or None
        Prose in the answer bubble.
    answer_image : PIL.Image.Image or None
        A picture in the answer bubble.
    thought : str or None
        A short reasoning line above the answer, for a mode that thinks first.
    width : int
        The final panel's side.

    Returns
    -------
    PIL.Image.Image
        A square chat card. It is one call drawn as a conversation, not a session.
    """
    scale = 2
    side = width * scale
    canvas = Image.new("RGB", (side, side), CHAT_BG)
    draw = ImageDraw.Draw(canvas)
    margin = 24
    label_font = font(22, True)
    text_font = font(28)
    draw.text((margin, 18), "REQUEST", font=label_font, fill=ROLE_GREY)

    top = 52
    if request_image is not None:
        text_x = margin + 16 + 150 + 18
        text_width = side - margin - 16 - text_x
        prompt_lines = wrapped_lines(draw, prompt, text_font, text_width)
        bubble_height = max(150 + 32, len(prompt_lines) * 34 + 32)
    else:
        text_x = margin + 16
        text_width = side - 2 * (margin + 16)
        prompt_lines = wrapped_lines(draw, prompt, text_font, text_width)
        bubble_height = max(94, len(prompt_lines) * 34 + 32)
    draw.rounded_rectangle(
        [margin, top, side - margin, top + bubble_height],
        radius=16, fill=CHAT_BUBBLE, outline=CHAT_LINE, width=2,
    )
    if request_image is not None:
        canvas.paste(panel(request_image, 150, 150), (margin + 16, top + 16))
    draw_wrapped(
        draw, (text_x, top + 16), prompt, text_font, INK, text_width, 34
    )

    bottom = top + bubble_height
    draw.text((margin, bottom + 22), "ANSWER", font=label_font, fill=ROLE_GREY)
    answer_top = bottom + 50
    draw.rounded_rectangle(
        [margin, answer_top, side - margin, side - margin],
        radius=16, fill=CHAT_BUBBLE, outline=CHAT_LINE, width=2,
    )
    inner_x = margin + 16
    inner_width = side - 2 * inner_x
    cursor = answer_top + 16
    if thought:
        draw.text((inner_x, cursor), "THINKING", font=font(20, True), fill=ROLE_GREY)
        cursor += 28
        cursor = draw_wrapped(
            draw, (inner_x, cursor), thought, font(24), GRID, inner_width, 30, max_lines=3
        )
        cursor += 8
    if answer_text:
        cursor = draw_wrapped(
            draw, (inner_x, cursor), answer_text, text_font, INK, inner_width, 36, max_lines=8
        )
    if answer_image is not None:
        available = side - margin - 16 - cursor
        picture = fit_image(answer_image, inner_width, max(available, 40))
        canvas.paste(picture, ((side - picture.width) // 2, cursor))
    if thought is None and answer_text is None and answer_image is None:
        draw.text((inner_x, cursor), "no answer", font=text_font, fill=GRID)
    return canvas.resize((width, width), Image.LANCZOS)


def download(work: Path) -> dict[str, Image.Image]:
    """Fetch the images the figures are drawn on, once per run."""
    sources = {
        "gwhd": f"{DATA}/gwhd/images/00000000.jpg",
        "gwhd4": f"{DATA}/gwhd/images/00000004.jpg",
        "gwfss": f"{DATA}/gwfss/images/00000000.png",
        "gwfss_mask": f"{DATA}/gwfss/masks/00000000.png",
        "gwfss4": f"{DATA}/gwfss/images/00000004.png",
        "gwfss4_mask": f"{DATA}/gwfss/masks/00000004.png",
        "instance": f"{DATA}/gwhs-temp/images/00000000.jpg",
        "potted": f"{DATA}/dev/potted-plant.jpg",
    }
    images = {}
    for name, url in sources.items():
        # Named after the figure, not after the object: two rows of one dataset would otherwise
        # collide on the same file name and the second would silently overwrite the first.
        path = work / f"{name}{Path(url).suffix}"
        urlretrieve(url, path)                                            # noqa: S310 (a fixed URL)
        images[name] = Image.open(path).convert("RGB")
    return images


def main() -> None:
    """Draw every figure from published images and live model answers."""
    client = global_wheat.Client(timeout=180)
    classes = client.describe("gwfss")["classes"]
    geometry = json.loads((FIXTURES / "row-geometry.json").read_text())
    boxes = geometry["gwhd_row0"]                    # 144 wheat heads, row 0 of gwhd
    objects = geometry["instance_row0"]["objects"]   # 24 objects, row 0 of the instance dataset
    rles = geometry["instance_row0"]["rles"]
    with tempfile.TemporaryDirectory() as work:
        images = download(Path(work))
        draw_gwhd(images["gwhd"], boxes)
        draw_bbox_diagram()
        draw_gwfss(images["gwfss"], images["gwfss_mask"], classes)
        draw_prediction(images["gwfss4"], images["gwfss4_mask"], classes)
        draw_sprout(images["gwfss4"], images["gwfss4_mask"], classes)
        draw_dinov3(images["gwfss4"], client)
        draw_sam31(images["gwhd4"], client)
        draw_tasselnet(images["gwhd4"], client)
        draw_wevisone(images["gwhd4"], client)
        draw_yolo(images["potted"], client)
        draw_internvl(images["gwfss4"], client)
        draw_nano_banana(images["gwfss4"])
        draw_instance(images["instance"], objects, rles)


def draw_gwhd(source: Image.Image, boxes: list[list[float]]) -> None:
    """The photograph with every published box on it, and the first one named."""
    side = 900                                    # boxes are drawn after the downscale, so they stay sharp
    scale = side / source.width
    photo = source.resize((side, side), Image.LANCZOS)
    draw = ImageDraw.Draw(photo)
    for _, x, y, w, h in boxes:
        draw.rectangle([x * scale, y * scale, (x + w) * scale, (y + h) * scale], outline=BOX, width=3)
    first = boxes[0]
    box = [first[1] * scale, first[2] * scale, first[3] * scale, first[4] * scale]
    draw.rectangle([box[0] - 5, box[1] - 5, box[0] + box[2] + 5, box[1] + box[3] + 5],
                   outline=(0, 90, 255), width=5)
    label(draw, (int(box[0]) - 4, int(box[1]) - 40), "object 0", 24, (0, 90, 255), bold=True)
    save(photo, DATASETS / "gwhd-row0-boxes.jpg", quality=84)


def draw_bbox_diagram() -> None:
    """The `[x, y, w, h]` convention, drawn: origin top left, y down, in image pixels."""
    template = Image.new("RGB", (900, 470), PAPER)
    draw = ImageDraw.Draw(template)
    draw.rectangle([140, 70, 860, 430], outline=GRID, width=2)
    draw.text((146, 44), "the image, in pixels, origin at its top left", font=font(20), fill=GRID)

    left, top, right, bottom = 420, 210, 800, 400
    draw.rectangle([left, top, right, bottom], outline=BOX, width=4)

    draw.line([left, 190, right, 190], fill=(0, 90, 255), width=3)          # the width, above the box
    for x in (left, right):
        draw.line([x, 182, x, 198], fill=(0, 90, 255), width=3)
    label(draw, (left + 40, 154), "w = 58 px", 22, (0, 90, 255), bold=True)

    draw.line([400, top, 400, bottom], fill=(0, 150, 90), width=3)          # the height, left of it
    for y in (top, bottom):
        draw.line([392, y, 408, y], fill=(0, 150, 90), width=3)
    label(draw, (390, (top + bottom) // 2), "h = 47", 22, (0, 150, 90), bold=True, anchor="rm")

    draw.ellipse([left - 9, top - 9, left + 9, top + 9], outline=INK, width=3)
    label(draw, (left + 18, top + 16), "(x, y) = (227, 3)", 22, INK, bold=True)   # clear of the w line

    text = '"bbox": [x, y, w, h]'                                           # the field itself
    text_font = font(26, True)
    width = draw.textlength(text, font=text_font)
    draw.rounded_rectangle([left + 30, top + 70, left + 60 + width, top + 128],
                           radius=10, fill=(248, 249, 251), outline=GRID, width=2)
    draw.text((left + 45, top + 84), text, font=text_font, fill=INK)
    save(template, DATASETS / "bbox-diagram.png")


def draw_gwfss(
    image: Image.Image, mask: Image.Image, classes: Sequence[Mapping[str, str]]
) -> None:
    """The semantic dataset: the image, the mask, and what one call returns for both."""
    save(image, DATASETS / "gwfss-row0-image.jpg")
    save(mask, DATASETS / "gwfss-row0-mask.png")

    mask_array = np.asarray(mask)
    labels = global_wheat.label_masks(mask_array, classes)
    tile_side, strip, gap = 420, 46, 14
    tiles = Image.new("RGB", (2 * tile_side + gap + 32, 2 * (tile_side + strip) + gap + 32), PAPER)
    for index, (name, boolean) in enumerate(labels.items()):
        cell = Image.new("RGB", (tile_side, tile_side + strip), PAPER)
        cell.paste(Image.fromarray(np.where(boolean, 255, 0).astype(np.uint8)).convert("RGB")
                   .resize((tile_side, tile_side), Image.NEAREST), (0, strip))
        cell_draw = ImageDraw.Draw(cell)
        cell_draw.text((8, 12), f"{name} — {int(boolean.sum()):,} px", font=font(22, True), fill=INK)
        cell_draw.rectangle([0, strip, tile_side - 1, strip + tile_side - 1], outline=GRID)
        if not boolean.any():
            cell_draw.text((tile_side // 2, strip + tile_side // 2), "no pixel of this class",
                           font=font(20), fill=(190, 197, 206), anchor="mm")
        tiles.paste(cell, (16 + (index % 2) * (tile_side + gap),
                           16 + (index // 2) * (tile_side + strip + gap)))
    save(tiles, DATASETS / "mask-output.png")
    save(mask, DATASETS / "mask-input.png")


def tint(base: np.ndarray, masks: Mapping[str, np.ndarray],
         colours: Mapping[str, tuple[int, int, int]]) -> Image.Image:
    """Lay a set of class masks over a photograph, keeping the photograph visible under them.

    The colour is mixed rather than painted, because the point of these figures is not that one
    picture is prettier — it is which pixels two answers disagree about, and an organ hidden under an
    opaque fill cannot be compared.

    Parameters
    ----------
    base : numpy.ndarray
        The photograph as `(height, width, 3)` float.
    masks : mapping of str to numpy.ndarray
        One boolean mask per class name.
    colours : mapping of str to tuple of int
        The RGB each class name is drawn in, from the dataset's own declaration.

    Returns
    -------
    PIL.Image.Image
        The tinted photograph.
    """
    canvas = base.copy()
    for name, mask in masks.items():
        colour = np.array(colours[name], dtype=np.float32)
        canvas[mask] = 0.55 * colour + 0.45 * base[mask]
    return Image.fromarray(canvas.astype(np.uint8))


def prediction_masks(body: Mapping[str, object]) -> dict[str, np.ndarray]:
    """Decode a recorded answer's objects into one boolean mask per class name."""
    return {
        str(obj["category"]): rle_mask(
            str(obj["rle"]["counts"]), int(obj["rle"]["size"][0]), int(obj["rle"]["size"][1])
        )
        for obj in body["objects"]
    }


def draw_semantic_card(
    image: Image.Image,
    prediction: Mapping[str, object],
    truth_mask: Image.Image,
    classes: Sequence[Mapping[str, str]],
    path: Path,
) -> None:
    """Draw a semantic model's card: the image, its classes, and the dataset's mask.

    The three panels carry their roles, so a reader does not mistake this request/answer/reference
    card for the multi-mode card on `sam3.1-meta`.
    """
    base = np.asarray(image).astype(np.float32)
    colours = {entry["name"]: ImageColor.getrgb(entry["color"]) for entry in classes}
    measured = global_wheat.label_masks(np.asarray(truth_mask), classes)
    measured.pop("background", None)
    save(
        figure(
            (panel(image, 420, 420), "the image", "INPUT"),
            (panel(tint(base, prediction_masks(prediction), colours), 420, 420),
             "the model's classes", "ANSWER"),
            (panel(tint(base, measured, colours), 420, 420),
             "the dataset's mask", "REFERENCE"),
        ),
        path,
    )


def draw_prediction(
    image: Image.Image, truth_mask: Image.Image, classes: Sequence[Mapping[str, str]]
) -> None:
    """The SegFormer's card on the row the demo uses."""
    prediction = json.loads(PREDICTION.read_text(encoding="utf-8"))
    draw_semantic_card(image, prediction, truth_mask, classes,
                       MODELS / "gwfss-row4-prediction.jpg")


def draw_sprout(
    image: Image.Image, truth_mask: Image.Image, classes: Sequence[Mapping[str, str]]
) -> None:
    """The SPROUT-L card, in the same layout and on the same row as the SegFormer's."""
    prediction = json.loads(SPROUT_PREDICTION.read_text(encoding="utf-8"))
    draw_semantic_card(image, prediction, truth_mask, classes,
                       MODELS / "sprout-row4-prediction.jpg")


def principal_colours(grid: np.ndarray) -> np.ndarray:
    """Project a patch grid onto its three leading directions, as an RGB picture.

    Parameters
    ----------
    grid : numpy.ndarray
        ``(rows, columns, dim)``, as the client decodes ``features``.

    Returns
    -------
    numpy.ndarray
        ``(rows, columns, 3)`` uint8. The directions are the grid's own, so the colours describe one
        picture and mean nothing on another.
    """
    rows, columns, dim = grid.shape
    flat = grid.reshape(-1, dim).astype(np.float32)
    centred = flat - flat.mean(axis=0, keepdims=True)
    _, _, directions = np.linalg.svd(centred, full_matrices=False)
    projected = centred @ directions[:3].T
    low = np.percentile(projected, 2, axis=0)
    high = np.percentile(projected, 98, axis=0)
    stretched = np.clip((projected - low) / np.maximum(high - low, 1e-6), 0, 1)
    return (stretched.reshape(rows, columns, 3) * 255).astype(np.uint8)


def draw_dinov3(image: Image.Image, client: global_wheat.Client) -> None:
    """A backbone's answer: the input and the patch grid projected onto its own directions."""
    answer = client.predict("dinov3-vitl16", "gwfss", 4, side=512)
    grid = answer["features"]["grid"]
    rows, columns = grid.shape[:2]
    colours = Image.fromarray(principal_colours(grid), "RGB").resize((420, 420), Image.LANCZOS)
    save(
        figure(
            (panel(image, 420, 420), "the image", "INPUT"),
            (colours, f"PCA of its {rows} × {columns} patch grid", "DERIVED"),
        ),
        MODELS / "dinov3-row4-features.jpg",
    )


def draw_sam31(image: Image.Image, client: global_wheat.Client) -> None:
    """The promptable segmenter's four prompt forms, the same image and the same target."""
    box = [459, 297, 86, 154, 1]
    point = [502, 374, 1]
    calls = [
        ("phrase \"seed head\"", {"prompt": "seed head"}, None),
        ("phrase + box", {"prompt": "seed head", "boxes": [box]}, "box"),
        ("box alone", {"boxes": [box]}, "box"),
        ("point alone", {"points": [point]}, "point"),
    ]
    panels = []
    for title, params, mark in calls:
        answer = client.predict(
            "sam3.1-meta", "gwhd", 4, confidence_threshold=0.31, **params
        )
        over = instance_overlay(image, answer["objects"], (255, 105, 180))
        cell = panel(over, 420, 420)
        draw = ImageDraw.Draw(cell)
        scale = min(cell.width / image.width, cell.height / image.height)
        offset_x = (cell.width - round(image.width * scale)) // 2
        offset_y = (cell.height - round(image.height * scale)) // 2
        if mark == "box":
            x, y, width, height = box[:4]
            draw.rectangle(
                [offset_x + x * scale, offset_y + y * scale,
                 offset_x + (x + width) * scale, offset_y + (y + height) * scale],
                outline=(0, 110, 255), width=4,
            )
        elif mark == "point":
            x = offset_x + point[0] * scale
            y = offset_y + point[1] * scale
            draw.ellipse([x - 10, y - 10, x + 10, y + 10], outline=(0, 110, 255), width=4)
            draw.line([x - 20, y, x + 20, y], fill=(0, 110, 255), width=3)
            draw.line([x, y - 20, x, y + 20], fill=(0, 110, 255), width=3)
            label(draw, (int(x) + 14, int(y) - 28), "point", 16, (0, 110, 255), bold=True)
        label(draw, (10, 8), f"{len(answer['objects'])} objects", 22, INK, bold=True)
        panels.append((cell, title, "MODE"))
    save(figure(*panels, columns=2), MODELS / "sam31-prompt-modes.jpg")


def draw_tasselnet(image: Image.Image, client: global_wheat.Client) -> None:
    """The counter's request — three exemplars — and the field it answers with."""
    boxes = [
        [306, 148, 55, 44, 1],
        [487, 313, 37, 70, 1],
        [933, 573, 58, 43, 1],
    ]
    answer = client.predict("tasselnetv4", "gwhd", 4, boxes=boxes)
    request = image.copy()
    draw = ImageDraw.Draw(request)
    for box in boxes:
        x, y, width, height = box[:4]
        draw.rectangle([x, y, x + width, y + height], outline=(0, 110, 255), width=6)
    field = density_overlay(
        image, np.asarray(answer["density"]["grid"], dtype=np.float32), boxes
    )
    save(
        figure(
            (panel(request, 420, 420), "the image and three exemplars", "REQUEST"),
            (panel(field, 420, 420), f"the density field — {answer['count']:.1f}", "ANSWER"),
        ),
        MODELS / "tasselnetv4-counting.jpg",
    )


def draw_wevisone(image: Image.Image, client: global_wheat.Client) -> None:
    """Open-vocabulary instances, from one phrase, with no score to threshold."""
    answer = client.predict("wevisone-vl-4b", "gwhd", 4, prompt="wheat head")
    over = instance_overlay(image, answer["objects"], (60, 200, 130))
    cell = panel(over, 420, 420)
    save(
        figure(
            (panel(image, 420, 420), "the image + \"wheat head\"", "REQUEST"),
            (cell, f"{len(answer['objects'])} instances · no score", "ANSWER"),
        ),
        MODELS / "wevisone-row4-instances.jpg",
    )


def draw_answer_boxes(
    cell: Image.Image, source: tuple[int, int], objects: Sequence[Mapping[str, object]]
) -> None:
    """Draw decoded boxes and their labels at the panel's scale, after the panel resize."""
    scale = min(cell.width / source[0], cell.height / source[1])
    drawn_w, drawn_h = round(source[0] * scale), round(source[1] * scale)
    offset_x, offset_y = (cell.width - drawn_w) // 2, (cell.height - drawn_h) // 2
    draw = ImageDraw.Draw(cell)
    for order, obj in enumerate(objects):
        bbox = obj.get("bbox")
        if not isinstance(bbox, Sequence) or len(bbox) < 4:
            continue
        x, y, width, height = (float(value) for value in bbox[:4])
        colour = ((255, 140, 0) if str(obj.get("category")) == "potted plant"
                  else STROKES[order % len(STROKES)])
        rectangle = [
            offset_x + x * scale,
            offset_y + y * scale,
            offset_x + (x + width) * scale,
            offset_y + (y + height) * scale,
        ]
        draw.rectangle(rectangle, outline=colour, width=4)
        score = obj.get("score")
        text = str(obj.get("category") or "object")
        if score is not None:
            text += f" {float(score):.2f}"
        label(draw, (int(rectangle[0]), max(2, int(rectangle[1]) - 24)),
              text, 16, colour, bold=True)


def draw_yolo(image: Image.Image, client: global_wheat.Client) -> None:
    """The same photograph through the detector and the instance-mask head."""
    prepared = image.copy()
    prepared.thumbnail((1024, 1024), Image.LANCZOS)
    payload = io.BytesIO()
    prepared.save(payload, format="JPEG", quality=90)
    bytes_ = payload.getvalue()
    detection = client.predict_file(
        "yolo26-ultralytics", bytes_, confidence_threshold=0.25, imgsz=1024
    )
    segmentation = client.predict_file(
        "yolo26-seg-ultralytics", bytes_, confidence_threshold=0.25, imgsz=1024
    )
    boxes = panel(prepared, 420, 420)
    draw_answer_boxes(boxes, prepared.size, detection["objects"])
    masks = instance_overlay(
        prepared, segmentation["objects"], (255, 140, 0), {"potted plant": (255, 140, 0)}
    )
    masks = panel(masks, 420, 420)
    draw_answer_boxes(masks, prepared.size, segmentation["objects"])
    save(
        figure(
            (boxes, "yolo26-ultralytics — boxes", "MODEL"),
            (masks, "yolo26-seg — boxes + masks", "MODEL"),
        ),
        MODELS / "yolo-detection-and-segmentation.jpg",
    )


def draw_internvl(image: Image.Image, client: global_wheat.Client) -> None:
    """The three modes as one-call chat cards: prose, an edit, and reason-then-draw."""
    describe_prompt = "What is in this picture? Answer in one sentence."
    edit_prompt = "The same heads under colder evening light."
    reason_prompt = "A single wheat spike against dark soil, morning light."
    described = client.predict(
        "internvl-u", "gwfss", 4, prompt=describe_prompt, mode="text"
    )
    edited = client.predict(
        "internvl-u", "gwfss", 4, prompt=edit_prompt, mode="image", size=768
    )
    reasoned = client.predict(
        "internvl-u", "gwfss", 4, prompt=reason_prompt, mode="text_image", size=768
    )
    save(
        figure(
            (chat_panel(image, describe_prompt, answer_text=described["answer"]),
             "mode=text", "ONE CALL"),
            (chat_panel(image, edit_prompt, answer_image=edited["image"]["picture"]),
             "mode=image", "ONE CALL"),
            (chat_panel(None, reason_prompt,
                        thought=reasoned.get("answer"),
                        answer_image=reasoned["image"]["picture"]),
             "mode=text_image", "ONE CALL"),
        ),
        MODELS / "internvl-u-answers.jpg",
        quality=94,
    )


def draw_nano_banana(image: Image.Image) -> None:
    """The hosted model's two routes, drawn from the pictures recorded once into a fixture."""
    calls = json.loads((NANO_BANANA / "calls.json").read_text(encoding="utf-8"))
    generation, edit = calls
    generated = Image.open(NANO_BANANA / generation["picture"]).convert("RGB")
    edited = Image.open(NANO_BANANA / edit["picture"]).convert("RGB")
    save(
        figure(
            (chat_panel(None, generation["params"]["prompt"], answer_image=generated),
             "generate", "ONE CALL"),
            (chat_panel(image, edit["params"]["prompt"], answer_image=edited),
             "edit", "ONE CALL"),
        ),
        MODELS / "nano-banana-generate-and-edit.jpg",
        quality=94,
    )


def draw_instance(image: Image.Image, objects: list[dict[str, float]],
                  rles: dict[str, dict[str, object]]) -> None:
    """An instance dataset's annotation: boxes on the image, then the decoded masks, then one object."""
    height, width = image.height, image.width
    grey = np.asarray(image.convert("L").convert("RGB")).astype(np.float32)
    tinted = grey.copy()
    for order, obj in enumerate(objects):
        mask = rle_mask(str(rles[str(int(obj["idx"]))]["counts"]), height, width)
        colour = np.array(CATEGORY_COLOURS.get(str(obj["category"]), (255, 255, 255)), dtype=np.float32)
        strength = 0.64 if order % 2 else 0.80                  # neighbours of one category still differ
        tinted[mask] = strength * colour + (1 - strength) * grey[mask]
        tinted[boundary(mask, 3)] = np.array(HALO, dtype=np.float32)          # the halo first
        stroke = np.array(STROKES[order % len(STROKES)], dtype=np.float32)
        tinted[boundary(mask, 2)] = 0.10 * grey[boundary(mask, 2)] + 0.90 * stroke
    tinted = Image.fromarray(tinted.astype(np.uint8))

    boxes_only = image.copy()
    draw = ImageDraw.Draw(boxes_only)
    for obj in objects:
        draw.rectangle([obj["x"], obj["y"], obj["x"] + obj["w"], obj["y"] + obj["h"]],
                       outline=CATEGORY_COLOURS.get(str(obj["category"]), (255, 255, 255)), width=2)

    shown = objects[:2]                                         # two neighbours, whole, not clipped
    pad = 30
    crop = (int(min(o["x"] for o in shown) - pad), int(min(o["y"] for o in shown) - pad),
            int(max(o["x"] + o["w"] for o in shown) + pad), int(max(o["y"] + o["h"] for o in shown) + pad))
    panel_size = (400, round(400 * (crop[3] - crop[1]) / (crop[2] - crop[0])))
    scale_to = (panel_size[0] * 2, panel_size[1] * 2)           # upscale first: strokes stay visible
    save(figure((panel(image.crop(crop).resize(scale_to, Image.LANCZOS), *panel_size), "the image"),
                (panel(boxes_only.crop(crop).resize(scale_to, Image.LANCZOS), *panel_size),
                 "objects as boxes"),
                (panel(tinted.crop(crop).resize(scale_to, Image.LANCZOS), *panel_size),
                 "objects as masks")),
         DATASETS / "gwhs-row0-annotation.jpg")

    obj = objects[0]
    decoded = rle_mask(str(rles["0"]["counts"]), height, width)
    box_x, box_y, box_w, box_h = (int(obj["x"]), int(obj["y"]), int(obj["w"]), int(obj["h"]))
    margin, zoom = 40, 3
    region = (box_x - margin, box_y - margin, box_x + box_w + margin, box_y + box_h + margin)
    size = ((region[2] - region[0]) * zoom, (region[3] - region[1]) * zoom)

    crop_image = image.crop(region).resize(size, Image.LANCZOS)
    draw = ImageDraw.Draw(crop_image)
    draw.rectangle([(margin - 2) * zoom, (margin - 2) * zoom,
                    (margin + box_w + 2) * zoom, (margin + box_h + 2) * zoom], outline=BOX, width=3)
    label(draw, (10, 8), '"bbox": [231, 923, 126, 162]', 22, BOX, bold=True)

    crop_grey = image.crop(region).convert("L").convert("RGB")
    overlaid = np.asarray(crop_grey).astype(np.float32)
    crop_mask = decoded[region[1]:region[3], region[0]:region[2]]
    colour = np.array((255, 45, 85), dtype=np.float32)
    overlaid[crop_mask] = 0.42 * colour + 0.58 * overlaid[crop_mask]        # translucent, so the ear shows
    overlay = Image.fromarray(overlaid.astype(np.uint8)).resize(size, Image.NEAREST)
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([(margin - 2) * zoom, (margin - 2) * zoom,
                    (margin + box_w + 2) * zoom, (margin + box_h + 2) * zoom], outline=BOX, width=3)
    label(draw, (10, 8), "1 object, 12,074 px", 22, PAPER, bold=True)

    flat = Image.fromarray((crop_mask * 255).astype(np.uint8)).convert("RGB").resize(size, Image.NEAREST)
    draw = ImageDraw.Draw(flat)
    draw.rectangle([(margin - 2) * zoom, (margin - 2) * zoom,
                    (margin + box_w + 2) * zoom, (margin + box_h + 2) * zoom], outline=BOX, width=3)
    label(draw, (10, 8), "1 object, 12,074 px", 22, PAPER, bold=True)

    save(figure((crop_image, "the object, in the image"),
                (flat, "the decoded mask"),
                (overlay, "the mask, on the image")),
         DATASETS / "rle-input-output.jpg")


if __name__ == "__main__":
    main()
