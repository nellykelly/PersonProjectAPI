/* Top Interview 150 tracker.
 *
 * The board is per-account and lives on the server. Marks are shaped
 * { <problem-slug>: "yes" | "no" }; the initial set is rendered into
 * #lc-initial-state by the template, and every change is POSTed to
 * /leetcode-150/api/progress (CSRF token from the <meta> tag). The UI
 * updates optimistically and rolls back if the request fails.
 */
(function () {
  "use strict";

  var TIMER_SECONDS = 20 * 60;
  var LEGACY_KEY = "lc-top150-progress-v1"; // pre-accounts localStorage board
  var API = "/leetcode-150/api/progress";

  // ---------- initial state ----------

  function readInitialState() {
    var el = document.getElementById("lc-initial-state");
    if (!el) return {};
    try {
      var parsed = JSON.parse(el.textContent || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (e) {
      return {};
    }
  }

  var state = readInitialState();

  // ---------- server sync ----------

  function csrfToken() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }

  function postJSON(path, body) {
    return fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  var noteEl = document.getElementById("lc-sync-note");
  var noteTimer = null;

  function syncNote(message, kind) {
    if (!noteEl) return;
    noteEl.textContent = message;
    noteEl.className = "lc-sync-note muted lc-sync-" + (kind || "ok");
    if (noteTimer) clearTimeout(noteTimer);
    if (kind === "ok") {
      noteTimer = setTimeout(function () {
        noteEl.textContent = "";
        noteEl.className = "lc-sync-note muted";
      }, 2000);
    }
  }

  // ---------- rows / marking ----------

  var rows = Array.prototype.slice.call(document.querySelectorAll(".lc-row"));
  var totalCount = rows.length;
  var rowBySlug = {};
  rows.forEach(function (r) {
    rowBySlug[r.getAttribute("data-slug")] = r;
  });

  function applyRow(row) {
    var slug = row.getAttribute("data-slug");
    var mark = state[slug];
    var yes = row.querySelector(".lc-yes");
    var no = row.querySelector(".lc-no");
    if (yes) yes.checked = mark === "yes";
    if (no) no.checked = mark === "no";
    row.classList.toggle("lc-row-yes", mark === "yes");
    row.classList.toggle("lc-row-no", mark === "no");
  }

  function setMark(slug, mark, row) {
    var previous = state[slug] || null;
    if (mark === null) delete state[slug];
    else state[slug] = mark;
    applyRow(row); // re-derives BOTH checkboxes from state -> mutual exclusion
    updateProgress();
    syncNote("Saving…", "pending");
    postJSON(API, { slug: slug, mark: mark })
      .then(function () {
        syncNote("Saved.", "ok");
      })
      .catch(function () {
        if (previous === null) delete state[slug];
        else state[slug] = previous;
        applyRow(row);
        updateProgress();
        syncNote("Couldn't save that change — check your connection and try again.", "err");
      });
  }

  rows.forEach(function (row) {
    var slug = row.getAttribute("data-slug");
    var yes = row.querySelector(".lc-yes");
    var no = row.querySelector(".lc-no");

    applyRow(row);

    if (yes) {
      yes.addEventListener("change", function () {
        setMark(slug, yes.checked ? "yes" : null, row);
      });
    }
    if (no) {
      no.addEventListener("change", function () {
        setMark(slug, no.checked ? "no" : null, row);
      });
    }
  });

  // ---------- progress panel ----------

  var elYes = document.getElementById("lc-count-yes");
  var elNo = document.getElementById("lc-count-no");
  var elUnattempted = document.getElementById("lc-count-unattempted");
  var elPct = document.getElementById("lc-pct");
  var elBar = document.getElementById("lc-progressbar");
  var elBarFill = document.getElementById("lc-progressbar-fill");

  function updateProgress() {
    var yes = 0;
    var no = 0;
    for (var i = 0; i < rows.length; i++) {
      var m = state[rows[i].getAttribute("data-slug")];
      if (m === "yes") yes++;
      else if (m === "no") no++;
    }
    var unattempted = totalCount - yes - no;
    var pct = totalCount ? Math.round((yes / totalCount) * 100) : 0;

    if (elYes) elYes.textContent = String(yes);
    if (elNo) elNo.textContent = String(no);
    if (elUnattempted) elUnattempted.textContent = String(unattempted);
    if (elPct) elPct.textContent = pct + "%";
    if (elBarFill) elBarFill.style.width = pct + "%";
    if (elBar) elBar.setAttribute("aria-valuenow", String(pct));
  }

  // ---------- 20-minute timer ----------

  var timerEl = document.getElementById("lc-timer");
  var display = document.getElementById("lc-timer-display");
  var startBtn = document.getElementById("lc-timer-start");
  var pauseBtn = document.getElementById("lc-timer-pause");
  var resetBtn = document.getElementById("lc-timer-reset");
  var contextEl = document.getElementById("lc-timer-context");

  var remaining = TIMER_SECONDS;
  var ticking = false;
  var intervalId = null;
  var currentTitle = null;

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function fmt(secs) {
    var s = Math.max(0, secs);
    var mm = Math.floor(s / 60);
    var ss = s % 60;
    return (mm < 10 ? "0" : "") + mm + ":" + (ss < 10 ? "0" : "") + ss;
  }

  function renderTimer() {
    if (display) display.textContent = fmt(remaining);
  }

  function setContext(html) {
    if (contextEl) contextEl.innerHTML = html;
  }

  function clearTick() {
    if (intervalId !== null) {
      clearInterval(intervalId);
      intervalId = null;
    }
  }

  function tick() {
    remaining -= 1;
    renderTimer();
    if (remaining <= 0) finishTimer();
  }

  function startTimer() {
    if (ticking) return;
    if (remaining <= 0) remaining = TIMER_SECONDS;
    ticking = true;
    clearTick();
    intervalId = setInterval(tick, 1000);
    timerEl.classList.add("lc-timer-running");
    timerEl.classList.remove("lc-timer-done");
    if (startBtn) startBtn.hidden = true;
    if (pauseBtn) {
      pauseBtn.hidden = false;
      pauseBtn.textContent = "Pause";
    }
    renderTimer();
  }

  function pauseTimer() {
    if (!ticking) {
      // button is acting as "Resume"
      startTimer();
      return;
    }
    ticking = false;
    clearTick();
    timerEl.classList.remove("lc-timer-running");
    if (pauseBtn) pauseBtn.textContent = "Resume";
  }

  function resetTimer() {
    ticking = false;
    clearTick();
    remaining = TIMER_SECONDS;
    renderTimer();
    timerEl.classList.remove("lc-timer-running", "lc-timer-done");
    if (startBtn) {
      startBtn.hidden = false;
      startBtn.textContent = "Start 20:00 Timer";
    }
    if (pauseBtn) pauseBtn.hidden = true;
    setContext(
      currentTitle
        ? "Ready on <strong>" + escapeHtml(currentTitle) + "</strong>. Press Start when you begin."
        : 'No problem selected &mdash; press Start, or use the <span class="mono">20:00</span> button on any row.'
    );
  }

  function finishTimer() {
    ticking = false;
    clearTick();
    remaining = 0;
    renderTimer();
    timerEl.classList.remove("lc-timer-running");
    timerEl.classList.add("lc-timer-done");
    if (startBtn) {
      startBtn.hidden = false;
      startBtn.textContent = "Restart 20:00 Timer";
    }
    if (pauseBtn) pauseBtn.hidden = true;
    // Deliberately does NOT touch any Yes/No mark -- the call is always the user's.
    setContext(
      currentTitle
        ? "Time's up on <strong>" +
            escapeHtml(currentTitle) +
            "</strong>. Compare your approach with the solution, then mark it Yes or No."
        : "Time's up. Compare your approach with the solution, then mark the problem Yes or No."
    );
  }

  if (startBtn) startBtn.addEventListener("click", startTimer);
  if (pauseBtn) pauseBtn.addEventListener("click", pauseTimer);
  if (resetBtn) resetBtn.addEventListener("click", resetTimer);

  // Per-row "20:00" button: make this the timer's current problem and
  // start a fresh countdown. Does not open LeetCode or mark anything.
  document.querySelectorAll(".lc-focus").forEach(function (btn) {
    btn.addEventListener("click", function () {
      currentTitle = btn.getAttribute("data-title");
      remaining = TIMER_SECONDS;
      ticking = false;
      clearTick();
      timerEl.classList.remove("lc-timer-done");
      renderTimer();
      startTimer();
      setContext(
        "Working on <strong>" +
          escapeHtml(currentTitle) +
          '</strong> &mdash; <span class="mono">20:00</span> on the clock.'
      );
      if (timerEl && timerEl.scrollIntoView) {
        timerEl.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });
  });

  // ---------- reset all ----------

  var resetAllBtn = document.getElementById("lc-reset-all");
  var dialog = document.getElementById("lc-reset-dialog");
  var resetCancelBtn = document.getElementById("lc-reset-cancel");
  var resetConfirmBtn = document.getElementById("lc-reset-confirm");

  function doResetAll() {
    var backup = state;
    state = {};
    rows.forEach(applyRow);
    updateProgress();
    syncNote("Resetting…", "pending");
    postJSON(API + "/reset", {})
      .then(function () {
        syncNote("All progress reset.", "ok");
      })
      .catch(function () {
        state = backup;
        rows.forEach(applyRow);
        updateProgress();
        syncNote("Reset failed — nothing was changed.", "err");
      });
  }

  function closeDialog() {
    if (dialog && typeof dialog.close === "function" && dialog.open) dialog.close();
  }

  var canModal = dialog && typeof dialog.showModal === "function";

  if (resetAllBtn) {
    resetAllBtn.addEventListener("click", function () {
      if (canModal) {
        dialog.showModal();
      } else if (
        window.confirm(
          "Are you sure you want to reset all LeetCode progress? This will uncheck every problem."
        )
      ) {
        doResetAll();
      }
    });
  }

  // Explicit button handlers rather than <form method="dialog"> + the
  // close event: that path closes the dialog but does not reliably fire
  // "close" across browsers, which silently dropped the reset.
  if (resetCancelBtn) resetCancelBtn.addEventListener("click", closeDialog);
  if (resetConfirmBtn) {
    resetConfirmBtn.addEventListener("click", function () {
      doResetAll();
      closeDialog();
    });
  }

  // ---------- one-time import of a pre-accounts localStorage board ----------

  (function offerImport() {
    var banner = document.getElementById("lc-import-banner");
    if (!banner) return;

    var legacy = null;
    try {
      var raw = localStorage.getItem(LEGACY_KEY);
      if (raw) {
        var parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") legacy = parsed;
      }
    } catch (e) {
      legacy = null;
    }

    var slugs = legacy ? Object.keys(legacy) : [];
    if (slugs.length === 0) return;

    var dismissed = false;
    try {
      dismissed = sessionStorage.getItem(LEGACY_KEY + ":dismissed") === "1";
    } catch (e) {
      /* ignore */
    }
    if (dismissed) return;

    banner.hidden = false;

    var noBtn = document.getElementById("lc-import-no");
    var yesBtn = document.getElementById("lc-import-yes");

    if (noBtn) {
      noBtn.addEventListener("click", function () {
        banner.hidden = true;
        try {
          sessionStorage.setItem(LEGACY_KEY + ":dismissed", "1");
        } catch (e) {
          /* ignore */
        }
      });
    }

    if (yesBtn) {
      yesBtn.addEventListener("click", function () {
        syncNote("Importing…", "pending");
        postJSON(API + "/import", { marks: legacy })
          .then(function (data) {
            slugs.forEach(function (slug) {
              var m = legacy[slug];
              if (!state[slug] && (m === "yes" || m === "no")) {
                state[slug] = m;
                if (rowBySlug[slug]) applyRow(rowBySlug[slug]);
              }
            });
            updateProgress();
            try {
              localStorage.removeItem(LEGACY_KEY);
            } catch (e) {
              /* ignore */
            }
            banner.hidden = true;
            var added = data && typeof data.added === "number" ? data.added : slugs.length;
            syncNote(added + " problem" + (added === 1 ? "" : "s") + " imported.", "ok");
          })
          .catch(function () {
            syncNote("Import failed — try again.", "err");
          });
      });
    }
  })();

  // ---------- keep other open tabs roughly in sync ----------

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState !== "visible") return;
    fetch(API, { credentials: "same-origin" })
      .then(function (r) {
        return r.ok ? r.json() : null;
      })
      .then(function (data) {
        if (!data || !data.marks) return;
        state = data.marks;
        rows.forEach(applyRow);
        updateProgress();
      })
      .catch(function () {
        /* offline / transient -- leave the board as-is */
      });
  });

  // ---------- first paint ----------

  updateProgress();
  renderTimer();
})();
