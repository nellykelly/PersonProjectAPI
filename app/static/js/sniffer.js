// Site Traffic Analytics: polls the aggregate rollup every few seconds
// and re-renders stats/charts/tables. An aggregate board doesn't need a
// push per raw entry the way a live log did, so a plain polled JSON
// endpoint replaces what used to be an SSE stream -- see net_monitor.py /
// sniffer/routes.py for what is (and deliberately isn't) captured.
(function () {
  "use strict";

  var POLL_INTERVAL_MS = 5000;
  var ENDPOINT = "/projects/network-sniffer/api/analytics";

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  function setStat(key, value) {
    var el = document.querySelector("[data-stat='" + key + "']");
    if (el) el.textContent = value;
  }

  function renderStats(data) {
    setStat("total", data.total);
    setStat("inbound", data.inbound);
    setStat("outbound", data.outbound);
    setStat("p50", data.inbound_latency.p50 != null ? data.inbound_latency.p50 + " ms" : "n/a");
    setStat("p99", data.inbound_latency.p99 != null ? data.inbound_latency.p99 + " ms" : "n/a");

    var errRate = data.server_error_rate_pct != null ? data.server_error_rate_pct : 0;
    setStat("server_error_rate_pct", errRate + "%");
    var errTile = document.querySelector(".error-rate-tile");
    if (errTile) errTile.classList.toggle("has-errors", errRate > 0);
  }

  function renderBarTable(bodyId, rows, keyField, labelFn) {
    var body = document.getElementById(bodyId);
    if (!body) return;
    if (!rows.length) {
      body.innerHTML = "<tr><td colspan='2' class='muted'>Nothing logged yet</td></tr>";
      return;
    }
    var maxCount = Math.max.apply(null, rows.map(function (r) { return r.count; }));
    body.innerHTML = rows
      .map(function (r) {
        var pct = maxCount ? Math.max(6, Math.round((r.count / maxCount) * 100)) : 0;
        return (
          "<tr><td class='mono'>" + escapeHtml(labelFn(r)) + "</td>" +
          "<td><div class='host-bar-cell'>" +
          "<div class='host-bar-track'><div class='host-bar-fill' style='width:" + pct + "%'></div></div>" +
          "<span class='host-bar-count'>" + r.count + "</span>" +
          "</div></td></tr>"
        );
      })
      .join("");
  }

  function renderEndpoints(rows) {
    var body = document.getElementById("endpoints-body");
    if (!body) return;
    if (!rows.length) {
      body.innerHTML = "<tr><td colspan='3' class='muted'>Nothing logged yet</td></tr>";
      return;
    }
    body.innerHTML = rows
      .map(function (r) {
        return (
          "<tr><td class='mono'>" + escapeHtml(r.endpoint || "(unknown)") + "</td>" +
          "<td class='mono'>" + r.count + "</td>" +
          "<td class='mono'>" + (r.avg_duration_ms != null ? r.avg_duration_ms + " ms" : "-") + "</td></tr>"
        );
      })
      .join("");
  }

  function renderVolumeChart(buckets) {
    var chart = document.getElementById("volume-chart");
    var labels = document.getElementById("volume-chart-labels");
    if (!chart) return;
    if (!buckets.length) {
      chart.innerHTML = "<p class='muted' style='padding:1rem 0;'>Not enough traffic yet to bucket over time.</p>";
      if (labels) labels.innerHTML = "";
      return;
    }
    var maxCount = Math.max.apply(null, buckets.map(function (b) { return b.inbound + b.outbound; }), 1);
    chart.innerHTML = buckets
      .map(function (b) {
        var total = b.inbound + b.outbound;
        var totalPct = Math.round((total / maxCount) * 100);
        var inPct = total ? Math.round((b.inbound / total) * 100) : 0;
        var outPct = total ? 100 - inPct : 0;
        var title = b.inbound + " in, " + b.outbound + " out";
        return (
          "<div class='volume-bar-col' style='height:" + Math.max(totalPct, total ? 3 : 0) + "%' title='" + escapeHtml(title) + "'>" +
          (b.outbound ? "<div class='volume-bar-out' style='height:" + outPct + "%'></div>" : "") +
          (b.inbound ? "<div class='volume-bar-in' style='height:" + inPct + "%'></div>" : "") +
          "</div>"
        );
      })
      .join("");
    if (labels) {
      var first = buckets[0].start.split("T")[1] || buckets[0].start;
      var last = buckets[buckets.length - 1].start.split("T")[1] || buckets[buckets.length - 1].start;
      labels.innerHTML =
        "<span>" + escapeHtml(first.split(".")[0]) + "</span><span>" + escapeHtml(last.split(".")[0]) + "</span>";
    }
  }

  function setRefreshIndicator(state) {
    var el = document.getElementById("refresh-indicator");
    if (!el) return;
    if (state === "ok") {
      el.textContent = "Updated " + new Date().toLocaleTimeString();
      el.className = "badge badge-open";
    } else {
      el.textContent = "Could not refresh";
      el.className = "badge badge-closed";
    }
  }

  function refresh() {
    fetch(ENDPOINT)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderStats(data);
        renderVolumeChart(data.volume_buckets);
        renderEndpoints(data.top_endpoints);
        renderBarTable("hosts-body", data.top_outbound_hosts, "host", function (r) { return r.host; });
        setRefreshIndicator("ok");
      })
      .catch(function () {
        setRefreshIndicator("error");
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    refresh();
    setInterval(refresh, POLL_INTERVAL_MS);
  });
})();
