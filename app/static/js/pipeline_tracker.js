// Pipeline World's build-tracker table: one row per recently-submitted
// character, one status cell per stage, updated live over Socket.IO as
// real pipeline runs happen -- modeled on an actual CI/CD run history
// table, not a game-world animation. Also drives the live build-log
// panel, which prints the actual pseudo-commands each stage runs the
// instant it starts, and the pass/fail result once it finishes.
(function () {
  "use strict";

  var STAGE_ORDER = window.PIPELINE_STAGE_ORDER || ["sanitize", "security_scan", "test_uniqueness", "test_profanity", "build", "deploy", "verify"];
  var STAGE_INFO = window.PIPELINE_STAGE_INFO || {};
  var LAST_STAGE = STAGE_ORDER[STAGE_ORDER.length - 1];
  var MAX_LOG_LINES = 300;
  var myCharacterId = null;

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

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  // ---------- live build log ----------

  function appendLogLines(lines) {
    var log = document.getElementById("build-log");
    if (!log) return;
    var atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.insertAdjacentHTML("beforeend", lines.join("\n") + "\n");
    while (log.children.length > MAX_LOG_LINES) {
      log.removeChild(log.firstChild);
    }
    if (atBottom) log.scrollTop = log.scrollHeight;
  }

  function logStageUpdate(data) {
    var stageLabel = (STAGE_INFO[data.stage] && STAGE_INFO[data.stage].label) || data.stage;
    var name = escapeHtml(data.character.full_name);
    var lines = [];

    if (data.status === "start") {
      lines.push("<span class='log-stage-start'>&gt; " + name + ": " + escapeHtml(stageLabel) + "</span>");
      (data.commands || []).forEach(function (cmd) {
        lines.push("<span class='log-command'>" + escapeHtml(cmd) + "</span>");
      });
    } else if (data.status === "pass") {
      lines.push("<span class='log-pass'>  ✓ PASS" + (data.detail ? " -- " + escapeHtml(data.detail) : "") + "</span>");
    } else if (data.status === "fail") {
      lines.push("<span class='log-fail'>  ✗ FAIL -- " + escapeHtml(data.detail) + "</span>");
    }
    appendLogLines(lines);
  }

  // ---------- tracker table ----------

  function stageCellHtml(stage) {
    return (
      "<td class='stage-cell' data-stage='" + stage + "'>" +
      "<span class='stage-status stage-status-none'>-</span>" +
      "</td>"
    );
  }

  function getOrCreateRow(character) {
    var body = document.getElementById("tracker-body");
    if (!body) return null;
    var row = body.querySelector("tr[data-character-id='" + character.id + "']");
    if (row) return row;

    var emptyState = document.querySelector("#tracker-table + .empty-state, .empty-state");
    if (emptyState && emptyState.textContent.indexOf("No one has joined") !== -1) {
      emptyState.style.display = "none";
    }

    row = document.createElement("tr");
    row.dataset.characterId = character.id;
    row.className = "row-new";
    var cells = "<td>" + escapeHtml(character.full_name) + "</td>";
    STAGE_ORDER.forEach(function (stage) {
      cells += stageCellHtml(stage);
    });
    row.innerHTML = cells;
    body.insertBefore(row, body.firstChild);
    return row;
  }

  function updateStageCell(character, stage, status) {
    var row = getOrCreateRow(character);
    if (!row) return;
    var cell = row.querySelector("td[data-stage='" + stage + "'] .stage-status");
    if (!cell) return;
    cell.className = "stage-status stage-status-" + status;
    cell.textContent = status === "running" ? "..." : status === "pass" ? "PASS" : status === "fail" ? "FAIL" : "-";

    if (stage === LAST_STAGE && status === "pass") {
      row.classList.remove("row-failed");
      row.classList.add("row-live");
    } else if (status === "fail") {
      row.classList.add("row-failed");
    }
  }

  function showJoinMessage(html, kind) {
    var el = document.getElementById("join-message");
    el.innerHTML = html;
    el.className = kind === "error" ? "flash flash-error" : "flash flash-success";
  }

  function handlePipelineUpdate(data) {
    logStageUpdate(data);

    var stage = data.stage;
    var status = data.status === "start" ? "running" : data.status;
    updateStageCell(data.character, stage, status);

    if (data.character.id !== myCharacterId) return;

    if (stage === LAST_STAGE && data.status === "pass") {
      showJoinMessage(
        escapeHtml(data.character.full_name) +
          " is live! <a href='/projects/pipeline-world/town'>View Production Town &rarr;</a>",
        "success"
      );
    } else if (data.status === "fail") {
      showJoinMessage(escapeHtml(data.character.full_name) + " failed at " + stage + ": " + escapeHtml(data.detail), "error");
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
    socket.on("pipeline_update", handlePipelineUpdate);
  }

  // ---------- join form ----------

  function setupPicker(selector, hiddenInputId, datasetKey) {
    document.querySelectorAll(selector).forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll(selector).forEach(function (b) {
          b.classList.remove("is-selected");
        });
        btn.classList.add("is-selected");
        document.getElementById(hiddenInputId).value = btn.dataset[datasetKey];
      });
    });
  }

  var ICEBREAKER_QUESTION_IDS = window.ICEBREAKER_QUESTION_IDS || ["food", "movie", "hobby", "weekend"];

  // Each picker group is scoped to its own container -- all four groups
  // reuse the same ".appearance-swatch" button style, so scoping the
  // selector to "#<container> .appearance-swatch" keeps clicking a head
  // type from clearing the selected outfit color's is-selected state.
  var PICKER_GROUPS = [
    { container: "appearance-picker", hiddenInputId: "appearance_id", datasetKey: "appearanceId" },
    { container: "head-type-picker", hiddenInputId: "head_type_id", datasetKey: "headTypeId" },
    { container: "body-type-picker", hiddenInputId: "body_type_id", datasetKey: "bodyTypeId" },
    { container: "hand-type-picker", hiddenInputId: "hand_type_id", datasetKey: "handTypeId" },
  ];

  function setupJoinForm() {
    var form = document.getElementById("join-form");
    if (!form) return;

    PICKER_GROUPS.forEach(function (group) {
      setupPicker("#" + group.container + " .appearance-swatch", group.hiddenInputId, group.datasetKey);
    });

    function resetForm() {
      form.reset();
      document.getElementById("confirm_last_name_collision").value = "0";
      PICKER_GROUPS.forEach(function (group) {
        document.getElementById(group.hiddenInputId).value = "";
        document.querySelectorAll("#" + group.container + " .appearance-swatch").forEach(function (b) {
          b.classList.remove("is-selected");
        });
      });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      for (var g = 0; g < PICKER_GROUPS.length; g++) {
        if (!document.getElementById(PICKER_GROUPS[g].hiddenInputId).value) {
          showJoinMessage("Please make a pick for every customization option.", "error");
          return;
        }
      }
      for (var i = 0; i < ICEBREAKER_QUESTION_IDS.length; i++) {
        var field = document.getElementById("icebreaker_answer_" + ICEBREAKER_QUESTION_IDS[i]);
        if (field && !field.value.trim()) {
          showJoinMessage("Please answer every icebreaker question.", "error");
          return;
        }
      }

      // Not `form.action` -- an unset action attribute reports the
      // *current page URL*, not empty/falsy, so a `form.action ||
      // fallback` pattern silently posts to the wrong place.
      fetch("/projects/pipeline-world/join", { method: "POST", body: new FormData(form) })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (data.ok) {
            myCharacterId = data.character.id;
            showJoinMessage(escapeHtml(data.character.full_name) + " is joining -- watch the log and table below.", "success");
            getOrCreateRow(data.character);
            resetForm();
          } else if (data.needs_confirmation) {
            var el = document.getElementById("join-message");
            el.innerHTML = "";
            el.className = "flash flash-error";
            var p = document.createElement("p");
            p.textContent = data.message;
            var confirmBtn = document.createElement("button");
            confirmBtn.type = "button";
            confirmBtn.className = "button small";
            confirmBtn.textContent = "Yes, continue anyway";
            confirmBtn.addEventListener("click", function () {
              document.getElementById("confirm_last_name_collision").value = "1";
              form.requestSubmit();
            });
            el.appendChild(p);
            el.appendChild(confirmBtn);
          } else {
            showJoinMessage(escapeHtml(data.message), "error");
          }
        })
        .catch(function () {
          showJoinMessage("Something went wrong submitting your character.", "error");
        });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    connectSocket();
    setupJoinForm();
  });
})();
