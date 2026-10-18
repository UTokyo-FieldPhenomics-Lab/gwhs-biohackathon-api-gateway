// Global Wheat API client: one import, then datasets, models, and masks.
//
// The API is eight HTTP routes; this file is those eight routes as functions, plus the two decoders
// a result needs before it is usable. It carries **no response models**: every answer is the API's
// own JSON, so this file cannot disagree with the server about a field it does not restate. Only
// the routes are named here, and `clients/routes.json` beside this file is what keeps them honest
// against the gateway's OpenAPI document.
//
// Three things are done for the caller rather than by the caller:
//
//   * an address, not a URL — `image("gwhd", 0)` resolves `(dataset, row)` for you;
//   * the cursor — `iter_images` walks every page so a loop does not have to;
//   * the geometry — every object comes back with `mask` already decoded, so nothing has to know
//     what COCO's compressed RLE is.
//
// Uploading a photograph is `predict_file`, and loading a row's image is `image_bytes`:
//
//   import {Client} from "https://docs.global-wheat.com/clients/global-wheat.js";
//   const client = new Client();                 // the key comes from GLOBAL_WHEAT_API_KEY, or pass it
//   const row = await client.image("gwhd", 0);
//   ctx.putImageData(new ImageData(rgba(row.objects[0].mask), row.width), 0, 0);
//
// The file has no dependencies, and it is usable three ways: as an ES module, as a CommonJS
// require in Node, and as a plain <script> that leaves the same four names on `globalThis`.

const DEFAULT_BASE = "https://api.global-wheat.com";
const BASE_ENV = "GLOBAL_WHEAT_API";
const KEY_ENV = "GLOBAL_WHEAT_API_KEY";
const API_KEY_HEADER = "X-API-Key";
const DEFAULT_TIMEOUT = 60; // seconds; a predict route answers 504 long before this
const DEFAULT_PAGE_SIZE = 100; // the API's own default; its ceiling is 1000

/** One refusal, in the API's own terms. */
class ApiError extends Error {
  /**
   * @param {number} status The HTTP status the API answered with.
   * @param {string} code The machine-readable code from the error body.
   * @param {string} message The human-readable sentence the API wrote.
   */
  constructor(status, code, message) {
    super(`${status} ${code}: ${message}`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.message = message;
  }
}

/** One participant's connection to the API. */
class Client {
  /**
   * @param {object} [options]
   * @param {string} [options.key] The participant token. Defaults to `GLOBAL_WHEAT_API_KEY`.
   * @param {string} [options.base] The API's origin. Defaults to `GLOBAL_WHEAT_API`, then the public host.
   * @param {number} [options.timeout] Seconds to wait for an answer.
   * @param {Function} [options.fetch] A transport to send through, where one is already wrapped.
   */
  constructor({key, base, timeout = DEFAULT_TIMEOUT, fetch: transport} = {}) {
    const environment = globalThis.process?.env ?? {};
    this.key = key ?? environment[KEY_ENV] ?? "";
    if (!this.key) {
      throw new Error(`no API key: pass key or set ${KEY_ENV} in the environment`);
    }
    this.base = (base ?? environment[BASE_ENV] ?? DEFAULT_BASE).replace(/\/+$/, "");
    this.timeout = timeout;
    this._fetch = transport ?? globalThis.fetch;
    this._descriptions = new Map();
  }

  // -- the dataset surface ---------------------------------------------------

