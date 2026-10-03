/* Personal AI assistant chat ("Hera").
 *
 * Posts { message, history } to /api/assistant/chat/stream first (Server-
 * Sent Events over a plain fetch()+ReadableStream -- EventSource can't send
 * a body or a custom CSRF header, so it's not an option here), which shows
 * live progress ("Searching the site", "Looking up a quote", ...) while the
 * turn runs. Falls back to the older blocking /api/assistant/chat (plain
 * JSON) transparently whenever the stream can't be used AT ALL, i.e. it
 * never produced a single event: no fetch/ReadableStream/TextDecoder
 * support, a network error, an unexpected non-2xx status, or a connection
 * that dropped before anything came back. A 400 (bad input), 503
 * (offline) or 429 (busy) response from the stream endpoint is a real,
 * meaningful answer, though -- those are handled in place, never treated
 * as a reason to fall back.
 *
 * A stream that DID produce at least one event (even just a "Searching
 * the site" progress note) and then dropped before a "final" is a
 * different case, deliberately NOT retried automatically: the server may
 * already have completed real, billable work for that turn, so silently
 * re-asking via the JSON endpoint would risk running it twice. That case
 * surfaces as its own "connection dropped, ask again" message instead.
 * See readEventStream()'s comment for the full reasoning.
 *
 * Sends the CSRF token from the <meta> tag on both endpoints, keeps the
 * last few turns in memory as context, and renders assistant replies
 * through a SMALL, allowlist-only Markdown renderer -- HTML is escaped
 * first, then a fixed set of inline/block transforms is applied, so a
 * model response (or a streamed progress label) can never inject markup;
 * progress text is always set via textContent, never innerHTML.
 */
