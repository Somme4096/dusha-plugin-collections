// The dusha WebUI: another client next to `dusha`, so it finds its server the same way and talks only
// to the public routes. The browser never sees the token; this process adds it on the way through.
import { resolve } from "node:path";
import { parseArgs } from "node:util";

import index from "./web/index.html";
import { BUILTINS, CORE_EVENTS } from "./lib/builtins";
import { type Connection, connect } from "./lib/connection";
import { scan } from "./lib/local";

const HELP = `Usage: bun server.ts [options]

Serves the dusha WebUI for one companion.

  -c, --companion NAME  Companion under ~/.config/dusha
      --home DIR        Companion home directory
      --config FILE     Configuration file
      --url URL         Server URL, overriding the config
      --token TOKEN     Server token, overriding the config (or DUSHA_TOKEN)
      --plugins DIR     Plugin directory to describe (default: this repository)
      --host HOST       Listen address (default 127.0.0.1)
  -p, --port PORT       Listen port (default 8767)
  -h, --help`;

const { values: flags } = parseArgs({
  args: Bun.argv.slice(2),
  options: {
    companion: { type: "string", short: "c" },
    home: { type: "string" },
    config: { type: "string" },
    url: { type: "string" },
    token: { type: "string" },
    plugins: { type: "string" },
    host: { type: "string", default: "127.0.0.1" },
    port: { type: "string", short: "p", default: "8767" },
    help: { type: "boolean", short: "h" },
  },
});

if (flags.help) {
  console.log(HELP);
  process.exit(0);
}

let connection: Connection;
try {
  connection = connect(flags);
} catch (error) {
  console.error(`dusha-webui: ${(error as Error).message}`);
  process.exit(2);
}
const pluginsDir = resolve(flags.plugins ?? resolve(import.meta.dir, ".."));
const TIMEOUT_MS = 15_000;

async function forward(request: Request, path: string): Promise<Response> {
  const url = new URL(request.url);
  const headers = new Headers({ accept: request.headers.get("accept") ?? "application/json" });
  const type = request.headers.get("content-type");
  if (type) headers.set("content-type", type);
  if (connection.token) headers.set("x-companion-token", connection.token);
  try {
    const upstream = await fetch(`${connection.url}/${path}${url.search}`, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch (error) {
    const reason = (error as Error).name === "TimeoutError" ? "timed out" : "unreachable";
    return Response.json({ detail: `${connection.url} is ${reason}` }, { status: 502 });
  }
}

const server = Bun.serve({
  hostname: flags.host,
  port: Number(flags.port),
  development: process.env.NODE_ENV !== "production" && { hmr: true, console: false },
  routes: {
    "/": index,
    "/api/meta": () =>
      Response.json({
        companion: connection.companion,
        server: connection.url,
        auth: Boolean(connection.token),
        config: connection.config,
        pluginsDir,
        local: scan(pluginsDir),
        builtins: BUILTINS,
        coreEvents: CORE_EVENTS,
      }),
    "/api/dusha/*": (request) => forward(request, new URL(request.url).pathname.replace(/^\/api\/dusha\//, "")),
  },
  fetch: () => new Response("not found", { status: 404 }),
});

const who = connection.companion ? `${connection.companion} at ${connection.url}` : connection.url;
console.log(`dusha webui for ${who}\n  ${server.url}`);
