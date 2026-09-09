# `/family` — Visual Design Record (v2: Daylight Glass)

The design authority for the `/family` section. **This replaces v1.** Behaviour,
routes, content, data, and the six screens are unchanged; the visual world is
fully replaced.

> **Status:** built into `app/static/family/family.css` + `_layout.html` (fonts
> swapped to Bricolage Grotesque + Figtree; `@media (prefers-color-scheme: dark)`
> removed — unconditionally light). Class names carried over, so no screen
> template changed. The liquid-glass material is a layered stack: a faint
> in-fill diagonal tint (so it reads as tinted glass even where the ground is
> pale), `backdrop-filter: blur(32px) saturate(220%)`, a white rim + darker
> bottom/right edge, a top-left radial sheen (`::before`), a warm bloom
> corner-catch (`::after`), and a real elevation shadow. The ground is five CSS
> colour blooms + an SVG trellis + fine turbulence grain (the high-frequency
> layer the blur needs). **It reads glassiest on a phone**, where a pane fills
> the column width over the full coloured ground; a desktop letterboxed view
> undersells it. Open for tuning against the owner's on-device look.

Mode: **Operate** (the household completes tasks) with an Experience opening on
unlock + home. Hand-translated into `app/static/family/family.css` +
`app/templates/family/*` (Flask/Jinja, hand-written CSS, no build step).

## Why v1 was replaced

1. **It went dark.** v1 followed `prefers-color-scheme`; on a dark-mode device it
   rendered a deep-forest ground. The brief is *bright* — solarpunk in full
   daylight. **v2 is unconditionally light.** A single deliberate `[data-theme="dusk"]`
   is provided but it is a warm evening light, never a dark UI, and nothing
   auto-switches to it.
2. **The glass was invisible.** v1's tiles were a translucent fill over a nearly
   flat ground — nothing to refract, so they read as flat panels. **v2 makes
   liquid glass the signature material** and, crucially, gives it a *luminous,
   coloured, layered ground* to catch. Glass without a ground worth blurring is
   just a rectangle.

## Direction contract

Transcribed verbatim into `family/_layout.html` as the first child of `<body>`.

- **THESIS:** A household's sunroom rendered in glass. Every surface is a frosted,
  light-catching pane floating over a garden of soft coloured light. Solarpunk in
  full daylight — living greens and blooms diffused through glass, organised by a
  fine botanical line. Refuses both the flat "wellness app" card and the dark
  glassmorphism cliché.
- **OWN-WORLD:** A luminous ground — overlapping blurred blooms of leaf, sky,
  marigold and dawn over warm white — under genuinely multi-layer glass: heavy
  blur, lifted saturation, a bright specular top edge, an inner contact shadow, a
  soft outer glow, a hairline refractive border. Greens in three registers, one
  marigold bloom accent, a quiet sky. Fraunces-free display in **Bricolage
  Grotesque**, body in **Figtree**. Botanical-line icons. Growth motion — panes
  bloom and settle, they don't slide.
- **STORY:** Two people (Nelson + Sav) unlock one glass door, land in a bright
  home showing today and the list, move between Calendar / Grocery / Hera by a
  floating glass tab bar. Every thing they add carries a small coloured seed
  marking who added it.
- **FIRST VIEWPORT (home):** Over the glowing garden ground: a greeting pane
  (Bricolage, the date large), a **Today** glass pane, an **Open order** glass
  pane, a low **Ask Hera** pane. A floating glass tab bar clears the bottom.
- **FORM:** daylight glassmorphism / visionOS-material vocabulary in a solarpunk
  palette — brief-pinned, rendered at full commitment. No seed key: the world was
  pinned by the brief and the roll was skipped per the pinned-direction rule.
- **FINISH:** unreviewed and undocumented is unfinished; this build ends with the
  finish review, the verdict, DESIGN.md, and every shipping raster carrying its
  provenance.

---

## The ground (this is what makes the glass work)

`body.family` paints a **fixed, non-scrolling luminous field**, built entirely in
CSS (no image files — CSP-safe, zero requests):

