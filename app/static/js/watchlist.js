// Trading Simulator watchlist grid: a genuinely live-updating price
// board. Not a real exchange stream (see watchlist.py for why) -- a
// server-side poller refreshes yfinance quotes on an interval during
// market hours and pushes each update over SSE the instant it's fetched.
(function () {
  "use strict";

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

  function applyUpdate(entry) {
    var cell = document.getElementById("tile-" + entry.ticker);
    if (!cell) return;

    var priceEl = cell.querySelector(".tile-price");
    var timeEl = cell.querySelector(".tile-time");
    if (priceEl) priceEl.textContent = "$" + entry.price.toFixed(2);
    if (timeEl) {
      var t = (entry.updated_at || "").split("T")[1] || "";
      timeEl.textContent = t.split(/[.-]/)[0];
    }

    cell.classList.remove("tile-flash-up", "tile-flash-down");
    if (entry.direction === "up") {
      cell.classList.add("tile-flash-up");
    } else if (entry.direction === "down") {
      cell.classList.add("tile-flash-down");
    }
    // force a reflow so re-triggering the same animation class works
    void cell.offsetWidth;
  }

  function connectStream() {
    if (!window.EventSource) return;
    var source = new EventSource("/projects/trading-simulator/api/watchlist/stream");
    source.onopen = function () { setLiveIndicator("connected"); };
    source.onerror = function () { setLiveIndicator("reconnecting"); };
    source.onmessage = function (event) {
      applyUpdate(JSON.parse(event.data));
    };
  }

  document.addEventListener("DOMContentLoaded", function () {
    var grid = document.getElementById("watchlist-grid");
    if (grid && grid.dataset.marketOpen === "true") {
      connectStream();
    }
  });
})();
