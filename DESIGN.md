---
name: Nelson Koskela — Portfolio (Home: Live Systems Topology)
description: A recruiter-facing engineering portfolio where the home page renders as a live wiring diagram of the real system, not a hero-and-card-grid landing page.
colors:
  topo-bg: "#0d1117"
  topo-bg-grid: "rgba(201, 209, 217, 0.045)"
  topo-line: "rgba(58, 160, 255, 0.28)"
  topo-line-active: "#3aa0ff"
  topo-node-bg: "#12181f"
  topo-node-border: "#232b34"
  topo-text: "#c9d1d9"
  topo-text-dim: "#9aa8bb"
  topo-live: "#3fb950"
  topo-live-dim: "rgba(63, 185, 80, 0.16)"
typography:
  display:
    fontFamily: "Archivo, Space Grotesk, Helvetica, sans-serif"
    fontSize: "clamp(1.9rem, 3.4vw, 2.75rem)"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "normal"
  body:
    fontFamily: "Inter, Source Sans Pro, Helvetica, sans-serif"
    fontSize: "0.95rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "Inter, Source Sans Pro, Helvetica, sans-serif"
    fontSize: "0.85rem"
    fontWeight: 400
    lineHeight: 1.2
    letterSpacing: "0.02em"
  mono:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "0.72rem"
    fontWeight: 400
    lineHeight: 1.3
    letterSpacing: "0.01em"
rounded:
  node: "3px"
  tag: "2px"
spacing:
  xs: "0.5rem"
  sm: "0.7rem"
  md: "0.9rem"
  lg: "2.5rem"
components:
  node-branch:
    backgroundColor: "{colors.topo-node-bg}"
    textColor: "{colors.topo-text}"
    rounded: "{rounded.node}"
    padding: "0.6rem 0.9rem"
  node-branch-hover:
    backgroundColor: "{colors.topo-node-bg}"
    textColor: "{colors.topo-line-active}"
    rounded: "{rounded.node}"
    padding: "0.6rem 0.9rem"
  node-leaf:
    backgroundColor: "{colors.topo-node-bg}"
    textColor: "{colors.topo-text}"
    rounded: "{rounded.node}"
    padding: "0.65rem 0.9rem"
  node-action:
    backgroundColor: "{colors.topo-node-bg}"
    textColor: "{colors.topo-live}"
    rounded: "{rounded.node}"
    padding: "0.6rem 0.9rem"
---

# Design System: Nelson Koskela — Portfolio (Home: Live Systems Topology)

## Overview

**Creative North Star: "The Live Systems Topology"**