```
background-color: var(--paper);              /* #fbfaf4 warm white */
background-image:
  radial-gradient(42vmax 42vmax at 12% 18%,  color-mix(in oklab, var(--leaf)   40%, transparent) 0%, transparent 60%),
  radial-gradient(38vmax 38vmax at 88% 12%,  color-mix(in oklab, var(--sky)    38%, transparent) 0%, transparent 62%),
  radial-gradient(46vmax 46vmax at 78% 88%,  color-mix(in oklab, var(--bloom)  34%, transparent) 0%, transparent 60%),
  radial-gradient(34vmax 34vmax at 8%  92%,  color-mix(in oklab, var(--dawn)   40%, transparent) 0%, transparent 60%),
  var(--trellis);                            /* faint SVG data-URI lattice, ~4% */
background-attachment: fixed;
```

- Four large, soft, overlapping colour blooms + a faint botanical lattice. On a
  414px phone these read as a gentle aurora of greens/gold/pink behind
  everything. This is the layer the glass blurs.
- A `body::before` adds one more slow-moving highlight (`background:
  radial-gradient(...); animation: drift 24s ease-in-out infinite alternate;`
  translating ±3%) so the ground has life; killed under
  `prefers-reduced-motion`.
- Contrast: all text sits on glass, never directly on the ground, so WCAG is
  measured against the glass fill (opaque-enough — see below), not the aurora.

`--trellis` data-URI (single colour, tinted by opacity):
`url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='300'%3E%3Cg fill='none' stroke='%232f6b45' stroke-width='1' opacity='0.5'%3E%3Cpath d='M0 150 150 0 300 150 150 300Z'/%3E%3Cpath d='M150 0v300M0 150h300'/%3E%3C/g%3E%3C/svg%3E")`
applied at `opacity` via a `body::after` layer at ~0.05, or baked at low alpha.

---

## Liquid glass — the material spec

One material, three weights. Every panel is a real stack, not a single rule.

### `--glass` (the standard pane)

```
background: color-mix(in oklab, var(--paper) 62%, transparent);   /* NOT pure white — tints toward warm */
-webkit-backdrop-filter: blur(28px) saturate(180%) brightness(1.06);
backdrop-filter:         blur(28px) saturate(180%) brightness(1.06);
border: 1px solid transparent;
border-radius: 26px;
box-shadow:
  0 1px 0 0 rgba(255,255,255,0.75) inset,                /* specular top edge */
  0 -1px 0 0 color-mix(in oklab, var(--leaf) 22%, transparent) inset,  /* faint bottom refraction */
  0 12px 40px -12px rgba(31, 74, 51, 0.28),              /* soft cast shadow */
  0 0 0 1px color-mix(in oklab, var(--leaf-deep) 10%, transparent);    /* hairline */
```

Plus a **refractive border** drawn as a masked gradient (so the rim catches light
top-left and colour bottom-right):

```
.glass { position: relative; }
.glass::after {
  content: ""; position: absolute; inset: 0; border-radius: inherit; padding: 1px;
  background: linear-gradient(135deg,
    rgba(255,255,255,0.9) 0%,
    transparent 40%,
    color-mix(in oklab, var(--bloom) 45%, transparent) 100%);
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor; mask-composite: exclude;
  pointer-events: none;
}
```

And a **top sheen** (the "wet" highlight): a `::before` with
`background: linear-gradient(180deg, rgba(255,255,255,0.55), transparent 55%);
mix-blend-mode: screen; height: 46%;`.

### `--glass-raised` (composer, tab bar, unlock card, primary CTA surfaces)

Same, but `blur(40px)`, `background` alpha ~72%, a stronger cast shadow
(`0 20px 60px -16px rgba(31,74,51,0.34)`), and a brighter specular edge. This is
the "closer to the viewer" weight.

### `--glass-quiet` (nested rows inside a pane, history items)

`background: color-mix(in oklab, var(--paper) 40%, transparent); backdrop-filter:
blur(14px); box-shadow: 0 1px 0 rgba(255,255,255,0.5) inset;` — no border
gradient, no sheen. Used so a pane can hold sub-rows without stacking full glass
on full glass.

### `@supports` fallback (Firefox older, `backdrop-filter` off)

