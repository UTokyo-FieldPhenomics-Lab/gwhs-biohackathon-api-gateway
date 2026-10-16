// Replay one recorded model call on the Models page.
//
// The record is a committed file beside this script: the call that was made, the image it was made
// on, and the response exactly as the API answered. Nothing here invents anything — a model id, a
// prompt, or a threshold in this file would be a second place to keep true, so the panel names none
// of them and reads all of them from the record.
//
// The decoder is the site's own `post-processing/rle_decode.js`, loaded by the page before this
// script: the same file the Post-processing page tells a participant to download, so the demo and
// the tutorial cannot drift apart.
//
// Two facts shape the drawing. `data.global-wheat.com` sends no `access-control-allow-origin`
// header, so the image is loaded without `crossorigin` and the canvas is tainted: **no pixel may be
// read back**. The mask is therefore composited from an offscreen canvas rather than read off the
// visible one. And the check the page makes in prose — the decoded pixel count equals `area` — is
// counted on the decoded array, never on the canvas, which is the only thing a tainted canvas
// allows.
(() => {
  "use strict";
  const RECORD = "example-prediction.json";
  // Close to the image's own size, so a mask's edge is the mask's edge rather than a resampling
  // artifact; the cap is for very large images, where the canvas would otherwise be enormous.
  const MAX_SIDE = 2600;
  const MASK = [255, 105, 180, 110];
  const BOX = "rgba(11, 107, 203, 0.9)";

  const canvas = document.getElementById("demo-canvas");
  const figure = document.getElementById("demo-figure");
  const state = document.getElementById("demo-state");
  const check = document.getElementById("demo-check");
  if (!canvas || !state || !check || !figure) return;

  const say = (text, equal) => {
    check.textContent = text;
    check.dataset.equal = equal === undefined ? "" : equal ? "yes" : "no";
  };

  const loadImage = (url) => new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("the image did not load"));
    image.src = url;
  });

  // Put one object's mask into a canvas of its own size; the visible canvas is only ever written to.
  // `putImageData` ignores scaling, so the mask's own canvas is what carries it. The decoded array is
  // COCO's order — flat index `row + column * height` — while `ImageData` is row-major, so the index
  // is mapped rather than copied, or the mask would be drawn transposed.
  const overlayFor = (object) => {
    const [height, width] = object.rle.size;
    const mask = rleDecode(object.rle.counts, height, width);
    const pixels = new ImageData(width, height);
    let painted = 0;
    for (let index = 0; index < mask.length; index++) {
      if (!mask[index]) continue;
      const at = ((index % height) * width + Math.floor(index / height)) * 4;
      pixels.data[at] = MASK[0];
      pixels.data[at + 1] = MASK[1];
      pixels.data[at + 2] = MASK[2];
      pixels.data[at + 3] = MASK[3];
      painted += 1;
    }
    const offscreen = document.createElement("canvas");
    offscreen.width = width;
    offscreen.height = height;
    offscreen.getContext("2d").putImageData(pixels, 0, 0);
    return {overlay: offscreen, painted: painted};
  };

  const draw = async (record) => {
    const answer = record.response;
    const objects = answer.objects || [];
    const image = await loadImage(record.image_url);
    const scale = Math.min(1, MAX_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const across = canvas.width / image.naturalWidth;
    const down = canvas.height / image.naturalHeight;

    let decoded = 0, declared = 0, areas = 0;
    for (const object of objects) {
      if (object.rle) {
        const drawn = overlayFor(object);
        context.drawImage(drawn.overlay, 0, 0, canvas.width, canvas.height);
        decoded += drawn.painted;
        if (typeof object.area === "number") {
          declared += object.area;
          areas += 1;
        }
      }
      if (object.bbox) {
        context.lineWidth = 2;
        context.strokeStyle = BOX;
        context.strokeRect(
          object.bbox[0] * across, object.bbox[1] * down,
          object.bbox[2] * across, object.bbox[3] * down,
        );
      }
    }
    figure.hidden = false;
    // Show the reader what the model found: the figure is a box as tall as it can be, and a portrait
    // row is taller than that, so the object could otherwise sit below the fold of a blank box. The
    // next frame, because scrolling a box the browser has not laid out is clamped to zero; and the
    // CSS scale, because `scrollTop` counts the pixels the canvas is drawn at rather than the
    // image's own.
    const found = objects.find((object) => object.bbox);
    if (found) {
      requestAnimationFrame(() => {
        const shown = canvas.clientHeight / image.naturalHeight;
        figure.scrollTop = Math.max(
          0, (found.bbox[1] + found.bbox[3] / 2) * shown - figure.clientHeight / 2,
        );
      });
    }

    if (!areas) {
      say(objects.length + " objects, none carrying an area to check");
    } else {
      const equal = decoded === Math.round(declared);
      say(
        "Σ decoded mask = " + decoded.toLocaleString() + " px · Σ area = " +
        Math.round(declared).toLocaleString() + " px" + (equal ? " — equal" : " — they differ"),
        equal,
      );
    }
    return objects.length;
  };

  (async () => {
    if (typeof rleDecode !== "function") {
      // Said, never left blank: an empty canvas would read as a model that found nothing.
      state.textContent =
        "The RLE decoder did not load, so no mask is drawn here — try reloading the page.";
      return;
    }
    let record;
    try {
      const answer = await fetch(RECORD);
      if (answer.status === 404) {
        state.textContent =
          "The recorded call this panel replays is not in the repository yet. Run one from the " +
          "API's own page above in the meantime.";
        return;
      }
      if (!answer.ok) throw new Error("HTTP " + answer.status);
      record = await answer.json();
    } catch (error) {
      state.textContent = "The recorded call could not be read (" + error.message + ").";
      return;
    }

    const request = record.request || {};
    const parameters = Object.entries(request.parameters || {})
      .map(([name, value]) => name + " = " + value)
      .join(", ");
    state.textContent =
      "A recorded call: " + request.model + " on " + request.dataset + " row " + request.row +
      (parameters ? ", " + parameters : "") +
      (record.response.resolution_used ? ", at " + record.response.resolution_used + " px." : ".");

    try {
      const count = await draw(record);
      state.textContent +=
        " " + count + (count === 1 ? " object" : " objects") + " came back; the box and the mask " +
        "below are the model's own.";
    } catch (error) {
      state.textContent += " The image did not load (" + error.message + ").";
    }
  })();
})();
