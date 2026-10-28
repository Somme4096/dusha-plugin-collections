---
name: dusha WebUI
description: A companion's insides, read like source code; one WebGL bit field shows the motion.
colors:
  ultramarine: "#2747ff"
  ultramarine-deep: "#1a35d6"
  ultramarine-wash: "#2747ff14"
  on-accent: "#ffffff"
  paper: "#f6f6f4"
  paper-2: "#eeeeeb"
  ink: "#16171a"
  ink-2: "#4d5058"
  ink-3: "#6b6e77"
  rule: "#dededa"
  rule-2: "#c8c8c2"
  state-active: "#19804a"
  state-idle: "#9a6700"
  state-failed: "#cc3517"
  bit-low: "#d4d4cf"
  bit-hot: "#e8462a"
  night-ultramarine: "#8c9bff"
  night-ultramarine-deep: "#a9b4ff"
  night: "#0c0d0f"
  night-2: "#141518"
  night-ink: "#e7e7e4"
  night-ink-2: "#a6a8af"
  night-ink-3: "#868994"
  night-rule: "#222429"
  night-rule-2: "#31343b"
  night-state-active: "#5fcf8c"
  night-state-idle: "#e5b04a"
  night-state-failed: "#ff7a5c"
  night-bit-low: "#24262c"
typography:
  display:
    fontFamily: "Geist Mono Variable, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "clamp(36px, 5.4vw, 64px)"
    fontWeight: 500
    lineHeight: 1
    letterSpacing: "-0.04em"
  headline:
    fontFamily: "Geist Mono Variable, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "30px"
    fontWeight: 500
    lineHeight: 1.1
    letterSpacing: "-0.03em"
  reading:
    fontFamily: "Geist Mono Variable, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "20px"
    fontWeight: 400
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Geist Mono Variable, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "15px"
    fontWeight: 500
    lineHeight: 1.55
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Geist Mono Variable, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.55
    fontFeature: "\"tnum\" 1, \"zero\" 1"
  code:
    fontFamily: "Geist Mono Variable, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "Geist Mono Variable, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.55
rounded:
  none: "0px"
  check: "2px"
  base: "3px"
  overlay: "6px"
spacing:
  hair: "4px"
  xs: "8px"
  sm: "12px"
  md: "16px"
  lg: "24px"
  xl: "28px"
  gutter: "40px"
  gutter-mobile: "16px"
  section: "56px"
  rail: "228px"
components:
  button:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.base}"
    padding: "0 12px"
    height: "28px"
  button-hover:
    backgroundColor: "{colors.paper-2}"
  button-primary:
    backgroundColor: "{colors.ultramarine}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.base}"
    padding: "0 12px"
    height: "28px"
  button-primary-hover:
    backgroundColor: "{colors.ultramarine-deep}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    padding: "0 6px"
    height: "28px"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.base}"
    padding: "0 10px"
    height: "30px"
  tag:
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
    rounded: "{rounded.base}"
    padding: "0 6px"
    height: "18px"
  nav-item:
    textColor: "{colors.ink-2}"
    rounded: "{rounded.base}"
    padding: "4px 8px"
  nav-item-current:
    backgroundColor: "{colors.paper-2}"
    textColor: "{colors.ink}"
  table-row-selected:
    backgroundColor: "{colors.ultramarine-wash}"
  status-dot:
    backgroundColor: "{colors.state-active}"
    rounded: "{rounded.none}"
    size: "7px"
  json-block:
    backgroundColor: "{colors.paper-2}"
    typography: "{typography.code}"
    rounded: "{rounded.base}"
    padding: "14px 16px"
---

# Design System: dusha WebUI

## Overview

**Creative North Star: "The Instrument Listing"**

The WebUI is a monospace document with live readings. Every surface reads like a source listing: one Geist Mono family at three weights, ruled with 1px hairlines, set on a near-white or near-black ground with nothing raised off it. Information sits in ruled tables, definition lists, and plain sentences. Labels are lowercase words, and no surface is a card.

Motion belongs to one instrument. The WebGL bit field is a grid of square, instanced voxels whose heights are quantized into stacked steps, so a value reads as a count of bits, not as a smooth bar. It drifts slowly, tilts toward the pointer, fogs into the page ground, and lights a pulse that sweeps across its columns when something happens. Everything else on the page holds still apart from short state transitions. Inline, the same idea shrinks to the 2D bit row: a value as a line of square cells.

Density is high but calm. The body is 13px with tabular, slashed-zero numerals, and the hierarchy comes from size and tracking more than weight. One ultramarine accent carries the voice: it marks the current place, the lit stage, the required argument, the primary action, and the high bits. It never decorates.

