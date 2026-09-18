// Tiny JVM in-page demo: loads the WebAssembly build of the same C
// interpreter that also runs natively and on the Wokwi board (build spec
// section 8), and steps it one instruction at a time so the value stack,
// locals, and console/GPIO output are all visible as the program runs.
//
// The WASM module never calls back into the page for anything except
// PRINT/PINMODE/DWRITE/DREAD (wasm_host.c's EM_JS callbacks) -- every
// other bit of VM state (ip, sp, stack values, locals) is read by
// polling small exported getters after each step, rather than the
// module pushing state to JS. Simpler seam, and it means "Step" always
// shows a fully consistent snapshot.
(function () {
  "use strict";

  var VM_RUNNING = 0;
  var MAX_RUN_STEPS = 2000; // a bounded "Run" so an intentionally
                             // infinite sample (the thermostat's
                             // `while (1)`) can't hang the tab.

  var SAMPLES = {
    fib: "/static/assets/tiny_jvm/fib.tvm",
    factorial: "/static/assets/tiny_jvm/factorial.tvm",
    thermostat: "/static/assets/tiny_jvm/thermostat.tvm",
  };

  var moduleInstance = null;
  var consoleLines = [];
  var pinModes = {};
  var pinValues = {};
  var sensorValue = 0;

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  function appendConsoleLine(text) {
    consoleLines.push(text);
    if (consoleLines.length > 200) {
      consoleLines.shift();
    }
    renderConsole();
  }

  // Host callbacks wasm_host.c's EM_JS functions call into. Checked with
  // typeof at call time on the C side, so it doesn't matter that these
  // are defined before or after the module finishes loading.
  window.tinyJvmOnPrint = function (value) {
    appendConsoleLine(String(value));
  };
  window.tinyJvmOnPinMode = function (pin, mode) {
    pinModes[pin] = mode;
    renderPins();
  };
  window.tinyJvmOnDigitalWrite = function (pin, value) {
    pinValues[pin] = value;
    renderPins();
  };
  window.tinyJvmOnDigitalRead = function () {
    return sensorValue;
  };

  function renderConsole() {
    var el = document.getElementById("tjvm-console");
    if (!el) {
      return;
    }
    el.innerHTML = consoleLines.length
      ? consoleLines.map(escapeHtml).join("<br>")
      : '<span class="muted">(no output yet)</span>';
    el.scrollTop = el.scrollHeight;
  }

  function renderPins() {
    var el = document.getElementById("tjvm-pins");
    if (!el) {
      return;
    }
    var pins = {};
    Object.keys(pinModes).forEach(function (p) { pins[p] = true; });
    Object.keys(pinValues).forEach(function (p) { pins[p] = true; });
    var rows = Object.keys(pins).sort().map(function (p) {
      var mode = pinModes[p];
      var value = pinValues[p];
      return "pin " + escapeHtml(p)
        + ": mode=" + (mode === undefined ? "?" : escapeHtml(mode))
        + " value=" + (value === undefined ? "?" : escapeHtml(value));
    });
    el.innerHTML = rows.length ? rows.join("<br>") : '<span class="muted">no GPIO activity yet</span>';
  }

  function renderState() {
    if (!moduleInstance) {
      return;
    }
    var ipEl = document.getElementById("tjvm-ip");
    var statusEl = document.getElementById("tjvm-status");
    var stackEl = document.getElementById("tjvm-stack");
    var localsEl = document.getElementById("tjvm-locals");

    var statusStrPtr = moduleInstance._tinyjvm_status_string();
    var statusStr = moduleInstance.UTF8ToString(statusStrPtr);

    if (ipEl) {
      ipEl.textContent = String(moduleInstance._tinyjvm_get_ip());
    }
    if (statusEl) {
      statusEl.textContent = statusStr;
    }

    var sp = moduleInstance._tinyjvm_get_sp();
    var stackVals = [];
    for (var i = 0; i < sp; i++) {
      stackVals.push(moduleInstance._tinyjvm_get_stack_value(i));
    }
    if (stackEl) {
      stackEl.textContent = stackVals.length ? stackVals.join(", ") : "(empty)";
    }

    // Only the first 8 local slots -- plenty for these sample programs,
    // and the point is to show "locals are a thing," not enumerate all
    // 256 possible slots most of which are unused.
    var localVals = [];
    for (var slot = 0; slot < 8; slot++) {
      localVals.push(moduleInstance._tinyjvm_get_local(slot));
    }
    if (localsEl) {
      localsEl.textContent = localVals.join(", ") + " ...";
    }
  }

  function resetDemoState() {
    consoleLines = [];
    pinModes = {};
    pinValues = {};
    renderConsole();
    renderPins();
  }

  function loadSample(name) {
    var path = SAMPLES[name];
    if (!path || !moduleInstance) {
      return;
    }
    fetch(path)
      .then(function (resp) {
        return resp.arrayBuffer();
      })
      .then(function (buf) {
        var bytes = new Uint8Array(buf);
        var maxSize = moduleInstance._tinyjvm_get_buffer_size();
        resetDemoState();
        if (bytes.length > maxSize) {
          appendConsoleLine("error: program is too large for the demo buffer");
          return;
        }
        var ptr = moduleInstance._tinyjvm_get_buffer();
        moduleInstance.HEAPU8.set(bytes, ptr);
        var loadStatus = moduleInstance._tinyjvm_load(bytes.length);
        renderState();
        if (loadStatus !== VM_RUNNING) {
          appendConsoleLine("load error: " + moduleInstance.UTF8ToString(moduleInstance._tinyjvm_status_string()));
        }
      })
      .catch(function () {
        appendConsoleLine("error: could not fetch " + path);
      });
  }

  function step() {
    if (!moduleInstance) {
      return;
    }
    moduleInstance._tinyjvm_step();
    renderState();
  }

  function runBounded() {
    if (!moduleInstance) {
      return;
    }
    var count = 0;
    while (moduleInstance._tinyjvm_get_status() === VM_RUNNING && count < MAX_RUN_STEPS) {
      moduleInstance._tinyjvm_step();
      count++;
    }
    renderState();
    if (moduleInstance._tinyjvm_get_status() === VM_RUNNING) {
      appendConsoleLine(
        "(paused after " + MAX_RUN_STEPS + " steps -- the program is still running; " +
        "click Run again to continue, or Step to go one instruction at a time)");
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var pickerEl = document.getElementById("tjvm-picker");
    if (!pickerEl) {
      return; // this page/build doesn't have the demo markup
    }
    var stepBtn = document.getElementById("tjvm-step-btn");
    var runBtn = document.getElementById("tjvm-run-btn");
    var resetBtn = document.getElementById("tjvm-reset-btn");
    var sensorEl = document.getElementById("tjvm-sensor");
    var statusEl = document.getElementById("tjvm-status");

    if (typeof TinyJvmModule !== "function") {
      if (statusEl) {
        statusEl.textContent = "WASM module failed to load";
      }
      return;
    }

    TinyJvmModule().then(function (Module) {
      moduleInstance = Module;
      loadSample(pickerEl.value);
    });

    pickerEl.addEventListener("change", function () {
      loadSample(pickerEl.value);
    });
    if (stepBtn) {
      stepBtn.addEventListener("click", step);
    }
    if (runBtn) {
      runBtn.addEventListener("click", runBounded);
    }
    if (resetBtn) {
      resetBtn.addEventListener("click", function () {
        loadSample(pickerEl.value);
      });
    }
    if (sensorEl) {
      sensorEl.addEventListener("input", function () {
        sensorValue = parseInt(sensorEl.value, 10) || 0;
      });
    }
  });
})();
