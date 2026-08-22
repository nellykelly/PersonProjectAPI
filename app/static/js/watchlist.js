// Trading Simulator watchlist: a genuinely live-updating price board,
// plus a live multi-stock chart -- click any ticker tile below the
// chart to add/remove it as a plotted line. Not a real exchange stream
// (see watchlist.py for why) -- a server-side poller refreshes yfinance
// quotes on an interval during market hours and pushes each update over
// SSE the instant it's fetched.
(function () {
  "use strict";

  var MAX_SELECTED = 8;
  var MAX_POINTS = 60;
  var SERIES_COLORS = [
    "#3aa0ff", "#4ade80", "#fbbf24", "#f87171",
    "#c084fc", "#2dd4bf", "#fb923c", "#f472b6",
  ];

  var chart = null;
  var seriesByTicker = {}; // ticker -> { color, datasetIndex }

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

  function timeLabel(isoTs) {
    var t = (isoTs || "").split("T")[1] || "";
    return t.split(/[.-]/)[0];
  }

  // ---------- chart ----------

  function nextAvailableColor() {
    var used = Object.keys(seriesByTicker).map(function (t) { return seriesByTicker[t].color; });
    for (var i = 0; i < SERIES_COLORS.length; i++) {
      if (used.indexOf(SERIES_COLORS[i]) === -1) return SERIES_COLORS[i];
    }
    return SERIES_COLORS[Object.keys(seriesByTicker).length % SERIES_COLORS.length];
  }

  function initChart() {
    var canvas = document.getElementById("watchlist-chart");
    if (!canvas || !window.Chart) return;
    chart = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: { labels: [], datasets: [] },
      options: {
        responsive: true,
        animation: false,
        interaction: { mode: "nearest", intersect: false },
        scales: {
          y: { title: { display: true, text: "Price ($)" } },
          x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
        },
        plugins: {
          legend: { display: true, labels: { color: "#ffffff", boxWidth: 12 } },
        },
      },
    });
  }

  function addTickerToChart(ticker) {
    if (!chart || seriesByTicker[ticker]) return;
    var color = nextAvailableColor();
    chart.data.datasets.push({
      label: ticker,
      data: chart.data.labels.map(function () { return null; }), // align with existing timeline
      borderColor: color,
      backgroundColor: color,
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.15,
      spanGaps: true,
    });
    seriesByTicker[ticker] = { color: color, datasetIndex: chart.data.datasets.length - 1 };
    chart.update();
    return color;
  }

  function removeTickerFromChart(ticker) {
    var series = seriesByTicker[ticker];
    if (!series || !chart) return;
    chart.data.datasets.splice(series.datasetIndex, 1);
    delete seriesByTicker[ticker];
    Object.keys(seriesByTicker).forEach(function (t) {
      if (seriesByTicker[t].datasetIndex > series.datasetIndex) {
        seriesByTicker[t].datasetIndex -= 1;
      }
    });
    chart.update();
  }

  function pushPoint(ticker, entry) {
    var series = seriesByTicker[ticker];
    if (!series || !chart) return;

    chart.data.labels.push(timeLabel(entry.updated_at));
    if (chart.data.labels.length > MAX_POINTS) chart.data.labels.shift();

    chart.data.datasets.forEach(function (ds, idx) {
      ds.data.push(idx === series.datasetIndex ? entry.price : null);
      if (ds.data.length > MAX_POINTS) ds.data.shift();
    });

    chart.update("none"); // no animation on live ticks -- keeps it snappy
  }

  // ---------- ticker selection ----------

  function updateHint() {
    var hint = document.getElementById("chart-hint");
    if (!hint) return;
    var count = Object.keys(seriesByTicker).length;
    hint.textContent = count === 0
      ? "Click up to " + MAX_SELECTED + " tickers below to plot their live price as it ticks in."
      : count + " / " + MAX_SELECTED + " selected -- click a ticker to remove it from the chart.";
  }

  function toggleTicker(tile) {
    var ticker = tile.dataset.ticker;
    var isSelected = tile.classList.contains("is-selected");

    if (isSelected) {
      tile.classList.remove("is-selected");
      tile.setAttribute("aria-pressed", "false");
      tile.style.removeProperty("--tile-series-color");
      removeTickerFromChart(ticker);
    } else {
      if (Object.keys(seriesByTicker).length >= MAX_SELECTED) return;
      var color = addTickerToChart(ticker);
      tile.classList.add("is-selected");
      tile.setAttribute("aria-pressed", "true");
      tile.style.setProperty("--tile-series-color", color);
    }
    updateHint();
  }

  function clearSelection() {
    Object.keys(seriesByTicker).slice().forEach(function (ticker) {
      var tile = document.getElementById("tile-" + ticker);
      if (tile) toggleTicker(tile);
    });
  }

  // ---------- live tiles + SSE ----------

  function applyUpdate(entry) {
    var cell = document.getElementById("tile-" + entry.ticker);
    if (!cell) return;

    var priceEl = cell.querySelector(".tile-price");
    var timeEl = cell.querySelector(".tile-time");
    if (priceEl) priceEl.textContent = "$" + entry.price.toFixed(2);
    if (timeEl) timeEl.textContent = timeLabel(entry.updated_at);

    cell.classList.remove("tile-flash-up", "tile-flash-down");
    if (entry.direction === "up") {
      cell.classList.add("tile-flash-up");
    } else if (entry.direction === "down") {
      cell.classList.add("tile-flash-down");
    }
    void cell.offsetWidth; // force reflow so re-triggering the same animation class works

    if (seriesByTicker[entry.ticker]) {
      pushPoint(entry.ticker, entry);
    }
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
    initChart();

    var grid = document.getElementById("watchlist-grid");
    if (grid) {
      grid.addEventListener("click", function (event) {
        var tile = event.target.closest(".watchlist-tile");
        if (tile) toggleTicker(tile);
      });
    }

    var clearBtn = document.getElementById("chart-clear");
    if (clearBtn) clearBtn.addEventListener("click", clearSelection);

    // Always connect -- the poller itself is a no-op while the market's
    // closed (see watchlist.py), so there's nothing wrong with an idle
    // connection sitting open ready for the next open bell.
    connectStream();
  });
})();
