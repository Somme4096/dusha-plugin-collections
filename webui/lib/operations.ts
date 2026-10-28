// What this WebUI did to the companion: every command, tool call, and write it sent, newest first.
export type Operation = {
  id: number;
  at: number;
  kind: "command" | "tool" | "write";
  label: string;
  args: string;
  status: number;
  ms: number;
  error: string;
};

function parse(body: ArrayBuffer | undefined): any {
  if (!body?.byteLength) return {};
  try {
    return JSON.parse(new TextDecoder().decode(body));
  } catch {
    return {};
  }
}

function brief(values: unknown): string {
  if (!values || typeof values !== "object") return "";
  return Object.entries(values as Record<string, unknown>)
    .filter(([, value]) => value !== null && value !== "" && value !== false && !(Array.isArray(value) && !value.length))
    .map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`)
    .join(" ")
    .slice(0, 160);
}

function describe(method: string, path: string, body: any): Pick<Operation, "kind" | "label" | "args"> | null {
  if (method === "GET" || method === "HEAD") return null;
  const command = path.match(/^cli\/v1\/run\/(.+)$/);
  if (command) return { kind: "command", label: command[1]!.replace(/\//g, " "), args: brief(body) };
  if (path === "mcp") {
    if (body?.method !== "tools/call") return null;
    return { kind: "tool", label: String(body.params?.name ?? "tool"), args: brief(body.params?.arguments) };
  }
  return { kind: "write", label: `${method} /${path}`, args: brief(body) };
}

export class Operations {
  private items: Operation[] = [];
  private next = 1;

  constructor(private limit: number) {}

  start(method: string, path: string, body: ArrayBuffer | undefined) {
    const found = describe(method, path, parse(body));
    if (!found) return null;
    const started = performance.now();
    const item: Operation = { id: this.next++, at: Date.now(), ...found, status: 0, ms: 0, error: "" };
    this.items.unshift(item);
    this.items.length = Math.min(this.items.length, this.limit);
    return {
      finish: (status: number, text: string) => {
        item.status = status;
        item.ms = Math.round(performance.now() - started);
        let reply: any = null;
        try {
          reply = JSON.parse(text);
        } catch {}
        if (status >= 400) item.error = String(typeof reply?.detail === "string" ? reply.detail : text).slice(0, 200);
        else if (reply?.result?.isError) item.error = String(reply.result.content?.[0]?.text ?? "tool failed").slice(0, 200);
      },
    };
  }

  list(): Operation[] {
    return this.items;
  }
}
