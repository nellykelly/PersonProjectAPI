// Network Sniffer: polls this app's own in-memory traffic log and
// renders it as a live table + stat tiles. See net_monitor.py for what
// is (and deliberately isn't) captured.
(function () {
  "use strict";

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  function renderRow(entry) {
    var time = (entry.ts || "").split("T")[1] || entry.ts;
    time = time ? time.split(".")[0] : "";
    var directionClass = entry.direction === "in" ? "pnl-positive" : "muted";
    return (
      "<tr>" +
      "<td class='mono'>" + escapeHtml(time) + "</td>" +
      "<td class='" + directionClass + "'>" + escapeHtml(entry.direction) + "</td>" +
      "<td>" + escapeHtml(entry.method) + "</td>" +
      "<td class='mono'>" + escapeHtml(entry.target) + "</td>" +
      "<td>" + escapeHtml(entry.status != null ? entry.status : "-") + "</td>" +
      "<td class='mono'>" + escapeHtml(entry.duration_ms != null ? entry.duration_ms + " ms" : "-") + "</td>" +
      "</tr>"
    );
  }

  function updateStats(stats) {
    var map = {
      total: stats.total,
      inbound: stats.inbound,
      outbound: stats.outbound,
      avg_duration_ms: stats.avg_duration_ms != null ? stats.avg_duration_ms + " ms" : "n/a",
    };
    Object.keys(map).forEach(function (key) {
      var el = document.querySelector("[data-stat='" + key + "']");
      if (el) el.textContent = map[key];
    });

    var hostsEl = document.getElementById("hosts-body");
    if (hostsEl) {
      var rows = Object.keys(stats.outbound_by_host || {}).map(function (host) {
        return "<tr><td class='mono'>" + escapeHtml(host) + "</td><td>" + stats.outbound_by_host[host] + "</td></tr>";
      });
      hostsEl.innerHTML = rows.length ? rows.join("") : "<tr><td colspan='2' class='muted'>No outbound calls logged yet</td></tr>";
    }
  }

  function poll() {
    fetch("/projects/network-sniffer/api/log")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var body = document.getElementById("log-body");
        if (body) {
          body.innerHTML = data.entries.length
            ? data.entries.map(renderRow).join("")
            : "<tr><td colspan='6' class='muted'>No traffic logged yet -- browse the site in another tab.</td></tr>";
        }
        updateStats(data.stats);
      })
      .catch(function () {});
  }

  document.addEventListener("DOMContentLoaded", function () {
    poll();
    setInterval(poll, 4000);
  });
})();
