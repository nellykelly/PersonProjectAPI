/* Live systems topology: draws the real edges between the diagram's
   nodes and polls the network-sniffer's own analytics endpoint so the
   "live" leaf shows a genuinely current number, not a decorative one.
   Runs only on the home page (main/index.html); mobile collapses the
   graph to a plain vertical list in CSS and this script no-ops there. */
(function () {
    "use strict";

    var hero = document.querySelector(".topo-hero");
    if (!hero) return;

    var svg = hero.querySelector(".topo-lines");
    var root = hero.querySelector('[data-node="root"]');
    var branchNodes = hero.querySelectorAll(".topo-branch .topo-node");
    var projectsNode = hero.querySelector('.topo-branch [data-node="projects"]');
    var leaves = hero.querySelectorAll(".topo-leaves .topo-node");

    function isMobileLayout() {
        return window.matchMedia("(max-width: 900px)").matches;
    }

    function pointOf(el, containerRect, side) {
        var r = el.getBoundingClientRect();
        return {
            x: side === "left" ? r.left - containerRect.left : r.right - containerRect.left,
            y: r.top - containerRect.top + r.height / 2,
        };
    }

    function curve(a, b) {
        var midX = (a.x + b.x) / 2;
        return "M " + a.x + " " + a.y + " C " + midX + " " + a.y + ", " + midX + " " + b.y + ", " + b.x + " " + b.y;
    }

    function sideLoop(a, b, containerRect) {
        // Both nodes sit in the same column; arc out past the right
        // edge of the leaves list rather than crossing through it.
        var bulge = containerRect.width - Math.max(a.x, b.x) > 60 ? 46 : 26;
        var x1 = a.x + bulge;
        var x2 = b.x + bulge;
        return "M " + a.x + " " + a.y + " C " + x1 + " " + a.y + ", " + x2 + " " + b.y + ", " + b.x + " " + b.y;
    }

    function draw() {
        if (!svg) return;
        if (isMobileLayout()) {
            svg.innerHTML = "";
            return;
        }
        var containerRect = hero.getBoundingClientRect();
        var frag = document.createDocumentFragment();

        function addPath(d, extraClass, fromId, toId) {
            var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
            path.setAttribute("d", d);
            if (extraClass) path.classList.add(extraClass);
            if (fromId) path.dataset.from = fromId;
            if (toId) path.dataset.to = toId;
            frag.appendChild(path);
        }

        var rootPoint = root ? pointOf(root, containerRect, "right") : null;
        if (rootPoint) {
            branchNodes.forEach(function (node) {
                var p = pointOf(node, containerRect, "left");
                addPath(curve(rootPoint, p), null, "root", node.dataset.node);
            });
        }

        if (projectsNode) {
            var projectsPoint = pointOf(projectsNode, containerRect, "right");
            leaves.forEach(function (leaf) {
                var p = pointOf(leaf, containerRect, "left");
                addPath(curve(projectsPoint, p), null, "projects", leaf.dataset.node);
            });
        }

        var pipeline = hero.querySelector('[data-node="pipeline-world"]');
        var sre = hero.querySelector('[data-node="sre-infra"]');
        if (pipeline && sre) {
            var pA = pointOf(pipeline, containerRect, "right");
            var pB = pointOf(sre, containerRect, "right");
            addPath(sideLoop(pA, pB, containerRect), "is-shared", "pipeline-world", "sre-infra");
        }

        svg.innerHTML = "";
        svg.appendChild(frag);
    }

    function highlight(nodeId, on) {
        if (!svg) return;
        svg.querySelectorAll('path[data-from="' + nodeId + '"], path[data-to="' + nodeId + '"]').forEach(function (p) {
            p.classList.toggle("is-active", on);
        });
    }

    hero.querySelectorAll("[data-node]").forEach(function (el) {
        var id = el.dataset.node;
        el.addEventListener("mouseenter", function () { highlight(id, true); });
        el.addEventListener("mouseleave", function () { highlight(id, false); });
        el.addEventListener("focus", function () { highlight(id, true); });
        el.addEventListener("blur", function () { highlight(id, false); });
    });

    var raf = null;
    function scheduleDraw() {
        if (raf) cancelAnimationFrame(raf);
        raf = requestAnimationFrame(draw);
    }
    window.addEventListener("resize", scheduleDraw);
    window.addEventListener("load", scheduleDraw);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(scheduleDraw);
    scheduleDraw();

    // ---- live readout: real numbers, not decoration ----
    var readout = hero.querySelector("[data-live-readout]");
    if (readout) {
        var endpoint = "/projects/network-sniffer/api/analytics";
        function poll() {
            fetch(endpoint, { headers: { Accept: "application/json" } })
                .then(function (res) { return res.ok ? res.json() : Promise.reject(res.status); })
                .then(function (data) {
                    var p50 = data.inbound_latency && data.inbound_latency.p50;
                    readout.textContent = data.total + " req" + (p50 != null ? " · p50 " + p50 + "ms" : "");
                })
                .catch(function () {
                    readout.textContent = "unavailable";
                });
        }
        poll();
        setInterval(poll, 5000);
    }
})();
