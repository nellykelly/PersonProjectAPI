// Trading Bot research page: one bar chart, return by year, colored by sign.
(function () {
  "use strict";

  var POSITIVE = "#4ade80";
  var NEGATIVE = "#f87171";

  function init() {
    var canvas = document.getElementById("trading-bot-by-year-chart");
    if (!canvas || typeof Chart === "undefined") return;

    var byYear = JSON.parse(canvas.getAttribute("data-by-year") || "[]");
    if (!byYear.length) return;

    var labels = byYear.map(function (row) { return row.year; });
    var values = byYear.map(function (row) { return row.pct; });
    var colors = values.map(function (v) { return v >= 0 ? POSITIVE : NEGATIVE; });

    new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: labels,
        datasets: [{
          label: "Return (%)",
          data: values,
          backgroundColor: colors,
          borderColor: colors,
        }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          y: { title: { display: true, text: "Return (%)" } },
        },
      },
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
