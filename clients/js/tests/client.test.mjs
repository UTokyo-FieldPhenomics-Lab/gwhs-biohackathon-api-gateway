// The client's own behaviour: address building, paging, parameters, and refusals.
//
// Every call is answered by a stub transport, so what these check is the request this file builds
// and the way it reads an answer — not the server. The one check that needs the server is the
// Python live test, which is the same file written once.

import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";

import {ApiError, Client, label_masks, rle_decode} from "../global-wheat.js";

const EXAMPLE = new URL("../../../clients/fixtures/example-prediction.json", import.meta.url);
// A recorded call: eighteen objects of a 512x512 prediction, each with its area and its RLE.

function fake(replies) {
  const calls = [];
  const fetch = async (url, options) => {
    calls.push({url: String(url), options});
    const reply = replies.shift();
    const text = JSON.stringify(reply.body ?? {});
    return {
      ok: reply.status < 400,
      status: reply.status,
      json: async () => reply.body,
      text: async () => (reply.body === undefined ? "" : text),
      arrayBuffer: async () => new Uint8Array([0xff, 0xd8]).buffer,
    };
  };
  return {fetch, calls};
}

function client(replies) {
  const transport = fake(replies);
  return {client: new Client({key: "t0ken", base: "https://api.test", fetch: transport.fetch}), calls: transport.calls};
}

test("a client without a key says so instead of answering 401 later", () => {
  const saved = globalThis.process.env.GLOBAL_WHEAT_API_KEY;
  delete globalThis.process.env.GLOBAL_WHEAT_API_KEY;
  try {
    assert.throws(() => new Client({fetch: async () => {}}), /GLOBAL_WHEAT_API_KEY/);
  } finally {
    if (saved !== undefined) globalThis.process.env.GLOBAL_WHEAT_API_KEY = saved;
  }
});

test("datasets unwraps the directory and sends the key", async () => {
  const {client: api, calls} = client([{status: 200, body: {datasets: [{id: "gwhd"}]}}]);
  assert.deepEqual(await api.datasets(), [{id: "gwhd"}]);
  assert.equal(calls[0].url, "https://api.test/datasets");
  assert.equal(calls[0].options.headers["X-API-Key"], "t0ken");
});

test("iter_images follows the cursor and keeps the filters", async () => {
  const {client: api, calls} = client([
    {status: 200, body: {images: [{row: 1}, {row: 2}], next_cursor: "c1"}},
    {status: 200, body: {images: [{row: 3}], next_cursor: null}},
  ]);
  const rows = [];
  for await (const row of api.iter_images("gwhd", {split: "train", limit: 2})) rows.push(row.row);
  assert.deepEqual(rows, [1, 2, 3]);
  assert.match(calls[0].url, /limit=2/);
  assert.match(calls[0].url, /split=train/);
  assert.match(calls[1].url, /cursor=c1/);
});

test("a counting answer passes through untouched", async () => {
  const body = {
    model: "tasselnetv4",
    objects: [],
    count: 21.7,
    density: {size: [2, 3], values: [0, 1.5, 0, 2, 0, 18.2]},
    resolution_used: 384,
  };
  const {client: api} = client([{status: 200, body}]);
  const answer = await api.predict("tasselnetv4", "gwfss", 0, {boxes: [[1, 2, 3, 4, 1]]});
  assert.deepEqual(answer, body);
  assert.equal(answer.objects[0], undefined);
});

test("a refusal becomes an ApiError with the API's own words", async () => {
  const {client: api} = client([
    {status: 401, body: {error: {code: "invalid_key", message: "that key is not in the pool"}}},
  ]);
  await assert.rejects(api.models(), (caught) => {
    assert.ok(caught instanceof ApiError);
    assert.equal(caught.status, 401);
    assert.equal(caught.code, "invalid_key");
    assert.equal(caught.message, "that key is not in the pool");
    return true;
  });
});

test("a body outside the contract still raises", async () => {
  const {client: api} = client([{status: 502, body: undefined}]);
  await assert.rejects(api.models(), (caught) => {
    assert.equal(caught.status, 502);
    assert.equal(caught.code, "internal_error");
    return true;
  });
});

test("every recorded object decodes to its own area", () => {
  const prediction = JSON.parse(readFileSync(EXAMPLE, "utf8")).response;
  for (const object of prediction.objects) {
    const [height, width] = object.rle.size;
    const mask = rle_decode(object.rle.counts, height, width);
    const total = mask.data.reduce((sum, pixel) => sum + pixel, 0);
    assert.equal(total, object.area);
  }
});

test("an image comes back with row-major masks and keeps the raw RLE", async () => {
  const flat = [1, 0, 0, 0, 1, 0, 0, 0, 0];
  const counts = encode(flat);
  const {client: api} = client([
    {
      status: 200,
      body: {objects: [{idx: 0, category: "head", bbox: [0, 0, 1, 1], area: 2, rle: {size: [3, 3], counts}}]},
    },
  ]);
  const body = await api.image("gwhd", 0);
  const mask = body.objects[0].mask;
  assert.deepEqual([mask.width, mask.height], [3, 3]);
  assert.equal(mask.data[0], 1);
  assert.equal(mask.data[4], 1);
  assert.equal(mask.data.reduce((sum, pixel) => sum + pixel, 0), 2);
  assert.equal(body.objects[0].rle.counts, counts);
});

