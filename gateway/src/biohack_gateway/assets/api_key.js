// One key, one place: the store both framed pages read and write.
//
// The reference page and the model page are served by the API host, so they are same-origin and see
// the same `localStorage`. A key entered on either is the key the other uses, and clearing it on
// either clears it for both — whether they are read as frames of the documentation site or opened on
// their own. There is deliberately **one** entry, and it is ours rather than Swagger UI's: two copies
// of a secret cannot be cleared together, and the page that would have to clear the copy it does not
// own is the one that cannot see it.
//
// Nothing above this needs to know whether storage worked. A browser in private mode, a frame whose
// third-party storage is blocked or partitioned away from the rest of the site, a quota error — all of
// them leave the key held for the lifetime of the page instead, which is what the page did before
// there was a store at all.
// A module is strict, and a strict module cannot assign to an undeclared name: the store is published
// through `globalThis` so the sibling scripts (modules of their own) can read it as `apiKeyStore`.
globalThis.apiKeyStore = (() => {
  "use strict";
  const KEY = "biohack-gateway/api-key";
  const LEGACY = "authorized"; // Swagger UI's own entry, from when it persisted the key for us.
  const watchers = [];
  let held = "";

  const read = () => {
    try {
      return localStorage.getItem(KEY);
    } catch (error) {
      // `undefined`, not `null`: "this browser will not tell me" and "there is nothing stored" are
      // different answers, and the difference is what Forget depends on. A `null` for both let a
      // page that had just been told the key was cleared fall back to the copy it still held.
      return undefined;
    }
  };

  const write = (name, value) => {
    try {
      if (value === null) localStorage.removeItem(name);
      else localStorage.setItem(name, value);
    } catch (error) {
      // Held for this page's lifetime instead, which is what every caller already handles.
    }
  };

  const announce = (value) => {
    for (const watcher of watchers) watcher(value);
  };

  // Another page of the same origin changed the key — the other framed page in another tab. The
  // `storage` event fires in every document but the one that wrote, which is the direction that is
  // wanted: the page that did the writing already knows.
  window.addEventListener("storage", (event) => {
    if (event.key !== null && event.key !== KEY) return; // null is a whole-storage clear
    const stored = read();
    if (stored === undefined) return; // nothing can be learned here, so nothing changes
    // The held copy follows the store, including on a clear: this is the document that has to
    // forget a key another page removed, and the in-memory copy is the only place it still exists.
    held = stored === null ? "" : stored;
    announce(held);
  });

  // A key left in Swagger UI's own entry by the version of the reference page that let it persist is
  // adopted once, and that entry dropped: the reader keeps the key they already entered, and this
  // origin stops holding two copies of it. Whatever scheme Swagger had remembered, its value is the
  // key — no name of ours has to be repeated to read it.
  const adopt = () => {
    let legacy = null;
    try {
      legacy = localStorage.getItem(LEGACY);
    } catch (error) {
      return;
    }
    if (legacy === null) return;
    try {
      for (const entry of Object.values(JSON.parse(legacy) || {})) {
        if (entry && typeof entry.value === "string" && entry.value) {
          localStorage.setItem(KEY, entry.value);
          held = entry.value;
          break;
        }
      }
    } catch (error) {
      // Not the shape we knew, so there is nothing to adopt.
    }
    write(LEGACY, null);
  };
  adopt();

  return {
    /** The key in use, or an empty string when there is none. */
    get: () => {
      const stored = read();
      // Only a store that cannot be read is what the held copy stands in for. A store that answers
      // "nothing" is an answer, and it is the one a cleared key leaves behind.
      if (stored === undefined) return held;
      return stored === null ? "" : stored;
    },

    /** Remember a key: for this browser where that is possible, for this page where it is not. */
    set: (value) => {
      held = value;
      write(LEGACY, null); // no second copy of the key on this origin
      write(KEY, value);
      announce(value);
    },

    /** Forget it everywhere: the stored copy, the legacy one, and this page. */
    clear: () => {
      held = "";
      write(LEGACY, null);
      write(KEY, null);
      announce("");
    },

    /** Be told when another document of this origin sets or clears the key. */
    onChange: (watcher) => {
      watchers.push(watcher);
    },
  };
})();