```
@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .glass, .glass-raised { background: color-mix(in oklab, var(--paper) 92%, transparent); }
  .glass-quiet { background: color-mix(in oklab, var(--paper) 80%, transparent); }
}
```
The ground still shows around the panes, so it doesn't collapse to flat white.

### Rules for using glass

- **Never stack `--glass` on `--glass`.** A pane's internal divisions use the
  botanical hairline or `--glass-quiet`.
- **Text lives on glass, never on the raw ground.** Even the greeting sits in a
  borderless glass "shelf".
- **Max blur 40px, one drift animation, no per-element parallax.** Glass is
  expensive; keep it to the panes and the tab bar.

---

## Palette (light — the only default)

| token | value | role |
|---|---|---|
| `--paper` | `#fbfaf4` | the warm-white base the ground and glass tint from |
| `--leaf` | `#3f8a53` | primary green — links, active state, secondary action, `--m1` |
| `--leaf-deep` | `#1f4a33` | text on glass, headings, botanical line |
| `--leaf-bright` | `#84c559` | new-growth — focus rings, small highlights |
| `--bloom` | `#ef8a3c` | marigold accent — today, the "got" check, primary CTA, `--m2` |
| `--bloom-ink` | `#7a3d12` | text on a bloom fill |
| `--sky` | `#5fa7c9` | quiet metadata, info chips |
| `--dawn` | `#f2b8c6` | ground-only pink bloom (never a UI colour) |
| `--soil` | `#22331f` | primary text (on glass) |
| `--soil-soft` | `#4c5f48` | secondary text |
| `--rule` | `color-mix(in oklab, var(--leaf-deep) 16%, transparent)` | hairline |
| `--m1` | `#3f8a53` | Nelson — attribution seed |
| `--m2` | `#ef8a3c` | Sav — attribution seed |

**`[data-theme="dusk"]`** (opt-in only, e.g. a future toggle): `--paper #f4efe4`,
the ground blooms shift warmer and ~10% more saturated, glass alpha +6%, text
unchanged. It is *warm evening daylight*, still light. There is **no**
`@media (prefers-color-scheme: dark)` block in v2.

## Type

`https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=Figtree:wght@400;500;600&display=swap`

| use | face | notes |
|---|---|---|
| screen titles, unlock wordmark, greeting date, big numerals | **Bricolage Grotesque** 500 / 700 | a warm, slightly quirky humanist grotesque with real character and an optical-size axis — displays large beautifully, not on the AI-default list. Fallback `"Bricolage Grotesque", "Hanken Grotesk", "Trebuchet MS", sans-serif`. |
| body, labels, buttons, calendar cells, chat | **Figtree** 400 / 500 / 600 | clean, friendly, excellent small-size legibility on mobile. Fallback `"Figtree", -apple-system, "Segoe UI", system-ui, sans-serif`. |
| calendar numerals, quantities | Figtree + `font-variant-numeric: lining-nums tabular-nums` | |

Scale (16px root, mobile-first): `--fs-xs .8125rem` · `--fs-sm .875rem` ·
`--fs-base 1rem` · `--fs-md 1.125rem` · `--fs-lg 1.375rem` · `--fs-h2 1.75rem` ·
`--fs-h1 2.375rem`. Line-height 1.55 body, 1.1 display. `family.css` sets its own
`html { font-size: 16px }` and `overflow-x: hidden`.

## Spacing / radii / motion

- Spacing 4px base: `--s1 4 · --s2 8 · --s3 12 · --s4 16 · --s5 24 · --s6 32 · --s7 48`.
  Content column `max-width: 32rem`, centred, `padding: var(--s5)`,
  `padding-bottom: calc(72px + env(safe-area-inset-bottom) + var(--s5))` to clear
  the floating tab bar.
- Radii: `--r-pane 26px` · `--r-ctl 16px` · `--r-pill 999px`. Generous — glass
  wants soft corners.
