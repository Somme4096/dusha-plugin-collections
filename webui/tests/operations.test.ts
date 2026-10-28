import { afterAll, beforeAll, expect, test } from "bun:test";
import { resolve } from "node:path";

// A stand-in dusha server, so the WebUI runs end to end without Python.
let dusha: ReturnType<typeof Bun.serve>;
let webui: ReturnType<typeof Bun.spawn>;
let base = "";

beforeAll(async () => {
  dusha = Bun.serve({
    port: 0,
    fetch: async (request) => {
      const path = new URL(request.url).pathname;
      if (request.headers.get("x-companion-token") !== "secret") return Response.json({ detail: "invalid companion token" }, { status: 401 });
      if (path === "/cli/v1/run/presence/status") return Response.json({ devices: [] });
      if (path === "/cli/v1/run/memo/done") return Response.json({ detail: "not found: 9" }, { status: 400 });
      if (path === "/mcp") return Response.json({ jsonrpc: "2.0", id: 1, result: { content: [{ type: "text", text: "{}" }], isError: true } });
      return Response.json({ path });
    },
  });
  const port = 18_900 + Math.floor(Math.random() * 500);
  base = `http://127.0.0.1:${port}`;
  webui = Bun.spawn(["bun", resolve(import.meta.dir, "../server.ts"), "--url", dusha.url.origin, "--token", "secret", "-p", String(port)], {
    env: { ...process.env, NODE_ENV: "production" },
    stdout: "ignore",
    stderr: "ignore",
  });
  for (let i = 0; i < 100; i++) {
    if (await fetch(`${base}/api/operations`).then((r) => r.ok, () => false)) return;
    await Bun.sleep(100);
  }
  throw new Error("the webui never came up");
});

afterAll(() => {
  webui?.kill();
  dusha?.stop(true);
});

const send = (path: string, body: unknown, quiet = false) =>
  fetch(`${base}/api/dusha/${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", ...(quiet ? { "x-webui-quiet": "1" } : {}) },
    body: JSON.stringify(body),
  });

test("commands, tool calls, and failures land in the operations log, quiet polls and reads don't", async () => {
  expect((await send("cli/v1/run/presence/status", {})).status).toBe(200);
  expect((await send("cli/v1/run/memo/done", { note_id: 9, reason: "done" })).status).toBe(400);
  await send("mcp", { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "look", arguments: { device: "macbook" } } });
  await send("mcp", { jsonrpc: "2.0", id: 2, method: "tools/list", params: {} });
  await send("cli/v1/run/proactive/plan", {}, true);
  await fetch(`${base}/api/dusha/state/v1/plugins`);

  const { operations } = await fetch(`${base}/api/operations`).then((r) => r.json());
  expect(operations.map((op: any) => `${op.kind} ${op.label}`)).toEqual(["tool look", "command memo done", "command presence status"]);
  expect(operations[0]).toMatchObject({ args: "device=macbook", error: "{}" });
  expect(operations[1]).toMatchObject({ status: 400, args: "note_id=9 reason=done", error: "not found: 9" });
  expect(operations[2]).toMatchObject({ status: 200, error: "" });
});

test("the token stays on the server side", async () => {
  const meta = await fetch(`${base}/api/meta`).then((r) => r.json());
  expect(JSON.stringify(meta)).not.toContain("secret");
  expect(meta.auth).toBe(true);
});
