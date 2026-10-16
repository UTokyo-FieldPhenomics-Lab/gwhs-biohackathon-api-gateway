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

    // What Swagger currently holds, if anything. Read rather than kept: the page that holds a key in
    // its own store is Swagger, and one copy of a secret is enough. What Swagger no longer owns is
    // *persistence* — this page asks it not to remember anything (`persistAuthorization` is false),
    // because the key is remembered in one place shared with the model page, and two remembered copies
    // cannot be cleared together.
    const authorized = () => {
      try {
        return ui.authSelectors.authorized().get(SCHEME) || null;
      } catch {
        return null; // Swagger has not finished loading its spec, which is not an error to report
      }
    };

    const describe = () => {
      const held = authorized();
      state.dataset.set = held ? "yes" : "no";
      // Only when it changed: the observer that keeps this line true watches the container this
      // line is in, and assigning the same text still replaces the text node — a mutation that
      // changes nothing is a mutation, and the callback would call itself without yielding.
      const text = held
        ? "a key is in use — paste another to replace it"
        : "no key yet — paste your team's token, and every call below can be tried";
      if (state.textContent !== text) state.textContent = text;
    };

    // Arm Swagger for this page, and let the store handle remembering: `preauthorizeApiKey` sets the
    // key for the session and stops there, which is exactly right — the browser remembers it because
    // the store wrote it down, and nothing remembers it twice.
    const arm = (value) => {
      ui.preauthorizeApiKey(SCHEME, value);
    };

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const value = input.value.trim();
      if (!value) return;
      arm(value);
      apiKeyStore.set(value);
      input.value = "";
      describe();
    });
    document.getElementById("key-forget").addEventListener("click", () => {
      const forget = ui.authActions.logoutWithPersistOption || ui.authActions.logout;
      forget([SCHEME]);
      // Ours is the copy that matters; `logoutWithPersistOption` also drops the one the previous
      // version of this page left behind in Swagger's own storage.
      apiKeyStore.clear();
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
    // can take this node with it or restore its own words, and putting both back is a few selectors.
    // The same observation keeps the state line true, and it is also where a remembered key is
    // applied: `preauthorizeApiKey` does nothing until Swagger has loaded the document's security
    // definitions, and this script runs before that fetch has answered. Applying the store's key here
    // — every time the page settles, and only while Swagger has none — is what makes a key the reader
    // entered on the model page work on this one without being pasted again.
    const settle = () => {
      place();
      const stored = apiKeyStore.get();
      if (stored && !authorized()) arm(stored);
      describe();
      relabel();
    };
    new MutationObserver(settle).observe(document.querySelector("#swagger-ui"), {
      childList: true,
      subtree: true,
    });
    window.addEventListener("load", settle);

    // A key set or cleared on the model page reaches this one while it is open: the store announces
    // it, and Swagger is armed or disarmed with what it now holds — or armed by `settle` as soon as
    // the document's security definitions exist, if that fetch has not answered yet.
    apiKeyStore.onChange((value) => {
      if (value) arm(value);
      else (ui.authActions.logout || ui.authActions.logoutWithPersistOption)([SCHEME]);
      settle();
    });

    settle();
    document.documentElement.classList.add("key-bar-ready");
  })();
