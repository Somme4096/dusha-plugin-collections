import { describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { connect, fromConfig, locate } from "../lib/connection";
import { scan } from "../lib/local";
import type { Command, Manifest, Meta, PluginStatus } from "../web/api";
import { buildPlugins, cliLine, parseLine, wiring } from "../web/model";

function root(companions: Record<string, object>): Record<string, string> {
  const base = mkdtempSync(join(tmpdir(), "dusha-webui-"));
  for (const [name, config] of Object.entries(companions)) {
    mkdirSync(join(base, "dusha", name), { recursive: true });
    writeFileSync(join(base, "dusha", name, "config.json"), JSON.stringify(config));
  }
  return { XDG_CONFIG_HOME: base, HOME: base };
}

describe("connection", () => {
  test("a server's own config uses its port and api_token", () => {
    expect(fromConfig({ host: "0.0.0.0", port: 9000, api_token: "t" })).toEqual({ url: "http://127.0.0.1:9000", token: "t" });
  });

  test("the proxy's saved server wins, without the local token", () => {
    const raw = { api_token: "local", proxy: { server: { url: "http://nas:8765/", token: "hub" } } };
    expect(fromConfig(raw)).toEqual({ url: "http://nas:8765", token: "hub" });
  });

  test("the only companion is picked, several need a name", () => {
    const one = root({ sophia: { port: 1 } });
    expect(connect({}, one)).toMatchObject({ url: "http://127.0.0.1:1", companion: "sophia" });
    const two = root({ a: {}, b: {} });
    expect(() => locate({}, two)).toThrow("several companions");
    expect(connect({ companion: "b" }, two).companion).toBe("b");
  });

  test("--url and DUSHA_TOKEN override the file", () => {
    const env = { ...root({ sophia: { api_token: "file" } }), DUSHA_TOKEN: "env" };
    expect(connect({}, env).token).toBe("env");
    expect(connect({ url: "http://x:1/", token: "flag" }, env)).toMatchObject({ url: "http://x:1", token: "flag", config: null });
  });
});

describe("local plugins", () => {
  const plugins = scan(resolve(import.meta.dir, "../.."));

  test("finds every plugin in this repository with its wiring", () => {
    const names = plugins.map((plugin) => plugin.name);
    expect(names).toEqual(expect.arrayContaining(["ambient", "better_proactive", "notification", "presence", "relay"]));
    const presence = plugins.find((plugin) => plugin.name === "presence")!;
    expect(presence.emits).toContain("presence.changed");
    expect(presence.collects).toContain("presence.capabilities");
    expect(presence.tools).toContain("presence");
    expect(presence.groups).toContain("presence");
    expect(presence.summary).toMatch(/heartbeats/);
    expect(plugins.find((plugin) => plugin.name === "better_proactive")!.groups).toContain("proactive");
  });

  test("ignores the webui itself", () => {
    expect(plugins.some((plugin) => plugin.name === "webui")).toBe(false);
  });
});

const status = (state: string, extra: Partial<PluginStatus> = {}): PluginStatus => ({
  state,
  error: "",
  inject: [],
  provides: [],
  events: [],
  ...extra,
});

const command = (group: string, name: string, args: Command["args"] = []): Command => ({ group, name, help: "", args });

describe("model", () => {
  const meta = {
    builtins: [{ name: "proactive", description: "", tools: [], routes: [] }],
    local: [
      { name: "better_proactive", groups: ["proactive"], tools: [], emits: ["proactive.sent"], collects: ["presence"], listens: [] },
      { name: "relay", groups: ["relay"], tools: ["look"], emits: [], collects: [], listens: [] },
      { name: "unused", groups: [], tools: [], emits: [], collects: [], listens: [] },
    ],
    coreEvents: ["message"],
  } as unknown as Meta;
  const live = {
    proactive: status("disabled"),
    better_proactive: status("active", { provides: ["proactive"], events: ["message"] }),
    relay: status("active", { events: ["proactive.sent"] }),
  };
  const manifest = { groups: {}, plugins: {}, commands: [command("", "health"), command("proactive", "plan"), command("relay", "look")] } as Manifest;
  const tools = [{ name: "look", description: "", inputSchema: { properties: {}, required: [] } }];

  test("attributes commands and tools, and lists unloaded local plugins", () => {
    const { plugins, coreCommands } = buildPlugins(meta, live, manifest, tools);
    const by = Object.fromEntries(plugins.map((plugin) => [plugin.name, plugin]));
    expect(by.better_proactive!.commands.map((c) => c.name)).toEqual(["plan"]);
    expect(by.relay!.tools.map((t) => t.name)).toEqual(["look"]);
    expect(by.unused!).toMatchObject({ loaded: false, state: "not loaded", origin: "local" });
    expect(by.proactive!.origin).toBe("built-in");
    expect(coreCommands.map((c) => c.name)).toEqual(["health"]);
  });

  test("draws an event edge from emitter to listener, and core events from core", () => {
    const { plugins } = buildPlugins(meta, live, manifest, tools);
    const edges = wiring(plugins, ["message"]);
    expect(edges).toContainEqual({ from: "better_proactive", to: "relay", kind: "event", label: "proactive.sent" });
    expect(edges).toContainEqual({ from: "core", to: "better_proactive", kind: "event", label: "message" });
  });
});

describe("command lines", () => {
  const args: Command["args"] = [
    { name: "text", type: "str", help: "", option: false, required: true, default: null, choices: [], repeated: false },
    { name: "pinned", type: "bool", help: "", option: true, required: false, default: false, choices: [], repeated: false },
    { name: "tags", type: "str", help: "", option: true, required: false, default: null, choices: [], repeated: true },
    { name: "limit", type: "int", help: "", option: true, required: false, default: 1, choices: [], repeated: false },
  ];
  const add = command("notes", "add", args);

  test("parses like click", () => {
    const parsed = parseLine(`dusha notes add "Buy tea" --pinned --tags home --tags errand --limit=3`, [add]);
    expect(parsed.error).toBeUndefined();
    expect(parsed.values).toEqual({ text: "Buy tea", pinned: true, tags: ["home", "errand"], limit: 3 });
  });

  test("reports what's wrong", () => {
    expect(parseLine("notes add x --nope", [add]).error).toBe("no such option: --nope");
    expect(parseLine("notes nope", [add]).error).toMatch(/unknown command/);
    expect(parseLine("notes add a b", [add]).error).toBe("unexpected argument: b");
  });

  test("round-trips through cliLine", () => {
    const values = { text: "Buy tea", pinned: true, tags: ["home"], limit: 1 };
    const line = cliLine(add, values);
    expect(line).toBe("dusha notes add 'Buy tea' --pinned --tags home");
    expect(parseLine(line, [add]).values).toEqual({ text: "Buy tea", pinned: true, tags: ["home"] });
  });
});
