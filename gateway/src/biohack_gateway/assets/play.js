(() => {
  "use strict";
  // The key this page calls with. It is typed into the page that uses it and sent to this host and
  // nowhere else; the browser remembers it in the one store both this page and the reference share,
  // and nothing else on this page writes it anywhere.
  let key = "";
  let fields = [];   // one entry per declared parameter: {name, type, input, read}
  // The text a reader typed, per model id, and the id whose controls are on screen. A phrase is the
  // one thing worth carrying across a model switch: the picture and a drawn box list already survive
  // one, and a reader comparing two models on the same words should not have to retype them.
  const typedText = new Map();
  let shownModel = "";
  let datasetRows = new Map();   // dataset id -> its row count, as the surface last reported it

  const $ = (id) => document.getElementById(id);
  const model = $("model"), variant = $("variant");
  const dataset = $("dataset"), parameters = $("parameters");
  const canvas = $("canvas"), figure = document.querySelector("#play figure");
  const pane = $("pane"), stage = $("stage"), marks = $("marks");
  // The switch that decides whether the picture edits a geometry list. It is written in the markup
  // once and then moved into the row of the parameter it belongs to, so it is read as part of that
  // control rather than as another row of the form; the second list gets a copy of it, and a model
  // that declares neither leaves both out of the document.
  const boxesMode = $("boxes-mode");
  const pointsMode = boxesMode.cloneNode(true);
  pointsMode.id = "points-mode";
  pointsMode.setAttribute("aria-label", "points on the picture");
  pointsMode.querySelector('[data-mode="edit"]').title = "add, move and delete points on the picture";
  // What a press on the picture does with a point: write label 1, write label 0, or remove it. One
  // group, because the two questions this replaced — what a new point gets and what the selected one
  // becomes — had the same two answers sitting beside each other, and a reader had to work out which
  // pair applied to what. Nothing is selected now: the pressed mode is the whole state, and a press
  // acts on the point under it.
  const pointModes = document.createElement("span");
  pointModes.className = "switch mode-switch point-mode";
  pointModes.setAttribute("role", "group");
  pointModes.setAttribute("aria-label", "what a press does to a point");
  for (const [value, text, title] of [
    ["1", "1", "a press on the picture adds a positive point, and one on a point makes it positive"],
    ["0", "0", "a press on the picture adds a negative point, and one on a point makes it negative"],
    ["drop", "×", "a press on a point deletes it"],
  ]) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.act = value;
    button.textContent = text;
    button.title = title;
    button.setAttribute("aria-label", title);
    pointModes.append(button);
  }
  let pointMode = "1";
  const showPointMode = () => {
    for (const button of pointModes.querySelectorAll("[data-act]")) {
      button.setAttribute("aria-pressed", String(button.dataset.act === pointMode));
    }
  };
  pointModes.addEventListener("click", (event) => {
    const button = event.target.closest("[data-act]");
    if (!button) return;
    pointMode = button.dataset.act;
    showPointMode();
    showPoints();
  });
  showPointMode();

  // What the picture shows first, and what a reader can start from, per model. This is the one
  // model-specific thing the page carries, and it is presentation only: it decides nothing about what
  // is sent, and an id the table does not name — a renamed model, or one added after this line —
  // falls back to the base view and to no examples.
  //
  // Two kinds of entry, one table. The view keys are what the eyes show first; a semantic model
  // answers with class extents that cover most of the frame, so its boxes open hidden and its legend,
  // which is where the class names and scores then live, opens shown. `examples` are calls known to
  // work: a chip writes the address and the parameters it names into the controls, so a reader never
  // types a value there is no reason to trust. An example names a `row` in a published dataset or an
  // `image` under the data distribution's `dev/` prefix — the one prefix whose cache behaviour
  // answers with CORS, which a browser needs before it can read another origin's bytes. The second is
  // what a model with no wheat classes can still show, and the page writes it into the file control
  // so everything after it is the path a chosen photograph takes. This table is also the only place a model id or a
  // parameter name appears, and the test that holds the page to its own invariant says so.
  // `landing` is which model the page opens on when a URL names none. Every one of these is a
  // presentation default, kept in the one table allowed to name a model, not a second id in the
  // connect path.
  //
  // A family groups ids that are one product line: the same model at different checkpoints, or a
  // cheaper and a larger tier of one model, as Nano Banana's three. The grouping is not this page's
  // to declare — the host's model list carries `family`, `label` and `order`, `GET /models`
  // publishes them, and `buildModelChoices` reads them from there. A new member of an existing
  // family therefore appears grouped with no change to this file, and a page cannot drift from what
  // the gateway serves. A family with one served id stays a plain id, because a second control with
  // one choice is a click that decides nothing.
  //
  // View order is the order a reader builds the picture in: the photograph, the model's pixel layer
  // over it, the rectangle geometry on top, then the two annotations the page adds.
  const VIEW_BASE = {image: true, masks: true, results: false, geometry: true, legend: false, watermark: true};
  // Where the example photographs are asked for: this host. The three objects live under the data
  // distribution's `dev/` prefix, and this host reads them there and answers them same-origin — the
  // page uploads the bytes, and a browser can only read another origin's bytes when that origin
  // answers a cross-origin request, which the dataset distribution deliberately does not.
  const DEMO_IMAGE_PREFIX = "/play/example/";
  // The same three for both YOLO ids: the detect answer is boxes, the mask head adds `rle`, and the
  // parameters are the ones this deployment measured the pair at.
  const yoloExamples = () => [
    {label: "carrots", image: DEMO_IMAGE_PREFIX + "carrot.jpg", params: {confidence_threshold: 0.25, imgsz: 1024}},
    // 0.5 rather than the detector's 0.25: at 0.25 this picture also answers "dining table" twice,
    // scoring 0.28 and 0.25 — the plastic bag under the heads. The two broccoli scores are 0.87 and
    // 0.65, so the higher threshold costs nothing and leaves one class in the answer.
    {label: "broccoli", image: DEMO_IMAGE_PREFIX + "broccoli.jpg", params: {confidence_threshold: 0.5, imgsz: 1024}},
    {label: "a potted plant", image: DEMO_IMAGE_PREFIX + "potted-plant.jpg", params: {confidence_threshold: 0.25, imgsz: 1024}},
  ];
  const MODEL_PRESETS = {
    "gwfss-segformer-b4": {geometry: false, legend: true, landing: true},
    // The same dataset and the same answer shape as the landing model, from the laboratory's own
    // foundation model fine-tuned on it — so a reader can put the two masks side by side without
    // learning a second set of colours. It does not take the landing slot: a first-time reader
    // should meet the checkpoint with the published evaluation.
    "sprout-l-gwfss": {geometry: false, legend: true},
    // A backbone has no objects and no mask; what it publishes is the grid its `results` eye draws.
    // `results` is the layer for a model's pixel output, whatever shape it takes: a feature grid
    // tinted over the photograph, or a picture that replaces it. The eye is hidden again for every
    // model without this entry.
    "dinov3-vitl16": {geometry: false, masks: false, results: true},
    "dinov3-vith16plus": {geometry: false, masks: false, results: true},
    // One product line, three tiers: 2.1 is the cheaper default, 2 the earlier checkpoint, Pro the
    // largest. They are different upstream models, not checkpoints of one; the picker still sends
    // the id, and the answer is the same `ModelPrediction` every model answers.
    "nano-banana-2.1": {
      geometry: false, masks: false, results: true,
      examples: [
        {
          label: "segmentation masks",
          dataset: "gwfss",
          row: 3,
          demo: true,
          params: {
            prompt: "generate segmentation masks for wheat head in the proposed image",
            image_size: "1K",
          },
        },
      ],
    },
    "nano-banana-2": {geometry: false, masks: false, results: true},
    "nano-banana-pro": {geometry: false, masks: false, results: true},
    "internvl-u": {
      geometry: false,
      masks: false,
      results: true,
      examples: [
        {
          label: "describe this picture",
          dataset: "gwfss",
          row: 4,
          params: {prompt: "What is in this picture? Answer in one sentence.", mode: "text"},
        },
        {
          label: "colder light",
          dataset: "gwfss",
          row: 4,
          params: {prompt: "The same heads under colder evening light.", mode: "image", size: 768},
        },
        {
          label: "reason, then draw",
          dataset: "gwfss",
          row: 4,
          params: {
            prompt: "A single wheat spike against dark soil, morning light.",
            mode: "text_image",
            size: 768,
          },
        },
      ],
    },
    // A counting model takes no prompt: the exemplar boxes are the whole request, so an example
    // without them would be a refusal. Three boxes on this row is the paper's setting — the model
    // matches what they show across the picture and answers a number and the field it integrates.
    "tasselnetv4": {
      examples: [
        {
          label: "3 exemplars",
          dataset: "gwhd",
          row: 4,
          params: {
            boxes: [
              [306, 148, 55, 44, 1],
              [487, 313, 37, 70, 1],
              [933, 573, 58, 43, 1],
            ],
          },
        },
      ],
    },
    // One family, one model with and without a mask head: Ultralytics' COCO checkpoints. Neither is
    // trained on wheat — a `gwhd` row answers an empty list, which the model page says — so their
    // chips upload one of three photographs the checkpoints do know. The detector answers boxes and
    // nothing else, so its `masks` layer starts off; the mask head opens both layers, because its
    // answer is the boxes and the masks together.
    "yolo26-ultralytics": {masks: false, examples: yoloExamples()},
    "yolo26-seg-ultralytics": {examples: yoloExamples()},
    // The call the documentation keeps as its known-good one: eighteen heads on this row, at this
    // threshold. A participant reproducing it here and in a client is testing both.
    "sam3.1-meta": {
      landing: false,
      examples: [
        {
          label: "18 heads",
          dataset: "gwfss",
          row: 4,
          params: {prompt: "wheat head", confidence_threshold: 0.25},
        },
        // The picture the model page measures "What the score means" on: `wheat head` alone answers
        // nothing here, and one box around a head brings 22 back, most of them outside the box —
        // an example of what to look for, not a region to look in.
        {
          label: "one box, 22 heads",
          dataset: "gwhd",
          row: 4,
          params: {
            prompt: "wheat head",
            boxes: [[459, 297, 86, 154, 1]],
            confidence_threshold: 0.31,
          },
        },
      ],
    },
    // Text in, instances out: the only model here whose class is a phrase rather than a declared
    // label list, which is why its chip writes a `prompt` and not a threshold. It writes one box per
    // instance and decodes a mask for each, so an answer takes about half a minute — `max_instances`
    // is the control that bounds the wait, and the legend is where the phrase and the areas are read.
    "wevisone-vl-4b": {
      legend: true,
      examples: [
        {label: "43 heads", dataset: "gwhd", row: 4, params: {prompt: "wheat head"}},
        // A photograph of the reader's own kind of subject, not a wheat row: the point of an
        // open-vocabulary model is that the phrase chooses the class, and six carrots answer in
        // seven seconds — which is also what a short answer costs compared with forty heads.
        {label: "carrots", image: DEMO_IMAGE_PREFIX + "carrot.jpg", params: {prompt: "carrot"}},
      ],
    },
  };
  let view = {...VIEW_BASE};
  // A demo example is a cached published-row answer: the call is the same one the reader can make,
  // and the result is already in the gateway's cache. The flag turns a cache hit into a three-second
  // wait, so the page does not reveal that the picture was computed before; editing any control
  // clears it, because the next call is the reader's own.
  let demoRun = false;

  // Only the keys the base view knows are view defaults: `examples` and `landing` are presets for the
  // controls, and copying them into `view` would put a fact about the page into the picture's state.
  const presetFor = (id) => MODEL_PRESETS[id] || {};
  const viewFor = (id) => {
    const defaults = {};
    for (const [name, value] of Object.entries(presetFor(id))) {
      if (name in VIEW_BASE) defaults[name] = value;
    }
    return {...VIEW_BASE, ...defaults};
  };

  // The first dropdown's choices, rebuilt from `GET /models`. A family is a value of its own that no
  // model answers: the second dropdown holds the ids, and `selectedModel` is the only reader of the
  // pair. Only families with two served ids become a family choice — a second control with one
  // option would ask the reader to confirm the only thing they can choose.
  let familyOfId = new Map();     // checked id -> the first dropdown's family value
  let familyChoices = new Map();  // family value -> {label, members: [{id, label}]}

  const buildModelChoices = (entries) => {
    const served = new Map();  // family label -> its entries, in the directory's order
    for (const entry of entries) {
      const family = entry.family;
      if (!family) continue;
      if (!served.has(family)) served.set(family, []);
      served.get(family).push(entry);
    }
    familyOfId = new Map();
    familyChoices = new Map();
    for (const [family, members] of served) {
      if (members.length < 2) continue;
      const value = "family:" + family;
      const ordered = members
        .map((entry) => ({
          id: entry.id,
          label: entry.label || entry.id,
          order: Number.isInteger(entry.order) ? entry.order : 0,
        }))
        .sort((left, right) => left.order - right.order);
      familyChoices.set(value, {label: family, members: ordered});
      for (const member of ordered) familyOfId.set(member.id, value);
    }
    model.replaceChildren();
    const placed = new Set();
    const option = (value, label) => {
      const element = document.createElement("option");
      element.value = value;
      element.textContent = label;
      return element;
    };
    for (const entry of entries) {
      const family = familyOfId.get(entry.id);
      if (!family) {
        model.append(option(entry.id, entry.id));
        continue;
      }
      if (placed.has(family)) continue;
      placed.add(family);
      model.append(option(family, familyChoices.get(family).label));
    }
    model.disabled = !model.options.length;
  };

  // Fill the checkpoint dropdown for the chosen family, or hide it when the choice is a plain id.
  // `preferred` is the id to land on — the one a URL named, or the family's first member when the
  // reader changed only the family.
  const syncVariant = (preferred) => {
    const family = (preferred && familyOfId.get(preferred)) || model.value;
    const choice = familyChoices.get(family);
    variant.replaceChildren();
    if (!choice) {
      variant.hidden = true;
      variant.disabled = true;
      return;
    }
    for (const member of choice.members) {
      const element = document.createElement("option");
      element.value = member.id;
      element.textContent = member.label;
      variant.append(element);
    }
    variant.hidden = false;
    variant.disabled = false;
    variant.value = choice.members.some((member) => member.id === preferred)
      ? preferred
      : choice.members[0].id;
  };

  /** The model id the controls name: the checkpoint when a family is chosen, otherwise the id. */
  const selectedModel = () => (familyChoices.has(model.value) ? variant.value : model.value);

  /** Point both dropdowns at one id, which is how a URL or the landing choice arrives. */
  const selectModel = (id) => {
    model.value = familyOfId.get(id) || id;
    syncVariant(id);
  };

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
  // A declared set of a few exclusive values, as buttons. The control carries a `value` like any
  // other input, so nothing downstream — the request, the curl, an example chip — has to know it is
  // not one. Which sets qualify is decided by the declaration (an `enum` of four or fewer), never by
  // a name, so a model this page has never heard of gets the same treatment.
  const segmented = (parameter, choices) => {
    const box = document.createElement("span");
    box.className = "seg";
    box.setAttribute("role", "radiogroup");
    box.setAttribute("aria-label", parameter.name);
    let current = parameter.default === null || parameter.default === undefined
      ? null
      : String(parameter.default);
    const buttons = [];
    const paint = () => {
      for (const button of buttons) {
        button.setAttribute("aria-checked", String(button.dataset.value === current));
      }
    };
    for (const choice of choices) {
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("role", "radio");
      button.dataset.value = String(choice);
      button.textContent = String(choice);
      button.addEventListener("click", () => {
        box.value = String(choice);
        delete box.dataset.untouched;
        box.dispatchEvent(new Event("input", {bubbles: true}));
      });
      buttons.push(button);
      box.append(button);
    }
    Object.defineProperty(box, "value", {
      get: () => (current === null ? "" : current),
      set: (next) => {
        current = String(next);
        paint();
      },
    });
    paint();
    return box;
  };

  const controlFor = (parameter, seed) => {
    // A carried phrase arrives as a seed rather than as the parameter's default: the declaration's
    // default belongs to the model, and a value the reader typed is theirs to keep — so a seeded
    // control is already "touched" and goes out with the request.
    const value = typeof seed === "string" ? seed : parameter.default;
    const bounded = typeof parameter.minimum === "number" && typeof parameter.maximum === "number";
    const numeric = bounded || typeof value === "number";
    const choices = parameter.values || parameter.enum;
    let input;
    if (Array.isArray(choices) && choices.length && parameter.type === "enum" && choices.length <= 4) {
      input = segmented(parameter, choices);
    } else if (Array.isArray(choices) && choices.length) {
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
      // A parameter whose bounds and default are all integers is stepped by one: a `side` in pixels
      // has no meaning between integers, and a hundredth makes every arrow key a no-op. The rest keep
      // a hundredth, which is what a confidence threshold wants.
      const integral = [parameter.default, parameter.minimum, parameter.maximum]
        .every((bound) => bound === null || bound === undefined || Number.isInteger(Number(bound)));
      input.step = integral ? "1" : "0.01";
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
    } else if (
      parameter.type === "text" &&
      !(typeof parameter.max_length === "number" && parameter.max_length < 40)
    ) {
      // A sentence is typed, not pasted into one line: a text parameter whose ceiling is a sentence
      // gets a field that wraps. `max_length` is the only thing that decides this, so a short label
      // stays an input and a sentence becomes a textarea, without the page naming either of them.
      input = document.createElement("textarea");
      input.rows = String(Math.min(5, Math.max(2, Math.ceil((parameter.max_length || 120) / 90))));
      if (typeof parameter.max_length === "number") input.maxLength = parameter.max_length;
      if (value !== null && value !== undefined) input.value = String(value);
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
  // default but no step, so the page chooses one: one for integer bounds, a hundredth otherwise. The
  // readout shows as many decimals as that step has places, which is what makes `0.5` read as `0.50`
  // beside a step of `0.01` and `512` read as an integer beside a step of one. No parameter name
  // appears here either.
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
    boxesMode.hidden = !boxInput;
    pointsMode.hidden = !pointInput;
    // The picture's cursor follows the editor and nothing else, so it is cleared for a model that
    // has no editor rather than left over from the model before it.
    stage.classList.toggle("drawing", Boolean(editor));
    for (const [element, type] of [[boxesMode, "boxes"], [pointsMode, "points"]]) {
      for (const button of element.querySelectorAll("button")) {
        const pressed = (button.dataset.mode === "edit") === (editor === type);
        button.setAttribute("aria-pressed", String(pressed));
      }
    }
  };

  /** The picture edits one list at a time: one press cannot both start a box and add a point. */
  const setEditor = (type) => {
    const available = type === "boxes" ? Boolean(boxInput) : type === "points" ? Boolean(pointInput) : false;
    editor = available ? type : null;
    // Editing a layer nobody can see would draw marks that vanish as they are made, so asking to
    // edit shows the geometry layer, which both lists share.
    if (editor && !view.geometry) {
      view.geometry = true;
      document.querySelector('[data-view="geometry"]').setAttribute("aria-pressed", "true");
    }
    showMode();
    showBoxes();
    showPoints();
  };

  const showParameters = (declaration, id) => {
    // What the model being left was holding, before its controls go: `fields` is read here and
    // replaced below, so this is the last moment the text exists. Kept per id as well, because a
    // model with no text parameter at all sits on the way back to one that has.
    const typed = {};
    for (const field of fields) {
      if (field.type !== "text") continue;
      const value = field.read();
      if (value !== null) typed[field.name] = value;
    }
    if (shownModel) {
      if (Object.keys(typed).length) typedText.set(shownModel, typed);
      else typedText.delete(shownModel);
    }
    shownModel = id;
    parameters.replaceChildren();
    // A declaration replaces the controls, and with them the picture's editor: a model without a
    // `boxes` parameter has nothing to draw, while the list itself stays for the next one that has.
    boxInput = null;
    pointInput = null;
    const declared = declaration.parameters || [];
    fields = [];
    for (const parameter of declared) {
      const label = document.createElement("label");
      const name = document.createElement("span");
      name.textContent = parameter.name;
      // The description goes on the name, not the label: the label is `display: contents`, so it has
      // no box of its own and a tooltip hung on it would never appear.
      if (parameter.description) name.title = parameter.description;
      // The model just left wins while the parameter names agree — that is the comparison the reader
      // is making — and otherwise this model's own last phrase, which is what comes back after a
      // switch through a model with no text field at all.
      const seed = parameter.type === "text"
        ? (typed[parameter.name] ?? (typedText.get(id) || {})[parameter.name])
        : undefined;
      const input = controlFor(parameter, seed);
      // A phrase carried from a model whose limit was longer arrives whole: cutting it silently would
      // answer a different question than the one on screen, and dropping it would lose the text. The
      // field says the limit here and the request's own refusal says the rest.
      if (parameter.type === "text" && typeof input.value === "string"
        && typeof parameter.max_length === "number"
        && input.value.length > parameter.max_length) {
        input.setAttribute("aria-invalid", "true");
        input.title = `this model takes at most ${parameter.max_length} characters`;
      }
      // One cell per row: the control and, for a slider, the number it is on. A grid places cells,
      // not controls, and a readout left loose would take a row of its own.
      const cell = document.createElement("span");
      cell.className = "control";
      cell.append(input);
      if (input.type === "range") cell.append(readoutFor(input));
      label.append(name, cell);
      parameters.append(label);
      fields.push({name: parameter.name, type: parameter.type, input, read: () => readValue(input)});
      // A `boxes` parameter is a list the picture can be drawn on: the input and the overlay are
      // two views of one list, so both directions are wired here and nowhere else. The switch that
      // says whether the picture edits that list is the third view, and it sits in the same cell as
      // the list — one control, read left to right.
      if (parameter.type === "boxes") {
        boxInput = input;
        cell.classList.add("geometry");
        cell.append(boxesMode);
        if (!input.value && boxes.length) {
          input.value = JSON.stringify(boxes);
          delete input.dataset.untouched;   // a list restored here is the reader's, so it is sent
        }
        syncBoxes();
        input.addEventListener("input", syncBoxes);
      } else if (parameter.type === "points") {
        // The same three views as a box list: the text, the marks it makes, and the switch that says
        // which list the picture is editing. The mode group says what a press does to a point.
        pointInput = input;
        cell.classList.add("geometry");
        cell.append(pointModes, pointsMode);
        if (!input.value && points.length) {
          input.value = JSON.stringify(points);
          delete input.dataset.untouched;   // a list restored here is the reader's, so it is sent
        }
        syncPoints();
        input.addEventListener("input", syncPoints);
      }
    }
    // The editor survives a model change only while the new model still declares that list: the
    // picture cannot be editing points a model does not take.
    if (editor === "boxes" && !boxInput) editor = null;
    if (editor === "points" && !pointInput) editor = null;
    showMode();
    // The overlay follows the editor: a model that takes no boxes must not leave another model's
    // boxes drawn on the picture it is about to replace.
    showBoxes();
    showPoints();
  };

  // A preset's examples, as chips. Which ones exist is the model's own, and each is checked against
  // what the page can actually reach: a dataset still in the directory, a row still inside it, and
  // parameters the model still declares. An example that outlives any of those disappears rather than
  // becoming a button that refuses — the page cannot promise a call it can no longer describe.
  const showExamples = (id, declaration) => {
    const box = $("examples");
    box.replaceChildren();
    const declared = new Set((declaration.parameters || []).map((entry) => entry.name));
    const usable = (presetFor(id).examples || []).filter(
      (example) =>
        (!example.dataset || datasetRows.has(example.dataset)) &&
        (typeof example.row !== "number" ||
          example.row < (datasetRows.get(example.dataset) || 0)) &&
        Object.keys(example.params || {}).every((name) => declared.has(name)),
    );
    for (const example of usable) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = example.label;
      button.title = example.image
        ? "a photograph this model knows a class for, with the parameters the call was measured at"
        : "the parameters this call was measured with";
      button.addEventListener("click", () => applyExample(example));
      box.append(button);
    }
    $("examples-row").hidden = !usable.length;
  };

  // An example that names a photograph is written into the file control, as the `File` a reader's own
  // choice would have produced. That is the whole point: the preview, the request, the command line
  // and Run read `#file`, so an example that goes through the control needs nothing else to change,
  // and the picture on screen is the picture that is uploaded.
  //
  // The fetch is cross-origin and works only because `dev/` is the one prefix the data distribution
  // answers with CORS. A refusal here is not the model's: it says so in the check line and leaves the
  // controls as they were, rather than switching the page to an upload it cannot make.
  const adoptPhotograph = async (example) => {
    const response = await fetch(example.image);
    if (!response.ok) throw new Error("the example photograph answered HTTP " + response.status);
    const blob = await response.blob();
    const name = example.image.split("/").pop() || "example.jpg";
    $("source").value = "upload";
    syncSource();
    const transfer = new DataTransfer();
    transfer.items.add(new File([blob], name, {type: blob.type || "image/jpeg"}));
    $("file").files = transfer.files;
  };

  // Writing an example is writing the controls: the same inputs the reader would have typed into, so
  // the command and the request that follow are the ones it produced. Nothing is sent — an example
  // fills the form in, and Run is still the reader's to press.
  const applyExample = async (example) => {
    if (example.image) {
      try {
        await adoptPhotograph(example);
      } catch (error) {
        say($("check"), error.message, false);
        return;
      }
    } else if (example.dataset) {
      $("source").value = "row";
      syncSource();
      dataset.value = example.dataset;
      if (typeof example.row === "number") $("row").value = String(example.row);
    }
    // The picture before the parameters, and for a reason: naming another picture drops the boxes
    // drawn on the previous one, which `preview` does before its first `await`. An example's own
    // boxes therefore have to be written after that drop, or the drop takes them. The fetch the call
    // starts is asynchronous, so the writes below land while the image is loading and the render
    // that arrives draws them.
    preview();
    for (const field of fields) {
      if (!(field.name in example.params)) continue;
      const value = example.params[field.name];
      // A control holds text, and a geometry parameter holds one JSON list rather than a flat
      // stringification of it — `String([[1, 2, 3, 4, 1]])` is `1,2,3,4,1`. So anything that is not
      // already a string is written as JSON: the same text a reader would have typed, and the same
      // text the request sends.
      field.input.value = typeof value === "string" ? value : JSON.stringify(value);
      // A value written here is the reader's, not the model's default, so it is sent: the same
      // bookkeeping a restored box list does, because it is the same kind of write.
      delete field.input.dataset.untouched;
      field.input.dispatchEvent(new Event("input", {bubbles: true}));
    }
    refreshRequest();
    // A chip is a presentation as well as a call: it shows the layers the answer needs. A mask
    // model's chip opens masks and geometry; a detector's chip leaves the masks layer off, because
    // an answer with no mask would draw nothing under it and read as a miss. The reader's own
    // toggles come back the moment they press one — this only sets where the chip starts.
    view = viewFor(selectedModel());
    showView();
    // After the writes above, because each one dispatches an `input` that clears the flag: the
    // example is a demo only while its own values are still the ones in the controls.
    demoRun = Boolean(example.demo);
  };

  const forgetDemo = () => { demoRun = false; };
  document.addEventListener("input", forgetDemo, true);
  document.addEventListener("change", forgetDemo, true);

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
    const id = selectedModel();
    if (!id) return "";
    const upload = $("source").value === "upload";
    const search = query();
    const address = location.origin + "/models/" + encodeURIComponent(id);
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

  // A declaration that arrives after the reader has chosen another model belongs to a control the
  // page no longer shows; applying it would leave the parameters of one id under the other id's name.
  // The counter is per call, and the latest call is the only one that may write.
  let modelLoad = 0;

  // The same idea for an answer, which outlives the controls that asked for it: every change of the
  // pair bumps this, and a run that started under an older value does not draw its answer under the
  // new model's name. A run has no cancellation — the request is already at a backend — so the
  // answer is discarded when it arrives rather than pretended to belong to the model now selected.
  let modelEpoch = 0;

  const loadModel = async (id) => {
    const token = ++modelLoad;
    const declaration = await call("/models/" + encodeURIComponent(id));
    if (token !== modelLoad) return;
    // Applied with the declaration, so the eye buttons describe the answer that is about to be asked
    // for. The answer on screen was cleared by the caller; nothing here redraws the picture, which is
    // still the one the next request will name.
    const preset = presetFor(id);
    view = viewFor(id);
    // A view only this answer can fill: the eyes are hidden where the layer would always be empty, so
    // the row names what the model actually publishes. A model that answers in pixels has no objects
    // and no mask, and its page shows the photograph, its own picture and the two annotations, and
    // nothing that would be a no-op button.
    const publishesPixels = Boolean(preset.results);
    document.querySelector('[data-view="results"]').hidden = !publishesPixels;
    document.querySelector('[data-view="masks"]').hidden = publishesPixels;
    document.querySelector('[data-view="geometry"]').hidden = publishesPixels;
    // The switch follows the layer: a model whose geometry opens hidden also opens locked, because a
    // drag that draws into a hidden layer is a gesture with no visible result.
    if (!view.geometry) editor = null;
    showView();
    showParameters(declaration, id);
    showExamples(id, declaration);
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
      datasetRows = new Map(datasets.datasets.map((entry) => [entry.id, entry.rows]));
      fill(dataset, datasets.datasets, (entry) =>
        entry.id + " (" + entry.rows + (entry.rows === 1 ? " row)" : " rows)"));
      syncSource();
      preview();
      const directory = await call("/models");
      buildModelChoices(directory.models);
      // A link may name the model it wants — `/play?model=<id>` — which is how a model page sends a
      // reader here with the choice already made. The id picks both dropdowns, so a family link lands
      // on the checkpoint it named. An id this gateway does not serve is ignored rather than shown:
      // the directory is the list of what answers, and a URL is not.
      const wanted = new URLSearchParams(location.search).get("model");
      // The directory is in id order, so a backbone sorts first and its answer is a grid to decode
      // before it is a picture. The landing model stays the one a first-time reader can run with no
      // parameters; the table marks it, and the fallback is the directory's own first entry.
      const landing = Object.keys(MODEL_PRESETS).find((id) => MODEL_PRESETS[id].landing);
      const chosen =
        directory.models.find((entry) => entry.id === wanted) ||
        directory.models.find((entry) => entry.id === landing) ||
        directory.models[0];
      if (chosen) {
        selectModel(chosen.id);
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
    variant.replaceChildren();
    variant.hidden = true;
    variant.disabled = true;
    familyOfId = new Map();
    familyChoices = new Map();
    // An answer still in flight belongs to the session the key just left, not to the controls that
    // are being emptied; the run's own check sees this and drops it.
    modelEpoch += 1;
    dataset.replaceChildren();
    parameters.replaceChildren();
    fields = [];
    typedText.clear();
    shownModel = "";
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

  const loadChosen = () => {
    const id = selectedModel();
    if (!id) return;
    // The request path, the declaration and the answer all follow the pair; a run already in flight
    // belongs to the model the reader just left, so it is told apart by this counter when it lands.
    modelEpoch += 1;
    // The picture stays, the answer does not: its layers, words and machine are the previous
    // model's claim about this photograph. `preview` then puts the photograph back with its caption.
    clearAnswer();
    preview();
    loadModel(id).catch((error) => {
      // The row now holds a message the ticker did not write, so the tick has to stop writing over it.
      clearFacts();
      say($("model-state"), error.message, false);
    });
  };

  // Changing the family fills the checkpoint dropdown and lands on its first member; changing the
  // checkpoint leaves the family where it is. Neither path writes the family anywhere: both end at
  // the id `selectedModel` reads from the pair.
  model.addEventListener("change", () => {
    syncVariant();
    loadChosen();
  });
  variant.addEventListener("change", loadChosen);

  $("source").addEventListener("change", () => {
    syncSource();
    preview();
  });
  syncSource();

  // A view toggle redraws from the answer already on screen: no request, and no decode — the mask
  // layer was built when that answer arrived.
  // Either switch chooses what the picture edits, and choosing one locks the other: the two lists
  // share the picture, and a press that meant one of them must not be read by both.
  for (const [element, type] of [[boxesMode, "boxes"], [pointsMode, "points"]]) {
    element.addEventListener("click", (event) => {
      const button = event.target.closest("[data-mode]");
      if (!button) return;
      setEditor(button.dataset.mode === "edit" ? type : null);
    });
  }

  $("display").addEventListener("click", (event) => {
    const button = event.target.closest("[data-view]");
    if (!button) return;
    const key = button.dataset.view;
    view[key] = !view[key];
    button.setAttribute("aria-pressed", String(view[key]));
    // Hiding the geometry layer locks the picture: a drag would make a mark nobody can see, and the
    // page would look as if the gesture had failed rather than the layer being off.
    if (key === "geometry" && !view.geometry) editor = null;
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
  // A feature field is not an answer over the picture but a reading of it, so it sits under the
  // photograph rather than replacing it; with the photograph off it is the whole picture.
  const FEATURE_TINT = 0.55;

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
  // Which geometry list the picture edits, if any — `"boxes"`, `"points"` or null. One at a time is
  // the whole rule: one press on the picture must not mean two things at once.
  let editor = "boxes";

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
    layer.hidden = !view.geometry;
    if (!last || !boxInput) {
      layer.replaceChildren();
      return;
    }
    layer.replaceChildren(...boxes.map((box, index) => {
      const element = document.createElement("div");
      element.className = "box";
      place(element, last.image, box[0], box[1], box[2], box[3]);
      // A box is positive unless the list says otherwise, so only the value that changes what it
      // means is written on it; a badge on every box would repeat what an absent one already says.
      if (box.length > 4 && box[4] === 0) {
        const badge = document.createElement("span");
        badge.className = "box-label";
        badge.textContent = "0";
        badge.title = "label 0: not the object";
        element.append(badge);
      }
      if (editor === "boxes") {
        moveBy(element, index);
        element.append(
          handleFor("resize tl", index),
          handleFor("resize br", index),
          handleFor("drop", index),
        );
        // The right button drops a box, the same gesture a point answers to: one meaning for the
        // second button across the geometry layers, rather than one layer with it and one without.
        element.addEventListener("contextmenu", (event) => {
          event.preventDefault();
          event.stopPropagation();
          boxes.splice(index, 1);
          commitBoxes();
        });
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
      boxInput.removeAttribute("title");
      // No write-back event: an empty field is the parameter sent nowhere, and the gateway's
      // "boxes is required" refusal is what a reader should meet if they run it like this.
    }
    showBoxes();
  };

  /** Write the list back into the input, which is what the query and the command read. */
  const commitBoxes = () => {
    boxInput.removeAttribute("aria-invalid");
    // An empty list is not a value this contract takes — the field means "no boxes" by being empty —
    // so removing the last box writes the empty field rather than `[]`, which would be refused.
    boxInput.value = boxes.length ? JSON.stringify(boxes) : "";
    // A script's own write fires no event, and the page reads the input rather than the list.
    boxInput.dispatchEvent(new Event("input", {bubbles: true}));
  };

  // The shape this contract publishes for a geometry value: N coordinates and an optional label of
  // 1 (positive) or 0 (negative), in the image's own pixels. Checked here the way the gateway checks
  // it, so a typo is marked in the field rather than discovered by a request; the gateway's copy
  // stays the authority, and this one only decides what the picture draws.
  //
  // The picture's size is the second half, and only this page has it: the gateway leaves bounds to
  // the backend, and nothing below the page decodes the image. A coordinate past the edge becomes a
  // normalized value outside [0, 1], which the encoder samples as padding — no error, and an answer
  // about nothing. Returns `{value}` or `{error}`; the error's sentence goes on the field's title,
  // because a red border can say that something is wrong but not what.
  const parseGeometry = (text, type, image) => {
    if (text.trim() === "") return {value: []};   // nothing typed yet, or everything deleted
    let value;
    try {
      value = JSON.parse(text);
    } catch {
      return {error: `params.${type} must be a JSON array of arrays`};
    }
    if (!Array.isArray(value) || !value.length) {
      return {error: `params.${type} must be a non-empty JSON array of arrays`};
    }
    const coordinates = type === "boxes" ? 4 : 2;
    const shaped = value.every((entry) =>
      Array.isArray(entry) &&
      (entry.length === coordinates || entry.length === coordinates + 1) &&
      entry.slice(0, coordinates).every((number) =>
        typeof number === "number" && Number.isFinite(number)) &&
      (entry.length === coordinates || entry[coordinates] === 0 || entry[coordinates] === 1));
    if (!shaped) {
      return {error: `each params.${type} entry is ${coordinates} numbers and an optional 1/0 label`};
    }
    if (image && !value.every((entry) => inside(entry, type, image))) {
      return {error: `params.${type} coordinates must be inside the picture`};
    }
    return {value};
  };

  // Whether one entry lies inside the picture, in the image's own pixels. A box must also have a
  // width and a height: a negative or empty extent is not a box any model can use.
  const inside = (entry, type, image) => {
    const [x, y] = entry;
    if (type === "points") {
      return x >= 0 && y >= 0 && x <= image.naturalWidth && y <= image.naturalHeight;
    }
    const width = entry[2], height = entry[3];
    return width > 0 && height > 0 && x >= 0 && y >= 0 &&
      x + width <= image.naturalWidth && y + height <= image.naturalHeight;
  };

  // The input's text as a list of boxes: `{value}` when it is one, a sentence when it is not.
  const parseBoxes = (text) => parseGeometry(text, "boxes", last && last.image);

  /** Take the input's text as the boxes on the picture, marking the field when it is not one. */
  const syncBoxes = () => {
    const parsed = parseBoxes(boxInput.value);
    if (!parsed.value) {
      boxInput.setAttribute("aria-invalid", "true");
      boxInput.title = parsed.error;
      return;
    }
    boxInput.removeAttribute("aria-invalid");
    boxInput.removeAttribute("title");
    boxes = parsed.value;
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
    if (editor !== "boxes" || !boxInput || !last || event.button !== 0) return;
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

  // The reader's points, as a second view of the `points` input and nothing more: every entry is
  // drawn where its numbers say, carrying the entry's own label. That label is the contract's — 1 and
  // 0 today — so the marker prints the value it was given rather than turning it into a vocabulary
  // of its own; a wider contract later would need no second renderer here.
  let pointInput = null;   // the input a `points` parameter created, or null for a model without one
  let points = [];         // the last list that parsed: [x, y, label] in the image's own pixels
  const showPoints = () => {
    const layer = $("points");
    // One eye for the reader's geometry, the same one the boxes answer to: the two lists are the
    // same kind of thing on the picture, and a second eye would be a control with no new question.
    layer.hidden = !view.geometry;
    layer.classList.toggle("editing", editor === "points");
    // The pointer says what a press will do before it lands: carry a point, or remove one.
    layer.classList.toggle("dropping", editor === "points" && pointMode === "drop");
    if (!last || !pointInput) {
      layer.replaceChildren();
      return;
    }
    layer.replaceChildren(...points.map((point, index) => {
      const label = point.length === 3 ? point[2] : 1;
      const element = document.createElement("span");
      element.className = "point-mark" + (label === 1 ? "" : " negative");
      element.textContent = String(label);
      element.title = label === 1 ? "label 1: a spot on the object" : "label 0: a spot that is not";
      element.style.left = (point[0] / last.image.naturalWidth) * 100 + "%";
      element.style.top = (point[1] / last.image.naturalHeight) * 100 + "%";
      if (editor === "points") {
        // The marker is the editor's while the points mode is on: the pressed mode decides whether a
        // press removes it or writes its label, and carrying it is the drag from the same press.
        // Stopping the press here is what keeps the picture from reading it as a new point.
        element.addEventListener("pointerdown", (event) => {
          if (event.button !== 0) return;
          event.stopPropagation();
          event.preventDefault();
          if (pointMode === "drop") {
            points.splice(index, 1);
            commitPoints();
            return;
          }
          paintPoint(element, index, Number(pointMode));
          dragPoint(element, index, event);
        });
        element.addEventListener("contextmenu", (event) => {
          event.preventDefault();
          event.stopPropagation();
          points.splice(index, 1);
          commitPoints();
        });
      }
      return element;
    }));
  };

  // One marker's label, changed where it is drawn: the press keeps the element it started on, which
  // is what the drag that may follow needs, and the input's text follows on commit.
  const paintPoint = (element, index, label) => {
    const point = points[index];
    points[index] = [point[0], point[1], label];
    element.textContent = String(label);
    element.classList.toggle("negative", label === 0);
    element.title = label === 1 ? "label 1: a spot on the object" : "label 0: a spot that is not";
  };

  // A point is carried rather than resized: its travel in the picture is its travel, held inside the
  // image because a point outside it is one no model can use.
  const dragPoint = (element, index, event) => {
    const [startX, startY] = imageAt(event);
    const origin = points[index].slice();
    const carried = (moveEvent) => {
      const [x, y] = imageAt(moveEvent);
      const moved = [
        Math.round(clamp(origin[0] + (x - startX), 0, last.image.naturalWidth)),
        Math.round(clamp(origin[1] + (y - startY), 0, last.image.naturalHeight)),
      ];
      if (origin.length === 3) moved.push(origin[2]);
      points[index] = moved;
      element.style.left = (moved[0] / last.image.naturalWidth) * 100 + "%";
      element.style.top = (moved[1] / last.image.naturalHeight) * 100 + "%";
    };
    const dropped = () => {
      element.removeEventListener("pointermove", carried);
      element.removeEventListener("pointerup", dropped);
      element.removeEventListener("pointercancel", dropped);
      commitPoints();
    };
    element.addEventListener("pointermove", carried);
    element.addEventListener("pointerup", dropped);
    element.addEventListener("pointercancel", dropped);
    try {
      element.setPointerCapture(event.pointerId);
    } catch {
      // nothing to do: the gesture is already under way
    }
  };

  /** Write the points back into the input, which is what the query and the command read. */
  const commitPoints = () => {
    if (!pointInput) return;
    pointInput.removeAttribute("aria-invalid");
    // As with the boxes: an empty list is not a value the contract takes, and an empty field is how
    // the page says there is nothing to send.
    pointInput.value = points.length ? JSON.stringify(points) : "";
    pointInput.dispatchEvent(new Event("input", {bubbles: true}));
  };

  /** Take the input's text as the points on the picture, marking the field when it is not one. */
  const syncPoints = () => {
    const parsed = parseGeometry(pointInput.value, "points", last && last.image);
    if (!parsed.value) {
      pointInput.setAttribute("aria-invalid", "true");
      pointInput.title = parsed.error;
      return;
    }
    pointInput.removeAttribute("aria-invalid");
    pointInput.removeAttribute("title");
    points = parsed.value;
    showPoints();
  };

  /** Drop every point, on a picture that is no longer the one they were typed for. */
  const forgetPoints = () => {
    points = [];
    if (pointInput) {
      pointInput.value = "";
      pointInput.removeAttribute("aria-invalid");
      pointInput.removeAttribute("title");
    }
    showPoints();
  };

  // A click on the picture adds a point; a drag across it adds nothing. The same travel that tells a
  // box from a click tells a point from a pan, and pressing a marker never reaches here because the
  // marker stops the press.
  let press = null;

  stage.addEventListener("pointerdown", (event) => {
    if (editor !== "points" || !pointInput || !last || event.button !== 0) return;
    press = {x: imageAt(event)[0], y: imageAt(event)[1], sx: event.clientX, sy: event.clientY};
  });

  stage.addEventListener("pointerup", (event) => {
    if (!press || event.button !== 0) return;
    const travelled = Math.abs(event.clientX - press.sx) >= MIN_DRAG ||
      Math.abs(event.clientY - press.sy) >= MIN_DRAG;
    const at = press;
    press = null;
    if (travelled) return;   // a drag across the picture is not a new point
    if (pointMode === "drop") return;   // a press on the picture removes nothing
    // The pressed mode is the entry's label, so what the row says is what the text and the marker say.
    points = points.concat([[Math.round(at.x), Math.round(at.y), Number(pointMode)]]);
    commitPoints();
  });

  stage.addEventListener("pointercancel", () => { press = null; });

  /** The picture changed: no coordinate on it describes the picture that replaces it. */
  const forgetGeometry = () => {
    forgetBoxes();
    forgetPoints();
  };

  // The answer on screen, kept so a view toggle redraws it: the image, the decoded mask layer, the
  // colours, and the address the picture came from. Nothing here is sent anywhere.
  let last = null;

  // Everything the eye buttons can change, drawn from what the answer already decoded — no request,
  // no second decode. The mask layer was composited once, at display size, when the answer arrived.
  const render = () => {
    if (!last) return;
    const {image, objects, colours, layer, url, feature, result} = last;
    // What the pane shows: the model's own picture where it made one, and the input otherwise. A
    // result replaces the frame rather than compositing over it — it is an answer, not a reading of
    // the input — so while it is on, the photograph and its layers are not drawn at all.
    const frame = view.results && result ? result : image;
    const scale = Math.min(1, MAX_SIDE / Math.max(frame.naturalWidth, frame.naturalHeight));
    canvas.width = Math.max(1, Math.round(frame.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(frame.naturalHeight * scale));
    const context = canvas.getContext("2d");
    if (frame !== image) {
      context.drawImage(frame, 0, 0, canvas.width, canvas.height);
    } else {
      // The photograph first, then the model's pixel layer over it. Each can be turned off: with the
      // layer off the photograph is the one thing on screen that is not a model's claim, and with the
      // photograph off the layer is read on its own — over a dark ground, because a pale tint on the
      // pane's light grey reads as nothing at all.
      if (view.image) {
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
      } else if (view.masks || view.results) {
        context.fillStyle = FIELD_BACKDROP;
        context.fillRect(0, 0, canvas.width, canvas.height);
      }
      if (view.results && feature) {
        context.globalAlpha = view.image ? FEATURE_TINT : 1;
        context.imageSmoothingEnabled = true;
        context.imageSmoothingQuality = "high";
        context.drawImage(feature.layer, 0, 0, canvas.width, canvas.height);
        context.globalAlpha = 1;
      }
      if (view.masks) context.drawImage(layer, 0, 0, canvas.width, canvas.height);
    }

    figure.hidden = false;
    fit(frame);
    fitted = frame;

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
    if (view.geometry) {
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
    // The picture's size is what a coordinate is judged against, so the lists are validated again now
    // that it is known: a value typed while the image was still loading arrives here, and a list
    // that was fine for the old picture cannot be — that picture's list was already cleared.
    if (boxInput) syncBoxes();
    if (pointInput) syncPoints();
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

  // A backbone's patches as three principal components: the standard way to see what an encoder
  // without a head has learned. The grid is rows×columns vectors of 1024 or 1280 numbers; projecting
  // it onto its first three components and mapping those to red, green and blue shows structure —
  // heads, stems, soil — that no single channel carries. The colours mean nothing between two
  // pictures: each answer supplies its own basis, which is why the caption says so.
  //
  // The components are found by power iteration on the data matrix, never by forming a covariance:
  // a 1024×1024 covariance is a billion multiply-adds, while one iteration here is two passes over
  // the patches. Ten iterations and three components are enough for a picture, and the seeds are
  // fixed, so the same answer draws the same colours twice.
  const FEATURE_COMPONENTS = 3;
  const FEATURE_ITERATIONS = 10;

  /** Scale a vector to unit length; answer its norm before scaling, or zero if it had none. */
  const unit = (vector) => {
    let squares = 0;
    for (let index = 0; index < vector.length; index += 1) squares += vector[index] * vector[index];
    const norm = Math.sqrt(squares);
    if (!(norm > 0)) return 0;
    for (let index = 0; index < vector.length; index += 1) vector[index] /= norm;
    return norm;
  };

  const featureLayer = (grid, rows, columns, dim) => {
    const patches = rows * columns;
    const mean = new Float32Array(dim);
    for (let patch = 0; patch < patches; patch += 1) {
      const base = patch * dim;
      for (let d = 0; d < dim; d += 1) mean[d] += grid[base + d];
    }
    for (let d = 0; d < dim; d += 1) mean[d] /= patches;
    const centred = new Float32Array(patches * dim);
    for (let patch = 0; patch < patches; patch += 1) {
      const base = patch * dim;
      for (let d = 0; d < dim; d += 1) centred[base + d] = grid[base + d] - mean[d];
    }

    const components = [];
    for (let k = 0; k < FEATURE_COMPONENTS; k += 1) {
      let vector = new Float32Array(dim);
      for (let d = 0; d < dim; d += 1) vector[d] = Math.sin((d + 1) * (k + 1) * 0.7);
      unit(vector);
      for (let iteration = 0; iteration < FEATURE_ITERATIONS; iteration += 1) {
        // y = X v, then w = X^T y: the covariance operator applied without ever forming it.
        const projection = new Float32Array(patches);
        for (let patch = 0; patch < patches; patch += 1) {
          const base = patch * dim;
          let sum = 0;
          for (let d = 0; d < dim; d += 1) sum += centred[base + d] * vector[d];
          projection[patch] = sum;
        }
        const next = new Float32Array(dim);
        for (let patch = 0; patch < patches; patch += 1) {
          const base = patch * dim;
          const weight = projection[patch];
          for (let d = 0; d < dim; d += 1) next[d] += centred[base + d] * weight;
        }
        // Keep it out of the components already found; twice, because one pass loses a little when
        // two eigenvalues are close.
        for (let pass = 0; pass < 2; pass += 1) {
          for (const component of components) {
            let dot = 0;
            for (let d = 0; d < dim; d += 1) dot += next[d] * component[d];
            for (let d = 0; d < dim; d += 1) next[d] -= dot * component[d];
          }
        }
        if (!unit(next)) break;
        vector = next;
      }
      // An eigenvector's sign is arbitrary; the sum fixes it, so the colours do not flip between runs.
      let total = 0;
      for (let d = 0; d < dim; d += 1) total += vector[d];
      if (total < 0) for (let d = 0; d < dim; d += 1) vector[d] = -vector[d];
      components.push(vector);
    }

    const channels = [
      new Float32Array(patches), new Float32Array(patches), new Float32Array(patches),
    ];
    for (let patch = 0; patch < patches; patch += 1) {
      const base = patch * dim;
      for (let d = 0; d < dim; d += 1) {
        const value = centred[base + d];
        channels[0][patch] += value * components[0][d];
        channels[1][patch] += value * components[1][d];
        channels[2][patch] += value * components[2][d];
      }
    }

    // A percentile stretch rather than the range: one unusual patch must not flatten every other
    // colour into the middle of the ramp.
    const stretch = (values) => {
      const sorted = Float32Array.from(values).sort();
      const low = sorted[Math.floor((sorted.length - 1) * 0.02)];
      const high = sorted[Math.floor((sorted.length - 1) * 0.98)];
      const span = high - low || 1;
      return (value) => Math.max(0, Math.min(1, (value - low) / span));
    };
    const red = stretch(channels[0]);
    const green = stretch(channels[1]);
    const blue = stretch(channels[2]);

    const layer = document.createElement("canvas");
    layer.width = columns;
    layer.height = rows;
    const context = layer.getContext("2d");
    const pixels = context.createImageData(columns, rows);
    for (let patch = 0; patch < patches; patch += 1) {
      const at = patch * 4;
      pixels.data[at] = Math.round(red(channels[0][patch]) * 255);
      pixels.data[at + 1] = Math.round(green(channels[1][patch]) * 255);
      pixels.data[at + 2] = Math.round(blue(channels[2][patch]) * 255);
      pixels.data[at + 3] = 255;
    }
    context.putImageData(pixels, 0, 0);
    return layer;
  };

  /** Decode one answer into a mask layer once, then let `render` draw it in whatever view is on. */
  const paint = async (url, objects, palette = new Map(), answer = {}) => {
    const image = await loadImage(url);
    const scale = Math.min(1, MAX_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
    const width = Math.max(1, Math.round(image.naturalWidth * scale));
    const height = Math.max(1, Math.round(image.naturalHeight * scale));

    const colours = objects.map((object, order) => colourOf(object, order, palette));
    const maskDecoder = typeof GlobalWheat?.rle_decode === "function";
    const layer = document.createElement("canvas");
    layer.width = width;
    layer.height = height;
    const painting = layer.getContext("2d");
    let decoded = 0, declared = 0, checked = 0;
    objects.forEach((object, order) => {
      if (!object.rle || !maskDecoder) return;
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

    // A backbone's grid is decoded and projected once, here, so a view toggle redraws the colours the
    // answer arrived with rather than decoding 2 MB again. A failure is kept as a sentence for the
    // caption: the response body is complete either way, and the photograph is still the one thing on
    // screen nobody has to decode.
    let feature = null;
    let featureError = null;
    if (answer.features) {
      if (typeof GlobalWheat?.decode_features === "function") {
        try {
          const [rows, columns] = answer.features.size;
          const dim = answer.features.dim;
          const grid = GlobalWheat.decode_features(answer.features);
          feature = {
            layer: featureLayer(grid, rows, columns, dim),
            rows, columns, dim,
            bytes: rows * columns * dim * 2,
          };
        } catch (error) {
          featureError = error.message;
        }
      } else {
        featureError = "the client does not decode features";
      }
    }

    // A counting answer's two fields travel with the picture exactly as `objects` does, so a view
    // toggle redraws them from the answer rather than from the response body.
    //
    // A model that answers with a picture is decoded here, once, for the same reason: a view toggle
    // redraws it instead of re-reading a megabyte of base64. A `data:` URL is the shortest path from
    // what the answer carries to an `<img>`, the page already admits `data:` for images, and nothing
    // has to be decoded by hand to show a file the container has already encoded.
    let result = null;
    let resultError = null;
    if (answer.image && answer.image.data) {
      try {
        result = await loadImage("data:" + answer.image.media_type + ";base64," + answer.image.data);
      } catch (error) {
        resultError = error.message;
      }
    }
    last = {
      image: image,
      objects: objects,
      colours: colours,
      layer: layer,
      url: url,
      count: answer.count,
      points: answer.points,
      feature: feature,
      featureError: featureError,
      result: result,
      resultError: resultError,
      text: typeof answer.answer === "string" ? answer.answer.trim() : "",
      warnings: Array.isArray(answer.warnings) ? answer.warnings : [],
    };
    render();
    return {
      decoder: maskDecoder,
      decoded: decoded,
      declared: declared,
      checked: checked,
      // The image's own size, so a caption describes what was drawn rather than what an index said.
      width: image.naturalWidth,
      height: image.naturalHeight,
    };
  };

  // What to try when an answer is empty, in the controls this model actually declares. Zero objects
  // with no warning is the one moment the page owes the reader a next step: an empty answer otherwise
  // reads as a threshold set too high, which is the wrong lever to spend a request on. A model that
  // declares no free text and no geometry has nothing to name, and says nothing rather than something
  // false.
  const emptyHint = () => {
    // Declared types, not names: a page built from `GET /models` cannot mention today's parameters.
    // Free text is what "another phrase" means; a boxes or points parameter is what "another
    // example" means, named as the control that parameter gives the reader.
    const types = new Set(fields.map((field) => field.type));
    const example = types.has("boxes") ? "box" : types.has("points") ? "point" : null;
    if (types.has("text")) {
      return example
        ? "0 objects · try another phrase, or draw an example " + example
        : "0 objects · try another phrase";
    }
    return example ? "0 objects · try another example " + example : "";
  };

  const draw = async (url, answer, palette) => {
    const objects = answer.objects || [];
    // Written before the picture, so the watermark is there when `paint` renders it.
    showMachine(answer);
    const painted = await paint(url, objects, palette, answer);
    // The body is printed for transparency, but a feature grid is 2.6 MB of base64 and a text node
    // that size makes the page unusable. The length is the useful fact; the numbers are in the API's
    // own answer and in `features.grid` beside it.
    $("response-body").textContent = JSON.stringify(
      answer,
      (name, value) => {
        if (name === "data" && typeof value === "string" && value.length > 4096) {
          return "… " + value.length + " base64 characters";
        }
        if (name === "embedding" && Array.isArray(value) && value.length > 64) {
          return ["… " + value.length + " floats"];
        }
        return value;
      },
      2,
    );
    $("response").hidden = false;

    // The answer in words, if the model gave one. Opened with every new answer: a reader who asked a
    // question should not have to find the answer behind a disclosure, and one who is done with it
    // closes it — the state is the reader's, not the page's, so it is not reset here.
    $("answer-text").textContent = last.text;
    $("answer").hidden = !last.text;
    if (last.text) $("answer").open = true;

    // The caption is one line beside four view buttons, so it says the one thing worth checking —
    // that the decoded mask covers exactly the area the answer declared — in as few words as it can.
    const tally = objects.length + (objects.length === 1 ? " object" : " objects");
    if (last.featureError) {
      say($("check"), "features: " + last.featureError, false);
    } else if (last.feature) {
      // What a feature answer can be checked against: the grid the response declared, its own byte
      // size, and the fact that the colours are this picture's own projection rather than classes.
      const warning = last.warnings[0];
      const grid = last.feature;
      const megabytes = (grid.bytes / 1e6).toFixed(1);
      say($("check"), warning ? warning.message :
        grid.rows + " × " + grid.columns + " × " + grid.dim + " · " + megabytes +
        " MB · colours: PCA of this image", null);
      $("check").classList.toggle("warn", Boolean(warning));
    } else if (last.resultError) {
      say($("check"), "picture: " + last.resultError, false);
    } else if (last.result || last.text) {
      // A picture or a sentence is the whole answer, so the caption says what came back rather than
      // counting objects the model never claimed. A warning still outranks it: a clamped frame is the
      // one thing about such an answer that the reader has to know before judging the result.
      const warning = last.warnings[0];
      const fact = last.result
        ? last.result.naturalWidth + " × " + last.result.naturalHeight + " · the model's own picture"
        : "text answer · " + last.text.length + " characters";
      say($("check"), warning ? warning.message : fact, null);
      $("check").classList.toggle("warn", Boolean(warning));
    } else if (!objects.length) {
      // A counting answer has no geometry to check: the count beside this line is the answer, and
      // "0 objects" would read as a model that found nothing. What is left to say is a warning —
      // an exemplar past the model's finest window, for one — and it belongs here, not in a toast.
      // A warning outranks the hint, because it already says why the answer is empty.
      const warning = last.warnings[0];
      const hint = typeof last.count === "number" ? "" : emptyHint();
      say($("check"), warning ? warning.message : hint, null);
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

  /**
   * Drop the answer on screen while keeping the picture it was drawn on.
   *
   * A change of model is not a change of image: the photograph and the reader's boxes stay, and only
   * what the previous model claimed goes. The canvas is redrawn from the image the answer arrived
   * with, synchronously, so a mask is never left on a picture whose model no longer exists on the
   * page. The address stays too, which is what keeps the next run about the same picture.
   */
  const clearAnswer = () => {
    previewToken += 1;   // a preview in flight belongs to the answer being dropped
    endDrag();
    const image = last && last.image;
    if (image) {
      const scale = Math.min(1, MAX_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
      fitted = image;
      fit(image);
      figure.hidden = false;
    }
    marks.replaceChildren();
    $("count").hidden = true;
    $("count").textContent = "";
    $("legend").replaceChildren();
    $("legend").hidden = true;
    $("full").hidden = true;
    $("answer").hidden = true;
    $("answer-text").textContent = "";
    $("check").textContent = "";
    $("check").className = "";
    showMachine(null);
    $("response").hidden = true;
    last = null;
    shownKey = "";
  };

  /** Empty the viewer: nothing on screen is the picture the controls no longer name. */
  const clearViewer = () => {
    clearAnswer();
    figure.hidden = true;
    $("boxes").replaceChildren();
    $("points").replaceChildren();
    fitted = null;
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
      forgetGeometry();
    }
    // A run owns the pane while it runs, and afterwards the pane is showing an answer about this
    // very picture — previewing it again would only take that answer off.
    if (running || picture === shownKey) return;
    // A preview is not an answer. The previous model's layers, words and machine go as soon as the
    // picture is no longer the one the controls name; the photograph is redrawn by the paint below.
    clearAnswer();
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
    // A request to this pool can take half a minute — a mask model writes one box per instance and
    // decodes a mask for each — and a line that never changes reads as a page that has stopped. The
    // clock counts how long the reader has been waiting, which is the page's own fact; it says
    // nothing about what the model is doing, and nothing here pretends to know.
    const startedAt = Date.now();
    say(state, "running… 0 s");
    const clock = setInterval(() => {
      say(state, "running… " + Math.round((Date.now() - startedAt) / 1000) + " s");
    }, 1000);
    // A preview still in flight belongs to a choice this run has moved past, and must not be allowed
    // to draw over the result.
    previewToken += 1;
    try {
      // The id, read once with the rest: the controls may change while the request is in flight, and
      // this run must keep naming the model it started with.
      const chosen = encodeURIComponent(selectedModel());
      const epoch = modelEpoch;
      const demo = demoRun;
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
        forgetGeometry();
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
      // A demo example's answer is already in the cache; the wait is the page's, not the model's,
      // and it happens before the answer is drawn so a changed model still wins the epoch check.
      if (demo) await new Promise((resolve) => setTimeout(resolve, 3000));
      // The reader changed the model while this request was at a backend. The answer is about the
      // model they left, so it is not drawn under the model they chose; the picture is still theirs.
      if (epoch !== modelEpoch) {
        say(state, "the model changed; this answer was not drawn", null);
        return;
      }
      await draw(imageUrl, answer, palette);
      if (epoch !== modelEpoch) {
        // The change landed while the answer was being decoded: withdraw what `draw` just put up.
        clearAnswer();
        say(state, "the model changed; this answer was not drawn", null);
        return;
      }
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
      // First, so a tick cannot overwrite what the catch is about to say, or the clear below.
      clearInterval(clock);
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
    const id = selectedModel();
    if (!key || !id || document.visibilityState !== "visible") return;
    try {
      const directory = await call("/models");
      // The reader may have changed the pair while the directory was in flight; an answer about the
      // model they left must not write over the facts of the one they chose.
      if (id !== selectedModel()) return;
      const entry = directory.models.find((candidate) => candidate.id === id);
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
