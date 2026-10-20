// The upload's size: which side the core asks for, the opt-out, and the two resizers.
//
// The core is checked against a stub transport and a stub resizer, which is all it knows about an
// image: the side it derives and the bytes it puts in FormData. The browser resizer is checked
// against a stubbed `createImageBitmap`/`OffscreenCanvas`; the Node one against `sharp`, and it is
// skipped where `sharp` is not installed, because that is a devDependency of these checks rather
// than of the file a participant downloads.

import assert from "node:assert/strict";
import {test} from "node:test";

import {Client} from "../global-wheat.js";
import {Client as WebClient, resize as web_resize} from "../global-wheat.web.js";

const SAM = {
  id: "sam3.1-meta",
  resolution: 1008,
  parameters: [{name: "imgsz", type: "enum", default: 1008, values: [672, 784, 896, 1008]}],
};
const SEGFORMER = {id: "gwfss-segformer-b4", resolution: 512, parameters: []};

/**
 * A transport that answers the declaration and the upload, and records both.
 *
 * @param {object} details The declarations this API serves, by model id; a missing one is a 404.
 * @returns {{fetch: Function, calls: object[]}}
 */
function fake(details) {
  const calls = [];
  const fetch = async (url, options) => {
    const parsed = new URL(String(url));
    calls.push({url: parsed, options});
    const path = parsed.pathname;
    if (path.endsWith("/predict/upload")) {
      return answer({model: "sam3.1-meta", objects: []});
    }
    const model = path.split("/").at(-1);
    if (details[model]) return answer(details[model]);
    return answer({error: {code: "not_found", message: "no such model"}}, 404);
  };
  return {fetch, calls};
}

/** One reply, in the shape the core reads. */
function answer(body, status = 200) {
  const text = JSON.stringify(body);
  return {
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => text,
    arrayBuffer: async () => new Uint8Array([0xff, 0xd8]).buffer,
  };
}

/**
 * A client whose resizer records the side it was asked for and returns a recognisable blob.
 *
 * @param {object} details The declarations the fake transport serves.
 * @param {Function|null} [output] What the resizer returns; `null` by default, meaning it declined.
 * @returns {{client: Client, calls: object[], sides: number[]}}
 */
function uploading(details, output = () => new Uint8Array([0x89, 0x50, 0x4e, 0x47])) {
  const transport = fake(details);
  const sides = [];
  const client = new Client({
    key: "t0ken",
    base: "https://api.test",
    fetch: transport.fetch,
    resizer: async (image, side) => {
      sides.push(side);
      return output(image, side);
    },
  });
  return {client, calls: transport.calls, sides};
}

/** The bytes of the blob an upload put in the `file` field. */
async function sent(call) {
  return [...new Uint8Array(await call.options.body.get("file").arrayBuffer())];
}

test("the target is the model's own side, and the image is resized to it", async () => {
  const {client, sides} = uploading({"sam3.1-meta": SAM});
  await client.predict_file("sam3.1-meta", new Uint8Array([1, 2, 3]));
  assert.deepEqual(sides, [1008]);
});

test("imgsz is capped by the side the model was run at", async () => {
  const {client, sides} = uploading({"sam3.1-meta": SAM});
  await client.predict_file("sam3.1-meta", new Uint8Array([1]), {imgsz: 504});
  await client.predict_file("sam3.1-meta", new Uint8Array([1]), {imgsz: 2000});
  assert.deepEqual(sides, [504, 1008]);
  assert.equal(sides.length, 2);
});

test("a model without imgsz is resized to its published resolution, not to 1008", async () => {
  const {client, sides} = uploading({"gwfss-segformer-b4": SEGFORMER});
  await client.predict_file("gwfss-segformer-b4", new Uint8Array([1]));
  await client.predict_file("gwfss-segformer-b4", new Uint8Array([1]), {imgsz: 800});
  assert.deepEqual(sides, [512, 512], "imgsz is not this model's parameter, so it cannot move the side");
});