**Key Characteristics:**
- One monospace family (Geist Mono) at 400, 500, and 600; tabular numerals with slashed zero everywhere.
- Hairline 1px rules for all structure; no cards, no shadows on resting surfaces.
- A single ultramarine accent, retuned per theme; muted state colors for active, idle, and failed.
- Square geometry: bits, status dots, the brand mark, and wiring nodes are unrounded squares.
- The WebGL bit field is the only ambient motion; it takes its colors live from the theme tokens.
- Light and dark are equal: every token has a counterpart, and the system preference wins until toggled.

## Colors

A cool, nearly achromatic paper-and-ink palette with one saturated ultramarine and three muted state colors; every role has a light and a dark value.

### Primary
- **Ultramarine** (`ultramarine` light, `night-ultramarine` dark): the one voice. It marks the current nav item (a 4px square), the lit pipeline stage, pinned field labels that are lit, required-argument stars, the console prompt, JSON numbers, `local` origins, focus outlines, the primary button, and the high end of every bit field.
- **Deep Ultramarine** (`ultramarine-deep` light, `night-ultramarine-deep` dark): the primary button's hover. In dark it goes lighter, not darker, so hover always moves toward more contrast.
- **Ultramarine Wash** (`ultramarine-wash`, about 8% in light and 10% in dark): the selected table row. It is the only tinted fill on the page.

### Neutral
- **Paper / Night** (`paper`, `night`): the page ground, and the fog color the bit field fades into.
- **Paper 2 / Night 2** (`paper-2`, `night-2`): hover fills, the current nav item, JSON blocks, and the core node in the wiring map.
- **Ink, Ink 2, Ink 3** (`ink`, `ink-2`, `ink-3`, and their `night-` counterparts): primary text, secondary prose and labels, and tertiary metadata (table headers, asides, counts, timestamps). Ink 3 still clears 4.5:1 on its ground in both themes.
- **Rule, Rule 2** (`rule`, `rule-2`, and their `night-` counterparts): hairline dividers, then control borders, tag outlines, wiring edges, and scrollbar thumbs.
- **Bit Low** (`bit-low`, `night-bit-low`): an unlit bit, in both the 2D bit rows and the 3D field.

### State
- **Active** (`state-active`, `night-state-active`): a running plugin, ok results, JSON strings, active presence.
- **Idle** (`state-idle`, `night-state-idle`): idle presence, JSON booleans, and the synthetic-data tag.
- **Failed** (`state-failed`, `night-state-failed`): errors, a blocked stage reading, the offline banner, sleeping or unreachable devices (mixed toward bit-low).
- **Bit Hot** (`bit-hot`, with `night-state-failed` as its dark value): the vermilion tone a field cell blends into when its tone channel is set, such as a stage that refused.

### Named Rules
**The One Voice Rule.** Ultramarine is the only hue that isn't a state color. It marks place, action, or a lit reading, and is never used as decoration or as a background panel.

**The Paired Token Rule.** A color exists only as a pair of `:root` and `:root[data-theme="dark"]` custom properties. Components and the WebGL field read the variables; neither hard-codes a hex.

## Typography

**Display Font:** Geist Mono Variable (with ui-monospace, SFMono-Regular, Menlo, monospace)
**Body Font:** Geist Mono Variable
**Label/Mono Font:** Geist Mono Variable

**Character:** One monospace family does every job. Hierarchy comes from size and negative tracking at the top, and from ink tone at the bottom, so the page reads like a well-set listing.

### Hierarchy
- **Display** (500, clamp(36px, 5.4vw, 64px), 1, -0.04em): the companion's name set over the lower left of the overview field. Once per page at most.
- **Headline** (500, 30px, 1.1, -0.03em): page titles and the plugin detail name.
- **Reading** (400, 20px, 1.2, -0.02em): an instrument's live value, such as a stage reading. Metric values (18px) and device states (28px) are siblings on the same tracking.
- **Title** (500, 15px, -0.01em): section heads, which sit on a hairline rule with an 11px aside on the right.
- **Body** (400, 13px, 1.55): everything else, with tabular and slashed-zero numerals. Prose caps at 64 to 72ch.
- **Code** (400, 12px, 1.6): JSON results and the wiring node names.
- **Label** (400, 11px): table headers, asides, counts, tags, CLI lines, field legends. Lowercase, never uppercase or letterspaced.

### Named Rules
**The Three Weights Rule.** Only 400, 500, and 600 exist. 600 is used once, for the brand word; headings use 500, and emphasis comes from ink tone, not bold.

**The Lowercase Label Rule.** Labels are plain lowercase words in Ink 3 at 11px. They name the thing beside them (a column, a stage, a field axis) and never sit above a heading as a tagline.

## Layout

A two-column shell: a sticky 228px text rail with a 1px right rule, and a main column with a 40px gutter. A 52px topbar sticks over the main column, showing breadcrumbs on the left and a one-line status on the right, on a translucent ground with a hairline bottom rule. Pages open 36px below the topbar. Sections are 56px apart, and each starts with its title on a rule.