The home page is drawn as the actual wiring diagram of the site: an identity node connects by curved edges to nav-branch nodes and to project leaf-nodes, one edge is marked shared infrastructure between two real projects, and one leaf polls a real endpoint for a live number. The thesis is explicit in the shipped source: the homepage *is* the system topology, not a hero followed by a project grid. Nothing on the page is decorative wiring — every edge and every "live" marker corresponds to something actually true about the deployed system (the Pipeline World ↔ SRE Infra Layer shared-Redis edge, the network-sniffer's polled request count).

This world is deliberately dark, blueprint-flat, and technical: a near-black slate ground with a faint grid, dim structural-blue connective lines that brighten only on hover/focus, and a single reserved green used exclusively for "this is genuinely live right now" — never for decoration or emphasis elsewhere. Ground color and structural blue were chosen to continue the site's existing hand-drawn project icon fills (`#0d1117` / `#3aa0ff`) rather than inventing an unrelated palette.

**This world currently governs one surface only.** `home.css` is scoped entirely under `body.page-home`; every other template (About, Projects, Documentation, Contact, individual project pages) still renders the prior HTML5-UP "Dimension" dark theme (`assets/css/main.css` + `css/custom.css`) untouched. Two small shared edits reach outside the home page: a `{% block body_class %}` hook in `base.html` (so `page-home` can scope the new world), and the header's mobile nav-toggle icon, which was changed site-wide from a raw Unicode hamburger glyph (`&#9776;`) to an authored inline SVG — a fix, not an extension of the topology world's visual language, and it carries no topology tokens (no blue, no mono type).

**Key Characteristics:**
- Wiring-diagram structure: nodes and SVG-drawn curved edges, not cards in a grid
- Near-black blueprint ground with a faint 40px grid, not a gradient or photographic hero
- One color (signal green) reserved strictly for genuinely-live state, never decorative
- Three-family type system with a clear division of labor: display for the identity heading, body for prose, mono for every live/technical/count readout
- Precise, non-decorative reactivity: hover/focus brightens only the edges connected to that node

## Colors

Ground-first, restrained-accent palette: one near-black base, one structural blue used at two intensities (dim at rest, bright on interaction), and one signal green reserved for a single live-state meaning.

### Primary
- **Structural Blue** (`--topo-line-active`, #3aa0ff): the interactive/active signal. Used on active SVG edges, node border on hover/focus, brand icon, footer links. At rest, edges use a dimmed version of the same hue (`--topo-line`, rgba(58,160,255,0.28)) so the graph reads as present-but-quiet until a node is engaged.

### Tertiary
- **Signal Green** (`--topo-live`, #3fb950, with a 16%-alpha wash `--topo-live-dim`): reserved exclusively for the one leaf-node (network-sniffer) that is genuinely polling a live endpoint — its border, pulse dot, and live-readout text. Never reused for success states, confirmations, or other emphasis.

### Neutral
- **Blueprint Slate** (`--topo-bg`, #0d1117): page ground, continued from the existing hand-drawn project icon fills rather than invented fresh.
- **Node Surface** (`--topo-node-bg`, #12181f): background of every node (branch links, leaf links).
- **Node Border** (`--topo-node-border`, #232b34): resting border on all nodes; also the mobile vertical-spine rule.
- **Primary Text** (`--topo-text`, #c9d1d9): headline, node labels, primary body copy.
- **Dim Text** (`--topo-text-dim`, #9aa8bb): secondary copy — subhead, annotation line, tag/count text, edge notes, nav links at rest. Blue-tinted, not flat gray: measures ~7.7:1 against both `--topo-bg` and `--topo-node-bg`, replacing an earlier flat gray (#6e7681) that measured ~3.9–4.1:1 and failed the 4.5:1 floor.
- **Grid Line** (`--topo-bg-grid`, rgba(201,209,217,0.045)): the faint background grid, barely-there texture rather than a visible rule.

### Named Rules
**The One Live Color Rule.** Signal green (`--topo-live`) marks exactly one thing on the page: a leaf-node whose readout is backed by a real, currently-polled endpoint. It is never applied to a node, button, or state that isn't backed by live data — reserving it is what makes it legible as a signal rather than decoration.

**The Never-Flat-Gray Rule.** Secondary/dim text is never a flat, unsaturated gray. `--topo-text-dim` is tinted from the world's structural blue (#9aa8bb) specifically so it clears WCAG AA (4.5:1) at every size it's used — this was a shipped contrast fix, not a stylistic preference, and applies to any future secondary-text token in this world.

## Typography

**Display Font:** Archivo (with Space Grotesk, Helvetica, sans-serif fallback)
**Body Font:** Inter (with Source Sans Pro, Helvetica, sans-serif fallback)
**Label/Mono Font:** JetBrains Mono (with ui-monospace, monospace fallback)

**Character:** A technical-editorial pairing — Archivo carries the one display moment (the name/identity node) with geometric weight, Inter carries readable prose, and JetBrains Mono is used everywhere something is a literal system value (a count, a tag, a live readout, an edge annotation) rather than prose. The mono/body split is semantic, not just decorative: mono means "this is data," body means "this is written."

### Hierarchy
- **Display** (700, `clamp(1.9rem, 3.4vw, 2.75rem)`, line-height 1.05): the identity node's `<h1>` only — one use per page.
- **Body** (400, 0.95rem, line-height 1.5, max-width 34ch): the identity subhead paragraph — the only extended prose on the page.
- **Label** (400, ~0.85–0.92rem, letter-spacing 0.02em): nav-branch and leaf-node link labels.
- **Mono/annotation** (400, 0.72rem, letter-spacing 0.01em): every live/technical/count value — the annotation line under the subhead, project tags, project counts, the live readout, the shared-infrastructure edge note. Consistently the smallest text on the page, marking it as a secondary technical signal rather than a heading.

### Named Rules
**The Mono-Means-Data Rule.** Any text rendering a literal count, tag, live value, or infrastructure fact is set in JetBrains Mono, never in the body or display face. Prose stays in Inter or Archivo. This is the page's only typographic signal for "this number/fact is real and current."

## Layout

Three-column CSS grid on desktop (`minmax(220px, 300px) minmax(170px, 240px) minmax(260px, 1fr)`): identity node, nav-branch column, project-leaves column, with an SVG overlay (`.topo-lines`, absolutely positioned, `inset: 0`) drawing curved connective edges between them at runtime via JS (`home.js` measures live `getBoundingClientRect()` positions and redraws on resize/font-load). Section padding scales with viewport (`clamp(2.5rem, 6vw, 4.5rem)` vertical), gap uses a fixed 2.5rem row gap against a fluid `clamp(2rem, 6vw, 6rem)` column gap. `main#content` in this world drops the site's default max-width/padding entirely (`max-width: none; padding: 0`) so the topology can use the full viewport width.

**Responsive collapse at 900px:** the three-column grid becomes a single flex column; the SVG edge-drawing layer is hidden entirely (`.topo-lines { display: none }` — `home.js` also short-circuits and clears the SVG under this breakpoint) and the branch/leaf lists instead get a plain 1px left border (`--topo-node-border`) with left padding, forming a vertical spine in place of drawn curves. This is a deliberate two-mode system, not a naive reflow: desktop gets true wiring, mobile gets an equivalent-meaning simplified spine.

## Elevation & Depth

Flat by construction — no box-shadows anywhere in this world. Depth and hierarchy are conveyed entirely through the grid background texture (a faint 40px two-axis line grid at 4.5% opacity), border color state (`--topo-node-border` at rest → `--topo-line-active` on hover/focus), and z-layering (`isolation: isolate` on the hero, nodes at `z-index: 1` above the `z-index: 0` SVG edge layer). This reads as a blueprint/schematic rather than a card-based UI with lifted surfaces.

### Named Rules
**The Flat-Wiring Rule.** No shadows, no elevation gradients. A node's state (resting / interactive / live) is shown only by border color and text color, matching the blueprint-diagram metaphor rather than a card-lifting metaphor.

## Shapes

Nodes are near-rectangular with a small, consistent radius (3px) — enough to soften the block without reading as a rounded "card." Tags/pills use an even tighter radius (2px). Borders are always 1px, always `--topo-node-border` at rest. There is no pill-shaped or fully-rounded element anywhere in this world; the geometry stays close to right angles, consistent with a schematic/diagram reading rather than a soft consumer-app one.

## Components

### Navigation (nav-branch nodes)
- **Shape:** 1px bordered rectangle, 3px radius, `--topo-node-bg` background, 0.6rem/0.9rem padding.
- **Default:** `--topo-text` label, `--topo-node-border` border.
- **Hover / Focus-visible:** border brightens to `--topo-line-active`; the SVG edge connected to that node also brightens to `--topo-line-active` (edges and nodes react together, driven by shared `data-node` ids in `home.js`).
- **Action variant (Contact):** border and text render in `--topo-live` green at rest (not just on hover), with an arrow glyph that translates 3px on hover/focus — the one node styled as an explicit call-to-action.

### Leaf nodes (projects)
- **Shape:** same node treatment as branch nodes (1px border, 3px radius, `--topo-node-bg`), flex-wrapped to hold an icon, label, and mono tag.
- **Icon:** the site's existing hand-drawn SVG project icons (`img.topo-icon`, 20×20, opacity 0.9) — continuing the pre-existing hand-crafted-icon commitment, not new iconography.
- **Live variant:** border rendered in `--topo-live-dim` (a soft green wash, not the full-saturation green used on the action node); carries a 7px pulsing dot (`@keyframes topo-pulse`, 2.4s, opacity 1→0.35, disabled under `prefers-reduced-motion`) and a mono live-readout text node populated by a real fetch to the project's own analytics endpoint, polled every 5s.

### Signature component: Topology Edges
Curved SVG paths (`M...C...` cubic Béziers) drawn between node bounding-box edges at runtime, not authored as static decoration. Three edge types: root→branch, branch(projects)→leaf, and one special same-column "side loop" edge that bulges outward past the leaves list to connect Pipeline World and SRE Infra Layer without crossing through unrelated nodes — rendered dashed (`stroke-dasharray: 3 4`) and colored `--topo-text-dim` to read as "documented relationship" rather than a live/interactive edge. All edges default to `--topo-line` (dim) and brighten to `--topo-line-active` only while their associated node has hover or focus.

## Do's and Don'ts

### Do:
- **Do** reserve `--topo-live` green for state that is backed by a real, currently-polled value — never apply it as decoration or generic emphasis.
- **Do** set any literal count, tag, or live/technical value in JetBrains Mono; keep prose in Inter and the single display heading in Archivo.
- **Do** keep secondary text on a blue-tinted dim color (`--topo-text-dim`, #9aa8bb) rather than a flat gray, to hold the ~7.7:1 contrast floor this world requires.
- **Do** scope any topology-world CSS under `body.page-home`; this world has not been extended to other pages and must not leak into them via unscoped selectors.
- **Do** draw relationships between nodes as real, verifiable facts (shared infrastructure, live polling) — an edge or live marker with nothing true behind it breaks the page's core claim.

### Don't:
- **Don't** add a kicker/eyebrow label above the H1. One was present in an earlier draft and was removed by finish review as a banned device; the fact it carried was folded into the subhead paragraph instead. Not part of this system.
- **Don't** add a "by the numbers" stat-grid section. One was built and removed by finish review as a generic hero-metric-template pattern; the underlying facts belong in the single-line mono annotation (`.topo-annot-line`) under the subhead, never a grid of stat tiles.
- **Don't** use box-shadows or lifted-card treatments anywhere in this world; depth is conveyed by border-color state and the background grid only.
- **Don't** use a raw Unicode glyph for icons in the shared header/nav; the mobile nav-toggle now uses an authored inline SVG. This is a site-wide fix inherited from this build, independent of the topology palette.
- **Don't** extend the topology palette, type ramp, or component styles to other pages without a corresponding rebuild pass — About, Projects, Documentation, and Contact are still on the prior "Dimension" theme, and mixing tokens without scoping would break both systems.