test("the declaration is fetched once per client", async () => {
  const {client, calls} = uploading({"sam3.1-meta": SAM});
  await client.predict_file("sam3.1-meta", new Uint8Array([1]));
  await client.predict_file("sam3.1-meta", new Uint8Array([1]));
  const declarations = calls.filter((call) => /\/models\/sam3\.1-meta$/.test(call.url.pathname));
  assert.equal(declarations.length, 1);
});

test("no declaration means the original bytes, and no invented side", async () => {
  const {client, calls, sides} = uploading({});
  await client.predict_file("sam3.1-meta", new Uint8Array([0x89, 0x50]));
  assert.deepEqual(sides, []);
  assert.equal(calls.at(-1).url.pathname, "/models/sam3.1-meta/predict/upload");
  assert.deepEqual(await sent(calls.at(-1)), [0x89, 0x50]);
});

test("resizeBeforeSubmit false sends the original bytes and asks nothing", async () => {
  const {client, calls, sides} = uploading({"sam3.1-meta": SAM});
  const photo = new Uint8Array([0xff, 0xd8, 0xff, 0xe0]);
  await client.predict_file("sam3.1-meta", photo, {resizeBeforeSubmit: false, prompt: "head"});
  assert.deepEqual(sides, [], "the resizer is not consulted");
  assert.equal(calls.length, 1, "not even the declaration is fetched");
  assert.equal(calls[0].url.searchParams.get("prompt"), "head");
  assert.equal(calls[0].url.searchParams.has("resizeBeforeSubmit"), false);
  assert.deepEqual(await sent(calls[0]), [...photo]);
});

test("resizeBeforeSubmit is never forwarded as a model parameter", async () => {
  const {client, calls} = uploading({"sam3.1-meta": SAM});
  await client.predict_file("sam3.1-meta", new Uint8Array([1]), {resizeBeforeSubmit: true});
  assert.equal(calls.at(-1).url.search, "");
});

test("a resizer that declines leaves the original bytes untouched", async () => {
  const {client, calls} = uploading({"sam3.1-meta": SAM}, () => null);
  const photo = new Uint8Array([0xff, 0xd8, 0xff, 0xe0]);
  await client.predict_file("sam3.1-meta", photo);
  assert.deepEqual(await sent(calls.at(-1)), [...photo]);
});

test("a resizer that throws leaves the original bytes untouched", async () => {
  const {client, calls} = uploading({"sam3.1-meta": SAM}, async () => {
    throw new Error("no decoder for this format");
  });
  const photo = new Uint8Array([0xff, 0xd8, 0xff, 0xe0]);
  await client.predict_file("sam3.1-meta", photo);
  assert.deepEqual(await sent(calls.at(-1)), [...photo]);
});

test("the web resizer draws at the model's side and encodes a JPEG", async (t) => {
  const seen = stub_browser(t, {width: 4000, height: 3000});
  const blob = await web_resize(new Uint8Array([1, 2, 3]), 1008);
  assert.equal(blob.type, "image/jpeg");
  assert.deepEqual(seen.canvases, [[1008, 756]]);
  assert.deepEqual(seen.draws, [[0, 0, 1008, 756]]);
  assert.deepEqual(seen.blobs, [{type: "image/jpeg", quality: 0.9}]);
  assert.equal(seen.closed, true);
});

test("the web resizer leaves an image that already fits alone", async (t) => {
  const seen = stub_browser(t, {width: 500, height: 400});
  assert.equal(await web_resize(new Uint8Array([1]), 1008), null);
  assert.equal(seen.canvases.length, 0);
  assert.equal(seen.closed, true);
});

test("the web resizer falls back to a canvas where OffscreenCanvas is absent", async (t) => {
  const seen = stub_browser(t, {width: 4000, height: 3000, offscreen: false});
  const blob = await web_resize(new Uint8Array([1]), 512);
  assert.equal(blob.type, "image/jpeg");
  assert.deepEqual(seen.canvases, [[512, 384]]);
  assert.deepEqual(seen.toBlob, {type: "image/jpeg", quality: 0.9});
});

