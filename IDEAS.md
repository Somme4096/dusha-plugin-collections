# dusha-plugin-collections

> [!NOTE] Note to Claude
> This is a fun proof-of-concept project so make it fun. 

## Ground rules

- One directory per plugin, each with its own `pyproject.toml` and `[tool.dusha]` table, so people pick only what they want.
- Plugins talk through the plugin API, never imports, so each one still works alone.
  - **Hard dependency**: `inject`. The plugin stays inactive without it.
  - **Soft dependency**: `ctx.collect("<name>")` to ask, `ctx.emit`/`ctx.on` to tell. The plugin falls back when nobody answers. Mutual dependencies (presence ↔ notification) always go this way, since two plugins injecting each other both stay pending.
- Plugin routes live under `/state/v1/<plugin>`.
- Anything an agent can do is an MCP tool, so harness integration and the harness's own permission layer just work. No guardrails of our own. This is a toy.
- Example configs: `config.hub.example.json` and `config.device.example.json`.

## Topology

A dusha instance is a hub or a device only by configuration. Same `dusha serve`, different plugins.

- **Hub**: always on (homeNAS). The companion lives here, with evergreen, affect, memo, and proactive on. Harnesses and bots (opencode, the Discord bot) talk to it.
- **Device**: desktop, MacBook. Runs `dusha serve` with companion plugins disabled, only sensors like presence, ambient, and screenshot, next to `dusha proxy` for local harnesses.
- Devices push to the hub. The hub never asks a device whether it's alive, because a dead machine can't answer.
- Devices reach each other over Tailscale, so the hub can call a live device's routes directly.

## Foundations

Almost everything else builds on these: ambient, presence, notification, better-proactive, and relay.

### ambient

The everything-log. Logs, metrics, and events from every device, merged into one picture agents can read.

- `POST /state/v1/ambient` takes `{device, source, text, ttl?}`. Also listens to `ambient.observe` for in-process plugins.
- Our own metrics pipeline, not an outsourced platform, so it pairs with presence and relay and stays one MCP surface on the hub: resources, processes, uptime through psutil or similar. Raw time series in sqlite under `ctx.data_dir`, downsampled over time.
- On a device, an option forwards everything to the hub. Every row carries `device`.
- `context` injects only what's salient ("desktop CPU pinned 20 min by `cargo`", "NAS disk at 92%") as one `render("ambient", ...)` block.
- MCP query tool for the full detail. Optional `/metrics` route in Prometheus format so Grafana works for free.

### presence

Two facts per machine, and the registry of all machines.

- **user**: `active` / `idle` / `locked` plus idle seconds (logind on Linux, the macOS equivalent on the MacBook).
- **machine**: `awake` / `sleeping` / `unreachable`.
- With `report_to` set (a device), it pushes a heartbeat `{device, user, idle_s, capabilities}` to the hub every N seconds, and a goodbye ("sleeping", "shutting down") before suspend (`PrepareForSleep`) and on `on_dispose`.
- On the hub, it keeps the registry: last heartbeat per device. No heartbeat for 2–3 intervals marks the device down.

  | Last thing the hub heard | State |
  | --- | --- |
  | Recent heartbeat | `macbook: active` / `idle 12m` / `locked` |
  | A goodbye | `macbook: asleep since 14:02` |
  | Silence | `macbook: unreachable since 14:02` |

- Emits `presence.changed(device, old, new)`, answers `ctx.collect("presence")`, and injects the registry as a context block.
- Listens to `notification.clicked`: a click means the user is back.

### notification

Reach the user, nothing more.

- `notify(title, body, urgency)` through the desktop (`notify-send`, or `osascript` on macOS) and/or the phone (ntfy).
- Runs on each device and delivers locally. The hub's model reaches a device's desktop through relay's `notify(device, ...)`.
- Emits `notification.clicked` when the desktop reports one.

### better-proactive

Replaces the built-in `proactive`. Moves to dusha core once a few days of use prove it.