(function () {
  "use strict";

  var win = document.getElementById("asst-window");
  if (!win) return;

  var scroller = document.getElementById("asst-scroll");
  var thread = document.getElementById("asst-thread");
  var empty = document.getElementById("asst-empty");
  var form = document.getElementById("asst-form");
  var input = document.getElementById("asst-input");
  var sendBtn = document.getElementById("asst-send");
  var newBtn = document.getElementById("asst-new");

  var MAX_HISTORY = parseInt(win.getAttribute("data-max-history"), 10) || 4;
  var API = "/api/assistant/chat";
  var STREAM_API = win.getAttribute("data-stream-url") || "/api/assistant/chat/stream";
  var STREAM_SUPPORTED = !!(window.fetch && window.ReadableStream && window.TextDecoder);
  var history = []; // [{role, content}]
  var busy = false;

  // ---------- analytics ----------
  // Optional and failure-proof: window.siteAnalytics only exists when
  // analytics is configured (see analytics.js), and a throw in here must
  // never affect the chat. The question text is passed only so analytics.js
  // can reduce it to a category -- it is never sent anywhere.
  var turn = null; // the in-flight turn: { question, via, started, reported }

  function track(name, props) {
    try {
      if (window.siteAnalytics) window.siteAnalytics.trackAssistantEvent(name, props);
    } catch (e) { /* ignore */ }
  }

  function startTurn(message, via) {
    turn = { question: message, via: via, started: Date.now(), reported: false };
    track("assistant_question_submitted", {
      question: message,
      interface: via,
      turn_number: Math.floor(history.length / 2) + 1,
    });
  }

  // Reports a turn's outcome once, however many code paths reach it.
  // outcome: "answered" | "busy" | "error" | "dropped" | "unreachable".
  function endTurn(outcome, errorType, recoverable) {
    if (!turn || turn.reported) return;
    turn.reported = true;
    var ok = outcome === "answered";
    track("assistant_response_completed", {
      question: turn.question,
      interface: turn.via,
      success: ok,
      outcome: outcome,
      response_time_ms: Date.now() - turn.started,
    });
    if (!ok) {
      track("assistant_error", {
        error_type: errorType || outcome,
        component: "assistant_chat",
        recoverable: !!recoverable,
        interface: turn.via,
      });
    }
  }

  // ---------- helpers ----------

  function csrfToken() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }

  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function safeUrl(u) {
    return /^(https?:\/\/|mailto:|\/)/i.test(u) ? u : null;
  }

  // Minimal Markdown -> HTML. Input is already HTML-escaped. Order:
  // fenced code, then blocks (lists / paragraphs), then inline spans.
  function renderMarkdown(text) {
    var escaped = escapeHtml(text.replace(/\r\n/g, "\n").trim());

    // fenced code blocks ```...```
    var codeBlocks = [];
    escaped = escaped.replace(/```([\s\S]*?)```/g, function (_, code) {
      codeBlocks.push(code.replace(/^\n+|\n+$/g, ""));
      return " CB" + (codeBlocks.length - 1) + " ";
    });

    var blocks = escaped.split(/\n{2,}/);
    var html = blocks
      .map(function (block) {
        var cb = block.match(/^ CB(\d+) $/);
        if (cb) return "<pre><code>" + codeBlocks[+cb[1]] + "</code></pre>";

        var lines = block.split("\n");
        var isUl = lines.every(function (l) { return /^\s*[-*]\s+/.test(l); });
        var isOl = lines.every(function (l) { return /^\s*\d+[.)]\s+/.test(l); });
        if (isUl || isOl) {
          var items = lines
            .map(function (l) {
              return "<li>" + inline(l.replace(/^\s*(?:[-*]|\d+[.)])\s+/, "")) + "</li>";
            })
            .join("");
          return isUl ? "<ul>" + items + "</ul>" : "<ol>" + items + "</ol>";
        }
        return "<p>" + inline(block).replace(/\n/g, "<br>") + "</p>";
      })
      .join("");
    return html;

    function inline(s) {
      return s
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s).,!?]|$)/g, "$1<em>$2</em>")
        .replace(/(^|[\s(])_([^_\s][^_]*?)_(?=[\s).,!?]|$)/g, "$1<em>$2</em>")
        .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (m, label, url) {
          var safe = safeUrl(url);
          return safe
            ? '<a href="' + safe + '" target="_blank" rel="noopener">' + label + "</a>"
            : label;
        });
    }
  }

  function nearBottom() {
    return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
  }
  function scrollDown(force) {
    if (force || nearBottom()) scroller.scrollTop = scroller.scrollHeight;
  }

  function el(tag, className) {
    var e = document.createElement(tag);
    if (className) e.className = className;
    return e;
  }

  // ---------- rendering ----------

  // Each user message cycles through a fixed 5-color cover (asst-cover-0..4
  // in custom.css) -- a different color per message sent, not random, so
  // it stays a closed, on-theme set. Hera's own cover is always the same
  // gradient, set directly in CSS on .asst-msg-bot, no class needed.
  var COVER_COUNT = 5;
  var userMsgSeq = 0;

  function addUser(text) {
    var row = el("div", "asst-msg asst-msg-user");
    var bubble = el("div", "asst-bubble asst-cover-" + (userMsgSeq % COVER_COUNT));
    userMsgSeq += 1;
    bubble.textContent = text;
    row.appendChild(bubble);
    thread.appendChild(row);
    scrollDown(true);
  }

  function addAssistantShell() {
    var row = el("div", "asst-msg asst-msg-bot");
    var av = el("span", "asst-msg-avatar");
    av.textContent = "H";
    av.setAttribute("aria-hidden", "true");
    var body = el("div", "asst-msg-body");
    row.appendChild(av);
    row.appendChild(body);
    thread.appendChild(row);
    return body;
  }

  // Slow turns are real, not a bug to hide: a question that makes Hera chain
  // a couple of tool calls can take several seconds of actual Groq round
  // trips, one per decision. The three-dot animation alone reads as "did
  // this hang?" past a few seconds, so a status line escalates in place
  // next to it -- cleared the moment a reply lands, so a fast turn never
  // shows either message.
  var THINKING_AFTER_MS = 2500;
  var STILL_THINKING_AFTER_MS = 7000;

  // Returns { stop, setNote }. `setNote` is how a streamed turn's real
  // progress events ("Searching the site", "Looking up a quote", ...)
  // override the generic escalating placeholders below -- the first real
  // progress line cancels those timers outright (there's no point telling
  // someone "Hera is thinking..." once we already know, and can say,
  // exactly what she's doing), and every subsequent call just swaps the
  // text in place. Always textContent, never innerHTML: this text either
  // comes from a fixed local string or a tool-name lookup table, but
  // nothing here should ever become a habit that lets server text into
  // innerHTML later.
  function typingInto(body) {
    var wrap = el("div", "asst-typing-wrap");
    var dots = el("div", "asst-typing");
    dots.innerHTML = "<span></span><span></span><span></span>";
    var note = el("span", "asst-typing-note");
    wrap.appendChild(dots);
    wrap.appendChild(note);
    body.appendChild(wrap);
    scrollDown(true);

    var timers = [
      setTimeout(function () {
        note.textContent = "Hera is thinking…";
        scrollDown();
      }, THINKING_AFTER_MS),
      setTimeout(function () {
        note.textContent = "Still working on it — this one may be chaining a few tool calls.";
        scrollDown();
      }, STILL_THINKING_AFTER_MS),
    ];
    function stop() {
      timers.forEach(clearTimeout);
    }
    return {
      stop: stop,
      setNote: function (text) {
        stop();
        note.textContent = text;
        scrollDown();
      },
    };
  }

  var chartSeq = 0;
  var CHART_PALETTE = ["#3aa0ff", "#f87171", "#4ade80", "#fb923c"];

  // A plain multi-series line -- get_traffic_summary's request-volume chart.
  function seriesChartConfig(chart) {
    var datasets = (chart.series || []).map(function (s, i) {
      var color = CHART_PALETTE[i % CHART_PALETTE.length];
      return {
        label: s.label,
        data: s.data,
        borderColor: color,
        backgroundColor: color,
        pointRadius: 0,
        borderWidth: 1.75,
        tension: 0.2,
      };
    });
    return {
      type: "line",
      data: { labels: chart.labels || [], datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: { y: { beginAtZero: true } },
        plugins: { legend: { display: datasets.length > 1, position: "bottom" } },
      },
    };
  }

  // Actual price (solid) + projected price (dashed) + a shaded 95% band --
  // the same shape market_warehouse.js's buildProjectionChart() draws on
  // the warehouse's own dashboard, for get_projection's chart data.
  function projectionChartConfig(chart) {
    var nActual = chart.actual.length;
    var pad = chart.actual.map(function () { return null; });
    var actualSeries = chart.actual.concat(chart.projected.map(function () { return null; }));
    var projectedSeries = pad.concat(chart.projected);
    var lowerSeries = pad.concat(chart.lower);
    var upperSeries = pad.concat(chart.upper);
    if (nActual && chart.projected.length) {
      actualSeries[nActual - 1] = chart.actual[nActual - 1];
      projectedSeries[nActual - 1] = chart.actual[nActual - 1];
    }
    return {
      type: "line",
      data: {
        labels: chart.labels || [],
        datasets: [
          {
            label: chart.ticker + " (actual)",
            data: actualSeries,
            borderColor: "#3aa0ff",
            backgroundColor: "#3aa0ff",
            spanGaps: false,
            pointRadius: 0,
            borderWidth: 1.75,
          },
          {
            label: chart.ticker + " (projected — not a forecast)",
            data: projectedSeries,
            borderColor: "#f472b6",
            backgroundColor: "#f472b6",
            borderDash: [6, 4],
            spanGaps: false,
            pointRadius: 0,
            borderWidth: 1.75,
          },
          {
            label: "95% band (upper)",
            data: upperSeries,
            borderColor: "rgba(244,114,182,0.25)",
            backgroundColor: "rgba(244,114,182,0.12)",
            pointRadius: 0,
            borderWidth: 1,
            fill: "+1",
          },
          {
            label: "95% band (lower)",
            data: lowerSeries,
            borderColor: "rgba(244,114,182,0.25)",
            backgroundColor: "rgba(244,114,182,0.12)",
            pointRadius: 0,
            borderWidth: 1,
            fill: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: { x: { ticks: { maxTicksLimit: 8 } } },
        plugins: { legend: { position: "bottom" } },
      },
    };
  }

  function renderCharts(body, charts) {
    if (!window.Chart || !charts || !charts.length) return;
    charts.forEach(function (chart) {
      var wrap = el("div", "asst-chart-wrap");
      var title = el("span", "asst-chart-title");
      title.textContent = chart.title || "Chart";
      var canvas = document.createElement("canvas");
      canvas.id = "asst-chart-" + (chartSeq += 1);
      wrap.appendChild(title);
      wrap.appendChild(canvas);
      if (chart.kind === "projection" && chart.r_squared != null) {
        var note = el("p", "asst-chart-note");
        note.textContent = "Fit quality: r² = " + Number(chart.r_squared).toFixed(2) +
          " — a low value means the trend line barely fits the recent data.";
        wrap.appendChild(note);
      }
      body.appendChild(wrap);

      var config = chart.kind === "projection" ? projectionChartConfig(chart) : seriesChartConfig(chart);
      new Chart(canvas.getContext("2d"), config);
    });
  }

  function fillReply(body, text, sources, charts) {
    // .asst-reveal-panel is created fresh, right here, instead of putting
    // the cover directly on the row: the row exists (showing typing dots)
    // well before there's a real reply to reveal, and a cover there would
    // hide that "thinking" status under an opaque panel the whole time.
    // Creating this element only now means its cover animation, which
    // auto-plays on creation like the user's bubble does, always starts
    // at the moment there's actually something to uncover.
    var panel = el("div", "asst-reveal-panel");
    panel.innerHTML = renderMarkdown(text);
    if (sources && sources.length) {
      var wrap = el("div", "asst-sources");
      var label = el("span", "asst-sources-label");
      label.textContent = "Sources";
      wrap.appendChild(label);
      sources.forEach(function (s) {
        var node;
        if (s.url) {
          node = el("a", "asst-source");
          node.href = s.url;
        } else {
          node = el("span", "asst-source");
        }
        node.textContent = s.label || s.title;
        wrap.appendChild(node);
      });
      panel.appendChild(wrap);
    }
    renderCharts(panel, charts);
    body.innerHTML = "";
    body.appendChild(panel);
    scrollDown();
  }

  function setBusy(state) {
    busy = state;
    sendBtn.disabled = state;
    input.disabled = state;
  }

  function autogrow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  }

  function resetChat() {
    thread.innerHTML = "";
    history = [];
    userMsgSeq = 0;
    if (empty) empty.hidden = false;
    newBtn.hidden = true;
    input.value = "";
    autogrow();
    input.focus();
  }

  // ---------- send ----------

  function postJson(url, message, turnHistory) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify({ message: message, history: turnHistory }),
    });
  }

  // Never throws: a body that isn't JSON at all (e.g. flask-limiter's own
  // plain-text/HTML 429 page, hit before this app's routes ever run)
  // resolves to null instead of rejecting the whole chain.
  function readJsonSafe(resp) {
    return resp.json().catch(function () {
      return null;
    });
  }

  // Renders a finished turn's reply -- success or a plain error string --
  // and, only on success, appends it to the in-memory history so the next
  // turn carries it as context. Shared by every path (stream final, stream
  // error, and the JSON fallback) so all three render identically.
  function applyResult(body, message, ok, reply, sources, charts) {
    fillReply(body, reply || "Something went wrong. Please try again.", ok ? sources : null, ok ? charts : null);
    endTurn(ok ? "answered" : "error", "server_error", true);
    if (ok) {
      history.push({ role: "user", content: message });
      history.push({ role: "assistant", content: reply });
      if (history.length > MAX_HISTORY * 2) history = history.slice(-MAX_HISTORY * 2);
    }
  }

  // Busy (429): show the wait, and hand the visitor's own text back to the
  // input box for a one-click retry -- this turn never joins history, since
  // nothing was actually answered.
  function applyBusy(body, message, text) {
    fillReply(body, text, null, null);
    endTurn("busy", "rate_limited", true);
    input.value = message;
    autogrow();
  }

  // A stream that had already started (at least one event arrived) died
  // before finishing. Unlike applyBusy, this is deliberately NOT retried
  // automatically -- see readEventStream's comment for why a mid-stream
  // drop must never silently re-run the turn. Same one-click-retry
  // treatment as busy: hand the text back, don't touch history.
  function applyDropped(body, message) {
    endTurn("dropped", "stream_dropped", true);
    fillReply(
      body,
      "The connection dropped before Hera finished answering. Ask again if you'd like to retry.",
      null,
      null
    );
    input.value = message;
    autogrow();
  }

  function defaultBusyText(retryAfter) {
    return "Hera's getting a lot of questions right now. Try again in about " + retryAfter + " seconds.";
  }

  // Handles a plain (non-streamed) JSON response from either endpoint --
  // the stream endpoint answers exactly this shape for its eager 400/503,
  // and flask-limiter's own 429 (hit before either route body runs) has no
  // JSON body at all, hence the readJsonSafe/null handling throughout.
  function handleJsonResponse(body, message, status, data) {
    if (status === 429) {
      var retryAfter = (data && data.retry_after) || 20;
      applyBusy(body, message, (data && data.reply) || defaultBusyText(retryAfter));
      return;
    }
    var ok = status === 200 && !!data && !data.error;
    applyResult(body, message, ok, data && data.reply, data && data.sources, data && data.charts);
  }

  // The transparent fallback: a plain blocking call to the JSON endpoint,
  // used whenever the stream can't be used or didn't finish cleanly.
  function sendJson(message, turnHistory, body, typing) {
    return postJson(API, message, turnHistory)
      .then(function (resp) {
        return readJsonSafe(resp).then(function (data) {
          return { status: resp.status, data: data };
        });
      })
      .then(function (r) {
        typing.stop();
        handleJsonResponse(body, message, r.status, r.data);
      })
      .catch(function () {
        typing.stop();
        endTurn("unreachable", "unreachable", true);
        fillReply(body, "The assistant is unreachable right now. Please try again later.", null, null);
      });
  }

  // Reads the stream endpoint's SSE body frame by frame ("data: {...}\n\n"),
  // dispatching each parsed event as it arrives. Resolves once a "final",
  // "busy", or "error" event has been fully handled.
  //
  // If the stream ends without ever producing one of those, the promise
  // rejects -- but the error it throws carries `droppedMidStream`, which
  // `send()` uses to decide what happens next. That distinction matters:
  // a stream that never got anywhere (network refused, dropped before the
  // first byte) is indistinguishable from a plain endpoint failure, so
  // falling back to the JSON endpoint is the same recovery a flaky first
  // attempt would get anyway. But once at least one event has arrived --
  // even just a "progress" note -- the server has already started (and
  // may well have *finished*) real work: retrieval, a live Groq call,
  // possibly a paid tool action. Falling back from there would silently
  // re-run the whole turn, spending it a second time and writing a second
  // log row, for a failure that's purely "the browser stopped listening,"
  // not "the turn never happened." So a mid-stream drop is reported as its
  // own outcome instead, never retried automatically.
  function readEventStream(reader, body, message, typing) {
    var decoder = new TextDecoder();
    var buf = "";
    var settled = false;
    var anyEventSeen = false;

    function handle(ev) {
      anyEventSeen = true;
      if (ev.type === "progress" && typeof ev.message === "string") {
        typing.setNote(ev.message);
      } else if (ev.type === "final") {
        settled = true;
        typing.stop();
        applyResult(body, message, true, ev.reply, ev.sources, ev.charts);
      } else if (ev.type === "busy") {
        settled = true;
        typing.stop();
        applyBusy(body, message, ev.message || defaultBusyText(ev.retry_after || 20));
      } else if (ev.type === "error") {
        settled = true;
        typing.stop();
        applyResult(body, message, false, ev.message, null, null);
      }
      // Any other/unknown event type is ignored, forward-compatibly.
    }

    function pump() {
      return reader.read().then(function (step) {
        if (step.done) {
          if (!settled) {
            var err = new Error("assistant stream ended without a result");
            err.droppedMidStream = anyEventSeen;
            throw err;
          }
          return;
        }
        buf += decoder.decode(step.value, { stream: true });
        var frames = buf.split("\n\n");
        buf = frames.pop(); // keep the last, possibly incomplete frame
        for (var i = 0; i < frames.length && !settled; i++) {
          var line = frames[i].trim();
          if (line.indexOf("data:") !== 0) continue; // blank line / SSE comment
          try {
            handle(JSON.parse(line.slice(5).trim()));
          } catch (e) {
            // Malformed frame: skip it rather than aborting the whole turn.
          }
        }
        return settled ? undefined : pump();
      });
    }
    return pump();
  }

  // Tries the streamed endpoint first. A 400/429/503 here is a real,
  // complete answer (bad input / offline / busy) and is handled in place,
  // same as the JSON endpoint would. Anything else that isn't a clean
  // stream open (no fetch/ReadableStream support, a network error, or the
  // response body isn't a readable stream at all) rejects with a plain
  // Error, which `send()` treats as "never started" and falls back to the
  // plain JSON endpoint transparently. A stream that DID open and start
  // emitting events but then died mid-turn rejects too, but with
  // `droppedMidStream` set -- see readEventStream() -- and `send()`
  // handles that case differently.
  function sendStream(message, turnHistory, body, typing) {
    if (!STREAM_SUPPORTED) return Promise.reject(new Error("no streaming support"));

    return postJson(STREAM_API, message, turnHistory).then(function (resp) {
      if (resp.status === 400 || resp.status === 429 || resp.status === 503) {
        return readJsonSafe(resp).then(function (data) {
          typing.stop();
          handleJsonResponse(body, message, resp.status, data);
        });
      }
      if (!resp.ok || !resp.body || typeof resp.body.getReader !== "function") {
        throw new Error("assistant stream unavailable");
      }
      return readEventStream(resp.body.getReader(), body, message, typing);
    });
  }

  function send(message) {
    if (busy) return;
    var via = message ? "suggested_chip" : "typed";
    message = (message || input.value || "").trim();
    if (!message) return;
    startTurn(message, via);

    if (empty) empty.hidden = true;
    newBtn.hidden = false;

    addUser(message);
    input.value = "";
    autogrow();
    setBusy(true);

    var body = addAssistantShell();
    var typing = typingInto(body);
    var turnHistory = history.slice(-MAX_HISTORY * 2);

    sendStream(message, turnHistory, body, typing)
      .catch(function (err) {
        if (err && err.droppedMidStream) {
          typing.stop();
          applyDropped(body, message);
          return;
        }
        return sendJson(message, turnHistory, body, typing);
      })
      .finally(function () {
        setBusy(false);
        input.focus();
      });
  }

  // ---------- wiring ----------

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    send();
  });

  input.addEventListener("input", autogrow);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  newBtn.addEventListener("click", resetChat);

  Array.prototype.forEach.call(document.querySelectorAll(".asst-chip"), function (chip) {
    chip.addEventListener("click", function () {
      send(chip.textContent.trim());
    });
  });

  autogrow();
  input.focus();
})();