test("decode false keeps the answer as written", async () => {
  const {client: api} = client([{status: 200, body: {objects: [{idx: 0, rle: {size: [1, 1], counts: "1"}}]}}]);
  const body = await api.image("gwhd", 0, {decode: false});
  assert.equal(body.objects[0].mask, undefined);
});

test("predict sends the address in the body and the parameters in the query", async () => {
  const {client: api, calls} = client([{status: 200, body: {model: "sam3.1-meta", objects: []}}]);
  await api.predict("sam3.1-meta", "gwhs-temp", 7, {prompt: "wheat head", confidence_threshold: 0.4});
  assert.equal(calls[0].url.split("?")[0], "https://api.test/models/sam3.1-meta/predict");
  assert.match(calls[0].url, /prompt=wheat\+head/);
  assert.match(calls[0].url, /confidence_threshold=0.4/);
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.body, JSON.stringify({dataset: "gwhs-temp", row: 7}));
});

test("an upload sends the file as the field named file", async () => {
  const {client: api, calls} = client([{status: 200, body: {model: "sam3.1-meta", objects: []}}]);
  await api.predict_file("sam3.1-meta", new Uint8Array([0x89, 0x50]), {prompt: "head"});
  assert.match(calls[0].url, /\/predict\/upload\?prompt=head$/);
  const file = calls[0].options.body.get("file");
  assert.ok(file instanceof Blob);
  assert.equal(file.size, 2);
  assert.equal(calls[0].options.headers["Content-Type"], undefined); // the boundary is the runtime's
});

test("a geometry parameter is one JSON value in one parameter", async () => {
  const {client: api, calls} = client([{status: 200, body: {model: "sam3.1-meta", objects: []}}]);
  await api.predict("sam3.1-meta", "gwhs-temp", 7, {
    prompt: "wheat head",
    boxes: [[480, 290, 110, 360, 1]],
  });
  const url = new URL(calls[0].url);
  assert.equal(url.searchParams.get("prompt"), "wheat head");
  assert.equal(url.searchParams.get("boxes"), "[[480,290,110,360,1]]");
  assert.equal(url.searchParams.getAll("boxes").length, 1, "one parameter, not five");
});

test("an upload encodes a geometry parameter the same way", async () => {
  const {client: api, calls} = client([{status: 200, body: {model: "sam3.1-meta", objects: []}}]);
  await api.predict_file("sam3.1-meta", new Uint8Array([0x89, 0x50]), {
    points: [[256, 300, 1], [40, 40, 0]],
  });
  const url = new URL(calls[0].url);
  assert.equal(url.searchParams.get("points"), "[[256,300,1],[40,40,0]]");
});

test("a boolean is true or false", async () => {
  const {client: api, calls} = client([{status: 200, body: {model: "sam3.1-meta", objects: []}}]);
  await api.predict("sam3.1-meta", "gwhs-temp", 7, {recursive: true});
  assert.equal(new URL(calls[0].url).searchParams.get("recursive"), "true");
});

test("a dataset filter keeps its repeated names", async () => {
  const {client: api, calls} = client([{status: 200, body: {images: [], next_cursor: null}}]);
  await api.images("gwhd", {split: ["train", "val"]});
  const url = new URL(calls[0].url);
  assert.deepEqual(url.searchParams.getAll("split"), ["train", "val"]);
});

test("label_masks uses the dataset's palette and refuses an undeclared colour", async () => {
  const classes = [{id: 0, name: "head", color: "#32ff84"}];
  const {client: api, calls} = client([{status: 200, body: {dataset: "gwfss", classes}}]);
  const pixels = new Uint8Array([0x32, 0xff, 0x84, 255]);
  const masks = await api.label_masks(pixels, 1, 1, "gwfss");
  assert.equal(masks.head[0], 1);
  assert.equal(calls.length, 1);
  const unknown = new Uint8Array([1, 2, 3, 255]);
  await assert.rejects(api.label_masks(unknown, 1, 1, "gwfss"), /not in the palette/);
});

test("label_masks understands three-channel pixels too", () => {
  const masks = label_masks(new Uint8Array([0x32, 0xff, 0x84]), 1, 1, [
    {name: "head", color: "#32ff84"},
  ]);
  assert.equal(masks.head[0], 1);
});

// COCO's compression, written here as well as read, so the round trip is a claim about the
// algorithm rather than about a string that might have been edited.
function encode(flat) {
  const runs = [];
  let last = 0;
  let count = 0;
  for (const value of flat) {
    if (value === last) count += 1;
    else {
      runs.push(count);
      count = 1;
      last = value;
    }
  }
  runs.push(count);
  let out = "";
  runs.forEach((run, index) => {
    let value = run - (index > 2 ? runs[index - 2] : 0);
    let more = true;
    while (more) {
      let chunk = value & 0x1f;
      value >>= 5;
      more = chunk & 0x10 ? value !== -1 : value !== 0;
      if (more) chunk |= 0x20;
      out += String.fromCharCode(chunk + 48);
    }
  });
  return out;
}