- Disable the built-in, `provide("proactive")` with the same routes and the same queue, lease, and ack protocol, so existing consumers (the Discord bot, AstrBot) don't notice. Its routes register during `apply`, ahead of the disabled built-in's 503 placeholders. No core hook needed.
- **Candidates (why)**: plugins offer reasons through `ctx.collect("proactive.candidates", now)` as `{reason, topic, score, context, dedup_key}`.
  - Today's silence and affect trigger is one source among many.
  - Others: presence ("came back after 3h away"), ambient ("build failing for an hour", "NAS disk at 92%"), memo, digest.
  - A candidate can be a task ("look into X, then tell her"). The consumer's agent does the work with its own tools, and its harness gates it.
- **Gate (whether now)**: presence (asleep, locked, away), quiet hours, cooldown, daily cap, unanswered cap. Falls back to today's clock rules without presence. Can answer `ctx.collect` for digest or voice later.
- **Pick**: highest score, one pending event at a time.
- **Deliver (where)**: the existing event queue. Options choose the consumer target instead of always `latest_route()`. Emits `proactive.sent(text, event)` after a `sent` ack, so the text can also show on the active device through relay.
- Words come from the consumer's LLM. A plugin that calls a model directly, for when no consumer is around, is a separate later idea (ideally a harness of our own, not an opencode integration).

## Device glue

- **relay**: the hub's MCP tools for other devices: `look(device)`, `notify(device, ...)`, later hands. One tool per action with a `device` argument, never one MCP server per device, since several servers exposing the same tool names don't work. `device: "auto"` asks presence for the device you were last active on. Checks the presence registry first and fails fast ("macbook is asleep since 14:02"), otherwise calls that device's route over Tailscale. The device's own plugins do the work.
- **screenshot**: device-side `/state/v1/screenshot` route, called on an `interval` or by `look`. Goes through `xdg-desktop-portal` on Wayland and `screencapture` on macOS. The image goes to a vision model, the description lands in ambient, the image is dropped.

## Built on the foundations

- **tui**: animated ASCII maid in the terminal. Mood from `ctx.affect` picks the animation, an SSE route feeds it, `dusha maid ...` commands and an MCP tool let the companion strike a pose.
- **pet**: the maid gets hungry or sleepy based on how well you look after yourself. Breaks, late-night commits, sane end of day. Feeds affect `deltas` through `decide`. Needs presence and git.
- **digest**: evening or morning summary of the day's ambient and memos, through notification.
- **inbox**: `dusha inbox` and an MCP tool listing what the companion wanted to say while notifications were muted.
- **voice**: local TTS (Piper or similar) for proactive messages, tone from the maid's mood.
- **journal**: an `interval` job that turns the day into a memo and a shared diary entry in an Obsidian vault.
- **focus**: time on one project and context switches, feeds affect `deltas` so mood follows your day, not only chat.
- **hands**: MCP tools to control the computer (shell, input, windows), exposed through relay per device. The harness approves.
- **playground**: the companion `explore`s the web on its own with a headless browser, keeps a log, and brings findings back as memos and "look what I found". Replaces the RSSHub feed whose X token keeps dying.

## Sensors (feed ambient)

Mostly tiny hooks that `curl` ambient, not plugins.

- **shell**: fish/zsh hook for failed commands and long-running jobs.
- **git**: post-commit and checkout hooks. Branch, what you just shipped.
- **calendar**: ICS file or URL. Knows you're in a meeting.

## Decisions

- **Heartbeats over Tailscale `LastSeen` or hub polling.** Push works through NAT, doesn't depend on the transport, and carries state (`user`, `idle_s`, `capabilities`), so a beat is the presence update itself. Goodbyes separate asleep from unreachable. A long-lived connection would still need pings to catch half-open sockets. Tailscale status can be an extra signal, not the design.
- **Own metrics over Netdata/Beszel/Glances.** Universal support, pairs with presence and relay, one MCP surface.
- **No separate policy plugin.** Routing ("where do I reach her") is relay's `device: "auto"` backed by the presence registry. The gate ("may I interrupt now") lives in better-proactive until something else needs it.

### Made while building the foundations (2026-10-28)

**Layout and tooling**

