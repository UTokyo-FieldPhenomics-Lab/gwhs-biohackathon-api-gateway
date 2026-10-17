"""Draw the documentation figures from the data the API actually serves.

    uv run --project docs --with pillow --with numpy python docs/scripts/make_figures.py

Every figure is built from real published data: row 0 of `gwhd` and `gwfss`, and row 0 of the
instance dataset whose schema `gwhs` will publish. The images are fetched from
`https://data.global-wheat.com` (no key: the image plane is public) and the geometry — boxes, object
boxes, and their run-length strings — comes from `fixtures/row-geometry.json`, which was read out of
the publisher's index. The figures land in `docs/content/`, where the pages reference them by a
relative path, so a page never depends on the CDN being up to render.

Regenerating is what keeps a figure honest: change a fixture, or a row, and re-run. This lives outside
`docs/content/` because it is tooling, not a page.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from urllib.request import urlretrieve

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DATASETS = REPO / "docs/content/datasets"
CLIENTS = REPO / "docs/content/clients"
CLIENT_PYTHON = REPO / "clients/python"
PAGES = REPO / "docs/content"

DATA = "https://data.global-wheat.com"

FONT = "/usr/share/fonts/TTF/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"

BOX = (255, 45, 85)             # the box colour, in every figure that draws one
GRID = (120, 130, 145)
INK = (32, 38, 48)
PAPER = (255, 255, 255)

CLASS_COLOURS = {               # the palette `GET /datasets/gwfss` publishes
    "background": (0, 0, 0),
    "head": (50, 255, 132),
    "stem": (50, 132, 255),
    "leaf": (214, 255, 50),
}
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


def figure(*panels: tuple[Image.Image, str], gap: int = 16, pad: int = 16) -> Image.Image:
    """Lay panels side by side, each with a title above it, and return the figure."""
    width = sum(p.width for p, _ in panels) + gap * (len(panels) - 1) + pad * 2
    height = max(p.height for p, _ in panels) + 56 + pad * 2
    canvas = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(canvas)
    x = pad
    for image, title in panels:
        label(draw, (x + image.width // 2, pad + 2), title, 21, INK, bold=True, anchor="ma")
        canvas.paste(image, (x, pad + 44))
        draw.rectangle([x, pad + 44, x + image.width - 1, pad + 44 + image.height - 1], outline=GRID)
        x += image.width + gap
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
    sys.path.insert(0, str(CLIENT_PYTHON))
    from global_wheat import rle_decode

    return rle_decode(counts, (height, width))


def download(work: Path) -> dict[str, Image.Image]:
    """Fetch the images the figures are drawn on, once per run."""
    sources = {
        "gwhd": f"{DATA}/gwhd/images/00000000.jpg",
        "gwfss": f"{DATA}/gwfss/images/00000000.png",
        "gwfss_mask": f"{DATA}/gwfss/masks/00000000.png",
        "instance": f"{DATA}/gwhs-temp/images/00000000.jpg",
    }
    images = {}
    for name, url in sources.items():
        path = work / Path(url).name.replace("00000000", name)
        urlretrieve(url, path)                                            # noqa: S310 (a fixed URL)
        images[name] = Image.open(path).convert("RGB")
    return images


def main() -> None:
    geometry = json.loads((FIXTURES / "row-geometry.json").read_text())
    boxes = geometry["gwhd_row0"]                    # 144 wheat heads, row 0 of gwhd
    objects = geometry["instance_row0"]["objects"]   # 24 objects, row 0 of the instance dataset
    rles = geometry["instance_row0"]["rles"]
    with tempfile.TemporaryDirectory() as work:
        images = download(Path(work))
        draw_gwhd(images["gwhd"], boxes)
        draw_bbox_diagram()
        draw_gwfss(images["gwfss"], images["gwfss_mask"])
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


def draw_gwfss(image: Image.Image, mask: Image.Image) -> None:
    """The semantic dataset: the image, the mask, and what one call returns for both."""
    save(image, DATASETS / "gwfss-row0-image.jpg")
    save(mask, DATASETS / "gwfss-row0-mask.png")

    mask_array = np.asarray(mask)
    labels = {name: np.all(mask_array == np.array(colour, dtype=np.uint8), axis=-1)
              for name, colour in CLASS_COLOURS.items()}
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
    save(tiles, CLIENTS / "mask-output.png")
    save(mask, CLIENTS / "mask-input.png")


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
         CLIENTS / "rle-input-output.jpg")


if __name__ == "__main__":
    main()
