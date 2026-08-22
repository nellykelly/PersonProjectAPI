// Trading Simulator: dynamic option-chain fields on the open-position
// form, live quote polling + PnL chart on the position detail page.
// Vanilla JS + fetch against the trading blueprint's JSON endpoints.
(function () {
  "use strict";

  var API_BASE = "/projects/trading-simulator/api";

  function initOpenForm() {
    var kindSelect = document.getElementById("kind");
    var tickerSelect = document.getElementById("ticker");
    var optionFields = document.getElementById("option-fields");
    var expirySelect = document.getElementById("expiry-select");
    var strikeSelect = document.getElementById("strike-select");
    var expiryHidden = document.getElementById("expiry");
    var strikeHidden = document.getElementById("strike");

    if (!kindSelect || !tickerSelect) return;

    function isOption() {
      return kindSelect.value === "call" || kindSelect.value === "put";
    }

    function toggleOptionFields() {
      optionFields.style.display = isOption() ? "" : "none";
      if (isOption()) loadExpiries();
    }

    function loadExpiries() {
      var ticker = tickerSelect.value;
      if (!ticker) return;
      expirySelect.innerHTML = "<option>Loading expiries...</option>";
      fetch(API_BASE + "/expiries/" + encodeURIComponent(ticker))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          expirySelect.innerHTML = "";
          if (!data.ok) {
            expirySelect.innerHTML = "<option value=''>" + data.error + "</option>";
            return;
          }
          data.expiries.slice(0, 12).forEach(function (exp) {
            var opt = document.createElement("option");
            opt.value = exp;
            opt.textContent = exp;
            expirySelect.appendChild(opt);
          });
          loadChain();
        })
        .catch(function () {
          expirySelect.innerHTML = "<option value=''>Could not load expiries</option>";
        });
    }

    function loadChain() {
      var ticker = tickerSelect.value;
      var expiry = expirySelect.value;
      if (!ticker || !expiry) return;
      expiryHidden.value = expiry;
      strikeSelect.innerHTML = "<option>Loading strikes...</option>";
      fetch(API_BASE + "/chain/" + encodeURIComponent(ticker) + "/" + encodeURIComponent(expiry))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          strikeSelect.innerHTML = "";
          if (!data.ok) {
            strikeSelect.innerHTML = "<option value=''>" + data.error + "</option>";
            return;
          }
          var rows = kindSelect.value === "call" ? data.calls : data.puts;
          if (!rows.length) {
            strikeSelect.innerHTML = "<option value=''>No contracts found</option>";
            return;
          }
          rows.forEach(function (row) {
            var opt = document.createElement("option");
            opt.value = row.strike;
            var iv = row.impliedVolatility != null ? (row.impliedVolatility * 100).toFixed(1) + "% IV" : "IV n/a";
            var last = row.lastPrice != null ? row.lastPrice : "-";
            opt.textContent = "Strike " + row.strike + " (last " + last + ", " + iv + ")";
            strikeSelect.appendChild(opt);
          });
          strikeHidden.value = strikeSelect.value;
        })
        .catch(function () {
          strikeSelect.innerHTML = "<option value=''>Could not load chain</option>";
        });
    }

    kindSelect.addEventListener("change", toggleOptionFields);
    tickerSelect.addEventListener("change", function () {
      if (isOption()) loadExpiries();
    });
    expirySelect.addEventListener("change", loadChain);
    strikeSelect.addEventListener("change", function () {
      strikeHidden.value = strikeSelect.value;
    });

    toggleOptionFields();
  }

  function initAutoRefresh() {
    var el = document.querySelector("[data-auto-refresh]");
    if (!el) return;
    setTimeout(function () {
      window.location.reload();
    }, 30000);
  }

  function initPositionDetail() {
    var quoteEl = document.querySelector("[data-quote-ticker]");
    if (!quoteEl) return;
    var ticker = quoteEl.dataset.quoteTicker;
    var priceEl = document.getElementById("live-price");

    function pollQuote() {
      fetch(API_BASE + "/quote/" + encodeURIComponent(ticker))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok && priceEl) {
            priceEl.textContent = "$" + data.price.toFixed(2);
          }
        })
        .catch(function () {});
    }

    pollQuote();
    setInterval(pollQuote, 15000);

    var positionId = quoteEl.dataset.positionId;
    var canvas = document.getElementById("pnl-chart");
    if (positionId && canvas && window.Chart) {
      fetch(API_BASE + "/positions/" + positionId + "/history")
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.ok) return;
          var labels = data.points.map(function (p) { return p.date; });
          var pnl = data.points.map(function (p) { return p.pnl; });
          var price = data.points.map(function (p) { return p.price; });
          new Chart(canvas.getContext("2d"), {
            type: "line",
            data: {
              labels: labels,
              datasets: [
                {
                  label: "PnL ($)",
                  data: pnl,
                  borderColor: "#3aa0ff",
                  backgroundColor: "rgba(58,160,255,0.1)",
                  yAxisID: "y",
                  tension: 0.15,
                },
                {
                  label: "Underlying price",
                  data: price,
                  borderColor: "rgba(255,255,255,0.35)",
                  yAxisID: "y1",
                  tension: 0.15,
                },
              ],
            },
            options: {
              responsive: true,
              scales: {
                y: { position: "left", title: { display: true, text: "PnL ($)" } },
                y1: { position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "Price ($)" } },
              },
            },
          });
        })
        .catch(function () {});
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    initOpenForm();
    initAutoRefresh();
    initPositionDetail();
  });
})();
