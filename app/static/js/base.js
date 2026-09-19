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
  initHeroDotField();
});

// The hero's dot-grid background, drawn on <canvas class="hero-dot-field">
// (full-bleed, see custom.css) instead of a CSS background-image: each
// dot's actual drawn position is its rest position pulled toward the
// cursor, within a radius, tapering to zero at the edge of that radius --
// dots near the pointer visibly move closer together and to it, dots
// outside the radius don't move at all. A tiled background-image can't do
// this (one flat image, no per-dot state), which is the whole reason this
// moved to canvas. A soft elliptical falloff centered over the hero's
// text/bubble content (FOCUS_*) still fades dots out near the true screen
// edges, the same job the old CSS mask did.
function initHeroDotField() {
  var hero = document.querySelector(".hero");
  var canvas = document.querySelector(".hero-dot-field");
  if (!hero || !canvas || !canvas.getContext) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  var ctx = canvas.getContext("2d");
  var GAP = 22; // px between dots at rest, matching the old background-size
  var BASE_ALPHA = 0.09;
  var INFLUENCE_RADIUS = 120; // px: how far the cursor's pull reaches
  var MAX_PULL = 9; // px: how far a dot right under the cursor moves
  // Elliptical falloff focus, as a fraction of the canvas's own box --
  // matches the old mask's "ellipse ... at 50% 38%" center. Dots inside
  // the 25%-radius core are fully opaque; outside the 92%-radius edge
  // they're invisible; linear ramp between.
  var FOCUS_X_FRAC = 0.5, FOCUS_Y_FRAC = 0.38;
  var FALLOFF_IN = 0.25, FALLOFF_OUT = 0.92;

  // Falling balls that bounce off the dot grid, pachinko-style. They
  // read each dot's CURRENT (pointer-pulled) position, not its rest
  // position -- dotCurrentPosition() is the one place that pull math
  // lives, shared by both this and the plain dot render below, so a
  // ball bounces off where a dot actually is on screen right now,
  // dragged-toward-the-cursor or not.
  var BALL_RADIUS = 4;
  var GRAVITY = 420; // px/s^2
  var MAX_FALL_SPEED = 260; // px/s -- capped so a fast ball can't tunnel through a dot between frames
  var RESTITUTION = 0.55; // velocity kept (not lost) in a dot bounce
  var COLLISION_DIST = BALL_RADIUS + 6;
  // DOM furniture (a bubble, a button, a tag) doesn't bounce a ball at
  // all -- it consumes it. Earlier this tried to bounce balls off these
  // too, the same as the dot grid, but a wide surface (the profile
  // photo especially) could put a ball into a long run of bounces that
  // never quite cleared it, and every fix for that -- a bouncier
  // restitution, a minimum escape speed, finally an outright forced
  // teleport once it had been bouncing too long -- was still visibly a
  // ball fighting the same spot, ending in a hard cut that read as the
  // ball just vanishing. Removing the ball on contact and glowing the
  // thing it hit sidesteps needing any of that: there's no bounce
  // physics left to get stuck.
  var GLOW_MS = 4000;
  var MAX_BALLS = 8;
  var SPAWN_MIN_MS = 750, SPAWN_MAX_MS = 1668; // prior interval raised 25% -- spawns 25% slower
  var PHYSICS_STEP = 1 / 120; // fixed substep so collisions stay reliable regardless of frame rate

  var width = 0, height = 0, dpr = 1;
  var dots = []; // {x, y, alpha} at rest, in CSS-pixel space
  var dotGrid = {}; // "gx,gy" (rest coords) -> dot, for a ball's neighborhood lookup
  var pointer = null; // {x, y} in CSS-pixel space, or null when not hovering
  var balls = [];
  var obstacles = []; // real page furniture a ball also bounces off -- see collectObstacles()
  var nextSpawnAt = 0;
  var pendingExits = []; // { el, at } -- a look-alike ball due to spawn at el's bottom edge once `at` (a tick timestamp) passes
  var EXIT_DELAY_MS = 5000;

  function buildDots() {
    dots = [];
    dotGrid = {};
    var focusX = width * FOCUS_X_FRAC;
    var focusY = height * FOCUS_Y_FRAC;
    // Wide enough that dots stay visible (and, via the pointer pull
    // below, movable) all the way out into the margin outside #content's
    // 1200px cap on a wide screen, not just within the narrower content
    // column -- width is the full viewport now (see .hero-dot-field), so
    // this has to reach much further than it would relative to just the
    // hero's own text/bubble column. 0.7 still let the horizontal
    // falloff crush dots out past roughly the content column's own edge
    // down to ~3% alpha -- technically nonzero, indistinguishable from
    // absent, and dead exactly where the bio-network's "+"/"-" controls
    // now sit on a wide screen. 1.15 keeps dots close to full brightness
    // (and clearly pullable) all the way to the true left/right edges.
    var radiusX = width * 1.15;
    var radiusY = height * 0.85;
    for (var y = 0; y <= height; y += GAP) {
      for (var x = 0; x <= width; x += GAP) {
        var d = Math.sqrt(
          Math.pow((x - focusX) / radiusX, 2) + Math.pow((y - focusY) / radiusY, 2)
        );
        var t = 1 - (d - FALLOFF_IN) / (FALLOFF_OUT - FALLOFF_IN);
        var alpha = BASE_ALPHA * Math.max(0, Math.min(1, t));
        if (alpha > 0.002) {
          var dot = { x: x, y: y, alpha: alpha };
          dots.push(dot);
          dotGrid[x + "," + y] = dot;
        }
      }
    }
  }

  // Where a dot actually draws right now: pulled toward the pointer
  // within INFLUENCE_RADIUS, same as before pull math existed only
  // inline in render(). Balls need this exact position too, or they'd
  // bounce off a dot's rest spot instead of the (possibly dragged)
  // spot it's actually rendered at.
  function dotCurrentPosition(dot) {
    var x = dot.x, y = dot.y, radius = 1, alpha = dot.alpha;
    if (pointer) {
      var dx = dot.x - pointer.x;
      var dy = dot.y - pointer.y;
      var dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < INFLUENCE_RADIUS) {
        // 1 at the cursor, 0 at the radius's edge -- both the pull
        // distance and a brightness/size lift share this so the dots
        // that moved the most also read as the most "activated."
        var strength = 1 - dist / INFLUENCE_RADIUS;
        var pull = strength * MAX_PULL;
        if (dist > 0.01) {
          x = dot.x - (dx / dist) * pull;
          y = dot.y - (dy / dist) * pull;
        }
        radius = 1 + strength * 1.4;
        alpha = Math.min(1, dot.alpha + strength * 0.5);
      }
    }
    return { x: x, y: y, radius: radius, alpha: alpha };
  }

  function resize() {
    var rect = canvas.getBoundingClientRect();
    width = rect.width;
    height = rect.height;
    dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    buildDots();
    render();
  }

  function spawnBall() {
    if (balls.length >= MAX_BALLS) return;
    balls.push({
      x: 20 + Math.random() * Math.max(1, width - 40),
      y: -BALL_RADIUS * 2,
      vx: (Math.random() - 0.5) * 50,
      vy: 0,
      age: 0,
    });
  }

  // Not literally the same ball resuming -- a fresh one spawned at the
  // bottom edge of whatever it was consumed by, timed so it reads as
  // "that ball passed through and came out the other side." Simpler and
  // more robust than actually pausing a ball's physics/rendering for
  // EXIT_DELAY_MS and resuming it: no state to keep in sync with an
  // element that might move (a bubble's reveal-up settling, a hover
  // lift) or vanish (the "-" button removing a satellite) in the
  // meantime -- this only ever needs the element's position at the
  // moment it actually fires.
  //
  // EXIT_CLEARANCE below matters more than it looks: a point exactly on
  // a circle's own bottom edge is still exactly `r` from its center --
  // still within that same circle's own consume radius (r + BALL_RADIUS)
  // once resolveObstacle() checks it on the very next tick. Spawned
  // flush against the edge, the "exit" ball was immediately re-consumed
  // by the very obstacle it just came from, which is what "spawns on a
  // DOM element just disappear instantly, never falls" actually was.
  var EXIT_CLEARANCE = 18; // px past the element's own bottom edge, comfortably outside its consume radius
  function spawnExitBall(el) {
    if (!el || !el.isConnected || balls.length >= MAX_BALLS) return;
    var canvasRect = canvas.getBoundingClientRect();
    var r = el.getBoundingClientRect();
    balls.push({
      x: r.left + r.width / 2 - canvasRect.left,
      y: r.bottom - canvasRect.top + EXIT_CLEARANCE,
      vx: (Math.random() - 0.5) * 50,
      vy: 0,
      age: 0,
    });
  }

  // Nearest dots to (x, y) via direct grid-index lookup (dots sit on a
  // known GAP spacing) instead of scanning the whole dots array --
  // cheap enough to run per ball per physics substep even with a couple
  // thousand dots on a wide screen.
  function nearbyDots(x, y) {
    var found = [];
    var gx = Math.round(x / GAP) * GAP;
    var gy = Math.round(y / GAP) * GAP;
    for (var oy = -GAP; oy <= GAP; oy += GAP) {
      for (var ox = -GAP; ox <= GAP; ox += GAP) {
        var dot = dotGrid[(gx + ox) + "," + (gy + oy)];
        if (dot) found.push(dot);
      }
    }
    return found;
  }

  // Real page furniture a ball gets consumed by, not just the dot grid:
  // the bio-network's photo and bubbles, the hero buttons, and the tag
  // pills (Python, Flask, ...). Read fresh every frame a ball exists
  // (see tick()) rather than cached once -- the "+" button adds bubbles
  // at runtime, .reveal-up slides these in on load, and .bio-photo-frame
  // lifts on hover, so a stale rect would drift out of sync with what's
  // actually on screen. Positions are converted into the same
  // canvas-local coordinate space stepBall() already works in; `el`
  // itself is kept too, so a hit can glow the actual element (see
  // glowElement()).
  function collectObstacles() {
    var canvasRect = canvas.getBoundingClientRect();
    var list = [];

    function addCircle(el) {
      var r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return; // hidden (e.g. mobile layout)
      list.push({
        shape: "circle",
        el: el,
        x: r.left + r.width / 2 - canvasRect.left,
        y: r.top + r.height / 2 - canvasRect.top,
        r: r.width / 2,
      });
    }
    function addRect(el) {
      var r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return;
      list.push({
        shape: "rect",
        el: el,
        left: r.left - canvasRect.left,
        top: r.top - canvasRect.top,
        right: r.right - canvasRect.left,
        bottom: r.bottom - canvasRect.top,
      });
    }

    var center = document.querySelector(".bio-node-center");
    if (center) addCircle(center);
    var bubbles = document.querySelectorAll(".bio-node-satellite .bio-node-bubble");
    for (var i = 0; i < bubbles.length; i++) addCircle(bubbles[i]);

    var buttons = document.querySelectorAll(".hero-actions .button, .bio-network-controls .bio-network-add, .bio-network-controls .bio-network-remove");
    for (var b = 0; b < buttons.length; b++) addRect(buttons[b]);

    var tags = document.querySelectorAll(".tech-strip .tag");
    for (var t = 0; t < tags.length; t++) addRect(tags[t]);

    return list;
  }

  // Restarting a glow on a second hit mid-glow needs the animation to
  // actually restart, which re-adding a class an element already has
  // won't do on its own -- removing the class, waiting a frame (so a
  // style recalculation actually happens before it goes back on), then
  // re-adding is what makes a CSS animation replay from the top. A
  // requestAnimationFrame round-trip rather than the more common
  // "remove, read offsetWidth, re-add" reflow trick specifically
  // because this also has to work on an SVG <path> (the connector line
  // below), and offsetWidth isn't reliably present on SVG elements the
  // way it is on ordinary HTML ones.
  function restartGlow(el, className, timeoutProp) {
    el.classList.remove(className);
    requestAnimationFrame(function () {
      el.classList.add(className);
    });
    clearTimeout(el[timeoutProp]);
    el[timeoutProp] = setTimeout(function () {
      el.classList.remove(className);
    }, GLOW_MS);
  }

  // Glows the element a ball was just consumed by, and, if it's a
  // bubble with its own connector line back to the profile photo,
  // glows that line too -- the bubble and the line it hangs off read as
  // one piece of the network, so a ball landing on the bubble alone
  // would look like it missed half of what it hit. Not every glowing
  // element has one of these (the profile photo, buttons, and tag
  // pills don't), hence the closest()/querySelector guard.
  function glowElement(el) {
    restartGlow(el, "ball-glow", "_ballGlowTimeout");

    var satellite = el.closest(".bio-node-satellite");
    var svg = document.querySelector(".bio-network-lines");
    if (!satellite || !svg) return;
    var line = svg.querySelector('path[data-node="' + satellite.dataset.node + '"]');
    if (line) restartGlow(line, "line-glow", "_lineGlowTimeout");
  }

  var TANGENT_ESCAPE = 45; // px/s -- floor on sideways speed after any dot bounce

  // Bounces the ball off the dot grid: decomposes velocity into the
  // part driving it INTO the dot (which reflects and gets damped by
  // `restitution`) and the part running ALONG its surface, which a real
  // bounce leaves untouched -- topped up to TANGENT_ESCAPE when it's
  // near zero (keeping whatever sign it already has, or picking one
  // once if it's exactly zero) so a ball landing dead-center on a dot
  // still visibly deflects to one side instead of bouncing straight up
  // and back down on the same spot. DOM furniture (bubbles, buttons,
  // tags) doesn't call this at all -- see resolveObstacle() -- a ball
  // is simply consumed on contact with those, no bounce involved.
  function bounce(ball, nx, ny, x, y, pushOut, restitution, minSpeed) {
    var tx = -ny, ty = nx; // tangent: perpendicular to the normal
    var normalSpeed = ball.vx * nx + ball.vy * ny; // negative == still moving into the surface
    var tangentSpeed = ball.vx * tx + ball.vy * ty;

    var outSpeed = Math.max(minSpeed || 0, -normalSpeed * restitution);
    if (Math.abs(tangentSpeed) < TANGENT_ESCAPE) {
      var sign = tangentSpeed === 0 ? (Math.random() < 0.5 ? -1 : 1) : (tangentSpeed > 0 ? 1 : -1);
      tangentSpeed = sign * TANGENT_ESCAPE;
    }

    ball.vx = outSpeed * nx + tangentSpeed * tx;
    ball.vy = outSpeed * ny + tangentSpeed * ty;
    ball.x = x + nx * pushOut;
    ball.y = y + ny * pushOut;
  }

  // Returns the obstacle's element (and glows it) the instant the ball
  // touches this obstacle, or null otherwise -- no bounce math, no
  // escape case to get wrong, since the ball is about to be removed
  // entirely (see stepBall()). The element is what stepBall() needs to
  // later spawn a look-alike ball at this same obstacle's bottom edge
  // (see EXIT_DELAY_MS in tick()).
  function resolveObstacle(ball, obstacle) {
    if (obstacle.shape === "circle") {
      var dx = ball.x - obstacle.x, dy = ball.y - obstacle.y;
      if (Math.sqrt(dx * dx + dy * dy) >= BALL_RADIUS + obstacle.r) return null;
    } else {
      var cx = Math.max(obstacle.left, Math.min(ball.x, obstacle.right));
      var cy = Math.max(obstacle.top, Math.min(ball.y, obstacle.bottom));
      var ddx = ball.x - cx, ddy = ball.y - cy;
      if (Math.sqrt(ddx * ddx + ddy * ddy) >= BALL_RADIUS) return null;
    }
    glowElement(obstacle.el);
    return obstacle.el;
  }

  function stepBall(ball, dt) {
    ball.vy = Math.min(MAX_FALL_SPEED, ball.vy + GRAVITY * dt);
    ball.x += ball.vx * dt;
    ball.y += ball.vy * dt;
    ball.age += dt;

    var candidates = nearbyDots(ball.x, ball.y);
    for (var i = 0; i < candidates.length; i++) {
      var pos = dotCurrentPosition(candidates[i]);
      var dx = ball.x - pos.x, dy = ball.y - pos.y;
      var dist = Math.sqrt(dx * dx + dy * dy);
      if (dist > 0.01 && dist < COLLISION_DIST) {
        var nx = dx / dist, ny = dy / dist;
        bounce(ball, nx, ny, pos.x, pos.y, COLLISION_DIST, RESTITUTION, 0);
      }
    }

    for (var o = 0; o < obstacles.length; o++) {
      var el = resolveObstacle(ball, obstacles[o]);
      if (el) {
        ball.consumed = true;
        ball.consumedBy = el;
        break;
      }
    }
  }

  function render() {
    ctx.clearRect(0, 0, width, height);
    for (var i = 0; i < dots.length; i++) {
      var pos = dotCurrentPosition(dots[i]);
      ctx.beginPath();
      ctx.arc(pos.x, pos.y, pos.radius, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255, 255, 255, " + pos.alpha.toFixed(3) + ")";
      ctx.fill();
    }
    for (var b = 0; b < balls.length; b++) {
      var ball = balls[b];
      var glow = ctx.createRadialGradient(ball.x, ball.y, 0, ball.x, ball.y, BALL_RADIUS * 3);
      glow.addColorStop(0, "rgba(58, 160, 255, 0.35)");
      glow.addColorStop(1, "rgba(58, 160, 255, 0)");
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(ball.x, ball.y, BALL_RADIUS * 3, 0, Math.PI * 2);
      ctx.fill();

      ctx.beginPath();
      ctx.arc(ball.x, ball.y, BALL_RADIUS, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(58, 160, 255, 0.9)";
      ctx.fill();
    }
  }

  var lastTick = null;
  function tick(timestamp) {
    if (lastTick === null) lastTick = timestamp;
    // Clamped so a backgrounded/inactive tab doesn't dump a huge dt on
    // return and send every ball flying through the whole board at once.
    var dt = Math.min(0.05, (timestamp - lastTick) / 1000);
    lastTick = timestamp;

    if (timestamp >= nextSpawnAt) {
      spawnBall();
      nextSpawnAt = timestamp + SPAWN_MIN_MS + Math.random() * (SPAWN_MAX_MS - SPAWN_MIN_MS);
    }

    // Only worth measuring real DOM rects when there's actually a ball
    // that could hit one.
    if (balls.length) obstacles = collectObstacles();

    var remaining = dt;
    while (remaining > 0) {
      var step = Math.min(PHYSICS_STEP, remaining);
      for (var i = 0; i < balls.length; i++) stepBall(balls[i], step);
      remaining -= step;
    }

    for (var c = 0; c < balls.length; c++) {
      var consumedBall = balls[c];
      if (consumedBall.consumed) {
        pendingExits.push({ el: consumedBall.consumedBy, at: timestamp + EXIT_DELAY_MS });
      }
    }
    balls = balls.filter(function (ball) {
      return !ball.consumed && ball.y - BALL_RADIUS < height + 40 && ball.age < 20;
    });

    pendingExits = pendingExits.filter(function (pending) {
      if (timestamp < pending.at) return true;
      spawnExitBall(pending.el);
      return false;
    });

    render();
    requestAnimationFrame(tick);
  }

  // Listened for on the document, not .hero -- .hero itself is only as
  // wide as #content's capped column (1200px), while the canvas it owns
  // breaks out to the full viewport (see custom.css). A listener on
  // .hero never fires while the cursor is out in the margin beyond that
  // 1200px column on a wide screen, so the dots there stayed put no
  // matter how close the cursor got, even once they were made bright
  // enough to actually see. document's own box always covers the full
  // viewport, matching where the canvas -- and now the bio-network's
  // "+"/"-" controls -- actually are.
  document.addEventListener("mousemove", function (event) {
    var rect = canvas.getBoundingClientRect();
    pointer = { x: event.clientX - rect.left, y: event.clientY - rect.top };
  });
  document.addEventListener("mouseleave", function () {
    pointer = null;
  });

  var resizeRaf = null;
  window.addEventListener("resize", function () {
    if (resizeRaf) cancelAnimationFrame(resizeRaf);
    resizeRaf = requestAnimationFrame(resize);
  });

  resize();
  requestAnimationFrame(tick);
}

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

  // No longer a descendant of wrap -- .bio-network-wrap's .reveal-up
  // carries a permanent transform once revealed, which would make it the
  // containing block for a position: fixed descendant instead of the
  // real viewport (see main/index.html's comment where this SVG lives).
  var svg = document.querySelector(".bio-network-lines");
  var center = wrap.querySelector('[data-node="center"]');
  var addBtn = document.getElementById("bio-network-add");
  var removeBtn = document.getElementById("bio-network-remove");
  // A live array (not the static NodeList querySelectorAll returns),
  // since the "+" button pushes new satellites into it -- draw() needs
  // to see whichever satellites exist right now, not just the ones that
  // existed at page load.
  var satellites = Array.prototype.slice.call(wrap.querySelectorAll(".bio-node-satellite"));

  function isStacked() {
    return window.matchMedia("(max-width: 720px)").matches;
  }

  function centerOf(el) {
    var bubble = el.querySelector(".bio-node-bubble") || el;
    var r = bubble.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }

  // Lines are always drawn, for every satellite, unconditionally -- no
  // bubble is ever mid-drag or removable anymore, so there's nothing
  // that would make a line worth hiding.
  function draw() {
    if (!svg || isStacked()) {
      if (svg) svg.innerHTML = "";
      return;
    }
    var from = centerOf(center);
    var frag = document.createDocumentFragment();

    satellites.forEach(function (sat) {
      var to = centerOf(sat);
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

  var raf = null;
  function scheduleDraw() {
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(draw);
  }

  // Collision avoidance: a newly added bubble organizes itself around
  // the center photo rather than landing on top of it, on top of any
  // other bubble, over the hero text column, or off-screen. Earlier
  // versions searched anywhere on rings out past the wrap's own box,
  // which could place a bubble over the text column to the left (the
  // wrap has no clipping, so nothing stopped it) or partway off the
  // right edge of the viewport. findPlacement() now only ever considers
  // spots fully inside the wrap's own box (SAFE_MARGIN keeps a bubble's
  // full radius, not just its center, inside that box, which is what
  // actually keeps it off the text column and on screen) and returns
  // null when nothing in that box is free -- the caller treats that as
  // "at capacity" and declines to add rather than force an overlap.
  var SAT_MIN_DIST = 168; // px between two satellite centers
  var CENTER_MIN_DIST = 208; // px between a satellite and the center bubble
  var SAFE_MARGIN = 80; // >= a satellite bubble's own radius (72px), so its edge stays inside the wrap
  var PLACEMENT_STEP = 18; // px grid resolution for the placement search

  function bubbleCenterPx(el) {
    var b = el.querySelector(".bio-node-bubble") || el;
    var r = b.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }

  function placementIsClear(x, y, excludeSat) {
    var centerPos = bubbleCenterPx(center);
    if (Math.hypot(x - centerPos.x, y - centerPos.y) < CENTER_MIN_DIST) return false;
    for (var i = 0; i < satellites.length; i++) {
      var other = satellites[i];
      if (other === excludeSat) continue;
      var p = bubbleCenterPx(other);
      if (Math.hypot(x - p.x, y - p.y) < SAT_MIN_DIST) return false;
    }
    return true;
  }

  // Scans a grid across the safe area (inside the wrap, clear of the
  // text column and the viewport edge) and picks at random among every
  // spot that's fully clear -- a grid instead of random sampling so
  // "nothing free" is an actual exhaustive answer, not a run of bad luck.
  function findPlacement(excludeSat) {
    var wrapRect = wrap.getBoundingClientRect();
    var minX = wrapRect.left + SAFE_MARGIN;
    var maxX = wrapRect.right - SAFE_MARGIN;
    var minY = wrapRect.top + SAFE_MARGIN;
    var maxY = wrapRect.bottom - SAFE_MARGIN;
    if (minX >= maxX || minY >= maxY) return null;

    var candidates = [];
    for (var x = minX; x <= maxX; x += PLACEMENT_STEP) {
      for (var y = minY; y <= maxY; y += PLACEMENT_STEP) {
        if (placementIsClear(x, y, excludeSat)) candidates.push({ x: x, y: y });
      }
    }
    if (!candidates.length) return null;
    return candidates[Math.floor(Math.random() * candidates.length)];
  }

  // Hover/focus on a satellite highlights its own connecting line.
  function wireSatellite(sat) {
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
  }

  satellites.forEach(wireSatellite);

  // "+"/"-" buttons: a stack. "+" pushes one bubble at a time from the
  // real project list rendered into #bio-network-extra (see
  // main/routes.py), each with its own icon and a real URL -- never
  // invented placeholder content. "-" pops the most recently added one
  // back off, in the reverse order it was added, and re-reveals "+" for
  // it. Only bubbles added this way are poppable -- the four built-in
  // satellites (JPMorgan, Ruth Asawa, Cogswell, StreetCode) are the
  // site's real bio, not part of this toy stack.
  (function initAddRemoveButtons() {
    if (!addBtn) return;
    var dataEl = document.getElementById("bio-network-extra");
    var pool = [];
    try {
      pool = dataEl ? JSON.parse(dataEl.textContent) : [];
    } catch (e) {
      pool = [];
    }
    var nextIndex = 0;
    var added = []; // stack of satellite elements pushed by "+"
    // Set once a click finds no clear spot left -- the safe area is
    // full. Cleared on every removal so freed space gets tried again;
    // left alone otherwise, since re-scanning on every click after
    // capacity is reached is wasted work for a result that can't change
    // until something is removed.
    var atCapacity = false;

    if (!pool.length) {
      addBtn.hidden = true;
      if (removeBtn) removeBtn.hidden = true;
      return;
    }

    function syncButtons() {
      addBtn.hidden = nextIndex >= pool.length || atCapacity;
      if (removeBtn) removeBtn.hidden = added.length === 0;
    }

    addBtn.addEventListener("click", function () {
      if (nextIndex >= pool.length) return;

      // Found before anything is built or added to the DOM/stack -- a
      // full safe area means declining the add outright (see
      // findPlacement()'s comment) rather than creating a bubble with
      // nowhere clean to put it.
      var placement = findPlacement(null);
      if (!placement) {
        atCapacity = true;
        syncButtons();
        return;
      }

      var item = pool[nextIndex++];

      var sat = document.createElement("a");
      sat.className = "bio-node bio-node-satellite";
      sat.dataset.node = item.id;
      sat.href = item.url;

      var bubble = document.createElement("span");
      bubble.className = "bio-node-bubble";
      var img = document.createElement("img");
      img.src = item.icon;
      img.alt = "";
      bubble.appendChild(img);

      var label = document.createElement("span");
      label.className = "bio-node-label";
      label.textContent = item.title;

      sat.appendChild(bubble);
      sat.appendChild(label);

      // Positioned in %, matching the wrap's own coordinate system the
      // built-in satellites use, converting through px only for the
      // placement search itself.
      var wrapRect = wrap.getBoundingClientRect();
      sat.style.left = (((placement.x - wrapRect.left) / wrapRect.width) * 100) + "%";
      sat.style.top = (((placement.y - wrapRect.top) / wrapRect.height) * 100) + "%";

      wrap.appendChild(sat);
      satellites.push(sat);
      added.push(sat);

      wireSatellite(sat);
      scheduleDraw();
      syncButtons();
    });

    if (removeBtn) {
      removeBtn.addEventListener("click", function () {
        var sat = added.pop();
        if (!sat) return;
        nextIndex--;
        atCapacity = false;
        var idx = satellites.indexOf(sat);
        if (idx !== -1) satellites.splice(idx, 1);
        sat.remove();
        scheduleDraw();
        syncButtons();
      });
    }

    syncButtons();
  })();

  // .bio-network-wrap carries .reveal-up, which slides/fades it in over
  // 0.8s on load (initScrollReveal() adds .is-visible, then the CSS
  // transition runs). The first draw() calls below can easily land while
  // that transition is still in flight, locking the lines onto the
  // bubbles' in-transit positions -- they'd only ever correct themselves
  // once some later resize/scroll happened to trigger a fresh draw,
  // which is the "loads wrong, then jumps into place" symptom. Redrawing
  // right when that transition actually finishes is the real fix.
  //
  // The lines also start invisible (see .bio-network-lines in
  // custom.css) and only fade in once that same transitionend fires --
  // event.target === wrap specifically, not any bubbling transitionend
  // from a bubble's own hover transition, so a stray hover mid-load
  // can't reveal the lines early. Under reduced motion .reveal-up's
  // transition is removed outright (see custom.css), so this event
  // would never fire at all -- revealed immediately below instead, same
  // as .reveal-up itself skips straight to its settled state there.
  function revealLines() {
    if (svg) svg.classList.add("is-visible");
  }
  wrap.addEventListener("transitionend", function (event) {
    if (event.target === wrap) revealLines();
    scheduleDraw();
  });
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) revealLines();

  window.addEventListener("resize", scheduleDraw);
  window.addEventListener("load", scheduleDraw);
  // Lines are drawn in viewport coordinates (see centerOf() above) while
  // the satellites themselves stay in normal page flow -- scrolling
  // changes every satellite's viewport position even though nothing
  // about the network itself moved, so redraw needs to track scroll too.
  window.addEventListener("scroll", scheduleDraw, { passive: true });
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
      unclipRevealMask(el);
    });
  }

  // .reveal-mask's overflow: hidden (custom.css) exists only to clip the
  // slide-in wipe itself, while the wrapped .reveal-up is still
  // physically translated out of its box -- once that one-time
  // transition actually finishes (or never runs at all, under reduced
  // motion) it serves no further purpose, and leaving it in place
  // forever clips anything later layered on top that extends past the
  // wrapper's own box, like a ball-glow flash on a button inside one
  // (see .ball-glow in custom.css) -- the exact "buttons cut off when
  // they glow" bug this exists to fix.
  function unclipRevealMask(el) {
    var mask = el.closest(".reveal-mask");
    if (!mask) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      mask.classList.add("reveal-mask-done");
      return;
    }
    el.addEventListener("transitionend", function handler(event) {
      if (event.target !== el) return;
      mask.classList.add("reveal-mask-done");
      el.removeEventListener("transitionend", handler);
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

