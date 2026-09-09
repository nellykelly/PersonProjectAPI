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
})();
