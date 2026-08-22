// Network Sniffer: a genuinely live view. Loads the current snapshot once
// on page load, then opens a Server-Sent Events connection and appends
// each new traffic entry the instant net_monitor records it -- no
// polling delay. See net_monitor.py / sniffer/routes.py (api_stream) for
// what is (and deliberately isn't) captured, and how the SSE side works.
(function () {
  "use strict";

  var MAX_ROWS = 200;
  var rowCount = 0;
  var stats = { total: 0, inbound: 0, outbound: 0, durationSum: 0, durationCount: 0, outboundByHost: {} };

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  function methodPillClass(method) {
    return method === "GET" || method === "POST" ? "pill-method-" + method : "pill-method-other";
  }

  function statusPillClass(status) {
    if (status == null) return "pill-status-none";
    var digit = Math.floor(status / 100);
    return digit >= 2 && digit <= 5 ? "pill-status-" + digit : "pill-status-none";
  }

  function rowHtml(entry, isLive) {
    var time = (entry.ts || "").split("T")[1] || entry.ts;
    time = time ? time.split(".")[0] : "";
    var directionPill = entry.direction === "in"
      ? "<span class='pill pill-in'>IN</span>"
      : "<span class='pill pill-out'>OUT</span>";
    var methodPill = "<span class='pill " + methodPillClass(entry.method) + "'>" + escapeHtml(entry.method) + "</span>";
    var statusPill = "<span class='pill " + statusPillClass(entry.status) + "'>" + escapeHtml(entry.status != null ? entry.status : "-") + "</span>";

    return (
      "<tr" + (isLive ? " class='row-flash-in'" : "") + ">" +
      "<td class='time-cell mono'>" + escapeHtml(time) + "</td>" +
      "<td>" + directionPill + "</td>" +
      "<td>" + methodPill + "</td>" +
      "<td class='target-cell mono' title='" + escapeHtml(entry.target) + "'>" + escapeHtml(entry.target) + "</td>" +
      "<td>" + statusPill + "</td>" +
      "<td class='duration-cell mono'>" + escapeHtml(entry.duration_ms != null ? entry.duration_ms + " ms" : "-") + "</td>" +
      "</tr>"
    );
  }

  function renderStats() {
    var map = {
      total: stats.total,
      inbound: stats.inbound,
      outbound: stats.outbound,
      avg_duration_ms: stats.durationCount ? (stats.durationSum / stats.durationCount).toFixed(1) + " ms" : "n/a",
    };
    Object.keys(map).forEach(function (key) {
      var el = document.querySelector("[data-stat='" + key + "']");
      if (el) el.textContent = map[key];
    });

    var hostsEl = document.getElementById("hosts-body");
    if (hostsEl) {
      var hosts = Object.keys(stats.outboundByHost);
      if (!hosts.length) {
        hostsEl.innerHTML = "<tr><td colspan='2' class='muted'>No outbound calls logged yet</td></tr>";
      } else {
        var maxCount = Math.max.apply(null, hosts.map(function (h) { return stats.outboundByHost[h]; }));
        hostsEl.innerHTML = hosts
          .sort(function (a, b) { return stats.outboundByHost[b] - stats.outboundByHost[a]; })
          .map(function (host) {
            var count = stats.outboundByHost[host];
            var pct = maxCount ? Math.max(6, Math.round((count / maxCount) * 100)) : 0;
            return (
              "<tr><td class='mono'>" + escapeHtml(host) + "</td>" +
              "<td><div class='host-bar-cell'>" +
              "<div class='host-bar-track'><div class='host-bar-fill' style='width:" + pct + "%'></div></div>" +
              "<span class='host-bar-count'>" + count + "</span>" +
              "</div></td></tr>"
            );
          })
          .join("");
      }
    }
  }

  function hostOf(target) {
    // Mirrors net_monitor.py's _host_of(): real http(s) URLs group by
    // hostname; synthetic pseudo-URLs (e.g. "yfinance://AAPL/info" --
    // yfinance manages its own HTTP client, so there's no real URL to
    // log) group by scheme name instead of misreading the ticker as a host.
    if (target.indexOf("http://") === 0 || target.indexOf("https://") === 0) {
      var parts = target.split("/");
      return parts.length > 2 ? parts[2] : target;
    }
    var schemeIdx = target.indexOf("://");
    return schemeIdx !== -1 ? target.slice(0, schemeIdx) : target;
  }

  function absorb(entry) {
    stats.total += 1;
    if (entry.direction === "in") {
      stats.inbound += 1;
    } else {
      stats.outbound += 1;
      var host = hostOf(entry.target);
      stats.outboundByHost[host] = (stats.outboundByHost[host] || 0) + 1;
    }
    if (entry.duration_ms != null) {
      stats.durationSum += entry.duration_ms;
      stats.durationCount += 1;
    }
  }

  function prependRow(entry) {
    var body = document.getElementById("log-body");
    if (!body) return;
    if (rowCount === 0) body.innerHTML = "";
    body.insertAdjacentHTML("afterbegin", rowHtml(entry, true));
    rowCount += 1;
    while (body.rows && body.rows.length > MAX_ROWS) {
      body.deleteRow(body.rows.length - 1);
    }
  }

  function setLiveIndicator(state) {
    var el = document.getElementById("live-indicator");
    if (!el) return;
    el.textContent = state === "connected" ? "● LIVE" : "○ Reconnecting...";
    el.className = state === "connected" ? "badge badge-open" : "badge badge-closed";
  }

  function loadInitialSnapshot(onDone) {
    fetch("/projects/network-sniffer/api/log")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var body = document.getElementById("log-body");
        if (body) {
          body.innerHTML = data.entries.length
            ? data.entries.map(function (e) { return rowHtml(e, false); }).join("")
            : "<tr><td colspan='6' class='muted'>No traffic logged yet -- browse the site in another tab.</td></tr>";
          rowCount = data.entries.length;
        }
        stats.total = data.stats.total;
        stats.inbound = data.stats.inbound;
        stats.outbound = data.stats.outbound;
        stats.outboundByHost = data.stats.outbound_by_host || {};
        if (data.stats.avg_duration_ms != null) {
          // seed the running average so it doesn't reset to "n/a" on load
          stats.durationSum = data.stats.avg_duration_ms * data.stats.total;
          stats.durationCount = data.stats.total;
        }
        renderStats();
      })
      .catch(function () {})
      .then(onDone);
  }

  function connectStream() {
    if (!window.EventSource) return; // graceful no-op on very old browsers
    var source = new EventSource("/projects/network-sniffer/api/stream");

    source.onopen = function () {
      setLiveIndicator("connected");
    };
    source.onerror = function () {
      setLiveIndicator("reconnecting");
    };
    source.onmessage = function (event) {
      var entry = JSON.parse(event.data);
      absorb(entry);
      prependRow(entry);
      renderStats();
    };
  }

  document.addEventListener("DOMContentLoaded", function () {
    loadInitialSnapshot(connectStream);
  });
})();
