(() => {
  "use strict";
  // The key is a variable for as long as the page is open: typed into the page that uses it,
  // sent to this host and nowhere else, and written to no storage the browser keeps.
  let key = "";
  let fields = [];   // one entry per declared parameter: {name, read}

  const $ = (id) => document.getElementById(id);
  const model = $("model"), dataset = $("dataset"), parameters = $("parameters");
  const canvas = $("canvas"), figure = document.querySelector("#play figure");

  const say = (element, text, ok) => {
    element.textContent = text;
    element.classList.toggle("good", ok === true);
    element.classList.toggle("bad", ok === false);
  };

  // One call to this host. The page is served by the API, so a relative path is the API and no
  // CORS is involved anywhere. A refusal carries this contract's own message: a 400 from the
  // container is the validator speaking, and its line is the only thing that says which
  // parameter it refused.
  const call = async (path, options) => {
    const settings = Object.assign({}, options);
    settings.headers = Object.assign({"X-API-Key": key}, settings.headers);
    const answer = await fetch(path, settings);
    const body = await answer.json().catch(() => null);
    if (!answer.ok) {
      throw new Error(body && body.error ? body.error.message : "HTTP " + answer.status);
    }
    return body;
  };

  // One declared parameter, as the control its declaration implies. A number with both bounds is
  // a slider, a number with one bound or none is a number field, a boolean is a checkbox, and
  // anything else is a text field. Nothing here names a parameter: the page is built from the
  // declaration, so a model it has never heard of needs no change here — and an unmapped
  // parameter degrades to a text field rather than to nothing, because the backend is the
  // validator.
  const controlFor = (parameter) => {
    const value = parameter.default;
    const bounded = typeof parameter.minimum === "number" && typeof parameter.maximum === "number";
    const numeric = bounded || typeof value === "number";
    const input = document.createElement("input");
    if (numeric && bounded) {
      input.type = "range";
      input.min = String(parameter.minimum);
      input.max = String(parameter.maximum);
      input.step = "0.01";
      input.value = String(
        typeof value === "number" ? value : (parameter.minimum + parameter.maximum) / 2,
      );
    } else if (numeric) {
      input.type = "number";
      input.step = "any";
      if (typeof value === "number") input.value = String(value);
      if (typeof parameter.minimum === "number") input.min = String(parameter.minimum);
      if (typeof parameter.maximum === "number") input.max = String(parameter.maximum);
    } else if (typeof value === "boolean") {
      input.type = "checkbox";
      input.checked = value;
    } else {
      input.type = "text";
      if (value !== null && value !== undefined) input.value = String(value);
    }
    // A parameter the model declared no default for is sent only once the reader has touched
    // it. Sending nothing asks the model for its own default; an empty string is a value it
    // would have to refuse.
    if (value === null || value === undefined) input.dataset.untouched = "1";
    input.addEventListener("input", () => { delete input.dataset.untouched; });
    return input;
  };

  const readValue = (input) => {
    if (input.dataset.untouched) return null;
    if (input.type === "checkbox") return input.checked ? "true" : "false";
    const text = input.value.trim();
    return text === "" ? null : text;
  };

  const showParameters = (declaration) => {
    parameters.replaceChildren();
    fields = [];
    for (const parameter of declaration.parameters || []) {
      const label = document.createElement("label");
      const name = document.createElement("span");
      name.textContent = parameter.name;
      const input = controlFor(parameter);
      if (parameter.description) label.title = parameter.description;
      label.append(name, input);
      parameters.append(label);
      fields.push({name: parameter.name, read: () => readValue(input)});
    }
  };

  const query = () => {
    const search = new URLSearchParams();
    for (const field of fields) {
      const value = field.read();
      if (value !== null) search.set(field.name, value);
    }
    return search.toString();
  };

  const fill = (select, entries, label) => {
    select.replaceChildren();
    for (const entry of entries) {
      const option = document.createElement("option");
      option.value = entry.id;
      option.textContent = label(entry);
      select.append(option);
    }
  };

  const loadModel = async (id) => {
    const declaration = await call("/models/" + encodeURIComponent(id));
    showParameters(declaration);
    if (!declaration.backends_up) {
      // A reader who pressed Run would wait out a timeout to learn what this line already knows.
      say($("model-state"),
        "no backend is answering for " + id + " (backends_up: 0), so nothing can be run", false);
    } else {
      say($("model-state"),
        "runs at " + (declaration.resolution || "an undeclared resolution") + " px", true);
    }
    $("run").disabled = !declaration.backends_up;
  };

  // The image choice: one dropdown decides it, and the controls for the choice that is not taken are
  // hidden rather than left in the row looking available.
  const syncSource = () => {
    const upload = $("source").value === "upload";
    $("row-choice").hidden = upload;
    $("file").hidden = !upload;
    dataset.disabled = upload || !dataset.options.length;
    $("row").disabled = upload;
  };

  $("connect").addEventListener("submit", async (event) => {
    event.preventDefault();
    key = $("key").value.trim();
    if (!key) {
      say($("connect-state"), "paste your team's token first", false);
      return;
    }
    say($("connect-state"), "connecting…");
    try {
      // One call each, and nothing polls: the lists are read when the reader connects, and a
      // model's declaration again when they pick another model. A key that is refused says so
      // here, before the reader has filled anything else in.
      const directory = await call("/models");
      fill(model, directory.models, (entry) =>
        entry.id + " — " + entry.backends_up +
        (entry.backends_up === 1 ? " backend up" : " backends up"));
      model.disabled = !directory.models.length;
      const datasets = await call("/datasets");
      fill(dataset, datasets.datasets, (entry) => entry.id + " (" + entry.rows + " rows)");
      syncSource();
      if (directory.models.length) await loadModel(directory.models[0].id);
      else say($("model-state"), "this gateway has no models configured", false);
      say($("connect-state"), "connected — the key stays in this page", true);
    } catch (error) {
      say($("connect-state"), error.message, false);
    }
  });

  model.addEventListener("change", () => {
    loadModel(model.value).catch((error) => say($("model-state"), error.message, false));
  });

  $("source").addEventListener("change", syncSource);
  syncSource();

  // No `crossorigin`: `data.global-wheat.com` sends no `access-control-allow-origin` header, and an
  // image that asks for CORS there does not load at all. The price is that this canvas can never be
  // read back — which is why nothing here ever reads it.
  const loadImage = (url) => new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("the image did not load"));
    image.src = url;
  });

  // The canvas holds the image at close to its own size, so the mask's edge is the mask's edge rather
  // than a resampling artifact. An upload can be far larger than any published row, which is what the
  // cap is for: a 5184×3456 photograph would otherwise be 72 MB of canvas.
  const MAX_SIDE = 2600;
  const MASK = [255, 105, 180, 110];
  const BOX = "rgba(11, 107, 203, 0.9)";

  // One object's geometry as something drawable, without a single pixel being read back: the
  // mask goes into an offscreen canvas of its own size, which is then drawn onto the visible
  // one at the display size. `putImageData` ignores scaling, so the offscreen canvas is what
  // carries the mask, and the visible canvas is only ever written to.
  //
  // The decoded array is COCO's order — flat index `row + column * height`, quoted as "column-major"
  // — while `ImageData` is row-major. The index is therefore mapped rather than copied, or every mask
  // would be drawn transposed: a mask lying down the image instead of across it.
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

  const draw = async (url, answer) => {
    const image = await loadImage(url);
    const scale = Math.min(1, MAX_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    // The mask and the box are in the image's own pixels, so both are mapped to the canvas by the
    // same two ratios.
    const across = canvas.width / image.naturalWidth;
    const down = canvas.height / image.naturalHeight;

    const decoder = typeof rleDecode === "function";
    const objects = answer.objects || [];
    let decoded = 0, declared = 0, checked = 0;
    for (const object of objects) {
      if (object.rle && decoder) {
        const drawn = overlayFor(object);
        context.drawImage(drawn.overlay, 0, 0, canvas.width, canvas.height);
        decoded += drawn.painted;
        if (typeof object.area === "number") {
          declared += object.area;
          checked += 1;
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
    $("response-body").textContent = JSON.stringify(answer, null, 2);
    $("response").hidden = false;
    // Show the reader what the model found. The figure is a box as tall as it can be and a portrait
    // photograph is taller than that, so without this the object — and the whole canvas — sits below
    // the fold of a blank grey box, which reads as a model that found nothing.
    //
    // In two steps on purpose: `scrollTop` counts the CSS pixels the canvas is *drawn* at, which is
    // the box's width divided by the image's, while the mask's own coordinates are the image's. Using
    // the bitmap's scale here instead is the same number only when the two happen to agree.
    const found = objects.find((object) => object.bbox);
    if (found) {
      requestAnimationFrame(() => {
        const shown = canvas.clientHeight / image.naturalHeight;
        figure.scrollTop = Math.max(
          0, (found.bbox[1] + found.bbox[3] / 2) * shown - figure.clientHeight / 2,
        );
      });
    }

    if (!decoder) {
      // Said rather than left blank: an empty canvas would read as a model that found nothing.
      say($("check"),
        "the decoder did not load from the documentation site, so no mask is drawn here — " +
        "the response body below is complete", false);
    } else if (!checked) {
      say($("check"), objects.length + " objects, none carrying an area and an RLE to check", null);
    } else {
      // The cheapest check that a decoder is right, and the one the documentation makes in prose:
      // the decoded pixel count equals the published area. It reads the decoded array, never the
      // canvas, which is the only thing the tainted canvas allows.
      const equal = decoded === Math.round(declared);
      say($("check"),
        "Σ decoded mask = " + decoded.toLocaleString() + " px, Σ area = " +
        Math.round(declared).toLocaleString() + " px" + (equal ? " — equal" : " — they differ"),
        equal);
    }
  };

  $("run").addEventListener("click", async () => {
    const state = $("run-state");
    const upload = $("source").value === "upload";
    const file = $("file").files[0];
    if (upload && !file) {
      say(state, "choose a photograph first", false);
      return;
    }
    $("run").disabled = true;
    say(state, "running…");
    try {
      const chosen = encodeURIComponent(model.value);
      const search = query();
      let imageUrl, answer;
      if (upload) {
        // The container takes raw bytes, and the public route takes them as the multipart
        // field this contract declares; the gateway decodes that once and forwards the bytes.
        const form = new FormData();
        form.append("file", file);
        imageUrl = URL.createObjectURL(file);
        answer = await call("/models/" + chosen + "/predict/upload?" + search,
          {method: "POST", body: form});
      } else {
        const index = Number($("row").value);
        // The image the box and the mask sit on is addressed through the dataset surface, so
        // the URL is resolved by the API rather than assembled here — and it needs no key.
        const row = await call(
          "/datasets/" + encodeURIComponent(dataset.value) + "/images/" + index);
        imageUrl = row.image_url;
        answer = await call("/models/" + chosen + "/predict?" + search, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({dataset: dataset.value, row: index}),
        });
      }
      await draw(imageUrl, answer);
      const count = (answer.objects || []).length;
      say(state, count + (count === 1 ? " object" : " objects") + ", at " +
        (answer.resolution_used || "an undeclared resolution") + " px", true);
    } catch (error) {
      say(state, error.message, false);
    } finally {
      $("run").disabled = false;
    }
  });
})();