- One module per plugin (`presence/presence.py`, ...), like `examples/plugins/system_one`. Plugin directory names use underscores (`better_proactive`) because `PLUGIN_NAME` doesn't allow dashes.
- Plugins import dusha core freely (`dusha.hooks`, `dusha.timeutil`, `dusha.client.settings`, `dusha.proactive`), never each other. The small `peer()` helper is duplicated in presence and ambient instead of shared.
- The root `pyproject.toml` is dev-only. `uv sync` installs dusha from `../dusha` (editable), psutil, pytest, ruff, and uvicorn. Switch `[tool.uv.sources]` to PyPI or git once the repo lives on its own. Ruff rules match dusha's (`E F I B UP`, 110 columns).
- Tests are end-to-end through `api.create_app` like system_one. Hub and device cases run two real gateways on loopback ports through uvicorn. Pure logic (welcomes, alerts, rollups) gets unit tests with a `FakeCtx`. No test ever pops a real notification or reaches ntfy.sh.

**presence**

- Each device's own name is the `device` option, defaulting to the short hostname. Its `url` defaults to `http://<device>:<port>`, which Tailscale MagicDNS resolves.
- `report_to` accepts `"proxy"`, which reuses the server `dusha proxy` already saved in the same config.json, or `{url, token}`. Hubs leave it unset.
- Sensors:
  - Linux: idle time from Mutter's IdleMonitor or KDE's ScreenSaver over `gdbus`, falling back to logind's `IdleHint`. Lock state from logind's `LockedHint`. The session comes from `loginctl show-user $USER -p Display`, so it works under a systemd user service. Idle means 5 minutes without input (`idle_after_seconds`).
  - macOS: `ioreg` `HIDIdleTime` and `CGSSessionScreenIsLocked`.
  - No extra Python dependencies, just subprocesses.
- Goodbyes:
  - "sleeping" comes from `gdbus monitor` on logind's `PrepareForSleep` (Linux only).
  - "shutting down" is sent on dispose.
  - A wall-clock gap of 3+ intervals between ticks also counts as sleep after the fact, so the change reads `sleeping -> active`.
  - macOS has no pre-sleep goodbye, so a sleeping MacBook shows as `unreachable`.
- `down_after_seconds` defaults to 3× the interval. The hub's clock stamps `last_seen`, so device clock skew doesn't matter.
- Statuses: `active`, `idle`, `locked`, `awake` (up, no user session, e.g. the NAS), `sleeping`, `off`, `unreachable`.
  - `active_device` is the awake device with the user active and the least idle time.
  - `last_active_device` is the one most recently used.
- Every instance keeps a registry, including itself. A device's registry just holds itself. `presence.changed` is emitted per instance.
- Capabilities come from `ctx.collect("presence.capabilities")`, so a plugin advertises itself (notification answers `["notify"]`, but only when it has a channel).
- "Came back" welcomes fire when a device turns `active` after `welcome_after_minutes` (60) away. They're offered as proactive candidates (score 0.6) for 15 minutes.
- A `notification.clicked` on this device counts as activity for one interval.

**notification**

- Channels are `desktop` (`notify-send`, `osascript`) and `phone` (ntfy JSON publish, so non-ASCII titles work).
- `auto` means desktop when this machine's user is `active`, otherwise the phone. Without presence, every channel.
- No MCP tool of its own: relay's `notify` is the one tool, and it reaches this plugin locally through `ctx.collect("notification.send")`, so the names never clash. Single-machine setups still get `dusha notification send` and the route.
- Clicks: `notify-send --action=default --wait` runs in a thread and emits `notification.clicked`. It falls back to a plain `notify-send` on versions without `--action`. macOS has no click.
- `desktop_command` exists for tests and for anyone who wants a different notifier.

**relay**

- MCP tools `look(device="auto")` and `notify(title, body, device="auto", urgency)`, plus `dusha relay look|notify`.
- Before calling, it checks presence and fails fast with readable errors: "macbook is off since 01:44 (5m ago)", "macbook can't look, it offers: notify", "unknown device".
- `notify(device="auto")`: the active device's desktop, otherwise the phone through the hub's own notification. Naming a device lets that device decide.
- Device auth: one `token` option for every device, with per-device overrides in `tokens`. Tokens are never sent in heartbeats, so they can't leak into the registry or the context.
- `look` calls `/state/v1/screenshot` on the device. The screenshot plugin itself isn't built yet, so look currently answers "can't look".
- Mirroring: after a proactive message is acked as sent, relay shows the text on the active device (`mirror_proactive`, title `mirror_title`). It skips mirroring when she's not at any device, because the consumer (Discord) already reaches the phone.

**ambient**

