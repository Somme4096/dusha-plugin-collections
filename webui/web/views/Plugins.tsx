import { useEffect } from "preact/hooks";

import { get, usePoll } from "../api";
import { go } from "../app";
import { Json } from "../components/Json";
import { Runner } from "../components/Runner";
import { ago } from "../format";
import type { PluginView } from "../model";
import { useStore } from "../store";
import { State } from "./Overview";

const PAGES: Record<string, string> = {
  presence: "presence",
  ambient: "ambient",
  better_proactive: "proactive",
  proactive: "proactive",
};

type Memo = { id: number; text: string; created_at: string };
type Fact = { fact_id: string; key: string; text: string; priority: number; revision: number; updated_at?: string; created_at: string };

/** What a built-in holds, read through its own routes. */
function Holdings({ plugin }: { plugin: PluginView }) {
  const reads: Record<string, string> = {
    memo: "state/v1/memo/list?status=active&limit=50",
    evergreen: "state/v1/evergreen/facts?limit=50",
    affect: "state/v1/affect",
  };
  const path = plugin.state === "active" ? reads[plugin.name] : undefined;
  const data = usePoll(() => (path ? get<any>(path) : Promise.resolve(null)), 15_000, [path]);
  if (!path) return null;
  if (!data.data) return <p class="faint">{data.error ? data.error.message : "Reading…"}</p>;
  if (plugin.name === "memo") {
    const memos: Memo[] = data.data.memos;
    return memos.length ? (
      <ul class="feed">
        {memos.map((memo) => (
          <li key={memo.id} style={{ gridTemplateColumns: "44px minmax(0,1fr) 90px" }}>
            <span class="faint">#{memo.id}</span>
            <span>{memo.text}</span>
            <span class="faint small num">{ago(memo.created_at)}</span>
          </li>
        ))}
      </ul>
    ) : (
      <p class="empty">No active memos.</p>
    );
  }
  if (plugin.name === "evergreen") {
    const facts: Fact[] = data.data.facts;
    return facts.length ? (
      <table class="table">
        <tbody>
          {facts.map((fact) => (
            <tr key={fact.fact_id}>
              <td class="faint small" style={{ width: "36%" }}>{fact.key}</td>
              <td>{fact.text}</td>
              <td class="num faint small">p{fact.priority} · r{fact.revision}</td>
            </tr>
          ))}
        </tbody>
      </table>
    ) : (
      <p class="empty">No facts remembered yet.</p>
    );
  }
  return <Json value={data.data} />;
}

function Detail({ plugin }: { plugin: PluginView }) {
  const store = useStore();
  const local = plugin.local;
  const page = PAGES[plugin.name];
  const routes = local?.routes.map((route) => `${route.method} ${route.path}`) ?? plugin.builtin?.routes ?? [];
  const wiredIn = store.edges.filter((edge) => edge.to === plugin.name);
  const wiredOut = store.edges.filter((edge) => edge.from === plugin.name);

  return (
    <article class="detail" aria-label={plugin.name}>
      <h2>{plugin.name}</h2>
      <div class="tags" style={{ marginTop: "10px" }}>
        <span class={`tag ${plugin.origin === "local" ? "accent" : ""}`}>{plugin.origin}</span>
        {local && <span class="tag">{local.package}</span>}
        {local && <span class="tag">{local.file} · {local.lines} lines</span>}
      </div>
      {plugin.description && <p class="lede">{plugin.description}</p>}
      {local?.description && local.summary && <p class="lede faint">{local.description}</p>}
      <div style={{ marginTop: "14px", display: "flex", gap: "16px", flexWrap: "wrap", alignItems: "center" }}>
        <State state={plugin.state} />
        {page && plugin.state === "active" && (
          <a class="btn" href={`#/${page}`}>
            Open {page}
          </a>
        )}
      </div>
      {plugin.error && <p class="error-line" style={{ marginTop: "10px" }}>{plugin.error}</p>}
      {!plugin.loaded && (
        <p class="empty" style={{ paddingBottom: "0px" }}>
          <strong>This server doesn't load {plugin.name}.</strong> Add it under <code>plugins</code> in config.json, with{" "}
          <code>plugins_dir</code> pointing at <code>{store.meta?.pluginsDir}</code>, then restart <code>dusha serve</code>.
        </p>
      )}

      <section class="detail-block">
        <h3>wiring</h3>
        <dl class="facts">
          <dt>injects</dt>
          <dd>{plugin.inject.join(", ") || "—"}</dd>
          <dt>provides</dt>
          <dd>{plugin.provides.join(", ") || "—"}</dd>
          <dt>hears</dt>
          <dd>{plugin.listens.join(", ") || "—"}</dd>
          {local && (
            <>
              <dt>emits</dt>
              <dd>{local.emits.join(", ") || "—"}</dd>
              <dt>asks</dt>
              <dd>{local.collects.join(", ") || "—"}</dd>
            </>
          )}
          <dt>from</dt>
          <dd>{wiredIn.map((edge) => `${edge.from} (${edge.label})`).join(", ") || "—"}</dd>
          <dt>to</dt>
          <dd>{wiredOut.map((edge) => `${edge.to} (${edge.label})`).join(", ") || "—"}</dd>
          {routes.length > 0 && (
            <>
              <dt>routes</dt>
              <dd>
                {routes.map((route) => (
                  <div key={route}>{route}</div>
                ))}
              </dd>
            </>
          )}
          {local?.intervals ? (
            <>
              <dt>jobs</dt>
              <dd>{local.intervals} interval{local.intervals > 1 ? "s" : ""}</dd>
            </>
          ) : null}
        </dl>
      </section>

      {["memo", "evergreen", "affect"].includes(plugin.name) && plugin.state === "active" && (
        <section class="detail-block">
          <h3>holds</h3>
          <Holdings plugin={plugin} />
        </section>
      )}

      <section class="detail-block">
        <h3>commands · {plugin.commands.length}</h3>
        {plugin.commands.length ? (
          plugin.commands.map((command) => <Runner key={`${command.group}/${command.name}`} kind="command" item={command} />)
        ) : (
          <p class="faint">{plugin.loaded ? "Registers no CLI commands." : "Commands appear once the server loads it."}</p>
        )}
      </section>

      <section class="detail-block">
        <h3>mcp tools · {plugin.tools.length}</h3>
        {plugin.tools.length ? (
          plugin.tools.map((tool) => <Runner key={tool.name} kind="tool" item={tool} />)
        ) : (
          <p class="faint">{local?.tools.length && !plugin.loaded ? `Offers ${local.tools.join(", ")} once loaded.` : "Offers no MCP tools."}</p>
        )}
      </section>
    </article>
  );
}

