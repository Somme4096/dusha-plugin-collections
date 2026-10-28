// Talks to the WebUI server: /api/meta for what this process knows, /api/dusha/* for the companion.
import { useEffect, useRef, useState } from "preact/hooks";

export type Arg = {
  name: string;
  type: "str" | "int" | "float" | "bool" | "choice";
  help: string;
  option: boolean;
  required: boolean;
  default: unknown;
  choices: string[];
  repeated: boolean;
};

export type Command = { group: string; name: string; help: string; args: Arg[] };
export type Manifest = { groups: Record<string, string>; commands: Command[]; plugins: Record<string, string> };

export type PluginStatus = {
  state: "active" | "disabled" | "inactive" | "failed" | "pending" | string;
  error: string;
  inject: string[];
  provides: string[];
  events: string[];
};

export type Tool = { name: string; description: string; inputSchema: { properties: Record<string, any>; required: string[] } };

export type LocalPlugin = {
  name: string;
  package: string;
  description: string;
  summary: string;
  entry: string;
  inject: string[];
  file: string;
  lines: number;
  emits: string[];
  collects: string[];
  listens: string[];
  tools: string[];
  groups: string[];
  routes: { method: string; path: string }[];
  intervals: number;
};

export type Builtin = { name: string; description: string; tools: string[]; routes: string[] };

export type Meta = {
  companion: string;
  server: string;
  auth: boolean;
  config: string | null;
  pluginsDir: string;
  local: LocalPlugin[];
  builtins: Builtin[];
  coreEvents: string[];
};

export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function json<T>(response: Response): Promise<T> {
  const text = await response.text();
  let body: any = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : body;
    throw new HttpError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body as T;
}

export const get = <T>(path: string) => fetch(`/api/dusha/${path}`).then((r) => json<T>(r));

export const post = <T>(path: string, body: unknown = {}) =>
  fetch(`/api/dusha/${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => json<T>(r));

export const meta = () => fetch("/api/meta").then((r) => json<Meta>(r));

export const run = (command: Command, values: Record<string, unknown>) =>
  post<unknown>(`cli/v1/run/${[command.group, command.name].filter(Boolean).join("/")}`, values);

let rpc = 0;

export async function mcp<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  const reply = await post<{ result?: T; error?: { message: string } }>("mcp", {
    jsonrpc: "2.0",
    id: ++rpc,
    method,
    params,
  });
  if (reply.error) throw new Error(reply.error.message);
  return reply.result as T;
}

export const callTool = async (name: string, args: Record<string, unknown>) => {
  const result = await mcp<{ content: { text: string }[]; isError?: boolean }>("tools/call", { name, arguments: args });
  const text = result.content.map((part) => part.text).join("\n");
  let value: unknown = text;
  try {
    value = JSON.parse(text);
  } catch {}
  if (result.isError) throw new Error(typeof value === "object" ? JSON.stringify(value) : text);
  return value;
};

export type Poll<T> = { data?: T; error?: Error; loading: boolean; at: number; refresh: () => void };

/** Calls `load` now and every `ms` while the tab is visible. Keeps the last good data through errors. */
export function usePoll<T>(load: () => Promise<T>, ms: number, deps: unknown[] = []): Poll<T> {
  const [state, setState] = useState<{ data?: T; error?: Error; loading: boolean; at: number }>({ loading: true, at: 0 });
  const tick = useRef(0);
  const loader = useRef(load);
  loader.current = load;

  const refresh = () => {
    const id = ++tick.current;
    loader
      .current()
      .then((data) => id === tick.current && setState({ data, error: undefined, loading: false, at: Date.now() }))
      .catch((error: Error) => id === tick.current && setState((old) => ({ ...old, error, loading: false })));
  };

  useEffect(() => {
    refresh();
    if (!ms) return;
    const timer = setInterval(() => document.hidden || refresh(), ms);
    return () => {
      clearInterval(timer);
      tick.current++;
    };
  }, deps);

  return { ...state, refresh };
}