- Its own sqlite file under `ctx.data_dir`, not dusha's state database, so it can churn without touching companion data.
- psutil is optional. Without it, events still work and only metrics are off.
- Metrics, sampled every 60s:
  - `cpu.percent`, `mem.percent`, `swap.percent`, `load.1`, `uptime.hours`
  - `disk.percent:<path>`, `net.rx_kbps`, `net.tx_kbps`
  - `battery.percent`, `battery.plugged`
  - Raw samples are kept 24h, hourly rollups (avg, min, max, n) 30 days.
- The default disk is `~`, not `/`: on Fedora Atomic `/` is the read-only image and always reads 100% (found on your machine).
- Alerts are computed on the device that has the metrics, then forwarded like any observation:
  - CPU ≥85% averaged over 10 min, with the top process named
  - memory ≥90%, disk ≥90%
  - battery ≤15% while unplugged
  - Each fires once, resolves with a `resolved` observation that expires it everywhere, and survives restarts.
- Devices forward observations and samples to the hub every sample tick, keeping up to 500 observations and 5000 samples while the hub is unreachable.
- Presence changes are recorded only where `forward_to` is unset (the hub), so they aren't duplicated.
- Context gets open alerts plus the last 2h of events (max 12). Metrics stay out of context unless they alert, and agents ask `ambient_metrics` for detail.
- Prometheus text lives at `/state/v1/ambient/prometheus`, inside the authenticated router, so scrape it with the `x-companion-token` header.
- Alerts become proactive candidates (score 0.5) with `requires_presence: "active"`, because "your disk is full" can wait until she's at a computer.
- Sensors in `ambient/hooks/`: a fish `fish_postexec` hook (failures except Ctrl-C, jobs of 5+ minutes) and a git `post-commit`, both using `curl` with JSON built through `python3`.

**better_proactive**

- Subclasses `dusha.proactive.ProactiveEngine` and reuses its queue, lease, ack, `on_message` cancellation, routes, and CLI unchanged, so consumers can't tell the difference. If the built-in is still on, it marks itself failed with a message saying to disable it, instead of crashing the gateway.
- The built-in caps (`max_per_day`, `cooldown_minutes`, `max_unanswered`, one pending event) are a shared budget across all reasons. The example hub config raises `max_per_day` to 4 and lowers the cooldown to 120 minutes.
- Quiet hours still apply, but presence overrides them when she's active on a device (`presence_overrides_quiet_hours`): awake at 2am means awake.
- Silence keeps its own `min_silence_minutes`. Every other reason waits `min_gap_minutes` (10) after her last message, so it doesn't interrupt a live chat.
- A reason is raised once, ever, per `dedup_key`, including events that were cancelled because she spoke first.
- The silence candidate's score is 0.4 plus the ruling feeling's deviation.
- Prompts: the generation instruction is the built-in one, plus "Why you're reaching out now: <topic>", plus for `task` candidates "Before you write, do this with your tools: ...". Context is built with the topic as the query.
- Options: `target` (`"latest"` or `{harness, conversation_id}`) and `min_score`.
- `dusha proactive plan` shows the gate result and every candidate with why it was skipped, without queueing anything.
- The payload adds `source`, `kind`, `topic`, and `candidate_context` next to every built-in field.

### WebUI (2026-10-28)

