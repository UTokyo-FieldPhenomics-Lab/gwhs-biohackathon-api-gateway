  (() => {
    // The reference is read inside the documentation site's frame, and a framed page cannot show
    // Swagger UI's own Authorize dialog where the reader is looking: the frame is as tall as the
    // page, so a centred dialog opens somewhere below the fold. This field is where a key goes
    // instead — it calls what the dialog calls, in the place a reader looks for page-wide settings.
    const SCHEME = "__SCHEME__";
    const bar = document.getElementById("key-bar");
    const form = document.getElementById("key-form");
    const input = document.getElementById("key-input");
    const state = document.getElementById("key-state");
    if (!bar || !form || !input || !state || typeof ui === "undefined") {
      // No bar rather than a dead one: without Swagger's own object a field would swallow keys.
      if (bar) bar.remove();
      return;
    }

    // Swagger's servers band is the strip between the description and the call list, where a reader
    // expects page-wide settings to live — and the band whose background makes this row part of the
    // page rather than something laid over it. React renders that band and has never heard of this
    // node, so a re-render can drop it; that is what the observer at the end is for.
    const anchor = () =>
      document.querySelector("#swagger-ui .scheme-container") ||
      document.querySelector("#swagger-ui .information-container");
    const place = () => {
      const target = anchor();
      if (!target) return;
      if (bar.parentElement !== target) target.prepend(bar);
      bar.hidden = false;
    };

    // Read Swagger's state rather than keeping a copy: the key is stored by Swagger (the page asks
    // it to persist one), and one copy of a secret is enough. A failure here says nothing rather
    // than something wrong.
    const describe = () => {
      try {
        const held = ui.authSelectors.authorized().get(SCHEME);
        state.dataset.set = held ? "yes" : "no";
        const text = held
          ? "a key is in use — paste another to replace it"
          : "no key yet — paste your team's token, and every call below can be tried";
        // Only when it changed: the observer that keeps this line true watches the container this
        // line is in, and assigning the same text still replaces the text node — a mutation that
        // changes nothing is a mutation, and the callback would call itself without yielding.
        if (state.textContent !== text) state.textContent = text;
      } catch {
        if (state.textContent !== "") state.textContent = "";
      }
    };

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const value = input.value.trim();
      if (!value) return;
      // Swagger remembers a key only when it is handed in the variant its own dialog uses, with the
      // definition it applies to; `preauthorizeApiKey` sets it for this page and loses it on the
      // next visit, which is the one thing this page asks Swagger to do for it.
      const schema = ui.specSelectors.securityDefinitions().get(SCHEME);
      if (schema && ui.authActions.authorizeWithPersistOption) {
        ui.authActions.authorizeWithPersistOption({[SCHEME]: {value: value, schema: schema}});
      } else {
        ui.preauthorizeApiKey(SCHEME, value);
      }
      input.value = "";
      describe();
    });
    document.getElementById("key-forget").addEventListener("click", () => {
      const forget = ui.authActions.logoutWithPersistOption || ui.authActions.logout;
      forget([SCHEME]);
      describe();
    });

    // Swagger's own words for the two states describe its idea of them — "locked" once a key is
    // set — which is the opposite of what the exchanged icons say. The label follows the icon, so a
    // reader who cannot see it hears what the picture shows.
    const relabel = () => {
      for (const button of document.querySelectorAll("#swagger-ui .authorization__btn")) {
        const label = button.querySelector("svg.locked")
          ? "this call sends your key"
          : "this call needs your key";
        if (button.getAttribute("aria-label") !== label) button.setAttribute("aria-label", label);
        if (button.getAttribute("title") !== label) button.setAttribute("title", label);
      }
    };

    // Placed, kept placed, and kept relabelled: anything Swagger re-renders inside its container
    // can take this node with it or restore its own words, and putting both back is a few selectors
    // The same observation keeps the state line true, because the two things that change it are
    // re-renders of this container — the specification arriving, and Swagger restoring a remembered
    // key when it mounts. `load` covers a restore that lands without one, so nothing polls.
    const settle = () => {
      place();
      describe();
      relabel();
    };
    new MutationObserver(settle).observe(document.querySelector("#swagger-ui"), {
      childList: true,
      subtree: true,
    });
    window.addEventListener("load", settle);
    settle();
    document.documentElement.classList.add("key-bar-ready");
  })();
