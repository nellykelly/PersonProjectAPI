// Production Town: the dedicated, large live-world viewer. Separate
// page from Pipeline World's join form + build tracker on purpose --
// cramming "the pipeline mechanics" and "the place characters live"
// into one small canvas made neither read clearly. This page only
// draws characters that have actually cleared Deploy (the pipeline's
// final content-changing stage -- Verify runs after but doesn't change
// anything, see pipeline.py).
//
// Every character answers the same fixed 4 icebreaker questions (see
// validators.FIXED_ICEBREAKER_QUESTIONS) -- that's what lets two nearby
// characters hold a real conversation: characters actively seek out the
// nearest free neighbor (see stepMovement), and once close enough, they
// run a small scripted back-and-forth built from both of their answers
// (see buildConversationScript) rather than just reciting "Prefix:
// answer" at each other.
//
// The background is a procedurally-generated, schematic top-down city
// (streets + blocks + a park) -- flat shapes only, no external map
// tiles/images, generated once at load and cached, consistent with the
// rest of the site's minimal-chrome visual style (see buildCity).
(function () {
  "use strict";

  var BOUNDS = window.PRODUCTION_TOWN_BOUNDS || [40, 40, 960, 560];
  var SPAWN_BUBBLE_DURATION_MS = 6000;

  var CONVERSATION_DISTANCE = 45; // virtual px -- how close two characters must be to start talking
  var CONVERSATION_LINE_MS = 2000; // how long each line of the exchange is shown
  var CONVERSATION_COOLDOWN_MS = 7000; // pause before either participant can start a new conversation
  var NOTICE_RADIUS = 300; // virtual px -- how far a character "notices" a free neighbor worth walking toward
  var WANDER_SPEED = 0.5;
  var SEEK_TURN_RATE = 0.05; // how quickly velocity turns toward a noticed neighbor, per frame

  var appearanceColor = {};
  var appearanceLabel = {};
  (window.APPEARANCE_COLORS || []).forEach(function (opt) {
    appearanceColor[opt.id] = opt.color;
    appearanceLabel[opt.id] = opt.label;
  });

  // Character customization lookups, keyed by the id a visitor picked
  // at join time (validators.HEAD_TYPE_OPTIONS/BODY_TYPE_OPTIONS/
  // HAND_TYPE_OPTIONS on the server side) -- these are real, validated
  // picks stored on the character, not client-side randomness, so every
  // viewer sees the same look for a given character.
  var headTypeById = {};
  (window.HEAD_TYPE_OPTIONS || []).forEach(function (opt) { headTypeById[opt.id] = opt; });
  var bodyTypeById = {};
  (window.BODY_TYPE_OPTIONS || []).forEach(function (opt) { bodyTypeById[opt.id] = opt; });
  var handTypeById = {};
  (window.HAND_TYPE_OPTIONS || []).forEach(function (opt) { handTypeById[opt.id] = opt; });

  var DEFAULT_HEAD_TYPE = { shape: "round", skin: "#e8b98c", hair: "#3b2a1e" };
  var DEFAULT_BODY_TYPE = { width: 13, height: 13 };

  var characters = {}; // id -> {character, color, x, y, vx, vy, bubbleUntil, facingX, facingY}
  var selectedId = null;

  // Conversation state, keyed by "idA:idB". busyIds/cooldownUntil are
  // keyed by a single character id so a character can only be in one
  // conversation at a time and can't immediately re-start one right
  // after finishing.
  var conversations = {};
  var busyIds = {};
  var cooldownUntil = {};

  function initials(character) {
    return ((character.first_name || "?")[0] + (character.last_name || "?")[0]).toUpperCase();
  }

  function findTopic(character, questionId) {
    return (character.icebreakers || []).filter(function (ib) {
      return ib.question_id === questionId;
    })[0];
  }

  function addCharacter(character, announce) {
    if (character.world_x == null || character.world_y == null) return;
    // The server picks world_x/world_y with no idea what the client's
    // procedurally-generated street layout looks like, so it can easily
    // land inside a block -- snap onto the nearest walkable street/park
    // point instead.
    var pos = snapToNearestWalkable(character.world_x, character.world_y);
    characters[character.id] = {
      character: character,
      color: appearanceColor[character.appearance_id] || "#3aa0ff",
      x: pos.x,
      y: pos.y,
      vx: (Math.random() - 0.5) * 0.5,
      vy: (Math.random() - 0.5) * 0.5,
      facingX: 0,
      facingY: 1,
      bubbleUntil: announce ? performance.now() + SPAWN_BUBBLE_DURATION_MS : 0,
    };
    updatePopulationCount();
  }

  function updatePopulationCount() {
    var el = document.getElementById("population-count");
    if (el) el.textContent = Object.keys(characters).length;
  }

  function setupInitialWorld() {
    (window.INITIAL_LIVE_WORLD || []).forEach(function (c) {
      addCharacter(c, false);
    });
  }

  function setLiveIndicator(state) {
    var el = document.getElementById("live-indicator");
    if (!el) return;
    if (state === "connected") {
      el.textContent = "● LIVE";
      el.className = "badge badge-open";
    } else {
      el.textContent = "○ Reconnecting...";
      el.className = "badge badge-closed";
    }
  }

  function connectSocket() {
    if (!window.io) return;
    var socket = window.io("/pipeline-world");
    socket.on("connect", function () {
      setLiveIndicator("connected");
    });
    socket.on("disconnect", function () {
      setLiveIndicator("reconnecting");
    });
    socket.on("pipeline_update", function (data) {
      if (data.stage === "deploy" && data.status === "pass") {
        addCharacter(data.character, true);
      }
    });
  }

  // ---------- conversation script generation ----------
  //
  // Every character answers the same 4 fixed topics, so a conversation
  // is built once (when two characters get close enough) as an ordered
  // script of {speakerId, text} lines -- a greeting, then each of the 4
  // topics as its own little ask/answer/reaction exchange (alternating
  // who asks), then a sign-off -- rather than the two of them just
  // reading their own answers out loud one after another.

  function pick(list) {
    return list[Math.floor(Math.random() * list.length)];
  }

  var OPENERS_A = [
    "Oh hey, {name}!",
    "{name}! Didn't expect to run into you here.",
    "Hey, small world!",
    "Well if it isn't {name}.",
  ];
  var OPENERS_B = [
    "Hey! How's it going?",
    "Oh, hi! Good to see a familiar face.",
    "Hey there, just out for a walk.",
    "Hi! Funny running into you.",
  ];
  var CLOSERS = [
    ["Anyway, good talking to you!", "You too, take care!"],
    ["I should get going -- catch you later!", "See you around!"],
    ["Let's catch up again sometime.", "Definitely, sounds good."],
    ["Alright, I'll let you go.", "Okay, see you next time!"],
  ];

  var TOPIC_PHRASES = {
    food: {
      ask: ["So, what's your favorite food?", "Are you much of a foodie? What do you like to eat?", "Speaking of favorites -- what's your go-to food?"],
      answer: ["Honestly? {answer}, every time.", "Gotta say, {answer}.", "{answer}, hands down."],
      same: ["No way, me too!", "Ha, we're twins on that one.", "Same here -- great taste."],
      diff: ["Interesting, never tried that.", "Nice, I'll have to give that a shot.", "Different pick than me, but I respect it."],
    },
    movie: {
      ask: ["What's your favorite movie?", "Got a movie you could watch on repeat?", "Any favorite films?"],
      answer: ["Probably {answer}.", "{answer} -- I never get tired of it.", "Gotta go with {answer}."],
      same: ["No way, that's one of mine too!", "Great minds -- same pick!", "Love that one too."],
      diff: ["Haven't seen that one, is it good?", "I'll have to check that out.", "Never seen it, but noted."],
    },
    hobby: {
      ask: ["What do you like to do for fun?", "Got any hobbies?", "What's something you enjoy outside of work?"],
      answer: ["Lately it's been {answer}.", "Mostly {answer}, honestly.", "I'm really into {answer}."],
      same: ["No way, me too -- we should do that together sometime.", "Same hobby! Small world.", "Love that, same here."],
      diff: ["That's cool, I've never tried that.", "Nice, sounds relaxing.", "Interesting -- how'd you get into that?"],
    },
    weekend: {
      ask: ["What's your ideal weekend look like?", "How do you like to spend your weekends?", "What's a perfect weekend for you?"],
      answer: ["Honestly? {answer}.", "Probably {answer}, if I'm being honest.", "{answer} sounds perfect to me."],
      same: ["That's exactly my kind of weekend too.", "Same! We should plan one.", "Couldn't agree more."],
      diff: ["Sounds nice, different pace than mine though.", "That sounds fun, actually.", "Nice, I need to try that sometime."],
    },
  };

  var TOPIC_ORDER = ["food", "movie", "hobby", "weekend"];

  // `idA`/`idB` are passed explicitly rather than read off `a`/`b` --
  // the character state objects stored in `characters` don't carry
  // their own map key as a `.id` field (only `a.character.id` does),
  // so building each script line's speakerId from `a.id`/`b.id` would
  // silently be `undefined` for both speakers and currentConversationLine
  // would never match either one -- the exact bug that made speech
  // bubbles never appear even though the conversation link line did.
  function buildConversationScript(a, b, idA, idB) {
    var bName = b.character.first_name;
    var script = [];

    script.push({ speakerId: idA, text: pick(OPENERS_A).replace("{name}", bName) });
    script.push({ speakerId: idB, text: pick(OPENERS_B) });

    TOPIC_ORDER.forEach(function (topicId, i) {
      var askerIsA = i % 2 === 0;
      var asker = askerIsA ? a : b;
      var answerer = askerIsA ? b : a;
      var askerId = askerIsA ? idA : idB;
      var answererId = askerIsA ? idB : idA;
      var askerAnswer = findTopic(asker.character, topicId);
      var answererAnswer = findTopic(answerer.character, topicId);
      if (!askerAnswer || !answererAnswer) return;

      var phrases = TOPIC_PHRASES[topicId];
      script.push({ speakerId: askerId, text: pick(phrases.ask) });
      script.push({ speakerId: answererId, text: pick(phrases.answer).replace("{answer}", answererAnswer.answer) });

      var same = askerAnswer.answer.trim().toLowerCase() === answererAnswer.answer.trim().toLowerCase();
      script.push({ speakerId: askerId, text: pick(same ? phrases.same : phrases.diff) });
    });

    var closer = pick(CLOSERS);
    script.push({ speakerId: idA, text: closer[0] });
    script.push({ speakerId: idB, text: closer[1] });

    return script;
  }

  // ---------- conversations ----------

  function endConversation(key) {
    var convo = conversations[key];
    if (!convo) return;
    var now = performance.now();
    delete busyIds[convo.aId];
    delete busyIds[convo.bId];
    cooldownUntil[convo.aId] = now + CONVERSATION_COOLDOWN_MS;
    cooldownUntil[convo.bId] = now + CONVERSATION_COOLDOWN_MS;
    delete conversations[key];
  }

  function updateConversations(now) {
    Object.keys(conversations).forEach(function (key) {
      var convo = conversations[key];
      if (now - convo.startedAt >= convo.script.length * CONVERSATION_LINE_MS) {
        endConversation(key);
      }
    });

    var ids = Object.keys(characters);
    for (var i = 0; i < ids.length; i++) {
      for (var j = i + 1; j < ids.length; j++) {
        var idA = ids[i], idB = ids[j];
        if (busyIds[idA] || busyIds[idB]) continue;
        if ((cooldownUntil[idA] || 0) > now || (cooldownUntil[idB] || 0) > now) continue;

        var a = characters[idA], b = characters[idB];
        if (!(a.character.icebreakers || []).length || !(b.character.icebreakers || []).length) continue;

        var dist = Math.hypot(a.x - b.x, a.y - b.y);
        if (dist > CONVERSATION_DISTANCE) continue;

        var key = idA + ":" + idB;
        conversations[key] = {
          aId: idA,
          bId: idB,
          startedAt: now,
          script: buildConversationScript(a, b, idA, idB),
        };
        busyIds[idA] = key;
        busyIds[idB] = key;
      }
    }
  }

  // Returns the single line `id` should be shown saying right now, or
  // null if it's not that character's turn to speak (or not in a
  // conversation at all).
  function currentConversationLine(id, now) {
    var key = busyIds[id];
    var convo = key && conversations[key];
    if (!convo) return null;

    var lineIndex = Math.floor((now - convo.startedAt) / CONVERSATION_LINE_MS);
    var line = convo.script[lineIndex];
    if (!line) return null;
    return String(line.speakerId) === String(id) ? line.text : null;
  }

  // ---------- movement: idle wander, or seek out a free neighbor ----------

  function findSeekTarget(id, c, now) {
    var best = null;
    var bestDist = NOTICE_RADIUS;
    Object.keys(characters).forEach(function (otherId) {
      if (otherId === id) return;
      if (busyIds[otherId]) return; // already talking to someone else
      if ((cooldownUntil[otherId] || 0) > now) return; // just finished a conversation
      var o = characters[otherId];
      var d = Math.hypot(c.x - o.x, c.y - o.y);
      if (d < bestDist) {
        bestDist = d;
        best = o;
      }
    });
    return best;
  }

  function stepMovement(id, c, now) {
    var target = findSeekTarget(id, c, now);

    if (target) {
      var dx = target.x - c.x, dy = target.y - c.y;
      var dist = Math.hypot(dx, dy) || 1;
      var desiredVx = (dx / dist) * WANDER_SPEED;
      var desiredVy = (dy / dist) * WANDER_SPEED;
      c.vx += (desiredVx - c.vx) * SEEK_TURN_RATE;
      c.vy += (desiredVy - c.vy) * SEEK_TURN_RATE;
    } else if (Math.random() < 0.02) {
      c.vx = (Math.random() - 0.5) * WANDER_SPEED;
      c.vy = (Math.random() - 0.5) * WANDER_SPEED;
    }

    // Axis-separated collision: try each axis independently against the
    // street mask (see isWalkable) so a character sliding along a wall
    // doesn't just stop dead the instant one axis is blocked -- it keeps
    // moving along the other axis, same as standard 2D tile collision.
    var nx = c.x + c.vx;
    if (isWalkable(nx, c.y)) {
      c.x = nx;
    } else {
      c.vx *= -1;
    }
    var ny = c.y + c.vy;
    if (isWalkable(c.x, ny)) {
      c.y = ny;
    } else {
      c.vy *= -1;
    }

    var speed = Math.hypot(c.vx, c.vy);
    if (speed > 0.05) {
      c.facingX = c.vx / speed;
      c.facingY = c.vy / speed;
    }
  }

  // ---------- city background (procedural, generated once) ----------
  //
  // A schematic top-down city: a grid of blocks separated by streets,
  // 1-3 flat-colored buildings per block, and one block turned into a
  // small park -- flat shapes only (no external map tiles/images),
  // generated once at load and cached in CITY, then just redrawn every
  // frame from that fixed layout (cheap, no per-frame recompute).
  //
  // Each building is hollow, not solid: only a thin wall band around its
  // own footprint blocks movement, with a gap at the door's position --
  // so characters can actually walk in through the door and roam the
  // interior (see isWalkable/isBlockedByBuildingWall). Streets, the
  // gaps/margins between buildings, and the park were never inside any
  // building's rect, so they're walkable without a separate rule. A
  // building's door is placed on whichever of its top/bottom edges faces
  // an actual street corridor rather than the outer map boundary (every
  // row has one except the very top and very bottom rows -- see the door
  // placement logic below).

  var CITY = null;

  // Building materials -- wood/stone/brick, "simple rustic quality"
  // rather than a uniform gray-blue box, per the top-down interiors
  // reference (slynyrd.com/blog/2021/11/30/pixelblog-35-top-down-interiors):
  // each has its own wall color, a darker roofline color, and a texture
  // pattern (brick coursing, wood planking, or rough stone blocking)
  // drawn over the fill so the wall reads as an actual material instead
  // of a flat rectangle.
  var MATERIALS = [
    { wall: "#8b4a3d", roof: "#5c2f26", texture: "brick" },
    { wall: "#7d7364", roof: "#544c40", texture: "stone" },
    { wall: "#6b4a35", roof: "#46301f", texture: "wood" },
    { wall: "#5c6b52", roof: "#3c4636", texture: "wood" },
    { wall: "#8a7150", roof: "#5f4d36", texture: "stone" },
    { wall: "#48586b", roof: "#2f3a47", texture: "brick" },
  ];

  function shadeColor(hex, amount) {
    var num = parseInt(hex.slice(1), 16);
    var r = (num >> 16) & 0xff, g = (num >> 8) & 0xff, b = num & 0xff;
    function adjust(ch) {
      var target = amount < 0 ? 0 : 255;
      var v = Math.round(ch + (target - ch) * Math.abs(amount));
      return Math.max(0, Math.min(255, v));
    }
    r = adjust(r); g = adjust(g); b = adjust(b);
    return "#" + [r, g, b].map(function (v) { return v.toString(16).padStart(2, "0"); }).join("");
  }

  function buildCity(bounds) {
    var x0 = bounds[0], y0 = bounds[1], x1 = bounds[2], y1 = bounds[3];
    var cols = 4, rows = 3;
    var streetWidth = 26;
    var totalW = x1 - x0, totalH = y1 - y0;
    var blockW = (totalW - streetWidth * (cols - 1)) / cols;
    var blockH = (totalH - streetWidth * (rows - 1)) / rows;

    var blocks = [];
    for (var r = 0; r < rows; r++) {
      for (var c = 0; c < cols; c++) {
        blocks.push({
          x: x0 + c * (blockW + streetWidth),
          y: y0 + r * (blockH + streetWidth),
          w: blockW,
          h: blockH,
        });
      }
    }

    var parkIndex = Math.floor(rows / 2) * cols + Math.floor(cols / 2);
    var park = blocks[parkIndex];
    var trees = [];
    var grassPatches = [];
    if (park) {
      var treeCount = 6;
      for (var t = 0; t < treeCount; t++) {
        trees.push({
          x: park.x + 10 + Math.random() * (park.w - 20),
          y: park.y + 10 + Math.random() * (park.h - 20),
          r: 4 + Math.random() * 3,
        });
      }
      // Mottled grass texture -- irregular patches of a slightly
      // different green so the park reads as grass, not a flat green
      // rectangle.
      for (var g = 0; g < 40; g++) {
        grassPatches.push({
          x: park.x + Math.random() * park.w,
          y: park.y + Math.random() * park.h,
          r: 3 + Math.random() * 5,
          lighter: Math.random() < 0.5,
        });
      }
    }

    var buildings = [];
    blocks.forEach(function (block, i) {
      if (i === parkIndex) return;
      var row = Math.floor(i / cols);
      // Every block row has a real street corridor above it (the gap
      // to the previous row) *except* row 0, and a real street corridor
      // below it *except* the last row -- since a building's door sits
      // on the block's own top/bottom margin, placing it on the edge
      // that faces the map's outer boundary instead of an actual street
      // would make it open onto nothing. Bottom-row blocks flip their
      // door to the top edge; every other row keeps the bottom edge
      // (which is always safe here since only the very last row lacks a
      // street below it).
      var doorOnTop = row === rows - 1;

      var count = 1 + Math.floor(Math.random() * 2);
      var gap = 6;
      var subW = (block.w - gap * (count + 1)) / count;
      for (var k = 0; k < count; k++) {
        var margin = 4 + Math.random() * 6;
        var bw = Math.max(14, subW);
        var bh = Math.max(14, block.h - margin * 2);
        var bx = block.x + gap + k * (subW + gap);
        var by = block.y + margin;
        var doorWidth = Math.min(14, Math.max(8, bw * 0.3));
        var material = pick(MATERIALS);
        buildings.push({
          x: bx,
          y: by,
          w: bw,
          h: bh,
          color: material.wall,
          roofColor: material.roof,
          texture: material.texture,
          windowCols: Math.max(1, Math.floor(bw / 12)),
          windowRows: Math.max(1, Math.floor(bh / 12)),
          door: {
            x: bx + bw / 2 - doorWidth / 2,
            y: doorOnTop ? by : by + bh - 2,
            w: doorWidth,
            side: doorOnTop ? "top" : "bottom",
          },
        });
      }
    });

    return { blocks: blocks, buildings: buildings, park: park, parkIndex: parkIndex, trees: trees, grassPatches: grassPatches, bounds: bounds };
  }

  // Buildings are hollow, not solid: only a thin wall band around each
  // building's perimeter blocks movement, with a gap at the door's exact
  // position on its side -- so a character can actually walk in through
  // the door and roam the interior, rather than the whole footprint (or
  // worse, the whole block) being an impassable block. Streets, the
  // gaps/margins between buildings, and the park were never part of any
  // building's rect, so they're walkable by default without any special
  // case here.
  var WALL_THICKNESS = 4;

  function isBlockedByBuildingWall(building, x, y) {
    var bx = building.x, by = building.y, bw = building.w, bh = building.h;
    if (x < bx || x > bx + bw || y < by || y > by + bh) return false; // outside this building entirely

    var nearTop = y - by <= WALL_THICKNESS;
    var nearBottom = by + bh - y <= WALL_THICKNESS;
    var nearLeft = x - bx <= WALL_THICKNESS;
    var nearRight = bx + bw - x <= WALL_THICKNESS;
    if (!nearTop && !nearBottom && !nearLeft && !nearRight) return false; // deep enough inside to be interior

    var door = building.door;
    if (door && ((door.side === "top" && nearTop) || (door.side === "bottom" && nearBottom))) {
      if (x >= door.x && x <= door.x + door.w) return false; // standing in the doorway gap
    }
    return true;
  }

  function isWalkable(x, y) {
    if (!CITY) return true;
    var b = CITY.bounds;
    if (x < b[0] || x > b[2] || y < b[1] || y > b[3]) return false;
    for (var i = 0; i < CITY.buildings.length; i++) {
      if (isBlockedByBuildingWall(CITY.buildings[i], x, y)) return false;
    }
    return true;
  }

  // A character's server-assigned spawn position has no idea about the
  // client-generated street layout, so it can easily land inside a
  // block -- snap it to the nearest walkable street/park point instead
  // of letting it spawn inside a wall. One-off cost per spawn, not
  // per-frame, so a coarse grid search is plenty fast.
  function snapToNearestWalkable(x, y) {
    if (isWalkable(x, y)) return { x: x, y: y };
    var b = CITY ? CITY.bounds : BOUNDS;
    var best = null, bestDist = Infinity, step = 6;
    for (var gy = b[1]; gy <= b[3]; gy += step) {
      for (var gx = b[0]; gx <= b[2]; gx += step) {
        if (!isWalkable(gx, gy)) continue;
        var d = Math.hypot(gx - x, gy - y);
        if (d < bestDist) {
          bestDist = d;
          best = { x: gx, y: gy };
        }
      }
    }
    return best || { x: b[0] + 5, y: b[1] + 5 };
  }

  // Cheap procedural wall texture -- a few semi-transparent stroked
  // lines over the fill, drawn per-material so brick/stone/wood each
  // read differently instead of all being one flat color:
  //  - brick: coursed horizontal lines + staggered vertical joints
  //  - wood: horizontal plank lines only (siding boards)
  //  - stone: sparse, irregular blocking (rougher, less regular than brick)
  function drawWallTexture(ctx, building) {
    var x = building.x, y = building.y, w = building.w, h = building.h;
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, w, h);
    ctx.clip();
    ctx.strokeStyle = "rgba(0,0,0,0.25)";
    ctx.lineWidth = 1;

    if (building.texture === "brick") {
      var courseH = 5;
      var row = 0;
      for (var yy = y; yy < y + h; yy += courseH, row++) {
        ctx.beginPath();
        ctx.moveTo(x, yy);
        ctx.lineTo(x + w, yy);
        ctx.stroke();
        var offset = row % 2 === 0 ? 0 : 5;
        for (var xx = x + offset; xx < x + w; xx += 10) {
          ctx.beginPath();
          ctx.moveTo(xx, yy);
          ctx.lineTo(xx, yy + courseH);
          ctx.stroke();
        }
      }
    } else if (building.texture === "wood") {
      var plankH = 6;
      for (var py = y; py < y + h; py += plankH) {
        ctx.beginPath();
        ctx.moveTo(x, py);
        ctx.lineTo(x + w, py);
        ctx.stroke();
      }
    } else {
      // stone -- sparser, irregular blocking
      var sRow = 0;
      for (var sy = y; sy < y + h; sy += 7, sRow++) {
        ctx.beginPath();
        ctx.moveTo(x, sy);
        ctx.lineTo(x + w, sy);
        ctx.stroke();
        var sOffset = (sRow % 2) * 6 + 3;
        for (var sx = x + sOffset; sx < x + w; sx += 12) {
          ctx.beginPath();
          ctx.moveTo(sx, sy);
          ctx.lineTo(sx, Math.min(y + h, sy + 7));
          ctx.stroke();
        }
      }
    }
    ctx.restore();
  }

  function drawCity(ctx, city) {
    var b = city.bounds;

    // Streets fill the whole area first -- blocks/buildings are drawn on
    // top, so a seam grid drawn now only ends up visible in the actual
    // street corridors once blocks cover the rest.
    ctx.fillStyle = "#20242b";
    ctx.fillRect(b[0], b[1], b[2] - b[0], b[3] - b[1]);

    ctx.strokeStyle = "rgba(255,255,255,0.04)";
    ctx.lineWidth = 1;
    for (var sx = b[0]; sx <= b[2]; sx += 20) {
      ctx.beginPath();
      ctx.moveTo(sx, b[1]);
      ctx.lineTo(sx, b[3]);
      ctx.stroke();
    }
    for (var sy = b[1]; sy <= b[3]; sy += 20) {
      ctx.beginPath();
      ctx.moveTo(b[0], sy);
      ctx.lineTo(b[2], sy);
      ctx.stroke();
    }

    city.blocks.forEach(function (block) {
      ctx.fillStyle = "#171b21";
      ctx.fillRect(block.x, block.y, block.w, block.h);
    });

    if (city.park) {
      ctx.fillStyle = "#1f3b2c";
      ctx.fillRect(city.park.x, city.park.y, city.park.w, city.park.h);
      (city.grassPatches || []).forEach(function (patch) {
        ctx.beginPath();
        ctx.arc(patch.x, patch.y, patch.r, 0, Math.PI * 2);
        ctx.fillStyle = patch.lighter ? "rgba(60,110,70,0.4)" : "rgba(15,45,30,0.4)";
        ctx.fill();
      });
      city.trees.forEach(function (tree) {
        ctx.beginPath();
        ctx.arc(tree.x + 1, tree.y + 1, tree.r, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(0,0,0,0.25)";
        ctx.fill();
        ctx.beginPath();
        ctx.arc(tree.x, tree.y, tree.r, 0, Math.PI * 2);
        ctx.fillStyle = "#2f6b43";
        ctx.fill();
        ctx.beginPath();
        ctx.arc(tree.x - tree.r * 0.3, tree.y - tree.r * 0.3, tree.r * 0.5, 0, Math.PI * 2);
        ctx.fillStyle = "#3f8a58";
        ctx.fill();
      });
    }

    city.buildings.forEach(function (building) {
      ctx.fillStyle = building.color;
      ctx.fillRect(building.x, building.y, building.w, building.h);

      drawWallTexture(ctx, building);

      // Roofline: a darker band along the top edge suggests a roof
      // overhang rather than a flat-topped box.
      ctx.fillStyle = building.roofColor || "rgba(0,0,0,0.3)";
      ctx.fillRect(building.x - 1, building.y - 1, building.w + 2, 4);

      ctx.fillStyle = "rgba(255, 214, 120, 0.35)";
      var padX = building.w / (building.windowCols + 1);
      var padY = building.h / (building.windowRows + 1);
      for (var wr = 1; wr <= building.windowRows; wr++) {
        for (var wc = 1; wc <= building.windowCols; wc++) {
          ctx.fillRect(building.x + wc * padX - 1.5, building.y + wr * padY - 1.5, 3, 3);
        }
      }

      if (building.door) {
        ctx.fillStyle = "#c9a86a";
        ctx.fillRect(building.door.x, building.door.y, building.door.w, 3);
      }
    });
  }

  // ---------- rendering ----------

  function wrapText(ctx, text, maxWidth) {
    var words = text.split(" ");
    var lines = [];
    var current = "";
    words.forEach(function (word) {
      var test = current ? current + " " + word : word;
      if (ctx.measureText(test).width > maxWidth && current) {
        lines.push(current);
        current = word;
      } else {
        current = test;
      }
    });
    if (current) lines.push(current);
    return lines;
  }

  // `topics` is an array of strings -- one conversational line for a
  // conversation exchange, or all 4 "Prefix: answer" pairs at once for a
  // spawn/selection bubble.
  function drawSpeechBubble(ctx, c, topics) {
    if (!topics || !topics.length) return;
    ctx.font = "11px sans-serif";
    var maxWidth = 180;
    var lines = [];
    topics.forEach(function (t) {
      lines = lines.concat(wrapText(ctx, t, maxWidth - 16));
    });
    if (!lines.length) return;

    var lineHeight = 14;
    var boxWidth = Math.min(maxWidth, Math.max.apply(null, lines.map(function (l) { return ctx.measureText(l).width; })) + 16);
    var boxHeight = lines.length * lineHeight + 12;
    var boxX = c.x - boxWidth / 2;
    var boxY = c.y - (c.spriteHeight || 20) - 8 - boxHeight;

    ctx.save();
    ctx.fillStyle = "#ffffff";
    ctx.strokeStyle = "rgba(0,0,0,0.2)";
    ctx.lineWidth = 1;
    var radius = 6;
    ctx.beginPath();
    ctx.moveTo(boxX + radius, boxY);
    ctx.arcTo(boxX + boxWidth, boxY, boxX + boxWidth, boxY + boxHeight, radius);
    ctx.arcTo(boxX + boxWidth, boxY + boxHeight, boxX, boxY + boxHeight, radius);
    ctx.arcTo(boxX, boxY + boxHeight, boxX, boxY, radius);
    ctx.arcTo(boxX, boxY, boxX + boxWidth, boxY, radius);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    // pointer triangle down to the character
    ctx.beginPath();
    ctx.moveTo(c.x - 6, boxY + boxHeight);
    ctx.lineTo(c.x + 6, boxY + boxHeight);
    ctx.lineTo(c.x, boxY + boxHeight + 8);
    ctx.closePath();
    ctx.fill();

    ctx.fillStyle = "#12161a";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    lines.forEach(function (line, i) {
      ctx.fillText(line, c.x, boxY + 8 + i * lineHeight + lineHeight / 2);
    });
    ctx.restore();
  }

  // ---------- top-down person sprite ----------
  //
  // Chibi-proportioned top-down figure (per the "condensed/abstracted
  // proportions" and "head is a third to half of the total sprite"
  // guidance from slynyrd's top-down character sprite reference):
  // a shirt-colored torso capsule with two arm/hand bumps, topped by an
  // oversized head with tiny eyes that look in the direction the
  // character is currently facing -- not a flat colored disc with
  // initials on it. Outfit color, head type, body type, and hand type
  // are all real per-character picks (see validators.py's
  // HEAD_TYPE_OPTIONS/BODY_TYPE_OPTIONS/HAND_TYPE_OPTIONS), looked up by
  // id via headTypeById/bodyTypeById/handTypeById -- not randomized here.
  var PERSON_HEAD_R = 7;

  function drawPerson(ctx, c, isSelected) {
    var headType = headTypeById[c.character.head_type_id] || DEFAULT_HEAD_TYPE;
    var bodyType = bodyTypeById[c.character.body_type_id] || DEFAULT_BODY_TYPE;
    var handType = handTypeById[c.character.hand_type_id];

    var bodyW = bodyType.width, bodyH = bodyType.height;
    var bodyTop = c.y - bodyH;
    var headCenterY = bodyTop - PERSON_HEAD_R + 3;

    // Cache the total sprite height (feet to head-top) on the character
    // itself so drawSpeechBubble can keep bubbles clear of the head
    // regardless of which body type this character has.
    c.spriteHeight = bodyH + PERSON_HEAD_R * 2 - 3;

    var handColor =
      !handType || handType.id === "bare"
        ? headType.skin
        : handType.id === "gloves_dark"
          ? "#2b2420"
          : shadeColor(c.color, -0.25);

    ctx.save();

    // Ground shadow -- the only thing that visually "roots" a top-down
    // sprite to a specific tile.
    ctx.beginPath();
    ctx.ellipse(c.x, c.y + 1, bodyW * 0.55, bodyW * 0.22, 0, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(0,0,0,0.35)";
    ctx.fill();

    if (isSelected) {
      ctx.beginPath();
      ctx.ellipse(c.x, c.y + 1, bodyW * 0.9, bodyW * 0.5, 0, 0, Math.PI * 2);
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    // Hands/arms peeking out from behind the torso.
    ctx.fillStyle = handColor;
    ctx.beginPath();
    ctx.arc(c.x - bodyW / 2, bodyTop + bodyH * 0.55, 2.6, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(c.x + bodyW / 2, bodyTop + bodyH * 0.55, 2.6, 0, Math.PI * 2);
    ctx.fill();

    // Torso.
    ctx.fillStyle = c.color;
    var r = 4;
    var bx = c.x - bodyW / 2, by = bodyTop, bw = bodyW, bh = bodyH;
    ctx.beginPath();
    ctx.moveTo(bx + r, by);
    ctx.arcTo(bx + bw, by, bx + bw, by + bh, r);
    ctx.arcTo(bx + bw, by + bh, bx, by + bh, r);
    ctx.arcTo(bx, by + bh, bx, by, r);
    ctx.arcTo(bx, by, bx + bw, by, r);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,0.35)";
    ctx.lineWidth = 1;
    ctx.stroke();

    // Head + hair, shaped per headType.shape -- "round" is a plain
    // circle, "square" a rounded-rect (blockier silhouette), "oval" a
    // taller ellipse, each with a matching hair/cap band across the top.
    ctx.fillStyle = headType.skin;
    ctx.strokeStyle = "rgba(0,0,0,0.3)";
    ctx.lineWidth = 1;
    if (headType.shape === "square") {
      var hr = PERSON_HEAD_R * 0.95;
      var hx = c.x - hr, hy = headCenterY - hr, hw = hr * 2, hh = hr * 2, hRadius = 2.5;
      ctx.beginPath();
      ctx.moveTo(hx + hRadius, hy);
      ctx.arcTo(hx + hw, hy, hx + hw, hy + hh, hRadius);
      ctx.arcTo(hx + hw, hy + hh, hx, hy + hh, hRadius);
      ctx.arcTo(hx, hy + hh, hx, hy, hRadius);
      ctx.arcTo(hx, hy, hx + hw, hy, hRadius);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = headType.hair;
      ctx.fillRect(hx, hy, hw, hh * 0.4);
    } else if (headType.shape === "oval") {
      var rx = PERSON_HEAD_R * 0.85, ry = PERSON_HEAD_R * 1.15;
      ctx.beginPath();
      ctx.ellipse(c.x, headCenterY, rx, ry, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      ctx.beginPath();
      ctx.ellipse(c.x, headCenterY, rx, ry, 0, Math.PI, Math.PI * 2);
      ctx.fillStyle = headType.hair;
      ctx.fill();
    } else {
      ctx.beginPath();
      ctx.arc(c.x, headCenterY, PERSON_HEAD_R, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(c.x, headCenterY, PERSON_HEAD_R, Math.PI, Math.PI * 2);
      ctx.fillStyle = headType.hair;
      ctx.fill();
    }

    // Eyes -- offset toward c.facingX/facingY so the character visibly
    // looks in the direction they're walking (or at whoever they're
    // talking to, see draw()'s conversation-facing logic).
    var fx = c.facingX || 0, fy = c.facingY || 1;
    var eyeOffsetX = fx * 2.2, eyeOffsetY = fy * 2.2 + 1;
    ctx.fillStyle = "#1a1410";
    ctx.beginPath();
    ctx.arc(c.x - 2.2 + eyeOffsetX, headCenterY + eyeOffsetY, 1.1, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(c.x + 2.2 + eyeOffsetX, headCenterY + eyeOffsetY, 1.1, 0, Math.PI * 2);
    ctx.fill();

    ctx.restore();

    // Tiny initials tag under the feet -- quick identification without
    // needing to click every character.
    ctx.save();
    ctx.font = "bold 8px sans-serif";
    ctx.textAlign = "center";
    var label = initials(c.character);
    var tagWidth = ctx.measureText(label).width + 6;
    ctx.fillStyle = "rgba(13,17,23,0.75)";
    ctx.fillRect(c.x - tagWidth / 2, c.y + 5, tagWidth, 10);
    ctx.fillStyle = "#e8ecf1";
    ctx.textBaseline = "middle";
    ctx.fillText(label, c.x, c.y + 10);
    ctx.restore();
  }

  function drawConversationLink(ctx, a, b) {
    ctx.save();
    ctx.strokeStyle = "rgba(255,255,255,0.35)";
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    ctx.restore();
  }

  function draw(ctx, canvas) {
    var now = performance.now();
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#12161a";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    drawCity(ctx, CITY);

    ctx.strokeStyle = "rgba(74,222,128,0.3)";
    ctx.lineWidth = 2;
    ctx.strokeRect(BOUNDS[0], BOUNDS[1], BOUNDS[2] - BOUNDS[0], BOUNDS[3] - BOUNDS[1]);

    updateConversations(now);

    // Characters mid-conversation hold their position (stop moving) for
    // the duration -- reads as "stopped to chat" rather than drifting
    // apart mid-sentence -- but still turn to face whoever they're
    // talking to. Everyone else either wanders idly or walks toward the
    // nearest free neighbor they've noticed.
    Object.keys(characters).forEach(function (id) {
      var c = characters[id];
      if (busyIds[id]) {
        var convo = conversations[busyIds[id]];
        var partnerId = convo && (String(convo.aId) === String(id) ? convo.bId : convo.aId);
        var partner = partnerId && characters[partnerId];
        if (partner) {
          var pdx = partner.x - c.x, pdy = partner.y - c.y;
          var pd = Math.hypot(pdx, pdy) || 1;
          c.facingX = pdx / pd;
          c.facingY = pdy / pd;
        }
      } else {
        stepMovement(id, c, now);
      }
    });

    // Dashed link drawn first so circles/bubbles layer on top of it.
    Object.keys(conversations).forEach(function (key) {
      var convo = conversations[key];
      var a = characters[convo.aId], b = characters[convo.bId];
      if (a && b) drawConversationLink(ctx, a, b);
    });

    Object.keys(characters).forEach(function (id) {
      var c = characters[id];
      var isSelected = String(id) === String(selectedId);

      drawPerson(ctx, c, isSelected);

      var conversationLine = currentConversationLine(id, now);
      if (conversationLine) {
        drawSpeechBubble(ctx, c, [conversationLine]);
      } else if (isSelected || (c.bubbleUntil && now < c.bubbleUntil)) {
        drawSpeechBubble(ctx, c, (c.character.icebreakers || []).map(function (ib) { return ib.text; }));
      }
    });

    requestAnimationFrame(function () {
      draw(ctx, canvas);
    });
  }

  function findCharacterAt(x, y) {
    var found = null;
    var bestDist = 16;
    Object.keys(characters).forEach(function (id) {
      var c = characters[id];
      var dist = Math.sqrt(Math.pow(c.x - x, 2) + Math.pow(c.y - y, 2));
      if (dist < bestDist) {
        found = id;
        bestDist = dist;
      }
    });
    return found;
  }

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  function showDetail(id) {
    var detailEl = document.getElementById("character-detail");
    if (!id || !characters[id]) {
      detailEl.textContent = "Click a character to see details.";
      detailEl.classList.remove("has-selection");
      return;
    }
    var c = characters[id].character;
    detailEl.classList.add("has-selection");
    var html =
      "<strong>" + escapeHtml(c.full_name) + "</strong><br>" +
      "<span class='muted'>Appearance: " + escapeHtml(appearanceLabel[c.appearance_id] || c.appearance_id) + "</span><br>";
    (c.icebreakers || []).forEach(function (ib) {
      html += "<span class='muted'>" + escapeHtml(ib.text) + "</span><br>";
    });
    detailEl.innerHTML = html;
  }

  function setupClickHandler(canvas) {
    canvas.addEventListener("click", function (event) {
      var rect = canvas.getBoundingClientRect();
      var scaleX = canvas.width / rect.width;
      var scaleY = canvas.height / rect.height;
      var x = (event.clientX - rect.left) * scaleX;
      var y = (event.clientY - rect.top) * scaleY;
      selectedId = findCharacterAt(x, y);
      showDetail(selectedId);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var canvas = document.getElementById("town-canvas");
    if (!canvas) return;
    // Built before any characters are placed -- addCharacter needs the
    // street layout already in hand to snap spawn positions onto it.
    CITY = buildCity(BOUNDS);
    setupInitialWorld();
    setupClickHandler(canvas);
    draw(canvas.getContext("2d"), canvas);
    connectSocket();
  });
})();
