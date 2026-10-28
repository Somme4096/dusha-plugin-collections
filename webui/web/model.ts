// One list of plugins out of three sources: what the server runs (/state/v1/plugins), what dusha ships, and
// what sits in the plugin directory. Commands and tools are attributed to the plugin that registers them.
import type { Builtin, Command, LocalPlugin, Manifest, Meta, PluginStatus, Tool } from "./api";

export type Origin = "built-in" | "local" | "external";

export type PluginView = {
  name: string;
  origin: Origin;
  loaded: boolean;
  state: string;
  error: string;
  inject: string[];
  provides: string[];
  listens: string[];
  emits: string[];
  description: string;
  local?: LocalPlugin;
  builtin?: Builtin;
  commands: Command[];
  tools: Tool[];
};

export const CORE = "core";

export function buildPlugins(
  meta: Meta | undefined,
  status: Record<string, PluginStatus> | undefined,
  manifest: Manifest | undefined,
  tools: Tool[] | undefined,
): { plugins: PluginView[]; coreCommands: Command[]; coreTools: Tool[] } {
  const builtins = new Map((meta?.builtins ?? []).map((item) => [item.name, item]));
  const locals = new Map((meta?.local ?? []).map((item) => [item.name, item]));
  const names = [...Object.keys(status ?? {})];
  for (const name of locals.keys()) if (!names.includes(name)) names.push(name);
  if (!status) for (const name of builtins.keys()) if (!names.includes(name)) names.unshift(name);

  const plugins: PluginView[] = names.map((name) => {
    const live = status?.[name];
    const local = locals.get(name);
    const builtin = builtins.get(name);
    const origin: Origin = builtin ? "built-in" : local ? "local" : "external";
    const loaded = Boolean(live);
    return {
      name,
      origin,
      loaded,
      state: live?.state ?? "not loaded",
      error: live?.error ?? "",
      inject: live?.inject.length ? live.inject : local?.inject ?? [],
      provides: live?.provides ?? [],
      listens: live?.events.length ? live.events : local?.listens ?? [],
      emits: [...new Set([...(local?.emits ?? []), ...(local?.collects ?? [])])],
      description: builtin?.description ?? local?.summary ?? local?.description ?? "",
      local,
      builtin,
      commands: [],
      tools: [],
    };
  });

  const byName = new Map(plugins.map((plugin) => [plugin.name, plugin]));
  const provider = (service: string) =>
    plugins.find((plugin) => plugin.state === "active" && plugin.provides.includes(service));

  const coreCommands: Command[] = [];
  for (const command of manifest?.commands ?? []) {
    const group = command.group;
    let owner: PluginView | undefined;
    if (group) {
      // proactive's group belongs to whoever provides it, the built-in or better_proactive.
      owner =
        (group === "proactive" ? provider(group) : undefined) ??
        plugins.find((plugin) => plugin.state === "active" && plugin.local?.groups.includes(group)) ??
        (byName.get(group)?.loaded ? byName.get(group) : undefined) ??
        provider(group);
    }
    if (owner) owner.commands.push(command);
    else coreCommands.push(command);
  }

  const coreTools: Tool[] = [];
  for (const tool of tools ?? []) {
    const owner = plugins.find((plugin) => plugin.builtin?.tools.includes(tool.name) || plugin.local?.tools.includes(tool.name));
    if (owner) owner.tools.push(tool);
    else coreTools.push(tool);
  }

  return { plugins, coreCommands, coreTools };
}

export type Edge = { from: string; to: string; kind: "service" | "event"; label: string };