- Motion: `--ease: cubic-bezier(0.32, 0.72, 0, 1)` (an iOS-ish glassy settle),
  `--dur: 220ms`.
  - **pane-bloom** (a pane entering / a new row): `opacity 0→1` +
    `transform: scale(0.96) translateY(6px) → none` + `filter: blur(6px) → none`,
    280ms. The blur-in is the "condensing glass" feel.
  - **check-bloom** (grocery got): checkbox `scale(0.7→1)` + a one-shot 6-petal
    SVG burst in `--bloom`, 420ms.
  - **tab-glide**: the active tab's glass pill translates along the bar, 260ms `--ease`.
  - **drift**: the ground highlight, 24s, alternate.
  - All disabled under `@media (prefers-reduced-motion: reduce)`.

---

## Component inventory

| component | spec |
|---|---|
| **Glass pane** (`.glass`) | the material above. Title in Bricolage `--fs-lg` `--leaf-deep`; `--s3` under it a botanical hairline; then content. |
| **Botanical hairline + node** | 1px `--rule`; right end terminates in a small SVG bud in `--leaf`. Divides content inside and between panes. |
| **Greeting shelf** | a borderless `.glass` (no shadow, no border gradient) so the greeting reads as floating text on frosted air, not a card. |
| **Pill button** | `--r-pill`, `--fs-base`. Primary: `--bloom` fill, `--bloom-ink` text, `box-shadow: 0 1px 0 rgba(255,255,255,.4) inset, 0 8px 20px -8px color-mix(in oklab, var(--bloom) 60%, transparent)`. Secondary: `.glass-quiet` fill + 1.5px `--leaf` border + `--leaf-deep` text. Pressed: `scale(.97)`. |
| **Text input** | `.glass-quiet` fill, 1px `--rule`, `--r-ctl`; focus → 2px `--leaf-bright` ring + the fill brightens. |
| **Attribution seed** | a 9px rounded-triangle (a seed shape, not a plain dot) in `--m1`/`--m2`, with a 1px white inner edge so it reads on glass. |
| **Checklist row** (grocery) | full-width tap target; leading 22px glass checkbox (1.5px `--leaf` ring; checked → `--bloom` fill + white check + petal burst). Got rows: `--soil-soft`, strikethrough, sink below unchecked, fill fades to `--glass-quiet`. |
| **Calendar day cell** | `aspect-ratio: 1`; numeral top-left (Figtree lining/tabular). Today: numeral gets a `--bloom` filled circle. Has-events: up to 3 attribution seeds along the bottom; overflow → "+N" `--soil-soft`. Out-of-month: `opacity: .35`. The whole grid sits in one `.glass` pane; cells are `--glass-quiet` on hover/active only. |
| **Event chip** | `.glass-quiet`, `--r-ctl`, a 3px left bar in the creator's `--m*`, time (`--fs-xs --sky`) + title (`--fs-sm --soil`). |
| **Chat bubble** | max-width 86%. Hera: `.glass` with a small drawn leaf-"H" node before the first line, left. User: `.glass` tinted +12% toward `--leaf`, right. Markdown via the allowlist renderer (escape-first) already in `family.js`. |
| **Member picker** (chat) | a `.glass-raised` pill: `--m*` seed + name + chevron; the native `<select>` is transparent on top. |
| **Floating tab bar** | **not** edge-to-edge — a `.glass-raised` capsule, `position: fixed; left/right: var(--s4); bottom: calc(env(safe-area-inset-bottom) + var(--s3))`, `border-radius: var(--r-pill)`, height 64px, 4 items. Active: a `--bloom`-filled icon + a `.glass-quiet` pill that **glides** behind it (`tab-glide`). Inactive: `--soil-soft` line icon. Icons inline SVG, one botanical-line grammar (leaf / trellis / basket / petal-bubble). |
| **Unlock card** | one centred `.glass-raised` pane on the full garden ground. "Family" wordmark Bricolage `--fs-h1` `--leaf-deep`, a line of `--soil-soft` copy, the password input, a primary pill "Come in". `{% if error %}` → `--bloom` line; `{% if unavailable %}` → calm message, no field. |

---

## Screen specs

### Unlock — `family/unlock.html` (standalone)

Full-viewport garden ground. Vertically-centred **Unlock card**. No tab bar.

### Home — `family/home.html`

1. **Greeting shelf** — "Good {morning|afternoon|evening}," Figtree `--fs-md`
   `--soil-soft`; the date Bricolage `--fs-h2` `--leaf-deep`. On the right, the
   two-**seed** member toggle (the active one filled + a faint `--leaf` ring).
