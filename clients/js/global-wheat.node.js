// Global Wheat API client, Node entry: the core plus `sharp`.
//
//   import {Client} from "./global-wheat.node.js";
//   import {readFile} from "node:fs/promises";
//   const client = new Client();                 // the key comes from GLOBAL_WHEAT_API_KEY, or pass it
//   const photo = await readFile("photo.jpg");   // a Buffer is a Uint8Array, which is what it takes
//   const answer = await client.predict_file("sam3.1-meta", photo, {prompt: "wheat head"});
//
// `global-wheat.js` beside this file is the core — the routes, the parameters, the decoders, and no
// image code. This file adds the one thing Node can do that the core cannot: shrink the photograph
// to the side the model runs at, upright, as JPEG, with `sharp` (`npm i sharp`). An image already
// at or under that side is not touched at all. Node ships `FormData` and `Blob` from v18, so
// nothing else is needed.

import sharp from "sharp";

import {ApiError, Client as Core, label_masks, rle_decode} from "./global-wheat.js";

const QUALITY = 90;

/**
 * Shrink a photograph to at most `side` pixels on its long edge, upright, as JPEG.
 *
 * `.rotate()` with no argument applies the EXIF orientation, so a phone photo arrives the way it
 * was taken rather than sideways. `metadata` reads the header only, which is what makes the
 * already-small case cheap.
 * @param {Blob|File|Uint8Array|ArrayBuffer} image
 * @param {number} side The long edge, in pixels.
 * @returns {Promise<?Uint8Array>} The JPEG, or `null` where the image already fits and must not be
 *   re-encoded.
 */
async function resize(image, side) {
  const bytes = image instanceof Blob ? Buffer.from(await image.arrayBuffer()) : Buffer.from(image);
  const {width, height} = await sharp(bytes).metadata();
  // A rotated image has its stored sides swapped, but its long edge is the same either way.
  if (!(width > 0 && height > 0) || Math.max(width, height) <= side) return null;
  return sharp(bytes)
    .rotate()
    .resize({width: side, height: side, fit: "inside", withoutEnlargement: true})
    .jpeg({quality: QUALITY})
    .toBuffer();
}

/** A client that resizes an upload with `sharp` before it is sent. */
class Client extends Core {
  /**
   * @param {object} [options] The core's options. `resizer` defaults to the `sharp` resizer above.
   */
  constructor(options = {}) {
    super({...options, resizer: options.resizer ?? resize});
  }
}

const api = {ApiError, Client, label_masks, rle_decode, resize};

export {ApiError, Client, label_masks, rle_decode, resize};
export default api;
