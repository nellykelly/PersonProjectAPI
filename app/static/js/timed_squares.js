// Timed-Squares: a turn-based survival game on a 10x10 grid.
//
// Strictly alternating, not real-time: the player moves exactly one
// cell on a key press, and only THEN do every obstacle take their own
// move -- see resolveTurn(). Every obstacle telegraphs its next move
// (an arrow, or a knight glyph for the L-mover) one turn before it
// executes it, decided in advance and drawn every render, so the game
// is dodgeable by reading the board rather than memorizing patterns.
(function () {
  "use strict";

  var GRID_SIZE = 10;
  var STORAGE_KEY = "timed-squares-best";
  var SUBMIT_URL = "/projects/timed-squares/api/scores";
  var LEADERBOARD_URL = "/projects/timed-squares/api/leaderboard";

  var COLORS = {
    player: "#3aa0ff",
    standard: "#f87171",
    jumper: "#fbbf24",
    lmover: "#c084fc",
    diagonal: "#2dd4bf",
    bouncer: "#818cf8",
    grid: "rgba(255,255,255,0.08)",
    glyph: "#0d1117",
  };

  // Difficulty curve -- feel-based, tuned by actually playing it rather
  // than derived from a formula (see the build prompt's own "left to
  // feel-based tuning" allowance). Both the spawn chance and which
  // edges/types are available widen with turns survived.
  var EDGE_UNLOCK_TURNS = { top: 0, bottom: 12, left: 24, right: 36 };
  var TYPE_UNLOCK_TURNS = { standard: 0, jumper: 10, bouncer: 18, diagonal: 26, lmover: 34 };
  // Spawn rate raised ~25% on top of the original tuning: the forced
  // spawn interval shortened (7 -> 6 turns, ~17% more often on its own)
  // and the probabilistic base/slope both scaled by 1.25 -- the cap
  // raised too, so the higher base/slope actually keep mattering at
  // high turn counts instead of saturating earlier than before.
  var FORCED_SPAWN_EVERY_TURNS = 6;
  function spawnChance(turn) {
    return Math.min(0.85, 0.15 + turn * 0.015);
  }

  var DIRS = {
    up: { dx: 0, dy: -1 },
    down: { dx: 0, dy: 1 },
    left: { dx: -1, dy: 0 },
    right: { dx: 1, dy: 0 },
  };

  var KEY_TO_DIR = {
    ArrowUp: "up", w: "up", W: "up",
    ArrowDown: "down", s: "down", S: "down",
    ArrowLeft: "left", a: "left", A: "left",
    ArrowRight: "right", d: "right", D: "right",
  };

  function randInt(n) {
    return Math.floor(Math.random() * n);
  }

  function pick(arr) {
    return arr[randInt(arr.length)];
  }

  function inBounds(x, y) {
    return x >= 0 && x < GRID_SIZE && y >= 0 && y < GRID_SIZE;
  }

  // ---------- obstacle movement decisions ----------
  // Each returns the {dx,dy} (or, for the L-mover, a knight-offset) the
  // obstacle will execute on its NEXT resolved turn. Called once at
  // spawn (for the first telegraph) and again every turn right after
  // that turn's move executes (for the following telegraph).

  function decideStandard(o) {
    return o.dir;
  }

  function decideJumper(o) {
    return { dx: o.dir.dx * 2, dy: o.dir.dy * 2 };
  }

  function decideBouncer(o) {
    // Reverse direction if the *next* straight step would leave the
    // board -- decided at telegraph time, so the arrow shown always
    // matches what actually happens next turn.
    var nx = o.x + o.dir.dx;
    var ny = o.y + o.dir.dy;
    if (!inBounds(nx, ny)) {
      o.dir = { dx: -o.dir.dx, dy: -o.dir.dy };
    }
    return o.dir;
  }

  function decideDiagonal(o) {
    return o.dir;
  }

  // Knight-style: always makes net progress along its primary (spawn-
  // edge-derived) axis, with the perpendicular axis's sign chosen
  // pseudo-randomly each turn -- unpredictable path, but each single
  // hop is fully telegraphed before it happens.
  function decideLMover(o) {
    var shortSign = pick([1, -1]);
    if (o.primaryAxis === "y") {
      return { dx: shortSign, dy: o.dir.dy * 2 };
    }
    return { dx: o.dir.dx * 2, dy: shortSign };
  }

  var DECIDERS = {
    standard: decideStandard,
    jumper: decideJumper,
    bouncer: decideBouncer,
    diagonal: decideDiagonal,
    lmover: decideLMover,
  };

  // ---------- spawning ----------

  function availableEdges(turn) {
    return Object.keys(EDGE_UNLOCK_TURNS).filter(function (edge) {
      return turn >= EDGE_UNLOCK_TURNS[edge];
    });
  }

  function availableTypes(turn) {
    return Object.keys(TYPE_UNLOCK_TURNS).filter(function (type) {
      return turn >= TYPE_UNLOCK_TURNS[type];
    });
  }

  var nextObstacleId = 1;

  function spawnObstacle(turn, occupied) {
    var edge = pick(availableEdges(turn));
    var type = pick(availableTypes(turn));
    var x, y, dir, primaryAxis;

    if (edge === "top") { x = randInt(GRID_SIZE); y = 0; dir = DIRS.down; primaryAxis = "y"; }
    else if (edge === "bottom") { x = randInt(GRID_SIZE); y = GRID_SIZE - 1; dir = DIRS.up; primaryAxis = "y"; }
    else if (edge === "left") { x = 0; y = randInt(GRID_SIZE); dir = DIRS.right; primaryAxis = "x"; }
    else { x = GRID_SIZE - 1; y = randInt(GRID_SIZE); dir = DIRS.left; primaryAxis = "x"; }

    if (occupied(x, y)) return null;

    if (type === "diagonal") {
      // Keep the edge-derived inward component, randomize the other axis
      // -- always makes net progress across the board, never travels
      // parallel to its spawn edge forever.
      dir = primaryAxis === "y" ? { dx: pick([1, -1]), dy: dir.dy } : { dx: dir.dx, dy: pick([1, -1]) };
    }

    var obstacle = { id: nextObstacleId++, x: x, y: y, type: type, dir: dir, primaryAxis: primaryAxis };
    obstacle.nextMove = DECIDERS[type](obstacle);
    return obstacle;
  }

  // ---------- game state / engine ----------

  function TimedSquares(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.resize();
    this.reset();
  }

  // The board is now sized by CSS (viewport-relative, see .ts-board-wrapper),
  // not a fixed pixel value -- so the canvas's actual *bitmap* resolution
  // has to be set to match its rendered CSS size (times devicePixelRatio,
  // for sharpness on high-DPI screens) or the browser stretches a small
  // bitmap to fill a much bigger box and everything renders blurry.
  // `this.cell` is derived from that real resolution, so drawing math
  // never hardcodes a size.
  TimedSquares.prototype.resize = function () {
    var rect = this.canvas.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    var size = Math.round(rect.width * dpr);
    if (size > 0 && this.canvas.width !== size) {
      this.canvas.width = size;
      this.canvas.height = size;
    }
    this.cell = this.canvas.width / GRID_SIZE;
  };

  TimedSquares.prototype.reset = function () {
    this.player = { x: Math.floor(GRID_SIZE / 2), y: Math.floor(GRID_SIZE / 2) };
    this.obstacles = [];
    this.turn = 0;
    this.gameOver = false;
    nextObstacleId = 1;
    this.render();
  };

  TimedSquares.prototype.occupiedByObstacle = function (x, y) {
    return this.obstacles.some(function (o) { return o.x === x && o.y === y; });
  };

  TimedSquares.prototype.checkCollision = function () {
    var player = this.player;
    return this.obstacles.some(function (o) { return o.x === player.x && o.y === player.y; });
  };

  TimedSquares.prototype.tryMovePlayer = function (dirName) {
    if (this.gameOver) return;
    var dir = DIRS[dirName];
    if (!dir) return;
    var nx = this.player.x + dir.dx;
    var ny = this.player.y + dir.dy;
    if (!inBounds(nx, ny)) return; // wall: not a valid turn, nothing advances

    this.player.x = nx;
    this.player.y = ny;
    this.resolveTurn();
  };

  TimedSquares.prototype.resolveTurn = function () {
    this.turn += 1;

    if (this.checkCollision()) {
      this.endGame();
      return;
    }

    // Execute every obstacle's already-telegraphed move -- decideLMover
    // already collapses a knight hop into one turn's net {dx,dy}, so
    // every obstacle type executes the same way regardless of pattern.
    var self = this;
    this.obstacles.forEach(function (o) {
      o.x += o.nextMove.dx;
      o.y += o.nextMove.dy;
    });
    // Drop anything that exited the board.
    this.obstacles = this.obstacles.filter(function (o) { return inBounds(o.x, o.y); });

    if (this.checkCollision()) {
      this.endGame();
      return;
    }

    // Spawn new obstacles for this turn.
    var shouldForceSpawn = this.turn % FORCED_SPAWN_EVERY_TURNS === 0;
    var chance = spawnChance(this.turn);
    var spawnAttempts = shouldForceSpawn ? 2 : (Math.random() < chance ? 1 : 0);
    for (var i = 0; i < spawnAttempts; i++) {
      var obstacle = spawnObstacle(this.turn, function (x, y) {
        return self.occupiedByObstacle(x, y) || (x === self.player.x && y === self.player.y);
      });
      if (obstacle) this.obstacles.push(obstacle);
    }

    // Re-telegraph every surviving obstacle's next move.
    this.obstacles.forEach(function (o) {
      o.nextMove = DECIDERS[o.type](o);
    });

    this.render();
  };

  TimedSquares.prototype.endGame = function () {
    this.gameOver = true;
    this.render();
    if (typeof this.onGameOver === "function") this.onGameOver(this.turn);
  };

  // ---------- rendering ----------

  TimedSquares.prototype.drawArrow = function (cx, cy, dx, dy, size, color) {
    var ctx = this.ctx;
    var angle = Math.atan2(dy, dx);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(angle);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(size, 0);
    ctx.lineTo(-size * 0.6, size * 0.6);
    ctx.lineTo(-size * 0.6, -size * 0.6);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  };

  // A small ring around the arrow marks the L-mover as the trickier
  // pattern, but the arrow itself always points at the exact {dx,dy} of
  // its next hop -- a generic "this is a knight-mover" icon wouldn't
  // tell the player *which* of the several possible hops is actually
  // coming next, which defeats the point of telegraphing at all.
  TimedSquares.prototype.drawKnightMarker = function (cx, cy, size, color) {
    var ctx = this.ctx;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(cx, cy, size * 1.15, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  };

  TimedSquares.prototype.render = function () {
    var ctx = this.ctx;
    var cell = this.cell;
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    // Grid.
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    for (var i = 0; i <= GRID_SIZE; i++) {
      ctx.beginPath();
      ctx.moveTo(i * cell, 0);
      ctx.lineTo(i * cell, GRID_SIZE * cell);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(0, i * cell);
      ctx.lineTo(GRID_SIZE * cell, i * cell);
      ctx.stroke();
    }

    // Obstacles + telegraph.
    var pad = cell * 0.12;
    this.obstacles.forEach(function (o) {
      var px = o.x * cell;
      var py = o.y * cell;
      ctx.fillStyle = COLORS[o.type] || COLORS.standard;
      ctx.fillRect(px + pad, py + pad, cell - pad * 2, cell - pad * 2);

      var cx = px + cell / 2;
      var cy = py + cell / 2;
      var move = o.nextMove;
      var mag = Math.max(Math.abs(move.dx), Math.abs(move.dy)) || 1;
      this.drawArrow(cx, cy, move.dx / mag, move.dy / mag, cell * 0.22, COLORS.glyph);
      if (o.type === "lmover") {
        this.drawKnightMarker(cx, cy, cell * 0.22, COLORS.glyph);
      }
    }, this);

    // Player.
    var pcx = this.player.x * cell + cell / 2;
    var pcy = this.player.y * cell + cell / 2;
    ctx.fillStyle = COLORS.player;
    ctx.beginPath();
    ctx.arc(pcx, pcy, cell * 0.32, 0, Math.PI * 2);
    ctx.fill();
  };

  // ---------- page wiring ----------

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  function bestScore() {
    return parseInt(window.localStorage.getItem(STORAGE_KEY) || "0", 10);
  }

  function maybeSaveBest(turns) {
    if (turns > bestScore()) {
      window.localStorage.setItem(STORAGE_KEY, String(turns));
    }
  }

  function renderLeaderboard(scores) {
    var body = document.getElementById("ts-leaderboard-body");
    if (!body) return;
    if (!scores.length) {
      body.innerHTML = "<tr><td colspan='3' class='muted'>No scores yet -- be the first.</td></tr>";
      return;
    }
    body.innerHTML = scores
      .map(function (s, i) {
        return (
          "<tr><td class='mono'>" + (i + 1) + "</td>" +
          "<td class='mono'>" + escapeHtml(s.player_name) + "</td>" +
          "<td class='mono'>" + s.turns_survived + "</td></tr>"
        );
      })
      .join("");
  }

  function refreshLeaderboard() {
    fetch(LEADERBOARD_URL)
      .then(function (r) { return r.json(); })
      .then(function (data) { if (data.ok) renderLeaderboard(data.scores); })
      .catch(function () {});
  }

  document.addEventListener("DOMContentLoaded", function () {
    var canvas = document.getElementById("ts-canvas");
    if (!canvas) return;

    var game = new TimedSquares(canvas);
    var scoreEl = document.getElementById("ts-score");
    var bestEl = document.getElementById("ts-best");
    var overlay = document.getElementById("ts-overlay");
    var finalScoreEl = document.getElementById("ts-final-score");
    var submitForm = document.getElementById("ts-submit-form");
    var nameInput = document.getElementById("ts-name-input");
    var submitStatus = document.getElementById("ts-submit-status");
    var playAgainBtn = document.getElementById("ts-play-again");

    bestEl.textContent = bestScore();

    var resizeTimer = null;
    window.addEventListener("resize", function () {
      // Debounced: resize fires continuously while dragging a window
      // edge, and re-syncing canvas resolution on every single event
      // would recreate the (large) bitmap dozens of times a second.
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        game.resize();
        game.render();
      }, 120);
    });

    game.onGameOver = function (turns) {
      maybeSaveBest(turns);
      bestEl.textContent = bestScore();
      finalScoreEl.textContent = turns;
      overlay.hidden = false;
      submitForm.hidden = false;
      playAgainBtn.hidden = true;
      submitStatus.textContent = "";
      nameInput.value = "";
      nameInput.focus();
    };

    function submitScore() {
      var body = new URLSearchParams({
        turns_survived: String(game.turn),
        player_name: nameInput.value || "",
      });
      submitStatus.textContent = "Submitting...";
      fetch(SUBMIT_URL, { method: "POST", body: body })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.ok) {
            submitStatus.textContent = data.error || "Could not submit score.";
            return;
          }
          submitStatus.textContent = data.made_leaderboard
            ? "On the leaderboard at #" + data.rank + "!"
            : "Saved. Rank #" + data.rank + ".";
          submitForm.hidden = true;
          playAgainBtn.hidden = false;
          refreshLeaderboard();
        })
        .catch(function () {
          submitStatus.textContent = "Could not submit score -- check your connection.";
        });
    }

    document.getElementById("ts-submit-score").addEventListener("click", submitScore);
    document.getElementById("ts-skip-submit").addEventListener("click", function () {
      submitForm.hidden = true;
      playAgainBtn.hidden = false;
    });
    playAgainBtn.addEventListener("click", function () {
      overlay.hidden = true;
      game.reset();
      scoreEl.textContent = "0";
    });
    document.getElementById("ts-restart").addEventListener("click", function () {
      overlay.hidden = true;
      game.reset();
      scoreEl.textContent = "0";
    });

    document.addEventListener("keydown", function (e) {
      var dirName = KEY_TO_DIR[e.key];
      if (!dirName) return;
      if (overlay && !overlay.hidden) return;
      e.preventDefault();
      game.tryMovePlayer(dirName);
      scoreEl.textContent = game.turn;
    });

    refreshLeaderboard();
  });

  // Exposed for tests/debugging via the browser console -- not used by
  // any other module.
  window.__timedSquaresEngine = TimedSquares;
})();
