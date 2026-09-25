// Assistant stats trend charts (/assistant/stats).
//
// Three independent Chart.js instances, each reading its own pre-computed
// series out of a canvas's `data-trend` attribute (server-rendered JSON,
// same "Jinja embeds the data, JS just draws it" pattern as
// market_warehouse.js's `#warehouse-chart` and `#projection-chart" -- no
// fetch(), no extra round trip, and nothing here ever asks the backend for
// more than the page already rendered):
//
//   #eval-trend-chart   -- one line, the eval suite's day-by-day pass rate
//                          (compute_eval_trend's `daily`).
//   #usage-volume-chart -- one bar series, message volume per day
//                          (compute_usage_trend's `daily`).
//   #usage-rates-chart  -- up to five lines, the reliability rates per day
//                          (cache hit / guard flagged / fallback / busy /
//                          deadline hit -- all 0-100%, one shared axis).
//
// Colors: the dataviz skill's validated dark categorical order, run
// through its palette validator against this site's own dark chart
// surface (#0f1012) -- all five checks (lightness band, chroma floor, CVD
// adjacent-pair separation, normal-vision floor, contrast vs. surface)
// pass for the first five slots used below. Single-series charts (eval
// pass rate, message volume) use slot 1, the same blue family as the
// site's own --accent, so they read consistent with the existing
// "Messages per day" sparkline above them on this page.
(function () {
  "use strict";

  var PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"];

  function readTrend(canvas) {
    if (!canvas || !canvas.dataset.trend) return null;
    try {
      var data = JSON.parse(canvas.dataset.trend);
      return Array.isArray(data) && data.length ? data : null;
    } catch (e) {
      return null;
    }
  }

  // ------------------------------------------------------------------
  // Eval pass-rate trend: one line, 0-100%.
  // ------------------------------------------------------------------
  function initEvalTrendChart() {
    var canvas = document.getElementById("eval-trend-chart");
    if (!canvas || !window.Chart) return;
    var daily = readTrend(canvas);
    if (!daily) return;

    var color = PALETTE[0];
    new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: daily.map(function (d) { return d.date; }),
        datasets: [
          {
            label: "Pass rate",
            data: daily.map(function (d) { return d.pass_rate; }),
            borderColor: color,
            backgroundColor: color,
            spanGaps: true,
            pointRadius: 0,
            pointHoverRadius: 4,
            borderWidth: 2,
            tension: 0.15,
          },
        ],
      },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { ticks: { maxTicksLimit: 10 } },
          y: { min: 0, max: 100, ticks: { callback: function (v) { return v + "%"; } } },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              afterLabel: function (ctx) {
                var d = daily[ctx.dataIndex];
                if (!d || !d.run_count) return "";
                return d.passed_cases + "/" + d.total_cases + " cases, " + d.run_count + " run(s)";
              },
            },
          },
        },
      },
    });
  }

  // ------------------------------------------------------------------
  // Usage volume: one bar series, raw message counts.
  // ------------------------------------------------------------------
  function initUsageVolumeChart() {
    var canvas = document.getElementById("usage-volume-chart");
    if (!canvas || !window.Chart) return;
    var daily = readTrend(canvas);
    if (!daily) return;

    var color = PALETTE[0];
    new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: daily.map(function (d) { return d.date; }),
        datasets: [
          {
            label: "Messages",
            data: daily.map(function (d) { return d.message_count; }),
            backgroundColor: color,
            borderRadius: 4,
            maxBarThickness: 24,
          },
        ],
      },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { ticks: { maxTicksLimit: 10 } },
          y: { beginAtZero: true, ticks: { precision: 0 } },
        },
        plugins: { legend: { display: false } },
      },
    });
  }

  // ------------------------------------------------------------------
  // Reliability rates: up to five lines, all 0-100% on one shared axis
  // (never a dual-axis chart against the volume counts above).
  // ------------------------------------------------------------------
  var RATE_SERIES = [
    { key: "cache_hit_pct", label: "Cache hit" },
    { key: "guard_flagged_pct", label: "Guard flagged" },
    { key: "fallback_pct", label: "Fallback" },
    { key: "busy_pct", label: "Busy" },
    { key: "deadline_hit_pct", label: "Deadline hit" },
  ];

  function initUsageRatesChart() {
    var canvas = document.getElementById("usage-rates-chart");
    if (!canvas || !window.Chart) return;
    var daily = readTrend(canvas);
    if (!daily) return;

    var datasets = RATE_SERIES.map(function (series, i) {
      var color = PALETTE[i % PALETTE.length];
      return {
        label: series.label,
        data: daily.map(function (d) { return d[series.key]; }),
        borderColor: color,
        backgroundColor: color,
        spanGaps: true,
        pointRadius: 0,
        pointHoverRadius: 4,
        borderWidth: 2,
        tension: 0.15,
      };
    });

    new Chart(canvas.getContext("2d"), {
      type: "line",
      data: { labels: daily.map(function (d) { return d.date; }), datasets: datasets },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { ticks: { maxTicksLimit: 10 } },
          y: { min: 0, max: 100, ticks: { callback: function (v) { return v + "%"; } } },
        },
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initEvalTrendChart();
    initUsageVolumeChart();
    initUsageRatesChart();
  });
})();
