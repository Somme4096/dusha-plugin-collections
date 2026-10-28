// A command or an MCP tool as a form. Commands take the CLI's argument specs; tools take a JSON schema,
// which we turn into the same specs so both render alike.
import { useState } from "preact/hooks";

import { type Arg, type Command, type Tool, callTool, run } from "../api";
import { cliLine } from "../model";
import { Chevron } from "./icons";
import { Json } from "./Json";

export function toolArgs(tool: Tool): Arg[] {
  const required = new Set(tool.inputSchema.required ?? []);
  return Object.entries(tool.inputSchema.properties ?? {}).map(([name, schema]) => {
    const repeated = schema.type === "array";
    const item = repeated ? schema.items ?? {} : schema;
    const choices: string[] = item.enum ?? [];
    const kinds: Record<string, Arg["type"]> = { string: "str", integer: "int", number: "float", boolean: "bool" };
    return {
      name,
      type: choices.length ? "choice" : kinds[item.type] ?? "str",
      help: schema.description ?? "",
      option: !required.has(name),
      required: required.has(name),
      default: schema.default ?? null,
      choices,
      repeated,
    };
  });
}

function initial(args: Arg[]): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const arg of args) {
    if (arg.type === "bool") values[arg.name] = Boolean(arg.default);
    else if (arg.default !== null && arg.default !== undefined) {
      values[arg.name] = Array.isArray(arg.default) ? arg.default.join(" ") : String(arg.default);
    } else values[arg.name] = "";
  }
  return values;
}

function convert(args: Arg[], raw: Record<string, unknown>): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const arg of args) {
    const value = raw[arg.name];
    if (arg.type === "bool") {
      values[arg.name] = Boolean(value);
      continue;
    }
    const text = String(value ?? "").trim();
    if (!text) continue;
    const one = (part: string) =>
      arg.type === "int" ? Number.parseInt(part, 10) : arg.type === "float" ? Number.parseFloat(part) : part;
    values[arg.name] = arg.repeated ? text.split(/\s+/).map(one) : one(arg.type === "str" ? String(value) : text);
  }
  return values;
}

type Props = { kind: "command"; item: Command } | { kind: "tool"; item: Tool };

export function Runner(props: Props) {
  const args = props.kind === "command" ? props.item.args : toolArgs(props.item);
  const [raw, setRaw] = useState(() => initial(args));
  const [result, setResult] = useState<{ ok: boolean; value: unknown; ms: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const values = convert(args, raw);
  const name =
    props.kind === "command" ? [props.item.group, props.item.name].filter(Boolean).join(" ") : props.item.name;
  const help = props.kind === "command" ? props.item.help : props.item.description;

  const submit = async (event: Event) => {
    event.preventDefault();
    setBusy(true);
    const started = performance.now();
    try {
      const value = props.kind === "command" ? await run(props.item, values) : await callTool(props.item.name, values);
      setResult({ ok: true, value, ms: performance.now() - started });
    } catch (error) {
      setResult({ ok: false, value: (error as Error).message, ms: performance.now() - started });
    } finally {
      setBusy(false);
    }
  };

  return (
    <details class="cmd">
      <summary>
        <span>{name}</span>
        <span class="help">{help}</span>
        <span class="chev"><Chevron /></span>
      </summary>
      <form class="cmd-form" onSubmit={submit}>
        {args.map((arg) => (
          <div class="arg" key={arg.name}>
            <label for={`${name}-${arg.name}`}>
              {arg.option ? `--${arg.name.replace(/_/g, "-")}` : arg.name}
              {arg.required && <span class="req"> *</span>}
            </label>
            {arg.type === "bool" ? (
              <span class="check">
                <input
                  id={`${name}-${arg.name}`}
                  type="checkbox"
                  checked={Boolean(raw[arg.name])}
                  onChange={(e) => setRaw({ ...raw, [arg.name]: e.currentTarget.checked })}
                />
              </span>
            ) : arg.type === "choice" ? (
              <select
                id={`${name}-${arg.name}`}
                class="input"
                value={String(raw[arg.name] ?? "")}
                onChange={(e) => setRaw({ ...raw, [arg.name]: e.currentTarget.value })}
              >
                {!arg.required && <option value="">(default)</option>}
                {arg.choices.map((choice) => (
                  <option key={choice}>{choice}</option>
                ))}
              </select>
            ) : (
              <input
                id={`${name}-${arg.name}`}
                class="input"
                inputMode={arg.type === "int" || arg.type === "float" ? "decimal" : undefined}
                placeholder={arg.repeated ? "space separated" : arg.type}
                value={String(raw[arg.name] ?? "")}
                onInput={(e) => setRaw({ ...raw, [arg.name]: e.currentTarget.value })}
                required={arg.required}
              />
            )}
            {arg.help && <span class="hint">{arg.help}</span>}
          </div>
        ))}
        <div class="cmd-actions">
          <button class="btn primary" type="submit" disabled={busy}>
            {busy ? "Running…" : props.kind === "command" ? "Run" : "Call"}
          </button>
          {props.kind === "command" && <code class="cli-line">{cliLine(props.item, values)}</code>}
          {result && (
            <span class={`small ${result.ok ? "faint" : "bad-text"}`}>
              {result.ok ? "ok" : "failed"} · {Math.round(result.ms)} ms
            </span>
          )}
        </div>
        {result &&
          (result.ok ? <Json value={result.value} /> : <p class="error-line">{String(result.value)}</p>)}
      </form>
    </details>
  );
}