Content uses CSS grids rather than boxes: the overview pairs the plugin table and mood bits in a 1.25fr / 1fr split, plugins use a master and sticky detail at 1fr / 1.15fr, and the pipeline is five ruled columns. Devices form an auto-fill grid of cells with shared hairlines, not separate panels. The spacing steps are 4, 8, 12, 16, 24, 28, 40, and 56px.

At 1080px and below, splits stack and the pipeline goes to two columns. At 760px and below, the gutter drops to 16px, the rail becomes a wrapping top row without counts or the server address, the topbar hides, the hero copy moves below the field, and the pipeline goes to one column. Under reduced motion, every CSS animation and transition is off and the field stops easing and swaying.

## Elevation & Depth

Resting surfaces are flat. Depth comes from tone (paper, paper 2, the ultramarine wash) and from hairlines, never from raised panels. The real depth lives inside the WebGL field: perspective, a fixed key light that shades the voxel sides to 60 to 100%, grooves cut at every quantized step, and distance fog that fades the far rows into the page ground. The field container is only a hairline frame around that.

Two floating layers break flatness, and only these two: the console's suggestion list and the command palette. They sit over the page, so they carry a soft, diffuse drop shadow. The palette's backdrop blurs the page by 3px, and the topbar's translucent ground blurs by 10px.

### Shadow Vocabulary
- **Suggest drop** (`box-shadow: 0 8px 24px -12px rgb(0 0 0 / 0.25)`): the console autocomplete list, anchored above the prompt.
- **Palette drop** (`box-shadow: 0 24px 64px -24px rgb(0 0 0 / 0.35)`): the command palette dialog.

### Named Rules
**The Floating-Only Shadow Rule.** A shadow means "this layer floats over the page and will go away." Nothing that rests in the document flow gets one.

**The Fog-to-Ground Rule.** The field clears to `--bg` and fogs its far rows toward `--bg`, so the instrument has no visible backdrop of its own and seems to rise out of the page.

## Shapes

Square by default. Bits, status dots (7px), the brand mark (10px), the current-place marker (4px), timeline cells, and wiring nodes (8px) have no radius. Controls, tags, JSON blocks, the field frame, and the console share a barely-there 3px corner. Checkboxes use 2px, and their checked mark is a 5px square. The palette dialog alone uses 6px. Borders are 1px everywhere. Inactive things are outlined, not filled: an off dot is a 1px inset outline, an off wiring node is dashed, and event wiring arcs are dotted (2 3) while service arcs are solid. Icons are drawn with a 1.5px square-capped stroke on a 16px grid and favor rectangles, so even the sun and the search glass are square.

### Named Rules
**The Square Bit Rule.** A bit is a square: in CSS, in the WebGL field, and in the favicon. Never round a bit, a dot, or a node.

## Components

### Buttons
Quiet, bordered, and short.
- **Shape:** 3px corner, 28px tall, 12px horizontal padding, 1px Rule 2 border on the page ground.
- **Primary:** Ultramarine fill and border with on-accent text. Hover moves to Deep Ultramarine. One per form or toolbar.
- **Hover / Focus:** the border goes to Ink 3 and the fill to Paper 2 over 160ms on the expo-out ease. `:active` nudges down 1px. Focus is a 1px Ultramarine outline at a 2px offset. Disabled drops to 45% opacity.
- **Ghost:** no border or fill, Ink 2 text, 6px padding. Hover fills Paper 2. Used for rail utilities (jump, theme).
- **Kbd:** an 11px Ink 3 key with a 1px Rule 2 outline and a 3px corner, inside buttons and hints.

### Chips
- **Tag:** an 11px label in a 1px Rule 2 outline, 18px line, 3px corner. The accent variant tints the border to 45% Ultramarine with Ultramarine text, and marks `local`.
- **Synthetic tag:** the same shape in Idle, on an 80% ground, pinned to the top right of a field whenever the data is simulated.

### Cards / Containers
There are none in the card sense. Grouping uses section rules, ruled tables, definition lists (`dt` in Ink 3, a max-content column, 24px gap), and grids of hairline-sharing cells. The only framed containers are instruments and logs: the field (a 1px Rule frame with a 3px corner) and the console (the same frame, with its input on a top rule).

### Inputs / Fields
- **Style:** 30px tall, 10px padding, 1px Rule 2 border, 3px corner, page ground, Ink 3 placeholder.
- **Focus:** the border shifts to Ultramarine; no glow.
- **Select:** native appearance removed; the chevron is two 4px gradient triangles in Ink 3.
- **Checkbox:** a 13px square with a 1px Ink 3 border and a 2px corner. Checked fills Ultramarine with a 5px on-accent square inside.
- **Argument row:** a 140px label column (Ink 2, options shown as `--flag`) beside the control, with an 11px hint under it. On mobile it stacks into one column.