  /** List the datasets this API serves. @returns {Promise<object[]>} */
  async datasets() {
    return (await this.#call("GET", "/datasets")).datasets;
  }

  /**
   * Describe one dataset: provenance, splits, palette, and filter keys.
   * The answer is remembered, because the palette is what `label_masks` needs on every call.
   * @param {string} dataset
   * @returns {Promise<object>}
   */
  async describe(dataset) {
    if (!this._descriptions.has(dataset)) {
      this._descriptions.set(dataset, await this.#call("GET", `/datasets/${segment(dataset)}`));
    }
    return this._descriptions.get(dataset);
  }

  /**
   * Return one page of rows a filter selects.
   * Repeating a filter key narrows rather than widens, and an array is sent as repeated pairs.
   * @param {string} dataset
   * @param {object} [options] `limit`, `cursor`, and any filter pair such as `split: "train"`.
   * @returns {Promise<{dataset: string, count: number, next_cursor: ?string, images: object[]}>}
   */
  async images(dataset, {limit = DEFAULT_PAGE_SIZE, cursor, ...filters} = {}) {
    const params = {limit, ...defined(filters)};
    if (cursor) params.cursor = cursor;
    return this.#call("GET", `/datasets/${segment(dataset)}/images`, {params});
  }

  /**
   * Walk every row a filter selects, page by page.
   * @param {string} dataset
   * @param {object} [options] The same as `images`.
   * @returns {AsyncGenerator<object>} One row summary at a time, in row order.
   */
  async *iter_images(dataset, options = {}) {
    let cursor;
    for (;;) {
      const page = await this.images(dataset, {...options, cursor});
      yield* page.images;
      cursor = page.next_cursor;
      if (!cursor) return;
    }
  }

  /**
   * Return one row's whole annotation.
   * @param {string} dataset
   * @param {number} row The absolute row of the pinned upstream revision.
   * @param {object} [options] `decode: false` leaves the answer exactly as the API wrote it.
   * @returns {Promise<object>}
   */
  async image(dataset, row, {decode = true} = {}) {
    const body = await this.#call("GET", `/datasets/${segment(dataset)}/images/${row}`);
    if (decode) decode_objects(body);
    return body;
  }

  /** @returns {Promise<Uint8Array>} The image file, from the public `data.*` plane. */
  async image_bytes(dataset, row) {
    const detail = await this.image(dataset, row, {decode: false});
    return this.fetch(detail.image_url);
  }

  /**
   * The row's mask image, where the dataset ships one.
   * @returns {Promise<Uint8Array>}
   */
  async mask_bytes(dataset, row) {
    const detail = await this.image(dataset, row, {decode: false});
    if (!detail.mask_url) throw new Error(`${dataset} row ${row} publishes no mask`);
    return this.fetch(detail.mask_url);
  }

  /**
   * Fetch a public object by the URL a response carried.
   * Deliberately a plain request with no key: `data.*` is a public distribution, and sending the
   * participant token to a second host is how a token leaks into a CDN's logs.
   * @param {string} url An `image_url` or `mask_url` from a response.
   * @returns {Promise<Uint8Array>}
   */
  async fetch(url) {
    const response = await this._fetch(url, {signal: timeout_signal(this.timeout)});
    if (!response.ok) throw new ApiError(response.status, "not_found", `no object at ${url}`);
    return new Uint8Array(await response.arrayBuffer());
  }

  // -- the model surface -----------------------------------------------------

  /** List the models this API serves. @returns {Promise<object[]>} */
  async models() {
    return (await this.#call("GET", "/models")).models;
  }

  /** @returns {Promise<object>} One model, freshly: its side, its machines, its parameters. */
  async model(model) {
    return this.#call("GET", `/models/${segment(model)}`);
  }

  /**
   * Run one model on one published row.
   * @param {string} model The model id.
   * @param {string} dataset
   * @param {number} row The absolute row of the pinned revision; the gateway resolves the address.
   * @param {object} [options] `decode`, plus the model's own parameters —
   *   `{prompt: "wheat head", confidence_threshold: 0.5}`. `model(id).parameters` lists them. A
   *   geometry parameter is one value: `{boxes: [[480, 290, 110, 360, 1]]}` goes out as one JSON
   *   array in one parameter, never as five repeated names.
   * @returns {Promise<object>}
   */
  async predict(model, dataset, row, {decode = true, ...params} = {}) {
    const body = await this.#call("POST", `/models/${segment(model)}/predict`, {
      params: modelParams(params),
      json: {dataset, row},
    });
    if (decode) decode_objects(body);
    return body;
  }

  /**
   * Run one model on your own photograph.
   * @param {string} model The model id.
   * @param {Blob|File|Uint8Array|ArrayBuffer} image JPEG, PNG, WebP, TIFF or GIF, at most 15 MB;
   *   the API decides the type from the bytes rather than from a declared one.
   * @param {object} [options] `decode`, plus the model's own parameters, encoded as in `predict`.
   * @returns {Promise<object>}
   */
  async predict_file(model, image, {decode = true, ...params} = {}) {
    const form = new FormData();
    const name = image.name ?? "upload.jpg";
    form.append("file", image instanceof Blob ? image : new Blob([image]), name);
    const body = await this.#call("POST", `/models/${segment(model)}/predict/upload`, {
      params: modelParams(params),
      form,
    });
    if (decode) decode_objects(body);
    return body;
  }