2. **Today** `.glass` pane — heading, hairline, today's events as Event chips;
   empty → "Nothing on the calendar today." Tapping the heading → `/family/calendar`.
3. **Groceries** `.glass` pane — heading + count, hairline, first ~4 unchecked
   item names, a secondary pill "Open list".
4. **Ask Hera** low borderless glass row — leaf-"H", "Ask Hera something",
   chevron → `/family/chat`.
5. Floating tab bar, Home active.

### Calendar — `family/calendar.html`

1. **Month header** — `‹  September 2026  ›` Bricolage `--fs-lg`; chevrons are
   botanical-line arrows; `?month=` links.
2. **Month grid** `.glass` pane — weekday initials row, then the weeks of day
   cells (recurring events expanded to seeds; today's numeral in a bloom
   circle). Tap a day → `/family/calendar/day/<iso>`.
3. **Coming up** `.glass` pane — `upcoming(10)` as rows: date (Figtree lining,
   `--leaf-deep`) + Event chip; recurring gets a small ↻.
4. **Add an event** — a `<details>` `.glass` pane: title, date, all-day toggle,
   start/end time, note, and a Repeat row (`none / weekly / monthly` → weekday
   chips + until). Attributes to the active member.
5. Tab bar, Calendar active.

### Grocery — `family/grocery.html`

1. **Current order** `.glass` pane — heading = order title + count; hairline; an
   add-item row (name, qty, `+`); then Checklist rows (unchecked first, checked
   sinking with the petal burst; each row shows the creator seed).
2. **Order actions** — two secondary pills: "Start new order", "Archive this order".
3. **Past orders** `.glass` pane — each archived order a `<details>` on
   `--glass-quiet`: date + count; expanded shows its items (read-only) and a
   primary pill "Copy forward".
4. Tab bar, Grocery active.

### Chat — `family/chat.html`

1. **Header** — leaf-"H" + "Hera" (Bricolage `--fs-h2`); on the right the
   member-picker `.glass-raised` pill. Botanical hairline under.
2. **Thread** — scrolling column of Chat bubbles for the selected member,
   newest pinned bottom. Empty → a short in-character Hera line as a static
   bubble.
3. **Composer** — a `.glass-raised` capsule fixed above the tab bar: a growing
   input + a send button (botanical arrow; `--bloom` when the field has text).
   Enter sends, Shift+Enter newlines. Awaiting reply → a three-dot bloom pulse
   in a Hera bubble.
4. Switching the picker reloads the thread (`?member=<slug>`).
5. Tab bar, Chat active.

### 404 — `family/404.html`

Garden ground, one small `.glass` pane — "This corner of the garden doesn't
exist." + a pill back to `/family/home`. No tab bar.

---

## Assets

**None as files.** The ground is CSS gradients; the trellis is an inline
data-URI; all icons are inline SVG in `family/_icons.html`. Favicon stays
`family/img/favicon.svg` (a leaf + bloom mark). No photography, no CDN images.

## Build notes for the hand-translation

- Rewrite `app/static/family/family.css` top-to-bottom from this record: drop the
  `@media (prefers-color-scheme: dark)` and `:root[data-theme="dark"]` blocks;
  add `[data-theme="dusk"]`; replace the tile rules with the three glass weights;
  add the ground layers on `body.family` + `body::before` drift.
- `_layout.html`: swap the font link to Bricolage + Figtree; the tab bar becomes
  a floating capsule (`.fam-tabs` restyle only — same markup); update the
  direction-contract comment to this v2 THESIS/OWN-WORLD/etc.
- Templates: class names carry over (`.fam-tile` → keep the name but it now
  renders as `.glass`; or rename to `.fam-glass` and update the 6 templates).
  Prefer keeping `.fam-tile` / `.fam-rule` / `.fam-btn` names to minimise
  template churn — only the CSS changes.
- After the rewrite: restart the dev server, view every screen at 414px width in
  **light** mode (force `prefers-color-scheme: light` in the browser), run the
  impeccable detector once, screenshot desktop + mobile, then the finish review.