export function Plugins({ selected }: { selected: string }) {
  const store = useStore();
  const plugin = store.plugins.find((item) => item.name === selected) ?? store.plugins[0];

  useEffect(() => {
    if (!selected && store.plugins[0]) history.replaceState(null, "", `#/plugins/${store.plugins[0].name}`);
  }, [selected, store.plugins.length]);

  const groups: [string, PluginView[]][] = [
    ["built into dusha", store.plugins.filter((item) => item.origin === "built-in")],
    [`in ${store.meta?.pluginsDir.split("/").pop() ?? "this directory"}`, store.plugins.filter((item) => item.origin === "local")],
    ["elsewhere", store.plugins.filter((item) => item.origin === "external")],
  ];

  return (
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Plugins</h1>
          <p>
            Everything the server runs, plus what sits in <code>{store.meta?.pluginsDir}</code>. Run any command as
            a form; it goes through the same <code>/cli/v1</code> routes as <code>dusha</code>.
          </p>
        </div>
      </div>
      <div class="master">
        <div>
          {groups
            .filter(([, items]) => items.length)
            .map(([label, items]) => (
              <section key={label} style={{ marginBottom: "32px" }}>
                <table class="table fixed">
                  <colgroup>
                    <col />
                    <col style={{ width: "34%" }} />
                    <col class="hide-sm" style={{ width: "64px" }} />
                    <col class="hide-sm" style={{ width: "64px" }} />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>{label}</th>
                      <th>state</th>
                      <th class="num hide-sm">cmds</th>
                      <th class="num hide-sm">tools</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr
                        class="row"
                        key={item.name}
                        aria-selected={item.name === plugin?.name}
                        onClick={() => go("plugins", item.name)}
                      >
                        <td>
                          <a href={`#/plugins/${item.name}`} style={{ textDecoration: "none" }}>
                            {item.name}
                          </a>
                        </td>
                        <td>
                          <State state={item.state} />
                        </td>
                        <td class="num faint hide-sm">{item.commands.length || ""}</td>
                        <td class="num faint hide-sm">{item.tools.length || ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            ))}
          {(store.coreCommands.length > 0 || store.coreTools.length > 0) && (
            <section style={{ marginTop: "16px" }}>
              <div class="section-head">
                <h2>core</h2>
                <span class="aside">dusha's own commands and unattributed tools</span>
              </div>
              {store.coreCommands.map((command) => (
                <Runner key={`${command.group}/${command.name}`} kind="command" item={command} />
              ))}
              {store.coreTools.map((tool) => (
                <Runner key={tool.name} kind="tool" item={tool} />
              ))}
            </section>
          )}
        </div>
        {plugin ? <Detail key={plugin.name} plugin={plugin} /> : <p class="empty">No plugins reported yet.</p>}
      </div>
    </div>
  );
}
