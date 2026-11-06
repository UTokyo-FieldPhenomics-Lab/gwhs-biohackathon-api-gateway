// Wire the framed API pages: their height, and which side of the palette they should render.
//
// Two pages of the API host are framed from this site — the interactive reference, and the page a
// participant runs one model from — and both report their own height the same way. A cross-origin
// frame cannot be measured from here, so the API host does the measuring: it injects a reporter into
// the page that posts `{docsHeight}` to this window, and this file applies it. The same channel runs
// the other way for the theme, because the site's palette is the reader's choice rather than the
// operating system's: the frame is asked for the current scheme once it loads, and told again
// whenever the reader switches. If the API host is unreachable — or has not answered yet — the frame
// keeps the height the page gives it, and the reader sees the failure rather than a stick.
//
// A frame is marked with `data-api-frame` rather than matched by the id of the box it happens to sit
// in, so one file sizes every frame this site shows; which page they are on is the page's business.
const FRAME_ORIGIN = "https://api.global-wheat.com";

/** The frame, wherever the page currently is — instant navigation replaces it without a reload. */
const frame = () => document.querySelector("iframe[data-api-frame]");

/** Which palette this site is on: Material publishes it as an attribute on `body`. */
const siteScheme = () =>
  document.body.dataset.mdColorScheme === "slate" ? "dark" : "light";

/** Tell the frame which palette the reader chose.
 *
 * Only once the frame has actually left this origin: posting to a window that never loaded the API
 * host — a blocked network, a host that is down — addresses the parent's own origin and is dropped
 * with a console warning. A cross-origin window's `location` throws when read, which is exactly the
 * test: readable means not ours to talk to yet.
 */
const tellScheme = (target) => {
  if (!target || !target.contentWindow) return;
  try {
    void target.contentWindow.location.href;
    return;
  } catch {
    target.contentWindow.postMessage({ scheme: siteScheme() }, FRAME_ORIGIN);
  }
};

/** Give the frame the scheme in its URL, so its first paint is already right. */
const startFrame = (target) => {
  if (target.dataset.frameWired) return;
  target.dataset.frameWired = "1";
  const base = target.src.split("?")[0];
  target.src = `${base}?scheme=${siteScheme()}`;
  target.addEventListener("load", () => tellScheme(target));
};

const scan = () => {
  const target = frame();
  if (target) {
    startFrame(target);
    tellScheme(target);
  }
};

window.addEventListener("message", (event) => {
  if (event.origin !== FRAME_ORIGIN || !event.data) return;
  const height = event.data.docsHeight;
  const target = frame();
  if (height && target) target.style.height = `${Math.max(Number(height), 600)}px`;
});

// The frame appears on a fresh load and again on every in-site navigation, and the palette changes
// whenever the reader touches the toggle: one observer covers all three.
new MutationObserver(scan).observe(document.body, {
  childList: true,
  subtree: true,
  attributes: true,
  attributeFilter: ["data-md-color-scheme"],
});
document.addEventListener("DOMContentLoaded", scan);
scan();
