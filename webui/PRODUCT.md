# dusha WebUI

> Written without an interview (the user was asleep and asked for no questions). Lines marked *assumed* are inferences from the brief, so confirm or correct them.

## What it is

A browser client for one dusha companion, next to `dusha` the CLI. It shows every plugin the server runs, the built-in ones (identity, memory, evergreen, memo, affect, proactive) and the external ones from `plugins_dir`, and the plugins in this repository that the server has not loaded. It runs any CLI command or MCP tool the server exposes, and it adds live views for presence, ambient, affect, and the proactive pipeline.

## Who uses it

- The person who runs the companion. They are a developer, at their desk, fluent in the terminal (fish, Helix), and they are checking on the companion's insides: is it up, what does it feel, why did it (not) speak up. *assumed*
- Usage is day or night with no clear default, so the light and dark themes are both first-class and the system preference wins until toggled. *assumed*

## Brand commitments (from the brief)

- Polished monospace minimalism in the vein of zed.dev and williamjansson.com.
- Light and dark themes.
- WebGL graphics that work as instruments: 3D, bit-like waveforms and heatmaps that react to what the companion is doing, especially each stage of the proactive flow.
- Bun via mise. No framework requirement.

## Truth constraints

- It talks to dusha only through its public HTTP surface: `/state/v1/*`, `/cli/v1/*`, and `/mcp`. It imports nothing from the plugins.
- It never leases proactive events (`proactive poll` would steal them from real consumers).
- Synthetic data is labeled synthetic wherever it appears.
