// Market Data Warehouse dashboard.
//
// Two independent Chart.js instances:
//   #warehouse-chart   -- relative performance (indexed to 100), redrawn
//                         when the timeframe picker changes via a fetch()
//                         to /api/chart (server does the reindexing).
//   #projection-chart   -- NOT FINANCIAL ADVICE: recent actual price (solid)
//                         + trend projection (dashed) + a shaded 95% band,
//                         for one ticker at a time, switched client-side
//                         since every ticker's projection is already
//                         embedded on the page (small enough not to need
//                         a round trip).
(function () {
  "use strict";

  var PALETTE = ["#3aa0ff", "#4ade80", "#f87171", "#fb923c", "#818cf8", "#2dd4bf", "#f472b6", "#facc15", "#94a3b8"];
  var CHART_ENDPOINT = "/projects/market-warehouse/api/chart";

  // ------------------------------------------------------------------
  // Relative performance chart + timeframe picker
  // ------------------------------------------------------------------
  var perfChart = null;

  function renderPerfChart(canvas, data) {
    var datasets = data.series.map(function (s, i) {
      var color = PALETTE[i % PALETTE.length];
      return {
        label: s.ticker,
        data: s.data,
        borderColor: color,
        backgroundColor: color,
        spanGaps: true,
        pointRadius: 0,
        borderWidth: 1.75,
        tension: 0.15,
      };
    });
    var config = {
      type: "line",
      data: { labels: data.labels, datasets: datasets },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { ticks: { maxTicksLimit: 10 } },
          y: { title: { display: true, text: "Indexed to 100 at window start" } },
        },
        plugins: { legend: { position: "bottom" } },
      },
    };
    if (perfChart) {
      perfChart.data = config.data;
      perfChart.update();
    } else {
      perfChart = new Chart(canvas.getContext("2d"), config);
    }
  }

  function setStatus(text) {
    var el = document.getElementById("mw-timeframe-status");
    if (el) el.textContent = text;
  }

  function fetchAndRenderWindow(canvas, params) {
    setStatus("Loading…");
    var url = CHART_ENDPOINT + "?" + params.toString();
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (payload) {
        if (!payload.ok) {
          setStatus("Could not load that range.");
          return;
        }
        renderPerfChart(canvas, payload.chart);
        setStatus("");
      })
      .catch(function () { setStatus("Could not load that range."); });
  }

  function clearPresetSelection() {
    document.querySelectorAll(".mw-timeframe-presets .button").forEach(function (b) {
      b.classList.remove("is-selected");
    });
  }

  function initTimeframePicker(canvas) {
    var picker = document.getElementById("mw-timeframe");
    if (!picker) return;
    var maxDate = canvas.dataset.maxDate; // yyyy-mm-dd, from the server
    var minDate = canvas.dataset.minDate;

    picker.querySelectorAll(".mw-timeframe-presets .button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        clearPresetSelection();
        btn.classList.add("is-selected");
        var days = parseInt(btn.dataset.days, 10);
        var params = new URLSearchParams();
        if (days > 0 && maxDate) {
          var end = new Date(maxDate + "T00:00:00Z");
          var start = new Date(end);
          start.setUTCDate(start.getUTCDate() - days);
          params.set("start", start.toISOString().slice(0, 10));
          params.set("end", maxDate);
        } else if (minDate && maxDate) {
          params.set("start", minDate);
          params.set("end", maxDate);
        }
        fetchAndRenderWindow(canvas, params);
      });
    });

    var applyBtn = document.getElementById("mw-apply-range");
    if (applyBtn) {
      applyBtn.addEventListener("click", function () {
        var start = document.getElementById("mw-start").value;
        var end = document.getElementById("mw-end").value;
        if (!start && !end) return;
        clearPresetSelection();
        var params = new URLSearchParams();
        if (start) params.set("start", start);
        if (end) params.set("end", end);
        fetchAndRenderWindow(canvas, params);
      });
    }
  }

  function initPerfChart() {
    var canvas = document.getElementById("warehouse-chart");
    if (!canvas || !window.Chart) return;
    var data;
    try {
      data = JSON.parse(canvas.dataset.chart);
    } catch (e) {
      return;
    }
    if (data && data.labels && data.labels.length) {
      renderPerfChart(canvas, data);
    }
    initTimeframePicker(canvas);
  }

  // ------------------------------------------------------------------
  // Projection chart: NOT FINANCIAL ADVICE -- see the page's disclaimer.
  // ------------------------------------------------------------------
  var projectionChart = null;

  function renderProjectionChart(canvas, ticker, projectionData, recentActualData) {
    var recent = recentActualData[ticker] || { dates: [], close: [] };
    var proj = projectionData.series[ticker] || { dates: [], projected: [], lower: [], upper: [] };

    var labels = recent.dates.concat(proj.dates);
    var actualSeries = recent.close.concat(proj.dates.map(function () { return null; }));
    var pad = recent.dates.map(function () { return null; });
    var projectedSeries = pad.concat(proj.projected);
    var lowerSeries = pad.concat(proj.lower);
    var upperSeries = pad.concat(proj.upper);
    // bridge the gap so the dashed line visually connects to the actual line
    if (recent.close.length && proj.projected.length) {
      actualSeries[recent.close.length - 1] = recent.close[recent.close.length - 1];
      projectedSeries[recent.close.length - 1] = recent.close[recent.close.length - 1];
    }

    var config = {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: ticker + " (actual)",
            data: actualSeries,
            borderColor: "#3aa0ff",
            backgroundColor: "#3aa0ff",
            spanGaps: false,
            pointRadius: 0,
            borderWidth: 1.75,
          },
          {
            label: ticker + " (projected -- not a forecast)",
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
        interaction: { mode: "index", intersect: false },
        scales: { x: { ticks: { maxTicksLimit: 10 } } },
        plugins: { legend: { position: "bottom" } },
      },
    };

    if (projectionChart) {
      projectionChart.data = config.data;
      projectionChart.update();
    } else {
      projectionChart = new Chart(canvas.getContext("2d"), config);
    }

    var r2El = document.getElementById("mw-projection-r2");
    if (r2El) {
      var r2 = proj.r_squared;
      var quality = r2 == null ? "n/a" : r2 < 0.3 ? "weak" : r2 < 0.6 ? "moderate" : "stronger (still not predictive)";
      r2El.textContent = ticker + "'s trend fit: R² = " + (r2 == null ? "n/a" : r2.toFixed(2)) + " (" + quality + ")";
    }
  }

  var PROJECTION_ENDPOINT = "/projects/market-warehouse/api/projection";

  function setProjectionStatus(text) {
    var el = document.getElementById("mw-projection-status");
    if (el) el.textContent = text;
  }

  function selectOnly(container, btn) {
    if (!container) return;
    container.querySelectorAll(".button").forEach(function (b) { b.classList.remove("is-selected"); });
    btn.classList.add("is-selected");
  }

  function initProjectionChart() {
    var canvas = document.getElementById("projection-chart");
    if (!canvas || !window.Chart) return;
    var projectionData, recentActualData;
    try {
      projectionData = JSON.parse(canvas.dataset.projection);
      recentActualData = JSON.parse(canvas.dataset.recentActual);
    } catch (e) {
      return;
    }
    if (!projectionData || !projectionData.tickers || !projectionData.tickers.length) return;

    var tickerPicker = document.getElementById("mw-projection-ticker-picker");
    var methodPicker = document.getElementById("mw-projection-method-picker");
    var lookbackPicker = document.getElementById("mw-projection-lookback-picker");

    // module-local state: which ticker is shown, and which precomputed
    // (method, lookback) combination's rows are currently loaded
    var state = {
      ticker: projectionData.tickers[0],
      method: canvas.dataset.defaultMethod,
      lookback: canvas.dataset.defaultLookback,
    };

    function rerender() {
      renderProjectionChart(canvas, state.ticker, projectionData, recentActualData);
    }

    rerender();

    if (tickerPicker) {
      tickerPicker.querySelectorAll(".button").forEach(function (btn) {
        btn.addEventListener("click", function () {
          selectOnly(tickerPicker, btn);
          state.ticker = btn.dataset.ticker;
          rerender();
        });
      });
    }

    function fetchAndSwap() {
      setProjectionStatus("Loading…");
      var params = new URLSearchParams({ method: state.method, lookback: state.lookback });
      fetch(PROJECTION_ENDPOINT + "?" + params.toString())
        .then(function (r) { return r.json(); })
        .then(function (payload) {
          if (!payload.ok || !payload.projection.tickers.length) {
            setProjectionStatus("Could not load that combination.");
            return;
          }
          projectionData = payload.projection;
          if (projectionData.tickers.indexOf(state.ticker) === -1) {
            state.ticker = projectionData.tickers[0];
          }
          rerender();
          setProjectionStatus("");
        })
        .catch(function () { setProjectionStatus("Could not load that combination."); });
    }

    if (methodPicker) {
      methodPicker.querySelectorAll(".button").forEach(function (btn) {
        btn.addEventListener("click", function () {
          selectOnly(methodPicker, btn);
          state.method = btn.dataset.method;
          fetchAndSwap();
        });
      });
    }

    if (lookbackPicker) {
      lookbackPicker.querySelectorAll(".button").forEach(function (btn) {
        btn.addEventListener("click", function () {
          selectOnly(lookbackPicker, btn);
          state.lookback = btn.dataset.lookback;
          fetchAndSwap();
        });
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    initPerfChart();
    initProjectionChart();
  });
})();
