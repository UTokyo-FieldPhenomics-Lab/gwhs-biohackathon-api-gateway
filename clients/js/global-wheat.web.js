// Global Wheat API client, browser entry: the core plus a canvas, and nothing to install.
//
//   import {Client} from "https://docs.global-wheat.com/clients/global-wheat.web.js";
//   const client = new Client();                 // the key comes from GLOBAL_WHEAT_API_KEY, or pass it
//   const answer = await client.predict_file("sam3.1-meta", file, {prompt: "wheat head"});
//
// `global-wheat.js` beside this file is the core — the routes, the parameters, the decoders, and no
// image code. This file adds the one thing a browser can do that the core cannot: draw the
// photograph onto a canvas at the side the model runs at and encode it again as JPEG. An image
// already at or under that side is not touched at all. The page must be on the API's own origin;
// see the Clients page for why, and load this file as a module — a plain <script> cannot import.

import {ApiError, Client as Core, decode_features, label_masks, rle_decode} from "./global-wheat.js";

const QUALITY = 0.9;

/**
 * Shrink a photograph to at most `side` pixels on its long edge, upright, as JPEG.
 *
 * `createImageBitmap` applies the image's EXIF orientation by default (`imageOrientation:
 * "from-image"`), so a phone photo arrives the way it was taken rather than sideways.
 * @param {Blob|File|Uint8Array|ArrayBuffer} image
 * @param {number} side The long edge, in pixels.
 * @returns {Promise<?Blob>} The JPEG, or `null` where the image already fits and must not be
 *   re-encoded.
 */
async function resize(image, side) {
  const source = image instanceof Blob ? image : new Blob([image]);
  const bitmap = await createImageBitmap(source);
  try {
    const scale = side / Math.max(bitmap.width, bitmap.height);
    if (!(scale < 1)) return null;
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    return await encode(bitmap, width, height);
  } finally {
    bitmap.close?.();
  }
}

/**
 * Encode a bitmap at one size, through an `OffscreenCanvas` where there is one.
 * @param {ImageBitmap} bitmap
 * @param {number} width
 * @param {number} height
 * @returns {Promise<Blob>} A JPEG at `QUALITY`.
 */
async function encode(bitmap, width, height) {
  if (typeof OffscreenCanvas === "function") {
    const canvas = new OffscreenCanvas(width, height);
    canvas.getContext("2d").drawImage(bitmap, 0, 0, width, height);
    return canvas.convertToBlob({type: "image/jpeg", quality: QUALITY});
  }
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d").drawImage(bitmap, 0, 0, width, height);
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("the canvas produced no image"))),
      "image/jpeg",
      QUALITY,
    );
  });
}

/**
 * Cut a picture answer back to the frame of the picture it edits.
 *
 * The service pads the picture it sends when the model has no ratio that matches, and records where
 * the caller's picture sits as `frame.content`. This applies that record: a crop at the answer's
 * own resolution, not a resize. A box covering the whole frame returns the bytes untouched.
 * @param {Uint8Array} bytes The picture the model returned.
 * @param {string} mediaType Its type, as the answer declared it.
 * @param {object} frame `{width, height, content: [left, top, right, bottom]}`, in fractions.
 * @returns {Promise<Uint8Array>}
 */
async function crop(bytes, mediaType, frame) {
  const [left, top, right, bottom] = frame.content;
  if (left <= 0 && top <= 0 && right >= 1 && bottom >= 1) return bytes;
  const bitmap = await createImageBitmap(new Blob([bytes], {type: mediaType}));
  try {
    const x = Math.max(0, Math.floor(left * bitmap.width));
    const y = Math.max(0, Math.floor(top * bitmap.height));
    const width = Math.max(1, Math.min(bitmap.width, Math.ceil(right * bitmap.width)) - x);
    const height = Math.max(1, Math.min(bitmap.height, Math.ceil(bottom * bitmap.height)) - y);
    return await cut(bitmap, x, y, width, height);
  } finally {
    bitmap.close?.();
  }
}

/**
 * Encode one rectangle of a bitmap as PNG, through an `OffscreenCanvas` where there is one.
 * @param {ImageBitmap} bitmap
 * @param {number} x
 * @param {number} y
 * @param {number} width
 * @param {number} height
 * @returns {Promise<Uint8Array>}
 */
async function cut(bitmap, x, y, width, height) {
  if (typeof OffscreenCanvas === "function") {
    const canvas = new OffscreenCanvas(width, height);
    canvas.getContext("2d").drawImage(bitmap, x, y, width, height, 0, 0, width, height);
    const blob = await canvas.convertToBlob({type: "image/png"});
    return new Uint8Array(await blob.arrayBuffer());
  }
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d").drawImage(bitmap, x, y, width, height, 0, 0, width, height);
  const blob = await new Promise((resolve, reject) => {
    canvas.toBlob(
      (result) => (result ? resolve(result) : reject(new Error("the canvas produced no image"))),
      "image/png",
    );
  });
  return new Uint8Array(await blob.arrayBuffer());
}

/** A client that resizes an upload and crops a picture answer, in the browser. */
class Client extends Core {
  /**
   * @param {object} [options] The core's options. `resizer` and `framer` default to the canvas
   *   implementations above.
   */
  constructor(options = {}) {
    super({...options, resizer: options.resizer ?? resize, framer: options.framer ?? crop});
  }
}

const api = {ApiError, Client, crop, decode_features, label_masks, rle_decode, resize};

export {ApiError, Client, crop, decode_features, label_masks, rle_decode, resize};
export default api;

// A plain <script type="module" src="…/global-wheat.web.js"> has no import of its own, so the same
// names are left where a page can find them, with the browser client rather than the bare one.
globalThis.GlobalWheat = api;
