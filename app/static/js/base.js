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
});
