  // The documentation site frames this page, and a cross-origin frame cannot be measured from
  // outside: the page reports its own height so the frame grows to its content and the reader
  // scrolls one page instead of two nested ones. Nothing here reads or sends anything but that
  // number, and only to the documentation site.
  //
  // The height is the bottom edge of the page's own container, not `documentElement.scrollHeight`:
  // observing `documentElement` catches a section expanding and misses it collapsing, because a
  // document's box does not shrink back to its content while the frame stays tall. The container
  // does shrink, so it is what gets observed — together with the class changes a framework makes
  // when a section is toggled, which is what a collapse looks like. `EXTRA` is counted as well,
  // where a page keeps a row of its own above that container: the reference's key field does, and a
  // frame that ignored it would cut off the one control a reader needs first.
  //
  // The same channel carries the theme: the site says which side of the palette it is on, as a
  // query parameter for the first paint and as a message whenever the reader switches.
  const SITE_ORIGIN = "__PARENT_ORIGIN__";
  const CONTAINER = "__CONTAINER__";
  const EXTRA = "__EXTRA__";
  const apply_scheme = (scheme) => {
    if (scheme === "dark" || scheme === "light") document.documentElement.dataset.scheme = scheme;
  };
  apply_scheme(
    new URLSearchParams(location.search).get("scheme")
      || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"),
  );

  if (window.parent !== window) {
    // Framed: the site around the frame owns the height, so this page must not scroll — a scrollbar
    // here would also narrow the viewport, reflow the text a few pixels taller, and keep itself
    // alive. Opened directly, nothing is hidden and the page scrolls as it intends.
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
  }
  const container = () => document.querySelector(CONTAINER) || document.body;
  const report_height = () => requestAnimationFrame(() => {
    // Opened on its own there is nobody to size, and a message addressed to the site's origin
    // from a page that is not framed is a console warning and nothing else.
    if (window.parent === window) return;
    const extra = EXTRA ? document.querySelector(EXTRA) : null;
    const bottom = Math.max(
      container().getBoundingClientRect().bottom,
      extra ? extra.getBoundingClientRect().bottom : 0,
    );
    window.parent.postMessage({docsHeight: Math.ceil(bottom + window.scrollY)}, SITE_ORIGIN);
  });
  window.addEventListener("message", (event) => {
    if (event.origin !== SITE_ORIGIN || !event.data) return;
    apply_scheme(event.data.scheme);
  });
  new ResizeObserver(report_height).observe(container());
  new MutationObserver(report_height).observe(document.body, {
    subtree: true,
    attributes: true,
    attributeFilter: ["class", "style", "hidden"],
  });
  window.addEventListener("load", report_height);
  window.addEventListener("resize", report_height);
