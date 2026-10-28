// Finds the dusha server the same way `dusha` does (dusha/homes.py and dusha/client/settings.py):
// --config, --home, --companion, DUSHA_HOME, DUSHA_COMPANION, then the only companion under
// $XDG_CONFIG_HOME/dusha. The proxy's saved server wins, otherwise the local server and its api_token.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

export const SERVER_URL = "http://127.0.0.1:8765";

export type Connection = { url: string; token: string; companion: string; config: string | null };

export type Selection = {
  config?: string;
  home?: string;
  companion?: string;
  url?: string;
  token?: string;
};

const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

function expand(value: string, env: Record<string, string | undefined>): string {
  const home = env.HOME || homedir();
  return value
    .replace(/^~(?=$|\/)/, home)
    .replace(/\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?/g, (_, key: string) => env[key] ?? "");
}

export function configRoot(env: Record<string, string | undefined> = process.env): string {
  return join(env.XDG_CONFIG_HOME || join(env.HOME || homedir(), ".config"), "dusha");
}

export function listCompanions(env: Record<string, string | undefined> = process.env): string[] {
  const root = configRoot(env);
  if (!existsSync(root) || !statSync(root).isDirectory()) return [];
  return readdirSync(root)
    .filter((name) => existsSync(join(root, name, "config.json")))
    .sort();
}

function companionHome(name: string, env: Record<string, string | undefined>): string {
  if (!NAME.test(name)) {
    throw new Error(`companion name must be letters, digits, dot, dash, or underscore, got ${JSON.stringify(name)}`);
  }
  return join(configRoot(env), name);
}

export function locate(selection: Selection, env: Record<string, string | undefined> = process.env): string | null {
  if (selection.config) return expand(selection.config, env);
  let home: string | null = null;
  if (selection.home) home = expand(selection.home, env);
  else if (selection.companion) home = companionHome(selection.companion, env);
  else if (env.DUSHA_HOME) home = expand(env.DUSHA_HOME, env);
  else if (env.DUSHA_COMPANION) home = companionHome(env.DUSHA_COMPANION, env);
  else {
    const names = listCompanions(env);
    if (names.length > 1) throw new Error(`several companions exist: ${names.join(", ")}. Name one with --companion`);
    if (names.length === 1) home = companionHome(names[0]!, env);
  }
  if (home === null) return null;
  const path = join(home, "config.json");
  if (!existsSync(path)) throw new Error(`configuration file not found: ${path}`);
  return path;
}

function localUrl(raw: Record<string, unknown>): string {
  let host = typeof raw.host === "string" ? raw.host : "127.0.0.1";
  const port = typeof raw.port === "number" ? raw.port : 8765;
  if (host === "" || host === "0.0.0.0" || host === "::") host = "127.0.0.1";
  return `http://${host.includes(":") ? `[${host}]` : host}:${port}`;
}

export function fromConfig(raw: Record<string, unknown>): { url: string; token: string } {
  const proxy = (raw.proxy ?? {}) as Record<string, unknown>;
  const server = (proxy.server ?? {}) as Record<string, unknown>;
  const local = localUrl(raw);
  const url = typeof server.url === "string" && server.url ? server.url.replace(/\/+$/, "") : local;
  let token = typeof server.token === "string" ? server.token : "";
  if (url === local && !token && typeof raw.api_token === "string") token = raw.api_token;
  return { url, token };
}

export function connect(selection: Selection, env: Record<string, string | undefined> = process.env): Connection {
  const path = selection.url ? null : locate(selection, env);
  let found = { url: SERVER_URL, token: "" };
  if (path) found = fromConfig(JSON.parse(readFileSync(path, "utf8")));
  const url = (selection.url ?? found.url).replace(/\/+$/, "");
  const token = selection.token ?? env.DUSHA_TOKEN ?? found.token;
  const companion = path ? resolve(path, "..").split("/").pop() ?? "" : "";
  return { url, token, companion, config: path };
}
