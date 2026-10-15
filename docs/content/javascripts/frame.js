// Give the framed Swagger UI the height of its own content, so the reference scrolls with this page
// instead of inside a box with a scrollbar of its own.
//
// A cross-origin frame cannot be measured from here, so the API host does the measuring: it injects a
// reporter into `/docs` that posts `{docsHeight}` to this window, and this file listens for it. If the
// API host is unreachable — or has not sent anything yet — the frame keeps the height the page gives
// it, which is what a reader on a blocked network sees.
const FRAME_ORIGIN = "https://api.global-wheat.com";

window.addEventListener("message", (event) => {
  if (event.origin !== FRAME_ORIGIN) return;
  const height = event.data && event.data.docsHeight;
  const frame = document.querySelector("#reference-frame iframe");
  if (height && frame) frame.style.height = `${Math.max(Number(height), 600)}px`;
});
