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

import {ApiError, Client as Core, decode_features, label_masks, rle_decode} from "./global-wheat.js";

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

/**
 * Cut a picture answer back to the frame of the picture it edits.
 *
 * The service pads the picture it sends when the model has no ratio that matches, and records where
 * the caller's picture sits as `frame.content`. This applies that record with `sharp`: a crop at the
 * answer's own resolution, not a resize. A box covering the whole frame keeps the bytes untouched.
 * @param {Uint8Array} bytes The picture the model returned.
 * @param {string} mediaType Its type, as the answer declared it; `sharp` reads the bytes.
 * @param {object} frame `{width, height, content: [left, top, right, bottom]}`, in fractions.
 * @returns {Promise<Uint8Array>}
 */
async function crop(bytes, mediaType, frame) {
  const [left, top, right, bottom] = frame.content;
  if (left <= 0 && top <= 0 && right >= 1 && bottom >= 1) return bytes;
  const image = sharp(Buffer.from(bytes));
  const {width, height} = await image.metadata();
  if (!(width > 0 && height > 0)) return bytes;
  const x = Math.max(0, Math.floor(left * width));
  const y = Math.max(0, Math.floor(top * height));
  return image
    .extract({
      left: x,
      top: y,
      width: Math.max(1, Math.min(width, Math.ceil(right * width)) - x),
      height: Math.max(1, Math.min(height, Math.ceil(bottom * height)) - y),
    })
    .png()
    .toBuffer();
}

/** A client that resizes an upload and crops a picture answer, with `sharp`. */
class Client extends Core {
  /**
   * @param {object} [options] The core's options. `resizer` and `framer` default to the `sharp`
   *   implementations above.
   */
  constructor(options = {}) {
    super({...options, resizer: options.resizer ?? resize, framer: options.framer ?? crop});
  }
}

const api = {ApiError, Client, crop, decode_features, label_masks, rle_decode, resize};

export {ApiError, Client, crop, decode_features, label_masks, rle_decode, resize};
export default api;
