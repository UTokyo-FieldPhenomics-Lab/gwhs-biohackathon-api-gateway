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

import {ApiError, Client as Core, label_masks, rle_decode} from "./global-wheat.js";

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

/** A client that resizes an upload in the browser before it is sent. */
class Client extends Core {
  /**
   * @param {object} [options] The core's options. `resizer` defaults to the canvas resizer above.
   */
  constructor(options = {}) {
    super({...options, resizer: options.resizer ?? resize});
  }
}

const api = {ApiError, Client, label_masks, rle_decode, resize};

export {ApiError, Client, label_masks, rle_decode, resize};
export default api;

// A plain <script type="module" src="…/global-wheat.web.js"> has no import of its own, so the same
// names are left where a page can find them, with the browser client rather than the bare one.
globalThis.GlobalWheat = api;
