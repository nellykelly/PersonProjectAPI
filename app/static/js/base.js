// Site-wide behavior: fade-in on load (mirrors Dimension's is-preload
// pattern from main.css) and the mobile nav toggle. Vanilla JS -- no
// jQuery dependency needed for this small amount of interaction.
window.addEventListener("load", function () {
  document.body.classList.remove("is-preload");
});

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
});

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
