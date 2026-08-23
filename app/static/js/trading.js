// Trading Simulator: dynamic option-chain fields on the open-position
// form, live quote polling + PnL chart on the position detail page.
// Vanilla JS + fetch against the trading blueprint's JSON endpoints.
(function () {
  "use strict";

  var API_BASE = "/projects/trading-simulator/api";
  var TRADING_BASE = "/projects/trading-simulator";

  // A navigation aborts any in-flight fetch, and the rejection is
  // indistinguishable from a real network failure at the catch site.
  // Reporting "Could not load chain" while the page is on its way out is
  // both wrong and alarming, so the error handlers check this first.
  var pageIsUnloading = false;
  window.addEventListener("beforeunload", function () {
    pageIsUnloading = true;
  });

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
          if (pageIsUnloading) return;
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
          if (pageIsUnloading) return;
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

  // Refreshes the shared position book so its prices don't go stale.
  //
  // It has to stay off the user's back while they're actually using the
  // open-position form. A reload mid-edit throws away every field they've
  // picked *and* aborts the in-flight option-chain fetch, which the catch
  // in loadChain() then reports as "Could not load chain" -- so an
  // unrelated background timer showed up as a broken option chain.
  function initAutoRefresh() {
    var el = document.querySelector("[data-auto-refresh]");
    if (!el) return;

    var INTERVAL_MS = 30000;
    // How long the form has to sit untouched before a refresh is allowed
    // again. Without this, one keystroke would disable refreshing for the
    // rest of the session.
    var IDLE_BEFORE_REFRESH_MS = 60000;

    var form = el.querySelector("form");
    var lastInteraction = 0;

    if (form) {
      ["input", "change", "focusin"].forEach(function (evt) {
        form.addEventListener(evt, function () {
          lastInteraction = Date.now();
        });
      });
    }

    function tick() {
      var focusInsideForm = form && form.contains(document.activeElement);
      var recentlyActive = Date.now() - lastInteraction < IDLE_BEFORE_REFRESH_MS;
      if (focusInsideForm || recentlyActive) {
        setTimeout(tick, INTERVAL_MS); // check back rather than giving up
        return;
      }
      window.location.reload();
    }

    setTimeout(tick, INTERVAL_MS);
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

  // position -> risk request -> report/live feed: a risk request is a
  // real POST that creates a persisted, queryable row (see
  // app/services/risk_engine.py), not just a number computed inline for
  // this one page render -- this panel is a thin client over that API,
  // not where the actual risk logic lives.
  function initRiskPanel() {
    var panel = document.getElementById("risk-panel");
    if (!panel) return;
    var positionId = panel.dataset.positionId;
    var base = TRADING_BASE + "/positions/" + positionId;
    var historyCount = 0;
    var eventSource = null;

    function fmt(n, digits) {
      return n == null ? "n/a" : Number(n).toFixed(digits == null ? 2 : digits);
    }

    function renderResult(result) {
      document.getElementById("risk-result-grid").style.display = "";
      document.getElementById("risk-pv").textContent = "$" + fmt(result.pv);
      document.getElementById("risk-pnl").textContent = "$" + fmt(result.pnl);
      document.getElementById("risk-delta").textContent = fmt(result.delta);
      document.getElementById("risk-gamma").textContent = fmt(result.gamma, 3);
      document.getElementById("risk-scenario-gamma").textContent = fmt(result.scenario_gamma, 3);
      document.getElementById("risk-theta").textContent = fmt(result.theta);
      document.getElementById("risk-vega").textContent = fmt(result.vega);
      document.getElementById("risk-ir-delta").textContent = fmt(result.ir_delta);
      document.getElementById("risk-ir-vega").textContent = result.ir_vega == null ? "n/m (not modeled)" : fmt(result.ir_vega);
    }

    function appendHistoryRow(riskRequest) {
      var emptyState = document.getElementById("risk-history-empty");
      if (emptyState) emptyState.style.display = "none";
      historyCount++;
      var row = document.createElement("tr");
      // Scenario values are stored in the units the form collects them in
      // (whole percent, whole IV points), so they render as-is. They used
      // to be scaled by 100 here, which was left over from when the engine
      // treated them as fractions.
      var scenarioText = riskRequest.scenario
        ? "spot " + riskRequest.scenario.spot_shock_pct + "%, vol " + riskRequest.scenario.vol_shock_pts + "pt"
        : "as-of-now";
      var reportLink = riskRequest.report_url
        ? "<a class='button small' href='" + riskRequest.report_url + "'>Open</a>"
        : "";
      row.innerHTML =
        "<td>" + historyCount + "</td>" +
        "<td class='muted'>" + new Date(riskRequest.requested_at).toLocaleTimeString() + "</td>" +
        "<td class='mono'>" + (riskRequest.model_key || "") + "</td>" +
        "<td>" + scenarioText + "</td>" +
        "<td class='mono'>$" + fmt(riskRequest.result.underlying_price_used) + "</td>" +
        "<td class='mono'>$" + fmt(riskRequest.result.pnl) + "</td>" +
        "<td>" + reportLink + "</td>";
      document.getElementById("risk-history-body").insertBefore(row, document.getElementById("risk-history-body").firstChild);
    }

    function submitRiskRequest(body) {
      var modelSelect = document.getElementById("risk-model");
      var payload = body || {};
      if (modelSelect) payload.model_key = modelSelect.value;
      fetch(base + "/risk-requests", { method: "POST", body: new URLSearchParams(payload) })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.ok) return;
          renderResult(data.risk_request.result);
          appendHistoryRow(data.risk_request);
        })
        .catch(function () {});
    }

    document.getElementById("risk-request-now").addEventListener("click", function () {
      submitRiskRequest(null);
    });

    document.getElementById("risk-request-scenario").addEventListener("click", function () {
      // Sent exactly as typed: the field says "%" and "IV points", and the
      // engine applies them as whole percent/points. Dividing here as well
      // made every scenario 100x too small.
      var spotPct = parseFloat(document.getElementById("spot-shock-pct").value || "0");
      var volPts = parseFloat(document.getElementById("vol-shock-pts").value || "0");
      submitRiskRequest({ spot_shock_pct: spotPct, vol_shock_pts: volPts });
    });

    document.getElementById("risk-feed-toggle").addEventListener("click", function () {
      var btn = this;
      var statusEl = document.getElementById("risk-feed-status");
      if (eventSource) {
        eventSource.close();
        eventSource = null;
        btn.textContent = "Start live risk feed";
        statusEl.textContent = "";
        return;
      }
      eventSource = new EventSource(API_BASE + "/positions/" + positionId + "/risk-feed");
      btn.textContent = "Stop live risk feed";
      statusEl.textContent = "connecting...";
      eventSource.onmessage = function (event) {
        var data = JSON.parse(event.data);
        if (data.error) {
          statusEl.textContent = data.error;
          return;
        }
        statusEl.textContent = "live -- last tick " + new Date().toLocaleTimeString();
        renderResult(data.result);
        appendHistoryRow(data);
      };
      eventSource.onerror = function () {
        statusEl.textContent = "reconnecting...";
      };
    });
  }

  // Position-level risk: one request across every leg in the position.
  function initPositionRiskPanel() {
    var panel = document.getElementById("position-risk-panel");
    if (!panel) return;
    var strategyId = panel.dataset.strategyId;
    var statusEl = document.getElementById("pos-risk-status");

    document.getElementById("pos-run-risk").addEventListener("click", function () {
      var payload = { model_key: document.getElementById("pos-risk-model").value };
      // Sent in the units the labels promise: whole percent, whole IV points.
      var spot = document.getElementById("pos-spot-shock").value;
      var vol = document.getElementById("pos-vol-shock").value;
      if (spot) payload.spot_shock_pct = spot;
      if (vol) payload.vol_shock_pts = vol;

      statusEl.textContent = "Pricing the position...";
      fetch(TRADING_BASE + "/strategies/" + strategyId + "/risk-requests", {
        method: "POST",
        body: new URLSearchParams(payload),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.ok) {
            statusEl.textContent = data.error || "That risk request failed.";
            return;
          }
          // The report is the deliverable, so go straight to it.
          window.location.href = data.risk_request.report_url;
        })
        .catch(function () {
          if (pageIsUnloading) return;
          statusEl.textContent = "That risk request could not be sent.";
        });
    });
  }

  // Book-level risk: one request across every open instrument in the
  // whole book, from the all-positions page.
  function initBookRiskPanel() {
    var panel = document.getElementById("book-risk-panel");
    if (!panel) return;
    var statusEl = document.getElementById("book-risk-status");

    document.getElementById("book-run-risk").addEventListener("click", function () {
      var payload = { model_key: document.getElementById("book-risk-model").value };
      var spot = document.getElementById("book-spot-shock").value;
      var vol = document.getElementById("book-vol-shock").value;
      if (spot) payload.spot_shock_pct = spot;
      if (vol) payload.vol_shock_pts = vol;

      statusEl.textContent = "Pricing every open instrument in the book...";
      fetch(TRADING_BASE + "/risk-requests", {
        method: "POST",
        body: new URLSearchParams(payload),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.ok) {
            statusEl.textContent = data.error || "That risk request failed.";
            return;
          }
          window.location.href = data.risk_request.report_url;
        })
        .catch(function () {
          if (pageIsUnloading) return;
          statusEl.textContent = "That risk request could not be sent.";
        });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initOpenForm();
    initPositionRiskPanel();
    initBookRiskPanel();
    initAutoRefresh();
    initPositionDetail();
    initRiskPanel();
  });
})();
