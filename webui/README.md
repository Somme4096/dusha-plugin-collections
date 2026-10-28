# webui

A browser client for one dusha companion, next to `dusha` the CLI. It finds its server the same way the CLI does and talks only to the public routes (`/state/v1/*`, `/cli/v1/*`, `/mcp`). The browser never sees the token, because this process adds it on the way through.

| Page | Shows |
| --- | --- |
| overview | The strongest affect dimensions as lanes of bits seen from above, a readout of messages, conversations, memory, devices, proactive, and operations, the latest messages, an activity timeline, and the wiring map |
| plugins | Built-in plugins, the plugins in this repository (loaded or not), and anything else the server runs. Each one shows its wiring, holdings (memos, facts, affect), and every CLI command and MCP tool as a runnable form |
| proactive | `proactive plan` every 3 seconds as a heat field over five stages, collect, gate, pick, queue, and deliver, with a pulse through the stages whenever the plan changes. `simulate` plays labeled synthetic plans, and `Evaluate now` runs one real pass |
| presence | Every device in the registry with a strip of its recent states |
| ambient | Metrics per device as a heat field, last-hour ranges, and the event log with filters |
| console | `dusha` in the browser, with completion and history |

<kbd>⌘K</kbd> or <kbd>Ctrl K</kbd> jumps to any page, plugin, or command. The theme follows the system until you toggle it.

## Run

```sh
cd webui
mise install       # bun, see mise.toml
bun install
mise run start     # or: bun server.ts --companion <name>
```

Then open http://127.0.0.1:8767.

| Option | Default |
| --- | --- |
| `-c, --companion NAME` / `--home DIR` / `--config FILE` | Like `dusha`: `DUSHA_HOME`, `DUSHA_COMPANION`, then the only companion under `~/.config/dusha` |
| `--url URL`, `--token TOKEN` | From the config: the proxy's saved server, otherwise the local server and its `api_token`. `DUSHA_TOKEN` also sets the token |
| `--plugins DIR` | This repository, for the local plugin descriptions and the wiring the source reveals |
| `--host`, `-p, --port` | `127.0.0.1`, `8767`. Anyone who can reach the port acts with the server's token, so keep it on loopback or Tailscale |

`mise run dev` reloads on save, and `mise run check` type-checks and runs the tests.

## How it reads plugins

- The server reports each plugin's state, injected and provided services, and the events it listens for.
- For plugins in `--plugins`, the WebUI also reads `pyproject.toml` and the entry module: description, what the plugin emits (`ctx.emit`) and asks (`ctx.collect`), its routes, CLI groups, and MCP tools. That's how the wiring map draws both ends of an event.
- Commands belong to the plugin whose CLI group they're in. `proactive` belongs to whichever plugin provides it, the built-in or better_proactive.
- It never calls `proactive poll`, which would lease events away from real consumers.
- Every command, tool call, and write sent through the WebUI lands in an in-memory operations log (`/api/operations`, last 200), which the overview's activity timeline reads next to ambient, memos, facts, and proactive sends. Background polls send `x-webui-quiet` and stay out of it.
