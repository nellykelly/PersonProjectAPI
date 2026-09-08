// Site-wide behavior: fade-in (mirrors Dimension's is-preload pattern
// from main.css) and the mobile nav toggle. Vanilla JS -- no jQuery
// dependency needed for this small amount of interaction.
//
// is-preload only suppresses entry animations, so it's cleared as soon
// as the DOM is parsed -- it must NOT wait on window.load, which doesn't
// fire until every font/image/CDN script has settled. A single stalled
// subresource would otherwise leave animations dead for the life of the
// page. window.load stays wired up too as a belt-and-suspenders.
function clearPreload() {
  document.body.classList.remove("is-preload");
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", clearPreload);
} else {
  clearPreload();
}
window.addEventListener("load", clearPreload);

// Welcome gate: an opaque, full-viewport, z-index 9999 overlay (see
// #welcome-gate in custom.css) that masks a slow first paint. It's
// pointer-events: none, so the page under it is already scrollable while
// it's visible -- these timers only control how long the WELCOME curtain
// is *shown*, not how long input is blocked (it never is). Dismissal
// still must not depend on window.load, which waits on every font, image
// and CDN script; the DOM being parsed is the real "there's something to
// show" signal, and MAX_MS is a short safety net for a throttled
// background tab or a DOMContentLoaded that never fires.
//
// This IIFE runs from a script tag at the very end of <body>, so
// #welcome-gate already exists and readyState is typically "interactive".
(function initWelcomeGate() {
  var gate = document.getElementById("welcome-gate");
  if (!gate) return;
  if (document.documentElement.classList.contains("skip-welcome")) return;

  // MIN_MS keeps the gate from flashing like a broken frame on a fast
  // connection: the word's draw-in animation (custom.css) runs 1.1s, so
  // this lets it finish. MAX_MS is the ceiling on how long the curtain
  // stays on screen. Both are short now that the gate never blocks input.
  var MIN_MS = 1100;
  var MAX_MS = 1800;
  var start = Date.now();
  var finished = false;

  function finish() {
    if (finished) return;
    finished = true;
    try { sessionStorage.setItem("welcomeShown", "1"); } catch (e) { /* private mode, etc. */ }
    gate.classList.add("is-leaving");
    setTimeout(function () { gate.remove(); }, 250);
  }

  function scheduleFinish() {
    setTimeout(finish, Math.max(0, MIN_MS - (Date.now() - start)));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scheduleFinish);
  } else {
    scheduleFinish();
  }

  setTimeout(finish, MAX_MS);
})();

document.addEventListener("DOMContentLoaded", function () {
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.querySelector(".site-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      nav.classList.toggle("is-open");
    });
  }

  initScrollReveal();
  initTooltipEdgeAlignment();
  initLandingStatsFocus();
  initBioNetwork();
});

// Landing-page "by the numbers" row: hovering one stat blurs+dims the
// others. Scoped to .landing-stats specifically so the shared .stat-tile
// component elsewhere (e.g. the trading risk report) is unaffected --
// this no-ops on every other page.
function initLandingStatsFocus() {
  var grid = document.querySelector(".landing-stats");
  if (!grid) return;
  var tiles = grid.querySelectorAll(".stat-tile");

  Array.prototype.forEach.call(tiles, function (tile) {
    tile.addEventListener("mouseenter", function () {
      grid.classList.add("is-stat-hovering");
      tile.classList.add("is-stat-active");
    });
    tile.addEventListener("mouseleave", function () {
      grid.classList.remove("is-stat-hovering");
      tile.classList.remove("is-stat-active");
    });
    tile.addEventListener("focus", function () {
      grid.classList.add("is-stat-hovering");
      tile.classList.add("is-stat-active");
    });
    tile.addEventListener("blur", function () {
      grid.classList.remove("is-stat-hovering");
      tile.classList.remove("is-stat-active");
    });
  });
}

// Bio network on the home page: draws the pulsating lines from the
// center bubble to each satellite, and highlights one satellite's own
// line on hover/focus. No-ops when the section isn't on the page, and
// the lines are hidden entirely under 720px (see custom.css) where the
// layout collapses to a plain vertical stack, so this skips drawing
// there rather than computing lines nobody sees.
function initBioNetwork() {
  var wrap = document.querySelector("[data-bio-network]");
  if (!wrap) return;

  var svg = wrap.querySelector(".bio-network-lines");
  var center = wrap.querySelector('[data-node="center"]');
  var satellites = wrap.querySelectorAll(".bio-node-satellite");

  function isStacked() {
    return window.matchMedia("(max-width: 720px)").matches;
  }

  function centerOf(el, containerRect) {
    var bubble = el.querySelector(".bio-node-bubble") || el;
    var r = bubble.getBoundingClientRect();
    return {
      x: r.left + r.width / 2 - containerRect.left,
      y: r.top + r.height / 2 - containerRect.top,
    };
  }

  function draw() {
    if (!svg || isStacked()) {
      if (svg) svg.innerHTML = "";
      return;
    }
    var containerRect = wrap.getBoundingClientRect();
    var from = centerOf(center, containerRect);
    var frag = document.createDocumentFragment();

    Array.prototype.forEach.call(satellites, function (sat) {
      var to = centerOf(sat, containerRect);
      var midX = (from.x + to.x) / 2;
      var midY = (from.y + to.y) / 2;
      // Slight bow so lines fan out visually instead of crossing
      // through the center bubble in a straight cluster.
      var bowX = (to.y - from.y) * 0.08;
      var bowY = (from.x - to.x) * 0.08;
      var d =
        "M " + from.x + " " + from.y +
        " Q " + (midX + bowX) + " " + (midY + bowY) + ", " + to.x + " " + to.y;

      var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      path.dataset.node = sat.dataset.node;
      frag.appendChild(path);
    });

    svg.innerHTML = "";
    svg.appendChild(frag);
  }

  Array.prototype.forEach.call(satellites, function (sat) {
    var id = sat.dataset.node;
    function activate() {
      var path = svg.querySelector('path[data-node="' + id + '"]');
      if (path) path.classList.add("is-active");
    }
    function deactivate() {
      var path = svg.querySelector('path[data-node="' + id + '"]');
      if (path) path.classList.remove("is-active");
    }
    sat.addEventListener("mouseenter", activate);
    sat.addEventListener("mouseleave", deactivate);
    sat.addEventListener("focus", activate);
    sat.addEventListener("blur", deactivate);
  });

  var raf = null;
  function scheduleDraw() {
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(draw);
  }
  window.addEventListener("resize", scheduleDraw);
  window.addEventListener("load", scheduleDraw);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(scheduleDraw);
  scheduleDraw();
}