  // -- geometry --------------------------------------------------------------

  /**
   * Split a palette mask into one mask per class, using the dataset's own palette.
   * @param {Uint8ClampedArray|Uint8Array} pixels RGBA or RGB pixels, as a canvas hands them back.
   * @param {number} width
   * @param {number} height
   * @param {string} dataset The dataset whose palette applies; fetched once and reused.
   * @returns {Promise<Object<string, Uint8Array>>} One mask per class, row-major.
   */
  async label_masks(pixels, width, height, dataset) {
    const classes = (await this.describe(dataset)).classes;
    if (!classes || !classes.length) throw new Error(`${dataset} publishes no palette`);
    return label_masks(pixels, width, height, classes);
  }

  // -- transport -------------------------------------------------------------

  async #call(method, path, {params, json, form} = {}) {
    const url = new URL(this.base + path);
    for (const [name, value] of Object.entries(params ?? {})) {
      for (const item of Array.isArray(value) ? value : [value]) {
        url.searchParams.append(name, String(item));
      }
    }
    const headers = {[API_KEY_HEADER]: this.key};
    let body;
    if (json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(json);
    } else if (form !== undefined) {
      body = form; // the browser writes the multipart boundary; never set Content-Type by hand
    }
    const response = await this._fetch(url.toString(), {
      method,
      headers,
      body,
      signal: timeout_signal(this.timeout),
    });
    if (!response.ok) throw await error(response);
    return response.json();
  }
}

/**
 * Decode a COCO compressed RLE into the mask it describes.
 * COCO flattens a mask column-major; the result here is row-major, so `data[y * width + x]` is the
 * pixel at `(x, y)` and the array goes straight into an `ImageData`.
 * @param {string} counts The `rle.counts` string, verbatim.
 * @param {number} height
 * @param {number} width
 * @returns {{data: Uint8Array, width: number, height: number}}
 */
function rle_decode(counts, height, width) {
  const runs = [];
  let value = 0;
  let shift = 0;
  for (let i = 0; i < counts.length; i++) {
    const chunk = counts.charCodeAt(i) - 48;
    value |= (chunk & 0x1f) << shift;
    shift += 5;
    if (chunk & 0x20) continue;
    if (chunk & 0x10) value |= -1 << shift; // the last chunk of a negative number is sign-extended
    runs.push(value + (runs.length > 2 ? runs[runs.length - 2] : 0));
    value = 0;
    shift = 0;
  }
  const pixels = height * width;
  const total = runs.reduce((sum, run) => sum + run, 0);
  if (total !== pixels) throw new Error(`the counts describe ${total} pixels, not ${pixels}`);

  // Column-major first, because that is the order the runs are written in, then one transpose.
  const column = new Uint8Array(pixels);
  let position = 0;
  for (let index = 0; index < runs.length; index++) {
    if (index % 2) column.fill(1, position, position + runs[index]);
    position += runs[index];
  }
  const data = new Uint8Array(pixels);
  for (let x = 0; x < width; x++) {
    for (let y = 0; y < height; y++) data[y * width + x] = column[x * height + y];
  }
  return {data, width, height};
}