/** Who feeds whom: a service a plugin injects from another, or an event one emits and another hears. */
export function wiring(plugins: PluginView[], coreEvents: string[]): Edge[] {
  const edges: Edge[] = [];
  const live = plugins.filter((plugin) => plugin.state !== "disabled");
  for (const plugin of live) {
    for (const service of plugin.inject) {
      const source = live.find((other) => other.provides.includes(service) && other.name !== plugin.name);
      if (source) edges.push({ from: source.name, to: plugin.name, kind: "service", label: service });
    }
    for (const event of plugin.listens) {
      const emitters = live.filter((other) => other.emits.includes(event) && other.name !== plugin.name);
      for (const emitter of emitters) edges.push({ from: emitter.name, to: plugin.name, kind: "event", label: event });
      if (!emitters.length && coreEvents.includes(event)) edges.push({ from: CORE, to: plugin.name, kind: "event", label: event });
    }
  }
  const seen = new Set<string>();
  return edges.filter((edge) => {
    const key = `${edge.from}>${edge.to}>${edge.label}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function cliLine(command: Command, values: Record<string, unknown>): string {
  const parts = ["dusha", command.group, command.name].filter(Boolean);
  const quote = (value: unknown) => {
    const text = String(value);
    return /^[\w./:@~-]+$/.test(text) ? text : `'${text.replace(/'/g, "'\\''")}'`;
  };
  for (const arg of command.args) {
    const value = values[arg.name];
    if (value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length)) continue;
    const items = Array.isArray(value) ? value : [value];
    if (!arg.option) parts.push(...items.map(quote));
    else if (arg.type === "bool") {
      if (value !== arg.default) parts.push(value ? `--${arg.name.replace(/_/g, "-")}` : `--no-${arg.name.replace(/_/g, "-")}`);
    } else if (value !== arg.default) {
      for (const item of items) parts.push(`--${arg.name.replace(/_/g, "-")}`, quote(item));
    }
  }
  return parts.join(" ");
}

/** Parses `group name args --opt value` the way click would for these specs. */
export function parseLine(
  line: string,
  commands: Command[],
): { command?: Command; values: Record<string, unknown>; error?: string } {
  const words = [...line.matchAll(/'([^']*)'|"((?:\\.|[^"])*)"|(\S+)/g)].map((m) => m[1] ?? m[2] ?? m[3] ?? "");
  if (words[0] === "dusha") words.shift();
  const [first, second] = words;
  let command = commands.find((item) => !item.group && item.name === first);
  let rest = words.slice(1);
  if (!command) {
    command = commands.find((item) => item.group === first && item.name === second);
    rest = words.slice(2);
  }
  if (!command) return { values: {}, error: first ? `unknown command: ${words.slice(0, 2).join(" ")}` : "" };

  const values: Record<string, unknown> = {};
  const positionals = command.args.filter((arg) => !arg.option);
  const options = new Map(command.args.filter((arg) => arg.option).map((arg) => [arg.name.replace(/_/g, "-"), arg]));
  const convert = (arg: (typeof command.args)[number], text: string): unknown => {
    if (arg.type === "int") return Number.parseInt(text, 10);
    if (arg.type === "float") return Number.parseFloat(text);
    return text;
  };
  const loose: string[] = [];
  for (let i = 0; i < rest.length; i++) {
    const word = rest[i]!;
    if (word.startsWith("--")) {
      const [flag, inline] = word.slice(2).split("=", 2) as [string, string | undefined];
      const negated = flag.startsWith("no-") && options.get(flag.slice(3))?.type === "bool";
      const arg = options.get(negated ? flag.slice(3) : flag);
      if (!arg) return { command, values, error: `no such option: --${flag}` };
      if (arg.type === "bool") {
        values[arg.name] = !negated;
        continue;
      }
      const text = inline ?? rest[++i];
      if (text === undefined) return { command, values, error: `--${flag} needs a value` };
      const value = convert(arg, text);
      values[arg.name] = arg.repeated ? [...((values[arg.name] as unknown[]) ?? []), value] : value;
    } else loose.push(word);
  }
  for (const arg of positionals) {
    if (arg.repeated) {
      values[arg.name] = loose.splice(0).map((text) => convert(arg, text));
    } else if (loose.length) values[arg.name] = convert(arg, loose.shift()!);
  }
  if (loose.length) return { command, values, error: `unexpected argument: ${loose[0]}` };
  return { command, values };
}