// Keeps [data-tooltip] bubbles inside the viewport.
//
// The tooltip is centred over its trigger by default, which runs off the
// screen for a trigger near either edge. That can't be solved in CSS
// here: the triggers are grid items that wrap into rows which aren't
// their own elements, so :first-child/:last-child can only see the ends
// of the whole grid, not the ends of each row. Measuring is the only way
// to know. Delegated from the document and only on hover/focus, so
// there's no per-element listener and no work until a tooltip is opened.
function initTooltipEdgeAlignment() {
  // Keep in step with the max-width on [data-tooltip]::after in custom.css.
  var TOOLTIP_MAX_WIDTH = 320;
  var VIEWPORT_MARGIN = 8;

  function align(event) {
    var el = event.target.closest ? event.target.closest("[data-tooltip]") : null;
    if (!el) return;

    el.classList.remove("tip-align-left", "tip-align-right");

    var rect = el.getBoundingClientRect();
    var viewportW = document.documentElement.clientWidth;
    var width = Math.min(TOOLTIP_MAX_WIDTH, viewportW - VIEWPORT_MARGIN * 2);
    var centre = rect.left + rect.width / 2;

    if (centre - width / 2 < VIEWPORT_MARGIN) {
      el.classList.add("tip-align-left");
    } else if (centre + width / 2 > viewportW - VIEWPORT_MARGIN) {
      el.classList.add("tip-align-right");
    }
  }

  document.addEventListener("mouseover", align, true);
  document.addEventListener("focusin", align, true);
}

// Scroll-reveal for .reveal-up elements (see custom.css for the start/end
// states). Works on whole *sections* rather than individual elements, so
// everything inside one section animates as a single staggered group in
// source order -- that grouping is what reads as one deliberate motion
// beat per section instead of a scattering of independent fades.
//
// Deliberately a plain geometry check on a passive, rAF-throttled scroll
// listener rather than an IntersectionObserver. IO is the usual answer
// here, but its callbacks are only guaranteed once the page is actually
// being rendered -- and this effect's start state is "invisible", so
// anything that defers those callbacks doesn't degrade the animation, it
// leaves the visitor on a blank page. getBoundingClientRect answers the
// same question synchronously and always. The listener detaches itself
// once the last section has been revealed, so nothing stays attached for
// the life of the page.
function initScrollReveal() {
  var STAGGER_SECONDS = 0.12;
  // Start a section slightly before its top edge clears the viewport
  // bottom, so the motion is already underway as it scrolls into view.
  var TRIGGER_MARGIN = 0.12;

  var revealEls = document.querySelectorAll(".reveal-up");
  if (!revealEls.length) return;

  var pending = [];
  Array.prototype.forEach.call(document.querySelectorAll("section"), function (section) {
    if (section.querySelector(".reveal-up")) pending.push(section);
  });

  if (!pending.length) {
    Array.prototype.forEach.call(revealEls, function (el) {
      el.classList.add("is-visible");
    });
    return;
  }

  function revealSection(section) {
    var items = section.querySelectorAll(".reveal-up");
    Array.prototype.forEach.call(items, function (el, i) {
      el.style.transitionDelay = i * STAGGER_SECONDS + "s";
      el.classList.add("is-visible");
    });
  }

  function check() {
    var viewportH = window.innerHeight || document.documentElement.clientHeight;
    var trigger = viewportH * (1 - TRIGGER_MARGIN);
    pending = pending.filter(function (section) {
      // Only the top edge is tested, with no matching "and is still on
      // screen" check: a section can legitimately be *past* the top of
      // the viewport the first time this runs -- a restored scroll
      // position, an anchor jump, or simply scrolling faster than the
      // trigger band is tall. Requiring it to still be visible would
      // leave those permanently stuck in the invisible start state.
      if (section.getBoundingClientRect().top < trigger) {
        revealSection(section);
        return false;
      }
      return true;
    });
    if (!pending.length) teardown();
  }

  // Called straight through rather than throttled behind
  // requestAnimationFrame: rAF is itself paused in a non-rendering tab,
  // which would reintroduce exactly the stall this approach exists to
  // avoid. The work is a couple of getBoundingClientRect reads over a
  // list that only shrinks, and the listener detaches entirely once the
  // last section lands -- there is nothing here worth deferring.
  function teardown() {
    window.removeEventListener("scroll", check);
    window.removeEventListener("resize", check);
    window.removeEventListener("load", check);
  }

  window.addEventListener("scroll", check, { passive: true });
  window.addEventListener("resize", check, { passive: true });
  // Late webfont/image loads can shift layout enough to bring another
  // section into view without any scrolling happening.
  window.addEventListener("load", check);

  check();
}

