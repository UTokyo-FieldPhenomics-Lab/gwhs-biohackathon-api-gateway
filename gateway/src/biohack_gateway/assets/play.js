(() => {
  "use strict";
  // The key this page calls with. It is typed into the page that uses it and sent to this host and
  // nowhere else; the browser remembers it in the one store both this page and the reference share,
  // and nothing else on this page writes it anywhere.
  let key = "";
  let fields = [];   // one entry per declared parameter: {name, read}

  const $ = (id) => document.getElementById(id);
  const model = $("model"), dataset = $("dataset"), parameters = $("parameters");
  const canvas = $("canvas"), figure = document.querySelector("#play figure");
  const pane = $("pane"), stage = $("stage"), marks = $("marks");

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
    const choices = parameter.values || parameter.enum;
    let input;
    if (Array.isArray(choices) && choices.length) {
      // A declared set — the sizes a model was measured at, or the words it accepts — is a choice,
      // not a number to type. This is why `values` exists in the declaration: a stride formula would
      // invite a value nobody has run, and the page would have no way to show that.
      input = document.createElement("select");
      if (value === null || value === undefined) {
        const empty = document.createElement("option");
        empty.value = "";
        empty.textContent = "—";
        input.append(empty);
      }
      for (const choice of choices) {
        const option = document.createElement("option");
        option.value = String(choice);
        option.textContent = String(choice);
        input.append(option);
      }
      if (value !== null && value !== undefined) input.value = String(value);
    } else if (numeric && bounded) {
      input = document.createElement("input");
      input.type = "range";
      input.min = String(parameter.minimum);
      input.max = String(parameter.maximum);
      input.step = "0.01";
      input.value = String(
        typeof value === "number" ? value : (parameter.minimum + parameter.maximum) / 2,
      );
    } else if (numeric) {
      input = document.createElement("input");
      input.type = "number";
      input.step = "any";
      if (typeof value === "number") input.value = String(value);
      if (typeof parameter.minimum === "number") input.min = String(parameter.minimum);
      if (typeof parameter.maximum === "number") input.max = String(parameter.maximum);
    } else if (typeof value === "boolean") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = value;
    } else {
      input = document.createElement("input");
      input.type = "text";
      // A geometry parameter is JSON in one value, so the placeholder shows the shape rather than
      // describing it in a sentence the reader has to parse before typing.
      if (parameter.type === "boxes") input.placeholder = "[[x, y, w, h, 1]]";
      else if (parameter.type === "points") input.placeholder = "[[x, y, 1]]";
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

  // A slider says nothing about the number it is on. The declaration carries the bounds and the
  // default but no step, so the page chooses one — and the readout shows as many decimals as that
  // step has places, which is what makes `0.5` read as `0.50` beside a step of `0.01`. No parameter
  // name appears here either.
  const readoutFor = (input) => {
    const decimals = (input.step.split(".")[1] || "").length;
    const output = document.createElement("output");
    const show = () => { output.textContent = Number(input.value).toFixed(decimals); };
    input.addEventListener("input", show);
    show();
    return output;
  };

  const showParameters = (declaration) => {
    parameters.replaceChildren();
    const declared = declaration.parameters || [];
    // A model that declares nothing has no controls to label, and the row is then only the button.
    $("parameters-legend").hidden = !declared.length;
    fields = [];
    for (const parameter of declared) {
      const label = document.createElement("label");
      const name = document.createElement("span");
      name.textContent = parameter.name;
      const input = controlFor(parameter);
      if (parameter.description) label.title = parameter.description;
      label.append(name, input);
      if (input.type === "range") label.append(readoutFor(input));
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

  // The same call, as a command line, so a reader can take it to a shell. Three things shape it, and
  // each is the reason for something that would otherwise look like fuss:
  //
  //   * the origin is this page's own, which is the API's — the page is served by the host it calls,
  //     so no address is written down here and there is nothing to keep in step;
  //   * the key is left as `$KEY` and is never printed: a page that holds a secret must not put it in
  //     its own DOM, where a screenshot or a pasted command line would carry a team's token;
  //   * every value is single-quoted, because a parameter's value is whatever the model declared and
  //     whatever the reader typed, and a shell is not the place to find out that one held a quote.
  const shell = (text) => "'" + String(text).replace(/'/g, "'\\''") + "'";

  const requestText = () => {
    if (!model.value) return "";
    const upload = $("source").value === "upload";
    const search = query();
    const address = location.origin + "/models/" + encodeURIComponent(model.value);
    const lines = ["curl -s -X POST \\", '  -H "X-API-Key: $KEY" \\'];
    if (upload) {
      const chosen = $("file").files[0];
      lines.push("  -F " + shell("file=@" + (chosen ? chosen.name : "photo.jpg")) + " \\");
      lines.push("  " + shell(address + "/predict/upload" + (search ? "?" + search : "")));
    } else {
      lines.push('  -H "Content-Type: application/json" \\');
      lines.push("  -d " + shell(JSON.stringify({
        dataset: dataset.value, row: Number($("row").value),
      })) + " \\");
      lines.push("  " + shell(address + "/predict" + (search ? "?" + search : "")));
    }
    return lines.join("\n");
  };

  /** Show the call the controls currently describe, or hide the panel when they describe none. */
  const refreshRequest = () => {
    const text = requestText();
    $("request-body").textContent = text;
    $("request").hidden = !text;
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

  // The model's attributes, as its declaration and the last probe give them: what it runs at, how many
  // machines are answering, how many requests they hold — and how long ago that was asked. The count
  // is a probe's verdict, so its age is part of the claim, and it keeps growing on screen: a verdict
  // the page can no longer refresh must not be shown as if it were fresh. This is also the one place
  // that decides whether Run may be pressed, so the button and the line can never disagree.
  let facts = {text: "", bad: false, age: 0, at: 0};

  /** Forget what the model row said, so the one-second tick stops rewriting whatever replaces it. */
  const clearFacts = () => { facts = {text: "", bad: false, age: 0, at: 0}; };

  const renderFacts = () => {
    if (!facts.text) return;
    const seconds = facts.age + Math.max(0, Math.round((Date.now() - facts.at) / 1000));
    say($("model-state"), facts.text + " · checked " + seconds + " s ago", facts.bad ? false : null);
  };

  const showFacts = (entry) => {
    const parts = [];
    if (entry.resolution) parts.push("runs at " + entry.resolution + " px");
    if (entry.backends_up) {
      parts.push(entry.backends_up +
        (entry.backends_up === 1 ? " backend up" : " backends up"));
      if (entry.max_in_flight) {
        parts.push(entry.max_in_flight +
          (entry.max_in_flight === 1 ? " request at a time" : " requests at a time"));
      }
    } else {
      parts.push("no backend is answering, so nothing can be run");
    }
    facts = {
      text: parts.join(" · "),
      bad: !entry.backends_up,
      age: entry.checked_seconds_ago || 0,
      at: Date.now(),
    };
    renderFacts();
  };

  const loadModel = async (id) => {
    const declaration = await call("/models/" + encodeURIComponent(id));
    showParameters(declaration);
    showFacts(declaration);
    $("run").disabled = !declaration.backends_up;
    // Refreshed after the declaration and not before: the command carries the parameters this model
    // declares, and until the answer arrives those are still the previous model's.
    refreshRequest();
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

  // Connect with whatever key is in play: the one this browser holds, entered on the reference page
  // or here on an earlier visit. The store is shared with the reference, so one key serves both pages
  // and Forget on either clears it for both.
  const connect = async () => {
    say($("connect-state"), "connecting…");
    try {
      // One call each, and nothing polls: the lists are read when a key arrives, and a model's
      // declaration again when the reader picks another model. A key that is refused says so here,
      // before the reader has filled anything else in.
      //
      // The image comes first, in the fetching as on the page: it is read from the dataset surface,
      // needs no model, and is what everything after it is about — so it is listed and previewed
      // before a declaration is asked for, and a reader with no model answering still sees their image.
      const datasets = await call("/datasets");
      fill(dataset, datasets.datasets, (entry) =>
        entry.id + " (" + entry.rows + (entry.rows === 1 ? " row)" : " rows)"));
      syncSource();
      preview();
      const directory = await call("/models");
      fill(model, directory.models, (entry) => entry.id);
      model.disabled = !directory.models.length;
      if (directory.models.length) await loadModel(directory.models[0].id);
      else {
        clearFacts();
        say($("model-state"), "this gateway has no models configured", false);
      }
      say($("connect-state"), "connected", true);
    } catch (error) {
      say($("connect-state"), error.message, false);
    }
  };

  /** Go back to where the page started: every control on it was filled in with the key that has gone. */
  const startOver = () => {
    for (const id of ["model-state", "run-state", "check"]) say($(id), "");
    model.replaceChildren();
    model.disabled = true;
    dataset.replaceChildren();
    parameters.replaceChildren();
    fields = [];
    $("run").disabled = true;
    clearViewer();
    if (held) {
      URL.revokeObjectURL(held);
      held = "";
    }
    clearFacts();
    $("response").hidden = true;
    previewToken += 1;
    syncSource();
    refreshRequest();
  };

  $("connect").addEventListener("submit", (event) => {
    event.preventDefault();
    const typed = $("key").value.trim();
    if (!typed) {
      say($("connect-state"), "paste your team's token first", false);
      return;
    }
    // Set before the store announces, so this page's own watcher stays quiet and the call below is
    // the only one: the watcher is for changes made somewhere else.
    key = typed;
    apiKeyStore.set(typed);
    $("key").value = "";
    connect();
  });

  $("forget").addEventListener("click", () => {
    key = "";
    apiKeyStore.clear();
    $("key").value = "";
    startOver();
    say($("connect-state"), "no key — paste your team's token", false);
  });

  model.addEventListener("change", () => {
    loadModel(model.value).catch((error) => {
      // The row now holds a message the ticker did not write, so the tick has to stop writing over it.
      clearFacts();
      say($("model-state"), error.message, false);
    });
  });

  $("source").addEventListener("change", () => {
    syncSource();
    preview();
  });
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

  // The canvas bitmap holds the image at close to its own size, so the mask's edge is the mask's edge
  // rather than a resampling artifact — how large that is *displayed* is a separate matter and
  // belongs to the pane, which fits the whole canvas inside itself. An upload can be far larger than
  // any published row, which is what the cap is for: a 5184×3456 photograph would otherwise be 72 MB
  // of canvas.
  const MAX_SIDE = 2600;
  const MASK = [255, 105, 180, 110];

  // One object's geometry as something drawable, without a single pixel being read back: the
  // mask goes into an offscreen canvas of its own size, which is then drawn onto the visible
  // one at the display size. `putImageData` ignores scaling, so the offscreen canvas is what
  // carries the mask, and the visible canvas is only ever written to.
  //
  // The client decodes to a row-major array, which is `ImageData`'s own order, so a mask drawn
  // transposed is not a thing this page can do: the pixels are copied straight across.
  const overlayFor = (object) => {
    const [height, width] = object.rle.size;
    const {data} = GlobalWheat.rle_decode(object.rle.counts, height, width);
    const pixels = new ImageData(width, height);
    let painted = 0;
    for (let index = 0; index < data.length; index++) {
      if (!data[index]) continue;
      const at = index * 4;
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

  // The pane shows the whole image at once: the photograph is fitted inside a box of fixed height, so
  // a 2509-pixel-tall row is a narrow strip rather than four viewports with the object somewhere in
  // the middle of it, and a picture never has to be scrolled inside.
  //
  // The stage is sized here rather than in CSS because the marks are placed in the image's fractions:
  // a percentage of the pane would be a percentage of the empty band around the image as well. Only
  // the box is sized — the bitmap is left alone — so a resize re-fits without redrawing anything, and
  // the marks follow on their own.
  let fitted = null;
  const fit = (image) => {
    const box = pane.getBoundingClientRect();
    const scale = Math.min(box.width / image.naturalWidth, box.height / image.naturalHeight);
    stage.style.width = Math.max(1, Math.round(image.naturalWidth * scale)) + "px";
    stage.style.height = Math.max(1, Math.round(image.naturalHeight * scale)) + "px";
  };
  new ResizeObserver(() => { if (fitted) fit(fitted); }).observe(pane);

  // One object's box, and the score that goes with it, as elements over the raster rather than pixels
  // inside it: the pane draws a photograph at whatever size fits, so a score burned into the bitmap
  // would be a smudge on a tall strip and a billboard on a wide crop. As text it is the same size in
  // both, it is text a reader can select, and it carries the model's own number to two decimals.
  const markFor = (object, image) => {
    const [x, y, width, height] = object.bbox;
    const mark = document.createElement("div");
    mark.className = "mark";
    mark.style.left = (x / image.naturalWidth) * 100 + "%";
    mark.style.top = (y / image.naturalHeight) * 100 + "%";
    mark.style.width = (width / image.naturalWidth) * 100 + "%";
    mark.style.height = (height / image.naturalHeight) * 100 + "%";
    if (typeof object.score === "number") {
      const score = document.createElement("span");
      score.textContent = object.score.toFixed(2);
      mark.append(score);
      // A box against the top edge of the image has no room above it, so its score goes inside the
      // box — at the object's own top, which is where the label would have pointed.
      if (y / image.naturalHeight < 0.06) mark.classList.add("inside");
    }
    return mark;
  };

  /** Draw one image, with whatever objects belong on it, and say what was drawn. */
  const paint = async (url, objects) => {
    const image = await loadImage(url);
    const scale = Math.min(1, MAX_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0, canvas.width, canvas.height);

    const decoder = typeof GlobalWheat?.rle_decode === "function";
    let decoded = 0, declared = 0, checked = 0;
    for (const object of objects) {
      if (!object.rle || !decoder) continue;
      const drawn = overlayFor(object);
      context.drawImage(drawn.overlay, 0, 0, canvas.width, canvas.height);
      decoded += drawn.painted;
      if (typeof object.area === "number") {
        declared += object.area;
        checked += 1;
      }
    }

    figure.hidden = false;
    fit(image);
    fitted = image;
    marks.replaceChildren();
    for (const object of objects) {
      if (object.bbox) marks.append(markFor(object, image));
    }
    const full = $("full");
    full.href = url;
    full.hidden = false;
    return {
      decoder: decoder,
      decoded: decoded,
      declared: declared,
      checked: checked,
      // The image's own size, so a caption describes what was drawn rather than what an index said.
      width: image.naturalWidth,
      height: image.naturalHeight,
    };
  };

  const draw = async (url, answer) => {
    const objects = answer.objects || [];
    const painted = await paint(url, objects);
    showMachine(answer);
    $("response-body").textContent = JSON.stringify(answer, null, 2);
    $("response").hidden = false;

    const tally = objects.length + (objects.length === 1 ? " object" : " objects");
    if (!painted.decoder) {
      // Said rather than left blank: an empty canvas would read as a model that found nothing.
      say($("check"),
        "the decoder did not load from the documentation site, so no mask is drawn here — " +
        "the response body below is complete", false);
    } else if (!painted.checked) {
      say($("check"), tally + " · none carrying an area and an RLE to check", null);
    } else {
      // The cheapest check that a decoder is right, and the one the documentation makes in prose:
      // the decoded pixel count equals the published area. It reads the decoded array, never the
      // canvas, which is the only thing the tainted canvas allows.
      const equal = painted.decoded === Math.round(painted.declared);
      say($("check"),
        tally + " · Σ decoded mask = " + painted.decoded.toLocaleString() + " px, Σ area = " +
        Math.round(painted.declared).toLocaleString() + " px" +
        (equal ? " — equal" : " — they differ"),
        equal);
    }
  };

  /** Empty the viewer: nothing on screen is the picture the controls no longer name. */
  const clearViewer = () => {
    figure.hidden = true;
    marks.replaceChildren();
    $("full").hidden = true;
    fitted = null;
    $("check").textContent = "";
    $("check").className = "";
    showMachine(null);
  };

  // What computed the answer, in the machine's own words: the name it answers under, its card, the
  // CUDA build, the torch that ran the pass. A mask's boundary depends on the kernels that ran, so two
  // machines can disagree on one call — this is the fact that turns that into something a reader can
  // check. Every field is optional, because a container that has not been rebuilt still answers: the
  // line carries what arrived, and a record with nothing in it shows no chip at all rather than an
  // empty one.
  const describeMachine = (computed) => {
    if (!computed) return "";
    const parts = [];
    if (computed.machine) parts.push(computed.machine);
    if (computed.gpu) parts.push(computed.gpu);
    if (computed.cuda) parts.push("CUDA " + computed.cuda);
    if (computed.torch) parts.push("torch " + computed.torch);
    return parts.join(" · ");
  };

  const showMachine = (answer) => {
    const text = describeMachine(answer && answer.computed_by);
    const chip = $("watermark");
    chip.textContent = text;
    chip.hidden = !text;
  };

  // The image the reader has chosen, drawn as soon as the choice is a picture — an address is two
  // values and nothing else, and a file is local, so neither needs a button to find out whether it
  // was got right. A row that does not exist says so before a model is asked to fetch it, and a
  // source that names no picture yet clears the viewer rather than leaving the last row on screen.
  //
  // The token is what keeps a preview from overwriting a result: a preview in flight while Run is
  // pressed belongs to a choice the reader has moved on from. `held` is the object URL of the file
  // being shown, released when another picture replaces it — without that, every photograph a reader
  // tried stays in memory for as long as the page is open.
  let previewToken = 0;
  let held = "";

  const preview = async () => {
    if (!key) return;
    const upload = $("source").value === "upload";
    const file = $("file").files[0];
    if (upload && !file) {
      previewToken += 1;
      clearViewer();
      $("response").hidden = true;
      return;
    }
    if (!upload && !dataset.options.length) return;
    const token = ++previewToken;
    try {
      let url, caption;
      if (upload) {
        if (held) URL.revokeObjectURL(held);
        held = URL.createObjectURL(file);
        url = held;
        caption = "your photograph";
      } else {
        if (held) {
          URL.revokeObjectURL(held);
          held = "";
        }
        const row = await call(
          "/datasets/" + encodeURIComponent(dataset.value) + "/images/" + Number($("row").value));
        if (token !== previewToken) return;
        url = row.image_url;
        caption = row.dataset + " row " + row.row;
      }
      const painted = await paint(url, []);
      if (token !== previewToken) return;
      $("response").hidden = true;
      // A preview is a picture, not a result: there is no machine to name until something runs.
      showMachine(null);
      // The size comes from the image itself in both cases, so the caption describes what is drawn
      // rather than what an index or a file system claimed.
      say($("check"),
        caption + " — " + painted.width + "×" + painted.height +
        " px, press Run to segment it", null);
    } catch (error) {
      if (token !== previewToken) return;
      say($("check"), error.message, false);
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
    running = true;
    say(state, "running…");
    // A preview still in flight belongs to a choice this run has moved past, and must not be allowed
    // to draw over the result.
    previewToken += 1;
    try {
      const chosen = encodeURIComponent(model.value);
      const search = query();
      let imageUrl, answer;
      if (upload) {
        // The container takes raw bytes, and the public route takes them as the multipart
        // field this contract declares; the gateway decodes that once and forwards the bytes.
        const form = new FormData();
        form.append("file", file);
        // The run shows the same file the preview did, so the preview's object URL is released and
        // this one takes its place as the picture on screen.
        if (held) URL.revokeObjectURL(held);
        held = URL.createObjectURL(file);
        imageUrl = held;
        answer = await call("/models/" + chosen + "/predict/upload?" + search,
          {method: "POST", body: form});
      } else {
        const index = Number($("row").value);
        // The mask's boxes sit on the image, and the address resolves the same way a preview's did,
        // so it is read again here rather than remembered from one.
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
      // What came back is in the figure's own caption, beside the picture it describes, and the
      // resolution was said once beside the model: this line is for what is still happening, so it
      // goes back to empty now that nothing is.
      say(state, "");
    } catch (error) {
      say(state, error.message, false);
    } finally {
      running = false;
      // Back to what the last probe said rather than to "enabled": a run that finished while the pool
      // went empty must not leave a button that would answer 503.
      $("run").disabled = facts.bad;
    }
  });

  // The count is a probe's verdict, and a verdict ages: the page asks the directory again every few
  // seconds while it is on screen. One call, and only to the directory — never to a model's own
  // record, because re-reading a declaration would rebuild the controls under the reader's hands, and
  // because what a model serves does not change while the process serving it runs. A failed ask
  // changes nothing: the age keeps climbing on screen, which is what says the page has stopped being
  // told anything.
  const POLL_SECONDS = 10;
  let running = false;

  const poll = async () => {
    if (!key || !model.value || document.visibilityState !== "visible") return;
    try {
      const directory = await call("/models");
      const entry = directory.models.find((candidate) => candidate.id === model.value);
      if (!entry) return;
      showFacts(entry);
      $("run").disabled = running || !entry.backends_up;
    } catch (error) {
      // Silence on purpose: an error line here would replace the facts with a message about the page.
    }
  };

  // The age is rewritten by the second so it can never look fresher than it is, and the directory is
  // asked again every POLL_SECONDS so it usually does not have to. Both are local and cheap; neither
  // touches a backend.
  setInterval(renderFacts, 1000);
  setInterval(poll, POLL_SECONDS * 1000);

  // The address is two values, so it is worth previewing on either of them changing. The number field
  // also fires per keystroke, and "12" would otherwise fetch rows 1 and 12: the typing waits.
  let rowTimer = null;
  $("row").addEventListener("input", () => {
    clearTimeout(rowTimer);
    rowTimer = setTimeout(preview, 400);
  });
  $("row").addEventListener("change", preview);
  dataset.addEventListener("change", preview);
  // A chosen file is a picture like any other, so it is drawn as soon as it is chosen — and choosing
  // none clears the viewer instead of keeping the row the dropdown no longer names. Registered here,
  // beside the other triggers, because the handler is passed by value: naming `preview` above its own
  // declaration is a ReferenceError, and it kills the whole script rather than one listener.
  $("file").addEventListener("change", preview);

  // The command follows the controls, so a reader can set the call up on the page and then take it
  // away to a shell. Every control that decides what is sent refreshes it — and the parameters row is
  // listened to as a whole, because how many controls it holds is the model's business and not this
  // page's. The model is deliberately not in this list: the command waits for its declaration, so the
  // parameters in it are the ones that model declares rather than the ones the last one did.
  for (const id of ["source", "dataset", "row", "file"]) {
    $(id).addEventListener("input", refreshRequest);
    $(id).addEventListener("change", refreshRequest);
  }
  parameters.addEventListener("input", refreshRequest);

  // A key this browser already holds — entered on the reference page, or here on an earlier visit —
  // is used without being typed again: one key, one place, and both pages start on the same one.
  apiKeyStore.onChange((value) => {
    if (value === key) return; // this page's own change, already acted on
    key = value;
    if (value) connect();
    else startOver();
  });
  const stored = apiKeyStore.get();
  if (stored) {
    key = stored;
    connect();
  } else {
    say($("connect-state"), "no key — paste your team's token", false);
  }
})();