### Navigation
- **Rail:** an 11px Ink 3 group label, then lowercase section names in Ink 2 with 11px counts on the right. Hover fills Paper 2. The current item gets Paper 2, Ink text, and a 4px Ultramarine square outside its left edge. A brand mark (a 10px Ultramarine square that breathes on a 4.8s cycle) leads the rail; jump, theme, and server status sit at its foot.
- **Topbar:** breadcrumbs in Ink 3 with the last crumb in Ink, slash-separated.
- **Mobile:** the rail becomes a wrapping top row without counts.

### Ruled Table
The primary way to show a collection. 11px Ink 3 headers in regular weight, 9px row padding, a 1px rule under every row, and the first column inset 8px. Clickable rows fill Paper 2 on hover (120ms), and the selected row takes the Ultramarine Wash. Numbers align right.

### Bit Field (signature)
The WebGL2 instrument. One instanced cube per cell, with height eased toward its target (0.06 to 0.12 per frame) and quantized up to the next step, with a minimum stub of 0.35 step, so even zero shows a floor. Voxels are 0.8 to 0.82 of the cell pitch. Color runs from Bit Low to Ultramarine by value and blends toward Bit Hot by a separate tone channel. A sweep pulse is a Gaussian band (1.4 to 2.2s, cubic-out) that pushes columns toward Ultramarine. Grooves cut at each step are deeper in dark. The camera auto-fits the field's bounds to 94 to 98% of the frame, sways ±0.04 to 0.08 rad on an 11-second cycle, and tilts slightly toward the pointer. A clear rectangle flattens any column whose top would land under text set over the field, so that text stays legible. History fields push new rows in from the front.
- **Pinned labels:** HTML spans projected onto cells each frame, set in Ink 3 and turning Ultramarine when lit. Axis labels (`now`, `1.5 min ago`) right-align to their cell.
- **Legend:** an 11px Ink 3 line in the top left naming what the field measures and how to interact.
- **Fallback:** without WebGL2, the frame holds a plain sentence saying so, and the rest of the page still works.
- **Theme:** colors are re-read from the custom properties whenever `data-theme` changes.

### Bit Row
The 2D sibling of the field: a value as a row of 2px-gapped square cells (32 by default, 10 in score bars), lit cells in Ultramarine, unlit in Bit Low, an optional reference cell in Ink 3, 9px tall (7px in score bars), and a 400ms fill transition. Presence timelines use the same grammar, colored by state.

### Wiring Arc Diagram
Plugins are stacked in a right-aligned 12px column, each ending in an 8px square node, 30px apart. Injected services are solid arcs in a tight lane next to the column. Events are dotted arcs that sweep out to the right in proportion to the distance they span. Hovering or focusing a node fades unrelated edges to 15%, turns its edges and neighbors Ultramarine, and reveals the edge labels.

### Pipeline Stages
Five ruled columns. Each has an 11px Ink 3 head with its `n/5` position, a 20px reading, and a 12px Ink 2 note. A lit stage turns its head Ultramarine, and a blocked stage turns its reading Failed.

### JSON Block
A Paper 2 block with a 3px corner, 14px by 16px padding, 12px code at 1.6, capped at 420px and wrapping anywhere. Keys are Ink, strings Active, numbers Ultramarine, booleans Idle, and null and punctuation Ink 3.

## Do's and Don'ts

### Do:
- **Do** set every surface in Geist Mono at 400 or 500, at the ramp sizes above, with tabular and slashed-zero numerals.
- **Do** structure with 1px hairlines (Rule for dividers, Rule 2 for control borders) and grids of shared rules.
- **Do** show values as square bits: a bit row inline, the WebGL field for anything that moves over time.
- **Do** pass every color through a paired custom property, so the WebGL field and both themes stay in step.
- **Do** keep text over a field inside the field's clear rectangle, and label synthetic data with the Idle tag where it appears.
- **Do** use the expo-out ease (`cubic-bezier(0.16, 1, 0.3, 1)`) at 120 to 240ms for state changes, and honor reduced motion.

### Don't:
- **Don't** build KPI cards or sparkline tiles; a reading is a line of text or a row in a ruled table.
- **Don't** put a shadow on anything that rests in the document flow; shadows are only for the suggestion list and the palette.
- **Don't** round bits, status dots, wiring nodes, or the brand mark.
- **Don't** add a second accent hue or use Ultramarine as a panel background beyond the selected-row wash.
- **Don't** introduce another typeface, an uppercase letterspaced label, or a 700 weight.
- **Don't** animate anything continuously except the field, the brand mark's breathing, and live status dots.
