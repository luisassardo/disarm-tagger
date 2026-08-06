/* Tool side of the Desk Embed Contract (STD-024).

   With ?embed=1 the desk is already drawing the rail, the topbar and the
   language toggle, so we hide ours and show only the work area: one chrome,
   not two. The desk then tells us which language it is in, and we follow it,
   so switching language in the desk switches it here too.

   The session needs nothing from this file: the cookie is set for
   .j-lab.tools and travels by itself. */
(function () {
  "use strict";
  var PLATFORM = "https://j-lab.tools";
  if (!document.body.classList.contains("embedded")) return;

  function applyLang(lang) {
    if (lang !== "en" && lang !== "es") return;
    if (window.ArgusLang && window.ArgusLang.set) window.ArgusLang.set(lang);
  }

  window.addEventListener("message", function (e) {
    if (e.origin !== PLATFORM) return;          // only the desk may steer us
    var d = e.data;
    if (!d || d.type !== "argus") return;
    if (d.lang) applyLang(d.lang);
  });

  // Tell the desk what to put in its bar, once we know our own title.
  window.addEventListener("DOMContentLoaded", function () {
    if (window.parent === window) return;
    try {
      window.parent.postMessage(
        { type: "argus", v: 1, title: document.title.split("·")[0].trim() }, PLATFORM);
    } catch (e) { /* the desk may be gone; nothing to do */ }
  });
})();