/**
 * Split a palette mask into one mask per class.
 * @param {Uint8ClampedArray|Uint8Array} pixels RGB or RGBA pixels; a 4th channel is ignored.
 * @param {number} width
 * @param {number} height
 * @param {object[]} classes Palette entries with `name` and `color` (`#rrggbb`), exactly as a
 *   dataset's description publishes its `classes`.
 * @returns {Object<string, Uint8Array>} One mask per class, row-major.
 */
function label_masks(pixels, width, height, classes) {
  const channels = pixels.length === width * height * 3 ? 3 : 4;
  const masks = {};
  const known = new Uint8Array(width * height);
  for (const entry of classes) {
    const hex = entry.color.replace("#", "");
    const red = parseInt(hex.slice(0, 2), 16);
    const green = parseInt(hex.slice(2, 4), 16);
    const blue = parseInt(hex.slice(4, 6), 16);
    const mask = new Uint8Array(width * height);
    for (let i = 0; i < mask.length; i++) {
      if (
        pixels[i * channels] === red &&
        pixels[i * channels + 1] === green &&
        pixels[i * channels + 2] === blue
      ) {
        mask[i] = 1;
        known[i] = 1;
      }
    }
    masks[entry.name] = mask;
  }
  if (known.includes(0)) throw new Error("the mask holds a colour that is not in the palette");
  return masks;
}

/**
 * Attach each object's decoded `mask`, in place.
 * @param {object} body A prediction or an image detail, either of which may carry `objects`.
 */
function decode_objects(body) {
  for (const object of body.objects ?? []) {
    if (object.rle) object.mask = rle_decode(object.rle.counts, ...object.rle.size);
  }
}

/** Quote one path segment, so an id can never add a path of its own. */
function segment(value) {
  return encodeURIComponent(String(value));
}

/** Drop the options a caller left unset, so no empty parameter is sent. */
function defined(options) {
  return Object.fromEntries(Object.entries(options).filter(([, value]) => value != null));
}

/**
 * Encode a model's own parameters for the query string.
 *
 * A scalar is written as it is; an array or object becomes **one JSON value in one parameter**, which
 * is the shape a geometry parameter declares — `boxes` is `[[x, y, w, h, label], …]`, not five
 * `boxes` parameters. This is the model routes only: on the dataset routes a repeated name is the
 * contract (`?split=train&split=val` narrows a listing), and that is a different meaning.
 * @param {object} params
 * @returns {object} The same names, with values ready for the transport.
 */
function modelParams(params) {
  const encoded = {};
  for (const [name, value] of Object.entries(defined(params))) {
    encoded[name] = typeof value === "object" ? JSON.stringify(value) : String(value);
  }
  return encoded;
}

/** Abort a request that outlives the client's budget. */
function timeout_signal(seconds) {
  return AbortSignal.timeout(seconds * 1000);
}

/** Turn a refusal into an ApiError, keeping the API's own code and words. */
async function error(response) {
  const text = await response.text();
  let detail = {};
  try {
    detail = JSON.parse(text).error ?? {};
  } catch {
    detail = {};
  }
  const fallback = response.status >= 500 ? "internal_error" : "invalid_request";
  return new ApiError(response.status, detail.code ?? fallback, detail.message ?? text.trim());
}

const api = {ApiError, Client, label_masks, rle_decode};

export {ApiError, Client, label_masks, rle_decode};
export default api;

// A plain <script> has no import, so the same four names are left where the pages that use one
// decoder can find them. Loading this file as a module leaves the same object.
globalThis.GlobalWheat = api;
