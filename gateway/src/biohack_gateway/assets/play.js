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

  // What the picture shows first, per model. This is the one model-specific thing the page carries,
  // and it is presentation only: it decides nothing about what is sent, and an id the table does not
  // name — a renamed model, or one added after this line — falls back to the base view. A semantic
  // model answers with class extents that cover most of the frame, so its boxes open hidden and its
  // legend, which is where the class names and scores then live, opens shown.
  //
  // The order is the order a reader builds the picture in: the photograph, the model's pixel layer
  // over it, the rectangle geometry on top, then the two annotations the page adds.
  const VIEW_BASE = {image: true, masks: true, boxes: true, legend: false, watermark: true};
  const VIEW_BY_MODEL = {
    "gwfss-segformer-b4": {boxes: false, legend: true},
  };
  let view = {...VIEW_BASE};

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

  // Whether the picture's boxes answer to the mouse. It is a switch rather than a lock on the input:
  // a reader who is typing coordinates still types, and the picture simply stops competing for the
  // press. Only a model that declares a `boxes` parameter has anything to switch.
  const showMode = () => {
    const group = $("boxes-mode");
    group.hidden = !boxInput;
    if (!boxInput) return;
    for (const button of group.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String((button.dataset.mode === "edit") === boxesEditable));
    }
    stage.classList.toggle("drawing", boxesEditable);
  };

  const showParameters = (declaration) => {
    parameters.replaceChildren();
    // A declaration replaces the controls, and with them the picture's editor: a model without a
    // `boxes` parameter has nothing to draw, while the list itself stays for the next one that has.
    boxInput = null;
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
      // A `boxes` parameter is a list the picture can be drawn on: the input and the overlay are
      // two views of one list, so both directions are wired here and nowhere else.
      if (parameter.type === "boxes") {
        boxInput = input;
        if (!input.value && boxes.length) {
          input.value = JSON.stringify(boxes);
          delete input.dataset.untouched;   // a list restored here is the reader's, so it is sent
        }
        syncBoxes();
        input.addEventListener("input", syncBoxes);
      }
    }
    showMode();
    // The overlay follows the editor: a model that takes no boxes must not leave another model's
    // boxes drawn on the picture it is about to replace.
    showBoxes();
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
      if ($("resize").checked) {
        lines.unshift(
          "# the page resizes to " + RESIZE_SIDE + " px before sending; this command sends the file as it is",
        );
      }
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

  // The pool, as the last probe found it: how many machines are answering, how many requests they
  // hold — and how long ago that was asked. The count is a probe's verdict, so its age is part of
  // the claim, and it keeps growing on screen: a verdict the page can no longer refresh must not be
  // shown as if it were fresh. This is also the one place that decides whether Run may be pressed,
  // so the button and the line can never disagree.
  //
  // The side the model runs at is not repeated here. When it is a choice it is a control one row
  // below, and when it is not, the answer's `resolution_used` is the fact; repeating it in prose is
  // the second place a reader has to reconcile, which is why the resolution left this line before.
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
    // Applied with the declaration, so the eye buttons describe the answer that is about to be asked
    // for. Nothing on screen is redrawn here: the picture there belongs to the previous answer.
    view = {...VIEW_BASE, ...(VIEW_BY_MODEL[id] || {})};
    // The switch follows the layer: a model whose boxes open hidden also opens locked, because a drag
    // that draws into a hidden layer is a gesture with no visible result.
    if (!view.boxes) boxesEditable = false;
    showView();
    showParameters(declaration);
    showFacts(declaration);
    $("run").disabled = !declaration.backends_up;
    // Refreshed after the declaration and not before: the command carries the parameters this model
    // declares, and until the answer arrives those are still the previous model's.
    refreshRequest();
  };

  // The eye buttons are the view's only controls: pressed while the layer they name is shown. Nothing
  // here knows a model; the defaults arrived with the declaration, and a click is the reader's say.
  const showView = () => {
    for (const button of document.querySelectorAll("#display [data-view]")) {
      button.setAttribute("aria-pressed", String(view[button.dataset.view]));
    }
  };

  // The image choice: one dropdown decides it, and the controls for the choice that is not taken are
  // hidden rather than left in the row looking available.
  const syncSource = () => {
    const upload = $("source").value === "upload";
    $("row-choice").hidden = upload;
    $("file").hidden = !upload;
    $("resize-choice").hidden = !upload;
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
      // A link may name the model it wants — `/play?model=<id>` — which is how a model page sends a
      // reader here with the choice already made. An id this gateway does not serve is ignored
      // rather than shown: the directory is the list of what answers, and a URL is not.
      const wanted = new URLSearchParams(location.search).get("model");
      const chosen = directory.models.find((entry) => entry.id === wanted) || directory.models[0];
      if (chosen) {
        model.value = chosen.id;
        await loadModel(chosen.id);
      } else {
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
    boxInput = null;
    boxes = [];
    showMode();
    $("run").disabled = true;
    clearViewer();
    // A palette was read with the key that just went; the next one may be a different team's, and a
    // failed read is cached as an empty map rather than retried.
    palettes.clear();
    view = {...VIEW_BASE};
    showView();
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

  // A view toggle redraws from the answer already on screen: no request, and no decode — the mask
  // layer was built when that answer arrived.
  $("boxes-mode").addEventListener("click", (event) => {
    const button = event.target.closest("[data-mode]");
    if (!button) return;
    boxesEditable = button.dataset.mode === "edit";
    // Editing a layer nobody can see would draw boxes that vanish as they are made, so asking to
    // edit shows the layer: visible is what editable means here.
    if (boxesEditable && !view.boxes) {
      view.boxes = true;
      document.querySelector('[data-view="boxes"]').setAttribute("aria-pressed", "true");
    }
    showMode();
    showBoxes();
  });

  $("display").addEventListener("click", (event) => {
    const button = event.target.closest("[data-view]");
    if (!button) return;
    const key = button.dataset.view;
    view[key] = !view[key];
    button.setAttribute("aria-pressed", String(view[key]));
    // Hiding the box layer locks the picture: a drag would make a box nobody can see, and the page
    // would look as if the gesture had failed rather than the layer being off.
    if (key === "boxes" && !view.boxes) boxesEditable = false;
    showMode();
    render();
  });

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
  // One alpha for every mask: a class differs from another by hue, never by opacity, because a
  // fainter tint would read as a less certain one.
  const TINT = 110;

  // A category the picture's dataset declares is painted in the dataset's own colour: `GET
  // /datasets/<id>` publishes the palette its mask images and its own page use, and a colour invented
  // here would be a second answer to what a class looks like. A category it does not declare is an
  // instance label rather than a class of the picture — the words a detector was given, say — and two
  // neighbours of one category still have to differ, so those cycle this list by object instead.
  // Nothing here names a class or a model; both lists arrive from the surface.
  const FALLBACK = ["#ff4fa3", "#00c2ff", "#ffb000", "#8b5cf6", "#00d68f", "#ff5a36"];

  // A counting answer's field takes the colour the answer's dots used, so a reader who has seen one
  // rendering recognises the other. It is one hue for the whole layer by design: the numbers say how
  // much, the hue would have to say which class, and a local-count model has no classes.
  const FIELD_COLOUR = "#ff4fa3";
  // The tint of a cell, from what share of the field's ninth decile it holds. A local-count field
  // spans an order of magnitude between its busiest cell and its ordinary ones — measured on `gwfss`
  // row 0, 1.9 against 0.05 — so scaling by the peak shows one blob and scaling by nothing paints the
  // whole frame. The decile is full tint and everything above it saturates; full opacity would hide
  // the objects the field is about.
  const FIELD_ALPHA = 150;
  const FIELD_DECILE = 0.9;
  // What a pixel layer is read against when the photograph is off. One dark neutral for both themes:
  // the layer is the same colour in either, and this is the ground a density figure is printed on.
  const FIELD_BACKDROP = "#12161c";

  const palettes = new Map();     // dataset id -> Promise of Map(lowercased class name -> #rrggbb)

  const paletteFor = (id) => {
    if (!id) return Promise.resolve(new Map());
    if (!palettes.has(id)) {
      // A palette that cannot be read is not a reason to fail the run it decorates: the answer's own
      // colours fall back rather than the picture not being drawn at all.
      palettes.set(id, call("/datasets/" + encodeURIComponent(id)).then((description) =>
        new Map((description.classes || []).map((entry) =>
          [String(entry.name).toLowerCase(), String(entry.color)]))).catch(() => new Map()));
    }
    return palettes.get(id);
  };

  const rgbOf = (colour) => {
    const hex = String(colour).replace("#", "");
    return [0, 2, 4].map((at) => parseInt(hex.slice(at, at + 2), 16) || 0);
  };

  // Black ink on the pale half of a palette and white on the dark half: a class the dataset paints
  // yellow must not carry white-on-yellow.
  const inkOn = (colour) => {
    const [red, green, blue] = rgbOf(colour);
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue > 150 ? "#14181f" : "#ffffff";
  };

  // One object's colour, and whether its category is a class the dataset declares — the flag the
  // legend and the label both hang on.
  const colourOf = (object, order, palette) => {
    const name = String(object.category || "").trim().toLowerCase();
    if (name && palette.has(name)) return {colour: palette.get(name), declared: true};
    return {colour: FALLBACK[order % FALLBACK.length], declared: false};
  };

  // One object's geometry as something drawable, without a single pixel being read back: the
  // mask goes into an offscreen canvas of its own size, which is then drawn onto the visible
  // one at the display size. `putImageData` ignores scaling, so the offscreen canvas is what
  // carries the mask, and the visible canvas is only ever written to.
  //
  // The client decodes to a row-major array, which is `ImageData`'s own order, so a mask drawn
  // transposed is not a thing this page can do: the pixels are copied straight across.
  const overlayFor = (object, colour) => {
    const [height, width] = object.rle.size;
    const {data} = GlobalWheat.rle_decode(object.rle.counts, height, width);
    const pixels = new ImageData(width, height);
    const [red, green, blue] = rgbOf(colour);
    let painted = 0;
    for (let index = 0; index < data.length; index++) {
      if (!data[index]) continue;
      const at = index * 4;
      pixels.data[at] = red;
      pixels.data[at + 1] = green;
      pixels.data[at + 2] = blue;
      pixels.data[at + 3] = TINT;
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

  // One rectangle of the image, in the stage's own percentages: the stage is sized to the fitted
  // picture, so this is the single mapping every overlay uses — an object's box, a point and a drawn
  // box all land together, and none of them can drift from the others. A point passes no size and
  // keeps the one its own rule gives it.
  const place = (element, image, x, y, width, height) => {
    element.style.left = (x / image.naturalWidth) * 100 + "%";
    element.style.top = (y / image.naturalHeight) * 100 + "%";
    if (typeof width === "number") element.style.width = (width / image.naturalWidth) * 100 + "%";
    if (typeof height === "number") element.style.height = (height / image.naturalHeight) * 100 + "%";
  };

  // One object's box, its class and the score that goes with it, as elements over the raster rather
  // than pixels inside it: the pane draws a photograph at whatever size fits, so a score burned into
  // the bitmap would be a smudge on a tall strip and a billboard on a wide crop. As text it is the
  // same size in both, it is text a reader can select, and it carries the model's own number to two
  // decimals. The frame and the label take the object's own colour, so a box names what its mask is.
  const markFor = (object, image, colour, label, stack) => {
    const [x, y, width, height] = object.bbox;
    const mark = document.createElement("div");
    mark.className = "mark";
    mark.style.borderColor = colour;
    place(mark, image, x, y, width, height);
    if (typeof object.score === "number") {
      const score = document.createElement("span");
      // The class is named on its own box when the legend can name it too — the dataset declares it,
      // or it has one object. Eighteen heads keep their scores alone: one category repeated that many
      // times is a label as long as the picture.
      score.textContent = (label ? label + " " : "") + object.score.toFixed(2);
      score.style.background = colour;
      score.style.color = inkOn(colour);
      mark.append(score);
      // A box against the top edge of the image has no room above it, so its score goes inside the
      // box — at the object's own top, which is where the label would have pointed.
      if (y / image.naturalHeight < 0.06) {
        mark.classList.add("inside");
        // Two classes can share an extent and then share a label point. Stacked rather than drawn
        // one over the other, which would hide one of them — half of what this page exists to show.
        if (stack) score.style.top = (-1.5 + stack * 18) + "px";
      }
    }
    return mark;
  };

  // The classes the answer carries, in the colours the objects were drawn with: a mask is a colour,
  // and a colour names a class only if the page says which. A category is listed when its colour is
  // one the dataset declared — every object of it then shares that colour — or when it has exactly
  // one object, whose own colour stands for it. A category repeated on eighteen neighbours is listed
  // not at all, because no single chip could stand for its six colours. A class with one object
  // carries that object's score after its name: with the boxes hidden, this is where the number
  // has to live.
  const showLegend = (objects, colours, counts) => {
    const legend = $("legend");
    legend.replaceChildren();
    const named = new Set();
    objects.forEach((object, order) => {
      const name = String(object.category || "");
      if (!name || named.has(name)) return;
      if (!colours[order].declared && counts.get(name) !== 1) return;
      named.add(name);
      const chip = document.createElement("span");
      chip.className = "chip";
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = colours[order].colour;
      const label = document.createElement("span");
      label.textContent = name;
      chip.append(swatch, label);
      if (counts.get(name) === 1 && typeof object.score === "number") {
        const score = document.createElement("span");
        score.className = "score";
        score.textContent = object.score.toFixed(2);
        chip.append(score);
      }
      legend.append(chip);
    });
    legend.hidden = named.size === 0;
  };

  // The picture as something to write on. A declaration that names a `boxes` parameter makes the
  // image drawable: press and drag draws one box, a click sets a corner and a second click finishes
  // it, and Esc or the right button abandons the one being drawn. Under four pixels of travel is a
  // click, not a box — a reader who meant to look at the picture must not find the list changed.
  //
  // The list is kept here as well as in the input, because the input is text a reader is halfway
  // through typing: the last value that parsed is what the picture shows, so a typo marks the field
  // rather than emptying the overlay or throwing.
  let boxInput = null;   // the input a `boxes` parameter created, or null for a model without one
  let boxes = [];        // the last list that parsed: [x, y, w, h, label] in the image's own pixels
  let drag = null;       // the box being drawn: {x0, y0, x, y} in the image's own pixels
  let band = null;       // the rectangle showing it while it is drawn
  let boxesEditable = true;   // whether the picture's boxes can be drawn, moved and deleted by hand

  const MIN_DRAG = 4;

  // A box answers to three gestures, split the way an annotation tool splits them: the body moves it,
  // a corner resizes it from that corner, and the × removes it. The layer itself still takes no
  // pointer events, so a press on the picture away from a box is still a new box.
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

  const MIN_BOX = 8;   // the smallest side a resize can leave, in the image's own pixels

  // The pointer keeps reporting to the element it started on however far it travels; a browser that
  // will not capture still draws, only stopping at the picture's edge, which is where the box is held.
  const capture = (element, pointerId) => {
    try {
      element.setPointerCapture(pointerId);
    } catch {
      // nothing to do: the gesture is already under way
    }
  };

  const handleFor = (kind, index) => {
    const element = document.createElement("button");
    element.type = "button";
    element.className = "handle " + kind;
    element.dataset.index = String(index);
    element.tabIndex = -1;
    if (kind === "drop") {
      element.textContent = "×";
      element.setAttribute("aria-label", "remove this box");
      element.addEventListener("pointerdown", (event) => event.stopPropagation());
      element.addEventListener("click", (event) => {
        event.stopPropagation();
        boxes.splice(index, 1);
        commitBoxes();
      });
    } else {
      element.setAttribute("aria-label", "resize this box");
      resizeBy(element, index, kind.endsWith("tl") ? "tl" : "br");
    }
    return element;
  };

  // The body carries the box: the pointer's travel in the picture is the box's travel, so the box
  // keeps its size while it is carried somewhere else — a corner is what changes a size.
  const moveBy = (element, index) => {
    let start = null;
    element.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !last) return;
      const [x, y] = imageAt(event);
      start = {x, y, box: boxes[index].slice()};
      event.stopPropagation();   // the picture would otherwise read the press as a new box
      event.preventDefault();
      capture(element, event.pointerId);
    });
    element.addEventListener("pointermove", (event) => {
      if (!start) return;
      const [x, y] = imageAt(event);
      const [, , width, height, label = 1] = start.box;
      const moved = [
        clamp(start.box[0] + (x - start.x), 0, last.image.naturalWidth - width),
        clamp(start.box[1] + (y - start.y), 0, last.image.naturalHeight - height),
        width,
        height,
        label,
      ];
      boxes[index] = moved;
      place(element, last.image, moved[0], moved[1], width, height);
    });
    element.addEventListener("pointerup", () => {
      if (!start) return;
      start = null;
      commitBoxes();
    });
  };

  // A corner dot moves that corner and leaves the opposite one where it was, so a box is made wider
  // without being made taller. Held inside the picture, and never smaller than one pixel of drag.
  const resizeBy = (dot, index, corner) => {
    let start = null;
    dot.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !last) return;
      const [x, y] = imageAt(event);
      start = {x, y, box: boxes[index].slice()};
      event.stopPropagation();
      event.preventDefault();
      capture(dot, event.pointerId);
    });
    dot.addEventListener("pointermove", (event) => {
      if (!start) return;
      const [x, y] = imageAt(event);
      const [left, top, width, height, label = 1] = start.box;
      const right = left + width;
      const bottom = top + height;
      let resized;
      if (corner === "tl") {
        const nearLeft = clamp(left + (x - start.x), 0, right - MIN_BOX);
        const nearTop = clamp(top + (y - start.y), 0, bottom - MIN_BOX);
        resized = [nearLeft, nearTop, right - nearLeft, bottom - nearTop, label];
      } else {
        const nearRight = clamp(right + (x - start.x), left + MIN_BOX, last.image.naturalWidth);
        const nearBottom = clamp(bottom + (y - start.y), top + MIN_BOX, last.image.naturalHeight);
        resized = [left, top, nearRight - left, nearBottom - top, label];
      }
      boxes[index] = resized;
      place(dot.parentElement, last.image, resized[0], resized[1], resized[2], resized[3]);
    });
    dot.addEventListener("pointerup", () => {
      if (!start) return;
      start = null;
      commitBoxes();
    });
  };

  const showBoxes = () => {
    const layer = $("boxes");
    // One toggle for every rectangle on the picture — the exemplars a reader drew and the boxes a
    // model answered — so the layer is hidden as a whole, handles and rubber band included.
    layer.hidden = !view.boxes;
    if (!last || !boxInput) {
      layer.replaceChildren();
      return;
    }
    layer.replaceChildren(...boxes.map((box, index) => {
      const element = document.createElement("div");
      element.className = "box";
      place(element, last.image, box[0], box[1], box[2], box[3]);
      if (boxesEditable) {
        moveBy(element, index);
        element.append(
          handleFor("resize tl", index),
          handleFor("resize br", index),
          handleFor("drop", index),
        );
      }
      return element;
    }));
    // A redraw while one is being drawn must not take the rubber band with it.
    if (band) layer.append(band);
  };

  /** Drop every box, on a picture that is no longer the one they were drawn on. */
  const forgetBoxes = () => {
    boxes = [];
    endDrag();
    if (boxInput) {
      boxInput.value = "";
      boxInput.removeAttribute("aria-invalid");
      // No write-back event: an empty field is the parameter sent nowhere, and the gateway's
      // "boxes is required" refusal is what a reader should meet if they run it like this.
    }
    showBoxes();
  };

  /** Write the list back into the input, which is what the query and the command read. */
  const commitBoxes = () => {
    boxInput.removeAttribute("aria-invalid");
    boxInput.value = JSON.stringify(boxes);
    // A script's own write fires no event, and the page reads the input rather than the list.
    boxInput.dispatchEvent(new Event("input", {bubbles: true}));
  };

  // The input's text as a list of boxes, or null when it is not one: null is a value still being
  // typed rather than an error, and the caller keeps what it already has.
  const parseBoxes = (text) => {
    if (text.trim() === "") return [];   // nothing drawn yet, or everything deleted
    let value;
    try {
      value = JSON.parse(text);
    } catch {
      return null;
    }
    if (!Array.isArray(value)) return null;
    const whole = value.every((box) => Array.isArray(box) && box.length >= 4 &&
      box.slice(0, 4).every((number) => typeof number === "number" && Number.isFinite(number)));
    return whole ? value : null;
  };

  /** Take the input's text as the boxes on the picture, marking the field when it is not one. */
  const syncBoxes = () => {
    const parsed = parseBoxes(boxInput.value);
    if (!parsed) {
      boxInput.setAttribute("aria-invalid", "true");
      return;
    }
    boxInput.removeAttribute("aria-invalid");
    boxes = parsed;
    showBoxes();
  };

  const endDrag = () => {
    if (band) band.remove();
    band = null;
    drag = null;
  };

  const showBand = () => {
    place(band, last.image, Math.min(drag.x0, drag.x), Math.min(drag.y0, drag.y),
      Math.abs(drag.x - drag.x0), Math.abs(drag.y - drag.y0));
  };

  // The pointer as a place in the image rather than on the page: the stage is the fitted picture's
  // own box, so one ratio converts back to the pixels the request carries. Held inside the picture,
  // because a box outside it is not one a model can use.
  const imageAt = (event) => {
    const box = stage.getBoundingClientRect();
    const image = last.image;
    return [
      clamp(((event.clientX - box.left) / box.width) * image.naturalWidth, 0, image.naturalWidth),
      clamp(((event.clientY - box.top) / box.height) * image.naturalHeight, 0, image.naturalHeight),
    ];
  };

  const finishDrag = () => {
    const x = Math.round(Math.min(drag.x0, drag.x));
    const y = Math.round(Math.min(drag.y0, drag.y));
    const width = Math.round(Math.abs(drag.x - drag.x0));
    const height = Math.round(Math.abs(drag.y - drag.y0));
    endDrag();
    if (width < MIN_DRAG && height < MIN_DRAG) return;   // a click, not a box
    boxes = boxes.concat([[x, y, width, height, 1]]);
    commitBoxes();
  };

  stage.addEventListener("pointerdown", (event) => {
    if (!boxInput || !boxesEditable || !last || event.button !== 0) return;
    const [x, y] = imageAt(event);
    if (drag) {
      // The second click of a two-click box: its corner is where it was clicked.
      drag.x = x;
      drag.y = y;
      finishDrag();
      return;
    }
    drag = {x0: x, y0: y, x: x, y: y, sx: event.clientX, sy: event.clientY};
    band = document.createElement("div");
    band.className = "band";
    $("boxes").append(band);
    showBand();
    event.preventDefault();
    // The pointer keeps reporting to the stage however far the drag travels; a browser that will not
    // capture still draws, only stopping at the picture's edge, which is where the box is held anyway.
    try {
      stage.setPointerCapture(event.pointerId);
    } catch {
      // nothing to do: the box is already being drawn
    }
  });

  stage.addEventListener("pointermove", (event) => {
    if (!drag) return;
    [drag.x, drag.y] = imageAt(event);
    showBand();
  });

  stage.addEventListener("pointerup", (event) => {
    if (!drag || event.button !== 0) return;
    // A press that travelled is a box; one that did not is a click, whose corner waits for the second.
    // The travel is measured on the page, not in the picture: four pixels of a 5000-pixel row is a
    // tenth of a screen pixel, so a shaking hand would draw a box where a reader meant to click.
    if (Math.abs(event.clientX - drag.sx) < MIN_DRAG &&
      Math.abs(event.clientY - drag.sy) < MIN_DRAG) return;
    finishDrag();
  });

  // The right button and Escape abandon the box being drawn, and leave the list as it was.
  stage.addEventListener("contextmenu", (event) => {
    if (!drag) return;
    event.preventDefault();
    endDrag();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && drag) endDrag();
  });

  // The answer on screen, kept so a view toggle redraws it: the image, the decoded mask layer, the
  // colours, and the address the picture came from. Nothing here is sent anywhere.
  let last = null;

  // Everything the eye buttons can change, drawn from what the answer already decoded — no request,
  // no second decode. The mask layer was composited once, at display size, when the answer arrived.
  const render = () => {
    if (!last) return;
    const {image, objects, colours, layer, url} = last;
    const scale = Math.min(1, MAX_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const context = canvas.getContext("2d");
    // The photograph first, then the model's pixel layer over it. Each can be turned off: with the
    // layer off the photograph is the one thing on screen that is not a model's claim, and with the
    // photograph off the layer is read on its own — over a dark ground, because a pale tint on the
    // pane's light grey reads as nothing at all.
    if (view.image) {
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
    } else if (view.masks) {
      context.fillStyle = FIELD_BACKDROP;
      context.fillRect(0, 0, canvas.width, canvas.height);
    }
    if (view.masks) context.drawImage(layer, 0, 0, canvas.width, canvas.height);

    figure.hidden = false;
    fit(image);
    fitted = image;

    // One category with one object can be named by that object's own colour; a category with several
    // objects can be named only when the dataset gave them all one colour.
    const counts = new Map();
    for (const object of objects) {
      const name = String(object.category || "");
      if (name) counts.set(name, (counts.get(name) || 0) + 1);
    }
    const label = (object, colour) =>
      object.category && (colour.declared || counts.get(String(object.category)) === 1)
        ? String(object.category) : "";

    marks.replaceChildren();
    if (view.boxes) {
      const anchors = new Map();        // how many labels already claim one point
      objects.forEach((object, order) => {
        if (!object.bbox) return;
        const [x, y] = object.bbox;
        const anchor = Math.round(x) + ":" + Math.round(y);
        const stack = anchors.get(anchor) || 0;
        anchors.set(anchor, stack + 1);
        marks.append(markFor(object, image, colours[order].colour,
          label(object, colours[order]), stack));
      });
    }

    // The points a counting answer places, each at its own pixel of the image and by the same mapping
    // the boxes use. A dot is a place rather than an extent, so nothing here draws a frame around one
    // and the legend names none of them; an answer without points leaves the picture as it was.
    for (const point of Array.isArray(last.points) ? last.points : []) {
      if (!Array.isArray(point) || point.length < 2) continue;
      const dot = document.createElement("div");
      dot.className = "point";
      place(dot, image, point[0], point[1]);
      marks.append(dot);
    }

    showLegend(objects, colours, counts);
    $("legend").hidden = !view.legend || !$("legend").childElementCount;
    // The count is one number, so it sits in the caption rather than over the photograph: present
    // only when the answer carried one, which is what keeps a mask model's page unchanged.
    const tally = $("count");
    tally.hidden = typeof last.count !== "number";
    tally.textContent = tally.hidden
      ? "" : "count " + last.count.toLocaleString(undefined, {maximumFractionDigits: 1});
    showBoxes();
    const machine = $("watermark");
    machine.hidden = !view.watermark || !machine.textContent;
    const full = $("full");
    full.href = url;
    full.hidden = false;
  };

  // A counting answer's field, as a layer the size of the picture. Its numbers are the model's own
  // cells — one cell is a counting window, not a pixel — so they are painted at grid size and then
  // stretched with smoothing on: an unsmoothed 21-cell grid over a 1024-pixel photograph reads as a
  // mosaic, while what mass does between two cells is what a heatmap is for. The tint follows the
  // value against the field's own ninth decile (see `FIELD_ALPHA`), so the photograph stays readable
  // under it and a cell holding almost nothing is not painted rather than washing the whole frame.
  const fieldLayer = (density, width, height) => {
    const [rows, columns] = Array.isArray(density.size) ? density.size : [];
    const values = Array.isArray(density.values) ? density.values : [];
    if (!(rows > 0 && columns > 0) || values.length !== rows * columns) return null;
    const occupied = values.map(Number).filter((value) => value > 0)
      .sort((left, right) => left - right);
    if (!occupied.length) return null;
    const reference = occupied[Math.floor((occupied.length - 1) * FIELD_DECILE)];

    const grid = document.createElement("canvas");
    grid.width = columns;
    grid.height = rows;
    const cells = grid.getContext("2d");
    const pixels = cells.createImageData(columns, rows);
    const [red, green, blue] = rgbOf(FIELD_COLOUR);
    values.forEach((value, at) => {
      const solidity = Math.min(1, Math.max(0, Number(value) / reference));
      if (!(solidity > 0)) return;
      pixels.data[at * 4] = red;
      pixels.data[at * 4 + 1] = green;
      pixels.data[at * 4 + 2] = blue;
      pixels.data[at * 4 + 3] = Math.round(solidity * FIELD_ALPHA);
    });
    cells.putImageData(pixels, 0, 0);

    const layer = document.createElement("canvas");
    layer.width = width;
    layer.height = height;
    const stretched = layer.getContext("2d");
    stretched.imageSmoothingEnabled = true;
    stretched.imageSmoothingQuality = "high";
    stretched.drawImage(grid, 0, 0, width, height);
    return layer;
  };

  /** Decode one answer into a mask layer once, then let `render` draw it in whatever view is on. */
  const paint = async (url, objects, palette = new Map(), answer = {}) => {
    const image = await loadImage(url);
    const scale = Math.min(1, MAX_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
    const width = Math.max(1, Math.round(image.naturalWidth * scale));
    const height = Math.max(1, Math.round(image.naturalHeight * scale));

    const colours = objects.map((object, order) => colourOf(object, order, palette));
    const decoder = typeof GlobalWheat?.rle_decode === "function";
    const layer = document.createElement("canvas");
    layer.width = width;
    layer.height = height;
    const painting = layer.getContext("2d");
    let decoded = 0, declared = 0, checked = 0;
    objects.forEach((object, order) => {
      if (!object.rle || !decoder) return;
      const drawn = overlayFor(object, colours[order].colour);
      painting.drawImage(drawn.overlay, 0, 0, width, height);
      decoded += drawn.painted;
      if (typeof object.area === "number") {
        declared += object.area;
        checked += 1;
      }
    });

    // A counting answer brings no objects and no mask: what it brings is the field its count was
    // summed from, painted into the same layer, so the masks toggle shows or hides it like any other
    // pixel layer and a view change never asks the model again.
    if (answer.density) {
      const field = fieldLayer(answer.density, width, height);
      if (field) painting.drawImage(field, 0, 0);
    }

    // A counting answer's two fields travel with the picture exactly as `objects` does, so a view
    // toggle redraws them from the answer rather than from the response body.
    last = {
      image: image,
      objects: objects,
      colours: colours,
      layer: layer,
      url: url,
      count: answer.count,
      points: answer.points,
      warnings: Array.isArray(answer.warnings) ? answer.warnings : [],
    };
    render();
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

  const draw = async (url, answer, palette) => {
    const objects = answer.objects || [];
    // Written before the picture, so the watermark is there when `paint` renders it.
    showMachine(answer);
    const painted = await paint(url, objects, palette, answer);
    $("response-body").textContent = JSON.stringify(answer, null, 2);
    $("response").hidden = false;

    // The caption is one line beside four view buttons, so it says the one thing worth checking —
    // that the decoded mask covers exactly the area the answer declared — in as few words as it can.
    const tally = objects.length + (objects.length === 1 ? " object" : " objects");
    if (!objects.length) {
      // A counting answer has no geometry to check: the count beside this line is the answer, and
      // "0 objects" would read as a model that found nothing. What is left to say is a warning —
      // an exemplar past the model's finest window, for one — and it belongs here, not in a toast.
      const warning = last.warnings[0];
      say($("check"), warning ? warning.message : "", null);
      $("check").classList.toggle("warn", Boolean(warning));
    } else if (!painted.decoder) {
      // Said rather than left blank: an empty canvas would read as a model that found nothing.
      say($("check"), tally + " · decoder did not load — the response body is complete", false);
    } else if (!painted.checked) {
      say($("check"), tally + " · no area or RLE to check", null);
    } else if (painted.decoded === Math.round(painted.declared)) {
      say($("check"), tally + " · Σ mask area = " + painted.decoded.toLocaleString() + " px", true);
    } else {
      say($("check"),
        tally + " · Σ mask area " + painted.decoded.toLocaleString() + " ≠ Σ area " +
        Math.round(painted.declared).toLocaleString() + " px", false);
    }
  };

  /** Empty the viewer: nothing on screen is the picture the controls no longer name. */
  const clearViewer = () => {
    figure.hidden = true;
    endDrag();
    marks.replaceChildren();
    $("boxes").replaceChildren();
    $("count").hidden = true;
    $("count").textContent = "";
    $("legend").replaceChildren();
    $("legend").hidden = true;
    $("full").hidden = true;
    fitted = null;
    last = null;
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
  // The machine's own words, one per line: name, card, CUDA build, torch. Joined with separators it
  // was one long strip across the picture; four right-aligned lines fit the corner it sits in.
  const describeMachine = (computed) => {
    if (!computed) return [];
    const lines = [];
    if (computed.machine) lines.push(computed.machine);
    if (computed.gpu) lines.push(computed.gpu);
    if (computed.cuda) lines.push("CUDA " + computed.cuda);
    if (computed.torch) lines.push("torch " + computed.torch);
    return lines;
  };

  const showMachine = (answer) => {
    const lines = describeMachine(answer && answer.computed_by);
    const chip = $("watermark");
    chip.replaceChildren(...lines.map((line) => {
      const span = document.createElement("span");
      span.textContent = line;
      return span;
    }));
    chip.hidden = !lines.length || !view.watermark;
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

  // The model runs at 1008 px on a side and the container resizes every input to it anyway, so a
  // photograph sent whole costs seconds of transfer for pixels nothing reads. The box beside the
  // file is the way out: untick it and the file goes exactly as it is.
  const RESIZE_SIDE = 1008;

  /** The same picture at the side the model would use, as the bytes a request carries. */
  const forUpload = async (file) => {
    if (!$("resize").checked || typeof createImageBitmap !== "function") return file;
    try {
      const bitmap = await createImageBitmap(file);
      const scale = Math.min(1, RESIZE_SIDE / Math.max(bitmap.width, bitmap.height));
      if (scale === 1) {
        bitmap.close();
        return file;
      }
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(bitmap.width * scale));
      canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      bitmap.close();
      const blob = await new Promise((done) => canvas.toBlob(done, "image/jpeg", 0.9));
      return blob || file;
    } catch {
      // A format this browser cannot decode is still a file the API may accept; send it whole.
      return file;
    }
  };

  // Which picture a coordinate belongs to: a dataset and a row, or the file that was chosen. Two
  // answers follow from it, and they are about different moments, so they are two values.
  const pictureOf = (upload, file, source, index) => upload
    ? "upload:" + (file ? file.name + ":" + file.size + ":" + file.lastModified : "")
    : source + ":" + index;
  let boxesKey = "";   // the picture the boxes on screen were drawn on
  let shownKey = "";   // the picture the pane is actually showing, answer included

  const preview = async () => {
    if (!key) return;
    const upload = $("source").value === "upload";
    const file = $("file").files[0];
    // Settled before anything is fetched: a run that starts inside the debounce must not keep
    // coordinates drawn on the picture it is replacing.
    const picture = pictureOf(upload, file, dataset.value, $("row").value);
    if (picture !== boxesKey) {
      boxesKey = picture;
      forgetBoxes();
    }
    // A run owns the pane while it runs, and afterwards the pane is showing an answer about this
    // very picture — previewing it again would only take that answer off.
    if (running || picture === shownKey) return;
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
      if (token !== previewToken || running) return;
      shownKey = picture;
      $("response").hidden = true;
      // A preview is a picture, not a result: there is no machine to name until something runs.
      showMachine(null);
      // The size comes from the image itself in both cases, so the caption describes what is drawn
      // rather than what an index or a file system claimed.
      say($("check"), caption + " · " + painted.width + "×" + painted.height, null);
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
    clearTimeout(rowTimer);   // a row number typed a moment ago must not preview over the answer
    say(state, "running…");
    // A preview still in flight belongs to a choice this run has moved past, and must not be allowed
    // to draw over the result.
    previewToken += 1;
    try {
      const chosen = encodeURIComponent(model.value);
      // Read once: a run outlives the control it started from, and the image, the body and the
      // palette must all describe the same dataset.
      const source = dataset.value;
      // The picture this run is about, read once with the rest: a preview that resolves later must
      // not be able to replace it, and a preview of the same picture has nothing to show.
      const picture = pictureOf(upload, file, source, Number($("row").value));
      // This run cancels the preview a typed row number scheduled, so the boxes are dropped here:
      // they were drawn on the picture the run is leaving, and sending them would count whatever is
      // now under those coordinates.
      if (picture !== boxesKey) {
        boxesKey = picture;
        forgetBoxes();
      }
      const search = query();
      let imageUrl, answer, palette;
      if (upload) {
        // The container takes raw bytes, and the public route takes them as the multipart
        // field this contract declares; the gateway decodes that once and forwards the bytes.
        const payload = await forUpload(file);
        const form = new FormData();
        form.append("file", payload, file.name);
        // The run shows the bytes it sent, because the answer's boxes are in their pixels: showing
        // the original would place every mark a quarter of the way in. The preview's object URL is
        // released and this one takes its place as the picture on screen.
        if (held) URL.revokeObjectURL(held);
        held = URL.createObjectURL(payload);
        imageUrl = held;
        // A photograph of the reader's is not a row of any dataset, so there is no palette it could
        // have been painted in: every object falls back to a colour of its own.
        palette = new Map();
        answer = await call("/models/" + chosen + "/predict/upload?" + search,
          {method: "POST", body: form});
      } else {
        const index = Number($("row").value);
        // The mask's boxes sit on the image, and the address resolves the same way a preview's did,
        // so it is read again here rather than remembered from one.
        const row = await call(
          "/datasets/" + encodeURIComponent(source) + "/images/" + index);
        imageUrl = row.image_url;
        // Asked for beside the prediction rather than after it: the palette is a metadata read and
        // the answer is the slow call, so waiting for the picture to come back first would add a
        // round trip to a page that can least afford one.
        const paletteLoaded = paletteFor(source);
        answer = await call("/models/" + chosen + "/predict?" + search, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({dataset: source, row: index}),
        });
        palette = await paletteLoaded;
      }
      await draw(imageUrl, answer, palette);
      // The pane now holds this picture's answer, so a preview of the same picture has nothing to add
      // and would only strip the marks it just drew.
      boxesKey = shownKey = picture;
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
      // A picture chosen while the run was in flight was not allowed to draw over the answer, and a
      // run refused because the picture had changed leaves the previous one on screen. Either way the
      // pane is now showing something the controls no longer name, so it is caught up here.
      const now = pictureOf($("source").value === "upload", $("file").files[0],
        dataset.value, $("row").value);
      if (now !== shownKey) preview();
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