test("the web entry is the core client with a resizer", () => {
  const client = new WebClient({key: "t0ken", base: "https://api.test", fetch: async () => {}});
  assert.equal(client.resizer, web_resize);
});

/**
 * Stub the browser image surface for one test, and put the real one back afterwards.
 *
 * @param {object} t The test's context, whose `after` restores the globals.
 * @param {object} size The bitmap's own pixels.
 * @param {boolean} [size.offscreen] Whether `OffscreenCanvas` exists.
 * @returns {object} What the resizer asked for, for the assertions.
 */
function stub_browser(t, {width, height, offscreen = true}) {
  const real = {
    createImageBitmap: globalThis.createImageBitmap,
    OffscreenCanvas: globalThis.OffscreenCanvas,
    document: globalThis.document,
  };
  const seen = {canvases: [], draws: [], blobs: [], toBlob: null, closed: false};
  globalThis.createImageBitmap = async () => ({
    width,
    height,
    close() {
      seen.closed = true;
    },
  });
  if (offscreen) {
    globalThis.OffscreenCanvas = class {
      constructor(w, h) {
        seen.canvases.push([w, h]);
      }
      getContext() {
        return {drawImage: (_bitmap, x, y, w, h) => seen.draws.push([x, y, w, h])};
      }
      async convertToBlob(options) {
        seen.blobs.push(options);
        return new Blob([new Uint8Array(777)], {type: options.type});
      }
    };
  } else {
    globalThis.OffscreenCanvas = undefined;
    globalThis.document = {
      createElement: () => {
        const element = {width: 0, height: 0};
        element.getContext = () => ({
          drawImage: (_bitmap, x, y, w, h) => {
            seen.canvases.push([element.width, element.height]);
            seen.draws.push([x, y, w, h]);
          },
        });
        element.toBlob = (callback, type, quality) => {
          seen.toBlob = {type, quality};
          callback(new Blob([new Uint8Array(777)], {type}));
        };
        return element;
      },
    };
  }
  t.after(() => {
    globalThis.createImageBitmap = real.createImageBitmap;
    globalThis.OffscreenCanvas = real.OffscreenCanvas;
    globalThis.document = real.document;
  });
  return seen;
}

// The Node resizer is the one path that needs a dependency, so it runs only where that dependency
// is installed; the shipped file imports `sharp` and the checks do not install it for them.
let node = null;
try {
  node = await import("../global-wheat.node.js");
} catch {
  node = null;
}
const needs_sharp = node ? false : "sharp is not installed";

test("the node resizer returns a JPEG upright and no wider than the side", {skip: needs_sharp}, async () => {
  const sharp = (await import("sharp")).default;
  const source = await sharp({
    create: {width: 1200, height: 800, channels: 3, background: {r: 120, g: 160, b: 80}},
  })
    .withMetadata({orientation: 6})
    .jpeg()
    .toBuffer();
  const output = await node.resize(source, 1008);
  assert.ok(output, "an image over the side is resized");
  const {width, height, format, orientation} = await sharp(output).metadata();
  assert.equal(format, "jpeg");
  assert.equal(orientation, undefined, "the orientation is applied, not copied");
  assert.ok(height > width, "orientation 6 is portrait once it is upright");
  assert.equal(Math.max(width, height), 1008);
  assert.ok(Buffer.isBuffer(output));
});

test("the node resizer leaves an image at the side untouched", {skip: needs_sharp}, async () => {
  const sharp = (await import("sharp")).default;
  const small = await sharp({
    create: {width: 300, height: 200, channels: 3, background: {r: 10, g: 20, b: 30}},
  })
    .jpeg()
    .toBuffer();
  assert.equal(await node.resize(small, 1008), null);
  const fitted = await node.resize(small, 200);
  assert.ok(fitted);
  assert.equal(await node.resize(fitted, 200), null, "a second pass has nothing to do");
});

test("the node entry is the core client with the sharp resizer", {skip: needs_sharp}, () => {
  const client = new node.Client({key: "t0ken", base: "https://api.test", fetch: async () => {}});
  assert.equal(client.resizer, node.resize);
});