- A client beside `dusha`, not a plugin: a bun server resolves the companion like the CLI (`homes.locate`, then the proxy's saved server or the local `api_token`) and proxies `/api/dusha/*` with the token, so the browser never holds it and CORS never comes up.
- Plugin metadata merges three sources: `/state/v1/plugins`, a built-in list (dusha's README words), and a scan of this repository's `pyproject.toml` files and entry modules. The scan finds `ctx.emit` and `ctx.collect` calls, which the server can't report, so the wiring map draws emitter → listener.
- Commands are attributed by CLI group and MCP tools by name, from the built-in list or the source scan. `proactive` goes to whichever plugin provides it.
- Preact plus Bun's HTML bundler, and one hand-written WebGL2 instanced-voxel field (`web/gl/bitfield.ts`) for every visualizer. No three.js.
- The proactive view polls `proactive plan` (read-only), never `proactive poll`. Delivery comes from affect's `last_proactive_sent_at` and `unanswered_proactive`. "Simulate" is synthetic and labeled.
- Ambient reports summaries, not series, so the metric field's history is what the page has polled, seeded from the last hour's min, average, and max.

### Screen, redaction, and the proactive judge (agreed 2026-10-28)

**Screen capture lives in ambient**, as one more source on each device.

- Backends are ported from `~/src/sophia-connector/services/screen.py`:
  - Linux Wayland: the `xdg-desktop-portal` Screenshot call through `dbus-next`, non-interactive, which works on GNOME and KDE. Falls back to `grim`, then `gnome-screenshot`.
  - macOS: `screencapture -x` instead of `mss`, one dependency fewer.
  - Dependencies: Pillow, plus `dbus-next` on Linux only.
- Capture every 30s (`screen.every_seconds`):
  - Skip it while presence says this device is `idle` or `locked`.
  - Downscale the image. Skip it if a perceptual hash says the screen hasn't changed since the last capture.
- Describe it with a vision model through the proxy's provider presets:
  - `screen.provider` names a `proxy.upstreams` profile from the same config.json. `{env:NAME}` placeholders are resolved the way the proxy does.
  - `screen.model` is required. `base_url`, `key`, and the other profile fields can be overridden.
  - OpenAI-compatible `chat/completions` with image content.
- Each capture is sent along with the previous description, so the model can judge continuity. It returns JSON:

  | Field | Holds |
  | --- | --- |
  | `app` | The app in front. On macOS the OS's frontmost bundle overrides the model's guess. |
  | `title` | Window, tab, or document title. |
  | `content` | 1–3 sentences on what's visible, with short error text quoted as-is. |
  | `activity` | What she's doing, as a verb phrase. |
  | `status` | `focused`, `stuck`, `waiting`, `communicating`, `browsing`, or `leisure`. |
  | `progress` | Compared with the previous capture: `new`, `continued`, `progressed`, or `switched`. |
  | `notable` | Optional: an error, a deadline, a message from a person. |

  If the model doesn't return valid JSON, the first lines of its plain text are used.
- Every changed description becomes its own `screen` row. An identical description is skipped.
  - The row text is `app — activity (status)` followed by `content`, and `data` holds every field.
  - Context shows the latest row in full plus the last few headlines.
- There's no `private` field for now.
- Images stay on the device in a small ring buffer, so relay's `look` can return the newest one. Ambient advertises `look` while screen capture is on.

**One redaction stage in `Ambient.observe()`.** Every source goes through it: the route, forwarded batches, events, alerts, hooks, and screen descriptions.

- Matching is layered:
  1. Known token formats (GitHub, `sk-…`, AWS, Slack, JWT, PEM blocks, `Bearer …`, `user:pass@` in URLs, `KEY=value` for secret-looking keys).
  2. A Shannon-entropy catch-all for long mixed-class strings. Git SHAs and UUIDs are exempt.
- A match becomes `[redacted:<kind>]`. `redact.patterns` adds your own patterns.
- It runs on the device before forwarding, and again on the hub at ingest.
- Written in-house rather than with `detect-secrets`, to keep the shared environment light.

**No thresholds anywhere: the gateway decides, like `decide` and system_one.**

- Ambient streams facts as candidates with no time rules. Every screen row whose `status` or `progress` changed, every alert, every presence change. "Stuck for 10 minutes" is just several rows in the timeline.
- better_proactive runs its gates first (shared budget, quiet hours, one pending). Then it fires `ctx.collect("proactive.decide", request)`:
  - `state`: the timeline, presence, affect, the last user message, and the budget used. Trimmed to stay well under 32k tokens.
  - `questions`: `speak` is a choice among the candidates plus `none`, and `kind` is `talk` or `task`, in system_one's typed-question format.
- system_one gets a second listener for `proactive.decide`, answered by the same `/v1/systemone` server, which takes several questions per request.
- Without a judge, better_proactive falls back to the highest score, the way affect works without system_one.
- `proactive.decide` is a custom event, so no core change is needed. When better_proactive moves to core, it can become a first-class hook next to `decide`.

#### Made while building it

- **ambient is a package now** (`ambient/ambient/{__init__,redact,screen}.py`). Every `ctx` call stays in `__init__.py`, so the webui's source scan still sees its emits and collects. `redact` and `screen` are plain code.
- **Redaction:**
  - A more specific rule wins: a GitHub token inside a URL reads `[redacted:github-token]`, not `url-credentials`.
  - Text that's already redacted is never matched again, so the hub's second pass is a no-op.
  - The kinds found are listed in `data.redacted` and merged across passes.
  - The entropy catch-all needs 24+ characters, all three of lower, upper, and digit, and entropy ≥ 3.8. Hex SHAs, UUIDs, snake_case identifiers, and paths never qualify.
  - The bare word `auth` is not a secret key, so `author: …` survives.
- **Screen:**
  - The portal call uses `interactive: false`. The handle path is computed up front and the signal match is registered before calling, so a fast reply isn't missed. The file the portal saves is deleted right after reading; GNOME puts it in `~/Pictures/Screenshots`.
  - Change detection is a 64-bit difference hash at a 4-bit threshold. Two captures of an idle screen differ by about 1 bit. A hash compares structure, not color.
  - The provider preset is loaded with `environ=False`, and only the values actually used get their `{env:…}` filled in. A missing variable gives a clear error, and an overridden key needs no variable.
  - `response_format: json_object` is sent first. A 400 means the server doesn't take it, so it's retried once without and remembered.
  - With an OS hint (macOS frontmost app), the hint overrides the model's `app`.
  - The ring buffer keeps 5 JPEGs by default (`keep_images`).
  - `look` reuses the last description if it's younger than `every_seconds`, otherwise it captures fresh, even while she's idle.
  - Timer ticks are skipped while presence says this device is idle or locked.
  - Every row emits `ambient.observed`, for the webui and for anything event-driven later.
- **Candidates and the timeline:**
  - Ambient offers screen moments: a row whose status turned, `progress: switched`, or a `notable`, from the last hour, at most 5. They carry `needs_judge: true`, and better_proactive never raises one without a judge.
  - `ctx.collect("ambient.timeline", hours, max_chars)` returns every recent row oldest first. When over budget, the newest lines win, capped at 40,000 characters, well under 32k tokens.
- **Judge:**
  - `proactive.decide` is only asked from `evaluate`, never from `dusha proactive plan`, because the webui polls that.
  - The answer is cached against the exact set of candidates. Unchanged candidates aren't asked about again for `judge_recheck_minutes` (10), so "none" doesn't cost a call every minute.
  - The state holds:
    - the local time, the timeline (3h), each device's status, and the active device
    - the ruling feeling, the last 6 messages of the target conversation, and minutes since she spoke
    - today's budget, the unanswered count, and each candidate's details
  - better_proactive now injects `messages` for that.
  - The judge's `kind` overrides the candidate's. The payload carries `judged: true`.
  - `judge_instructions` replaces the default prompt.
  - An answer outside the choices is logged and treated as no judge.
- **system_one**, in dusha's repo:
  - `_ask()` now holds the HTTP, allowlist, and redirect handling, shared by `decide` and the new `decide_proactive`.
  - Every answer must be one of its question's criteria.
  - Tests and a README section were added. The collection's tests copy it from `../dusha/examples/plugins/system_one`.
- **Ambient's store is closed under its lock**, and calls after close are no-ops. An interval thread querying while dispose closed the connection is the likely cause of the one core dump seen during testing.

#### Vision prompt in prompts.json (2026-10-28)

- The vision prompt lives at `plugins.ambient.screen` in dusha's prompts.json, as a string or a list of lines. The packaged prompt in `screen.py` is the default.
- dusha core needed a change for this: its prompts.json rejected unknown slots. It now has an optional `plugins` slot, an object keyed by plugin name. Core checks only that each entry is an object, and each plugin checks its own keys. ambient injects `prompts` to read it.
- A custom prompt should still ask for the same JSON keys. A `status` or `progress` outside the known values is dropped, and plain text falls back to the first lines.

#### system_one moved here (2026-10-28)

- system_one moved from dusha's `examples/plugins` into this collection as a plain copy, without its history. `proactive.decide` is this collection's event, so its answer now sits beside it.
- Pointing `plugins_dir` at the collection loads it with everything else. dusha's own `decide` hook stays covered by `tests/test_external_plugins_e2e.py`.
- everos_memory followed the same way, and dusha's `examples/` is gone. dusha's docs link here for working external plugins.
