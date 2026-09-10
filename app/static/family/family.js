/* /family — progressive enhancement. Every screen works without this file;
   it just makes the chat live and the grocery toggle instant. */
(function () {
  "use strict";

  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  /* ---------- tiny allowlist markdown (escape first) ---------- */
  function esc(s) {
    return s.replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function render(md) {
    var out = esc(md);
    out = out.replace(/```([\s\S]*?)```/g, function (_, b) {
      return "<pre><code>" + b.replace(/^\n/, "") + "</code></pre>";
    });
    out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
    out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|\/[^\s)]*)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');
    out = out.replace(/\n/g, "<br>");
    return out;
  }

  /* ---------- chat ---------- */
  var form = document.getElementById("chat-form");
  if (form) {
    var thread = document.getElementById("chat-thread");
    var input = document.getElementById("chat-input");
    var send = document.getElementById("chat-send");
    var member = thread.getAttribute("data-member");

    function scrollDown() { thread.scrollTop = thread.scrollHeight; }
    scrollDown();

    function bubble(role, html) {
      var wrap = document.createElement("div");
      wrap.className = "chat-msg chat-msg--" + role;
      wrap.innerHTML =
        (role === "assistant" ? '<span class="chat-h"></span>' : "") +
        '<div class="chat-bubble">' + html + "</div>";
      thread.appendChild(wrap);
      scrollDown();
      return wrap.querySelector(".chat-bubble");
    }

    function autogrow() {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 140) + "px";
    }
    input.addEventListener("input", autogrow);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var text = input.value.trim();
      if (!text) return;
      bubble("user", esc(text));
      input.value = ""; autogrow();
      input.disabled = send.disabled = true;
      var pending = bubble("assistant", '<span class="chat-dots"><i></i><i></i><i></i></span>');

      fetch("/family/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify({ member_id: member, message: text }),
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          pending.innerHTML = render(String(res.d && res.d.reply || "…"));
        })
        .catch(function () {
          pending.textContent = "I couldn't reach anything just now. Try again.";
        })
        .finally(function () {
          input.disabled = send.disabled = false;
          input.focus(); scrollDown();
        });
    });
  }

  /* ---------- calendar day popup ---------- */
  var calDialog = document.getElementById("cal-dialog");
  if (calDialog && typeof calDialog.showModal === "function") {
    var byDay = {};
    try { byDay = JSON.parse((document.getElementById("cal-data") || {}).textContent || "{}"); }
    catch (e) { byDay = {}; }

    var titleEl = document.getElementById("cal-dialog-title");
    var listEl = document.getElementById("cal-dialog-list");
    var emptyEl = document.getElementById("cal-dialog-empty");
    var dateInput = document.getElementById("cal-date");
    var addBox = calDialog.querySelector(".cal-add");
    var recurTpl = document.getElementById("cal-recur-tpl");
    var dayCells = [].slice.call(document.querySelectorAll("a.cal-day"));

    function headingFor(iso) {
      var d = new Date(iso + "T00:00:00");
      return d.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
    }

    function fill(iso) {
      var rows = byDay[iso] || [];
      titleEl.textContent = headingFor(iso);
      listEl.textContent = "";
      rows.forEach(function (ev) {
        var li = document.createElement("li");
        li.className = "cal-up__row";

        var when = document.createElement("span");
        when.className = "cal-up__date";
        when.textContent = ev.time;

        var chip = document.createElement("span");
        chip.className = "fam-chip";
        var dot = document.createElement("span");
        dot.className = "fam-dot fam-dot--m" + (ev.m === 1 ? 1 : 2);
        chip.appendChild(dot);
        chip.appendChild(document.createTextNode(" " + ev.title + " "));
        if (ev.recur && recurTpl) chip.appendChild(recurTpl.content.cloneNode(true));

        var del = document.createElement("form");
        del.method = "post";
        del.action = "/family/calendar/events/" + ev.id + "/delete";
        del.className = "cal-up__del";
        var t1 = document.createElement("input");
        t1.type = "hidden"; t1.name = "csrf_token"; t1.value = csrf;
        var t2 = document.createElement("input");
        t2.type = "hidden"; t2.name = "day"; t2.value = iso;
        var btn = document.createElement("button");
        btn.type = "submit"; btn.setAttribute("aria-label", "Delete event");
        btn.title = "Delete"; btn.textContent = "×";
        del.appendChild(t1); del.appendChild(t2); del.appendChild(btn);

        li.appendChild(when); li.appendChild(chip); li.appendChild(del);
        listEl.appendChild(li);
      });
      emptyEl.hidden = rows.length > 0;
      if (addBox) addBox.open = rows.length === 0;
      if (dateInput) dateInput.value = iso;
    }

    function openFor(iso, cell) {
      fill(iso);
      dayCells.forEach(function (c) { c.classList.remove("is-selected"); });
      if (cell) cell.classList.add("is-selected");
      if (calDialog.open) calDialog.close();
      calDialog.showModal();
      try {
        var u = new URL(cell ? cell.href : window.location.href);
        history.replaceState(null, "", u.pathname + u.search);
      } catch (e) {}
    }

    dayCells.forEach(function (cell) {
      cell.addEventListener("click", function (e) {
        var iso = cell.getAttribute("data-day");
        if (!iso) return;
        e.preventDefault();
        openFor(iso, cell);
      });
    });

    calDialog.addEventListener("click", function (e) {
      if (e.target === calDialog) { calDialog.close(); return; }   // backdrop
      if (e.target.closest("[data-close]")) { e.preventDefault(); calDialog.close(); }
    });

    // Landed on a ?day= URL: upgrade the server-rendered open panel to a modal.
    if (calDialog.hasAttribute("open")) {
      calDialog.removeAttribute("open");
      calDialog.showModal();
    }
  }
})();
