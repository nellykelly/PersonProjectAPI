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
    chaser: "#4ade80",
    turret: "#fb923c",
    turretBeam: "rgba(251, 146, 60, 0.35)",
    zigzag: "#f472b6",
    grid: "rgba(255,255,255,0.08)",
    glyph: "#0d1117",
    stackBadge: "#facc15",
    stackBadgeEdge: "#0d1117",
  };

  var TYPE_LABELS = {
    standard: "Standard",
    jumper: "Jumper",
    lmover: "L-mover",
    diagonal: "Diagonal",
    bouncer: "Bouncer",
    chaser: "Chaser",
    turret: "Turret",
    zigzag: "Zigzag",
  };

  // The telegraph arrow already shows a single obstacle's next move, but
  // once two share a cell their arrows are drawn on top of each other and
  // neither is readable -- so the stacked-cell tooltip has to say it in
  // words instead of relying on the glyph.
  function describeMove(move) {
    if (!move) return "stays put";
    // The turret doesn't move at all -- its nextMove instead carries a
    // fire flag/axis or a turns-left countdown, which needs its own
    // wording rather than falling through to the dx/dy phrasing below.
    if (move.fire) return "fires down its " + (move.axis === "row" ? "row" : "column");
    if (move.turnsLeft != null) {
      return "fires in " + move.turnsLeft + (move.turnsLeft === 1 ? " turn" : " turns");
    }
    if (!move.dx && !move.dy) return "stays put";
    var parts = [];
    if (move.dy) parts.push(Math.abs(move.dy) + " " + (move.dy > 0 ? "down" : "up"));
    if (move.dx) parts.push(Math.abs(move.dx) + " " + (move.dx > 0 ? "right" : "left"));
    return "moves " + parts.join(" and ");
  }

  // Difficulty curve -- feel-based, tuned by actually playing it rather
  // than derived from a formula (see the build prompt's own "left to
  // feel-based tuning" allowance). Both the spawn chance and which
  // edges/types are available widen with turns survived.
  var EDGE_UNLOCK_TURNS = { top: 0, bottom: 12, left: 24, right: 36 };
  // The original five keep their exact unlock turns. zigzag slots in next
  // to diagonal (same family: fixed diagonal stepping); chaser and turret
  // -- genuinely new kinds of behavior, not just new geometry -- unlock
  // last, after every fixed-pattern mover is already in play.
  var TYPE_UNLOCK_TURNS = {
    standard: 0, jumper: 10, bouncer: 18, diagonal: 26, lmover: 34,
    zigzag: 30, chaser: 40, turret: 46,
  };
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

  // Alternates between the two diagonals on either side of its spawn-edge
  // axis: the inward component (o.dir's fixed axis) never changes, but the
  // perpendicular sign flips every single turn -- unlike Diagonal, which
  // locks onto one direction for its whole life, this traces an actual
  // zigzag rather than a straight diagonal line.
  function decideZigzag(o) {
    o.zigSign = -o.zigSign;
    if (o.primaryAxis === "y") {
      return { dx: o.zigSign, dy: o.dir.dy };
    }
    return { dx: o.dir.dx, dy: o.zigSign };
  }

  // The one obstacle with no fixed pattern at all: every turn it re-aims
  // one step toward the player's *current* position (whatever it was
  // right after their last move -- exactly what's knowable at telegraph
  // time, same as every other obstacle). No pathfinding, just a single
  // greedy step along whichever axis is currently further off -- the same
  // "steer toward, don't route around" philosophy Pipeline World's
  // Production Town characters use, applied to a hostile instead of a
  // friendly.
  function decideChaser(o, player) {
    var dx = player.x - o.x;
    var dy = player.y - o.y;
    if (!dx && !dy) return { dx: 0, dy: 0 };
    if (Math.abs(dx) >= Math.abs(dy)) {
      return { dx: dx > 0 ? 1 : -1, dy: 0 };
    }
    return { dx: 0, dy: dy > 0 ? 1 : -1 };
  }

  // Never moves from its spawn cell. Instead counts down a fuse; once it
  // hits zero, that turn's "move" is a full-row/column beam fired along
  // whichever axis matches its spawn edge (top/bottom -> a column, left/
  // right -> a row) -- telegraphed the same way as everything else: this
  // return value describes what happens on the *next* resolveTurn, so a
  // firing turn is shown a full turn ahead as a lit-up danger stripe, and
  // a charging turn shows its countdown instead of an arrow.
  function decideTurret(o) {
    o.fuse -= 1;
    if (o.fuse <= 0) {
      o.fuse = 2 + randInt(3); // 2-4 turns until the next shot
      return { dx: 0, dy: 0, fire: true, axis: o.axis };
    }
    return { dx: 0, dy: 0, fire: false, turnsLeft: o.fuse };
  }

  var DECIDERS = {
    standard: decideStandard,
    jumper: decideJumper,
    bouncer: decideBouncer,
    diagonal: decideDiagonal,
    lmover: decideLMover,
    zigzag: decideZigzag,
    chaser: decideChaser,
    turret: decideTurret,
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

  function spawnObstacle(turn, occupied, player) {
    var edge = pick(availableEdges(turn));
    var type = pick(availableTypes(turn));
    var x, y, dir, primaryAxis;

    if (edge === "top") { x = randInt(GRID_SIZE); y = 0; dir = DIRS.down; primaryAxis = "y"; }
    else if (edge === "bottom") { x = randInt(GRID_SIZE); y = GRID_SIZE - 1; dir = DIRS.up; primaryAxis = "y"; }
    else if (edge === "left") { x = 0; y = randInt(GRID_SIZE); dir = DIRS.right; primaryAxis = "x"; }
    else { x = GRID_SIZE - 1; y = randInt(GRID_SIZE); dir = DIRS.left; primaryAxis = "x"; }

    if (occupied(x, y)) return null;

    var zigSign;
    if (type === "diagonal") {
      // Keep the edge-derived inward component, randomize the other axis
      // -- always makes net progress across the board, never travels
      // parallel to its spawn edge forever.
      dir = primaryAxis === "y" ? { dx: pick([1, -1]), dy: dir.dy } : { dx: dir.dx, dy: pick([1, -1]) };
    } else if (type === "zigzag") {
      zigSign = pick([1, -1]);
      dir = primaryAxis === "y" ? { dx: zigSign, dy: dir.dy } : { dx: dir.dx, dy: zigSign };
    }

    var obstacle = { id: nextObstacleId++, x: x, y: y, type: type, dir: dir, primaryAxis: primaryAxis };
    if (type === "zigzag") {
      obstacle.zigSign = zigSign;
    } else if (type === "turret") {
      // Fires along the axis its spawn edge implies -- a top/bottom
      // entrant guards a column, a left/right entrant guards a row --
      // the same "keep the inward component from the spawn edge" idea
      // Diagonal already uses, applied to a beam instead of a step.
      obstacle.axis = primaryAxis === "y" ? "col" : "row";
      // Decremented once immediately below before the first telegraph is
      // shown, so the real starting range a player ever sees matches the
      // steady-state reset range in decideTurret (2-4), not 3-5.
      obstacle.fuse = 3 + randInt(3);
    }
    obstacle.nextMove = DECIDERS[type](obstacle, player);
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
    this.stacks = [];  // recomputed by render(); see stackedCells()
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

  // True when an obstacle and the player traded places this turn -- the
  // obstacle started on the square the player just moved to, and ended on
  // the square the player just left. Their paths cross head-on even
  // though neither is standing on the other once everything has settled,
  // so final positions alone would let the player walk straight through
  // an oncoming obstacle.
  //
  // Deliberately only an exact swap, not a general path-crossing test: a
  // jumper passing *over* the player's square mid-hop is an explicit rule
  // of the game (see the README), and a broader check would break it. A
  // two-cell move also can't produce a swap, since that requires ending
  // one cell from where you started.
  TimedSquares.prototype.swappedWithPlayer = function (origins, playerFrom) {
    if (!playerFrom) return false;
    var player = this.player;
    return origins.some(function (rec) {
      return (
        rec.fromX === player.x && rec.fromY === player.y &&
        rec.obstacle.x === playerFrom.x && rec.obstacle.y === playerFrom.y
      );
    });
  };

  TimedSquares.prototype.tryMovePlayer = function (dirName) {
    if (this.gameOver) return;
    var dir = DIRS[dirName];
    if (!dir) return;
    var nx = this.player.x + dir.dx;
    var ny = this.player.y + dir.dy;
    if (!inBounds(nx, ny)) return; // wall: not a valid turn, nothing advances

    var from = { x: this.player.x, y: this.player.y };
    this.player.x = nx;
    this.player.y = ny;
    this.resolveTurn(from);
  };

  // Collision is decided on where everything ENDS this turn, not on what
  // the player moved through on the way.
  //
  // This used to test for a collision before the obstacles moved, which
  // killed the player for stepping onto a square an obstacle was in the
  // middle of leaving -- even though its own telegraph arrow had promised
  // it was going somewhere else. That directly contradicts the premise of
  // the game: the arrows are a contract about where things will be, and
  // reading them correctly has to be rewarded. Stepping into a square as
  // its occupant steps out is now safe, which is what the board was
  // already telling the player.
  //
  // The one path-based exception is a head-on swap, see swappedWithPlayer.
  TimedSquares.prototype.resolveTurn = function (playerFrom) {
    this.turn += 1;

    // Where each obstacle stood before moving, needed for the swap test.
    var origins = this.obstacles.map(function (o) {
      return { obstacle: o, fromX: o.x, fromY: o.y };
    });

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

    if (this.checkCollision() || this.swappedWithPlayer(origins, playerFrom) || this.checkBeamCollision()) {
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
      }, this.player);
      if (obstacle) this.obstacles.push(obstacle);
    }

    // Re-telegraph every surviving obstacle's next move.
    this.obstacles.forEach(function (o) {
      o.nextMove = DECIDERS[o.type](o, self.player);
    });

    this.render();
  };

  // A turret's beam is checked separately from checkCollision: it isn't
  // an obstacle occupying the player's cell, it's a full row/column that
  // was telegraphed as "about to fire" on last render and resolves the
  // instant the player's new position lands anywhere on that line.
  TimedSquares.prototype.checkBeamCollision = function () {
    var player = this.player;
    return this.obstacles.some(function (o) {
      if (o.type !== "turret" || !o.nextMove || !o.nextMove.fire) return false;
      return o.nextMove.axis === "row" ? o.y === player.y : o.x === player.x;
    });
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

  // A turret's countdown, in place of the arrow every other obstacle
  // draws -- there's no {dx,dy} to point at, and a raw number is a more
  // exact telegraph than an icon could be: it says precisely how many
  // more resolves until the beam fires.
  TimedSquares.prototype.drawCountdown = function (cx, cy, cell, n, color) {
    var ctx = this.ctx;
    ctx.save();
    ctx.fillStyle = color;
    ctx.font = "bold " + Math.round(cell * 0.34) + "px system-ui, -apple-system, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(String(n), cx, cy + cell * 0.02);
    ctx.restore();
  };

  // The full row or column a turret is about to fire down, lit up a full
  // turn ahead of the shot actually resolving -- same telegraph contract
  // as every arrow: what's drawn now is exactly what checkBeamCollision
  // tests against on the very next resolveTurn.
  TimedSquares.prototype.drawBeam = function (o, cell) {
    var ctx = this.ctx;
    ctx.save();
    ctx.fillStyle = COLORS.turretBeam;
    if (o.nextMove.axis === "row") {
      ctx.fillRect(0, o.y * cell, GRID_SIZE * cell, cell);
    } else {
      ctx.fillRect(o.x * cell, 0, cell, GRID_SIZE * cell);
    }
    ctx.restore();
  };

  // Cells holding more than one obstacle.
  //
  // Nothing prevents this: spawning avoids an occupied cell, but
  // resolveTurn moves every obstacle independently, so two can land on
  // the same square. When they do, the second square is drawn over the
  // first and their telegraph arrows overlap -- the board silently stops
  // being readable at exactly the moment it matters most, since a stacked
  // cell is two threats rather than one. Marked with a badge, and the
  // detail moved into a hover tooltip rather than crammed into the cell.
  TimedSquares.prototype.stackedCells = function () {
    var byCell = {};
    this.obstacles.forEach(function (o) {
      var key = o.x + "," + o.y;
      (byCell[key] = byCell[key] || []).push(o);
    });
    return Object.keys(byCell)
      .filter(function (key) { return byCell[key].length > 1; })
      .map(function (key) {
        var parts = key.split(",");
        return { x: Number(parts[0]), y: Number(parts[1]), obstacles: byCell[key] };
      });
  };

  TimedSquares.prototype.drawStackBadge = function (stack, cell) {
    var ctx = this.ctx;
    var r = cell * 0.19;
    var cx = stack.x * cell + cell - r - cell * 0.07;
    var cy = stack.y * cell + r + cell * 0.07;

    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.stackBadge;
    ctx.fill();
    ctx.lineWidth = Math.max(1, cell * 0.022);
    ctx.strokeStyle = COLORS.stackBadgeEdge;
    ctx.stroke();

    ctx.fillStyle = COLORS.glyph;
    ctx.font = "bold " + Math.round(r * 1.45) + "px system-ui, -apple-system, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("!", cx, cy + r * 0.08);
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

    // Turret beams: drawn as a full-board pass before any obstacle body,
    // so a turret's own square renders on top of its own warning stripe
    // rather than getting buried under it.
    this.obstacles.forEach(function (o) {
      if (o.type === "turret" && o.nextMove && o.nextMove.fire) {
        this.drawBeam(o, cell);
      }
    }, this);

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

      // The turret never has a {dx,dy} worth drawing as an arrow -- it
      // either shows a countdown while charging or lets the beam stripe
      // above speak for itself on the turn it actually fires.
      if (o.type === "turret") {
        if (!move.fire) {
          this.drawCountdown(cx, cy, cell, move.turnsLeft, COLORS.glyph);
        }
        return;
      }

      var mag = Math.max(Math.abs(move.dx), Math.abs(move.dy)) || 1;
      this.drawArrow(cx, cy, move.dx / mag, move.dy / mag, cell * 0.22, COLORS.glyph);
      if (o.type === "lmover") {
        this.drawKnightMarker(cx, cy, cell * 0.22, COLORS.glyph);
      }
    }, this);

    // Cached on the instance rather than recomputed on every pointer move:
    // the hover lookup then describes exactly what is currently drawn,
    // instead of a board state that may have advanced since.
    this.stacks = this.stackedCells();
    this.stacks.forEach(function (stack) {
      this.drawStackBadge(stack, cell);
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

    // ---------- stacked-cell tooltip ----------
    //
    // The "!" badge says *that* a square holds more than one obstacle;
    // this says which ones and where each is about to go, since their
    // telegraph arrows are drawn on top of each other and unreadable.
    var stackTip = document.getElementById("ts-stack-tip");

    function cellFromPointer(evt) {
      var rect = canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return null;
      var size = rect.width / GRID_SIZE;
      var x = Math.floor((evt.clientX - rect.left) / size);
      var y = Math.floor((evt.clientY - rect.top) / size);
      return inBounds(x, y) ? { x: x, y: y, size: size, rect: rect } : null;
    }

    function hideStackTip() {
      if (stackTip) stackTip.hidden = true;
    }

    function updateStackTip(evt) {
      if (!stackTip) return;
      var at = cellFromPointer(evt);
      if (!at) return hideStackTip();

      var stack = (game.stacks || []).filter(function (s) {
        return s.x === at.x && s.y === at.y;
      })[0];
      if (!stack) return hideStackTip();

      stackTip.innerHTML =
        '<b class="ts-tip-title">' + stack.obstacles.length + " obstacles on this square</b>" +
        stack.obstacles
          .map(function (o) {
            var colour = COLORS[o.type] || COLORS.standard;
            return (
              '<span class="ts-tip-row">' +
              '<i class="ts-tip-swatch" style="background:' + colour + '"></i>' +
              "<span>" + escapeHtml(TYPE_LABELS[o.type] || o.type) + " &mdash; " +
              escapeHtml(describeMove(o.nextMove)) + "</span>" +
              "</span>"
            );
          })
          .join("");

      // Unhide before measuring -- offsetWidth is 0 on a hidden element,
      // which would put every tooltip in the wrong place.
      stackTip.hidden = false;
      var w = stackTip.offsetWidth;
      var h = stackTip.offsetHeight;

      var left = (at.x + 0.5) * at.size - w / 2;
      var top = at.y * at.size - h - 8;
      if (top < 4) top = (at.y + 1) * at.size + 8;  // no room above: flip below
      left = Math.max(4, Math.min(left, at.rect.width - w - 4));

      stackTip.style.left = Math.round(left) + "px";
      stackTip.style.top = Math.round(top) + "px";
    }

    canvas.addEventListener("pointermove", updateStackTip);
    canvas.addEventListener("pointerdown", updateStackTip);  // touch: tap to inspect
    canvas.addEventListener("pointerleave", hideStackTip);

    document.addEventListener("keydown", function (e) {
      var dirName = KEY_TO_DIR[e.key];
      if (!dirName) return;
      if (overlay && !overlay.hidden) return;
      e.preventDefault();
      // The board is about to change, so whatever the tooltip is
      // describing is about to be stale -- drop it rather than leave a
      // confident description of a square that no longer looks like that.
      hideStackTip();
      game.tryMovePlayer(dirName);
      scoreEl.textContent = game.turn;
    });

    // Same purpose as __timedSquaresEngine below, but the *wired* instance:
    // the class alone can't verify anything that depends on the page's own
    // event handlers (the stacked-cell tooltip reads this instance's
    // `stacks`), so a fresh instance built in the console tests the drawing
    // and misses the wiring entirely.
    window.__timedSquaresGame = game;

    refreshLeaderboard();
  });

  // Exposed for tests/debugging via the browser console -- not used by
  // any other module.
  window.__timedSquaresEngine = TimedSquares;
})();
