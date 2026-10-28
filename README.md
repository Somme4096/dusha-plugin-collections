# dusha-plugin-collections

> [!NOTE] 
> 

A small set of [dusha](https://github.com/Somme4096/dusha) plugins that shows what dusha is capable of, and I use daily.
See [IDEAS.md](IDEAS.md) for the design and every decision behind it.

| Plugin | What it does |
| --- | --- |
| [presence](presence) | Is she active, idle, or locked, is each machine up, asleep, or gone. Devices send heartbeats to the hub, and the hub keeps the registry. |
| [relay](relay) | The hub's `look` and `notify` MCP tools for my other devices. Paired with presence. |
| [notification](notification) | Desktop (`notify-send`, `osascript`) and phone (ntfy). Picks the channel from presence. |
| [ambient](ambient) | Events and metrics from every device in one place, system logs, commit histories, fish, screenshot, process monitoring, network interception, etc. The rest over MCP and Prometheus text. |
| [better_proactive](better_proactive) | Drop-in replacement for the built-in proactive plugin, with reasons from any plugin instead of only silence. |

The [webui](webui) is a client. A browser view of any companion's plugins, commands, presence, ambient, and proactive pipeline, with WebGL visualizers. Run it with `cd webui && bun install && bun server.ts`.

## Use

1. Point `plugins_dir` in your `config.json` at this repository, or copy the plugin directories you want into your plugins directory.
2. Install `psutil` into dusha's environment for ambient's metrics: `uv tool install dusha --with psutil`.
3. Start from [config.hub.example.json](config.hub.example.json) for the always-on machine and [config.device.example.json](config.device.example.json) for laptops and desktops.
4. Optional sensors live in [ambient/hooks](ambient/hooks): a fish hook for failed and long commands, and a git `post-commit`.
5. Screen descriptions: give ambient a `screen` section naming a vision model through one of your `proxy.upstreams` presets (see the device example). It needs Pillow, and `dbus-next` on Linux. To change the vision prompt, set `plugins.ambient.screen` in your prompts.json overlay (`prompts.path` in config.json), as a string or a list of lines.
6. The proactive judge is dusha's own `examples/plugins/system_one`. Copy it into the same plugins directory and point it at your SystemOne server (see the hub example). Without it, better_proactive picks by score and never raises screen moments.

## Develop

```sh
uv sync              # dusha from ../dusha, plus psutil, pytest, ruff
uv run pytest
uv run ruff check .
```
