// What moving the resize onto the participant's machine costs, measured rather than assumed.
//
// The failure this guards against is a client that spends seconds preparing an upload before it
// sends anything. The fixtures are a 4000×3000 JPEG and a 4000×3000 PNG — the JPEG exercises
// `sharp`'s shrink-on-load shortcut, the PNG is the slow path with no shortcut — and both are
// generated here, never committed. One warm-up, then the median of five, and a generous ceiling
// that catches "seconds" rather than a small regression. Skipped where `sharp` is not installed,
// because it is a devDependency of the checks rather than of the file a participant downloads.

import assert from "node:assert/strict";
import {test} from "node:test";

const WIDTH = 4000;
const HEIGHT = 3000;
const SIDE = 1008;
const RUNS = 5;

let node;
let sharp;
try {
  node = await import("../global-wheat.node.js");
  sharp = (await import("sharp")).default;
} catch {
  node = null;
}
const needs_sharp = node ? false : "sharp is not installed";

/**
 * A photograph's worth of pixels: deterministic noise, so a run can be repeated.
 *
 * Noise rather than one colour, because a flat image compresses to nothing and would make both the
 * decode and the encode cheaper than a real photograph's.
 * @param {number} width
 * @param {number} height
 * @returns {Buffer} RGB pixels, row-major.
 */
function photograph(width, height) {
  const pixels = Buffer.allocUnsafe(width * height * 3);
  let seed = 20261207;
  for (let index = 0; index < pixels.length; index += 1) {
    seed = (Math.imul(seed, 1103515245) + 12345) >>> 0;
    pixels[index] = seed >>> 24;
  }
  return pixels;
}

/**
 * Time one preparation five times after a warm-up, and report the middle one.
 *
 * @param {Function} run An async function that prepares one upload.
 * @returns {Promise<{median: number, output: *}>} Milliseconds and the last result.
 */
async function measure(run) {
  await run();
  const samples = [];
  let output;
  for (let index = 0; index < RUNS; index += 1) {
    const started = performance.now();
    output = await run();
    samples.push(performance.now() - started);
  }
  samples.sort((left, right) => left - right);
  return {median: samples[Math.floor(samples.length / 2)], output};
}

/**
 * Report one measurement in the test's own output, in ms and KB.
 *
 * @param {string} label The path measured.
 * @param {number} median Milliseconds.
 * @param {number} bytes The prepared upload's size.
 */
function report(label, median, bytes) {
  console.log(`${label}: ${median.toFixed(1)} ms, ${(bytes / 1024).toFixed(0)} KB`);
}

test(
  "preparing a 12 MP upload stays well under a second",
  {skip: needs_sharp, timeout: 120_000},
  async () => {
    const raw = {raw: {width: WIDTH, height: HEIGHT, channels: 3}};
    const jpeg = await sharp(photograph(WIDTH, HEIGHT), raw).jpeg({quality: 92}).toBuffer();
    const png = await sharp(photograph(WIDTH, HEIGHT), raw).png().toBuffer();

    const from_jpeg = await measure(() => node.resize(jpeg, SIDE));
    report("4000x3000 JPEG", from_jpeg.median, from_jpeg.output.length);
    assert.ok(from_jpeg.median < 200, `JPEG preparation took ${from_jpeg.median.toFixed(1)} ms`);

    const from_png = await measure(() => node.resize(png, SIDE));
    report("4000x3000 PNG ", from_png.median, from_png.output.length);
    assert.ok(from_png.median < 300, `PNG preparation took ${from_png.median.toFixed(1)} ms`);

    // The image is already at the model's side, so there must be no second encode at all.
    const fitted = from_jpeg.output;
    const pass = await measure(() => node.resize(fitted, SIDE));
    assert.equal(pass.output, null, "an image at the target is handed on untouched");
    report("already at target", pass.median, fitted.length);
    assert.ok(pass.median < 10, `pass-through took ${pass.median.toFixed(1)} ms`);
  },
);
