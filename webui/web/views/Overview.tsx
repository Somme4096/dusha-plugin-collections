import { useMemo, useState } from "preact/hooks";

import { type Operation, get, operations, post, usePoll } from "../api";
import { go } from "../app";
import { type Lane, Plane } from "../components/Plane";
import { Wiring } from "../components/Wiring";
import { ago, clock } from "../format";
import { useStore } from "../store";

export type Affect = {
  base: Record<string, number>;
  mood: Record<string, number>;
  last_user_message_at: string | null;
  last_proactive_sent_at: string | null;
  unanswered_proactive: number;
};

export type Observation = { id: number; device: string; source: string; kind: string; text: string; at: string; created_at: number };

type Message = {
  id: number;
  role: string;
  text: string;
  occurred_at: string;
  harness: string;
  external_conversation_id: string;
};

type Memo = { id: number; text: string; status: string; created_at: string; archived_at: string | null; reason: string };
type Fact = { fact_id: string; key: string; text: string; revision: number; created_at: string };
type Device = { device: string; status: string };
type Snapshot = { active_device: string | null; devices: Device[] };

type Activity = { key: string; at: number; source: "webui" | "ambient" | "memory" | "proactive"; tag: string; text: string; detail?: string; tone?: "bad" | "accent" };

const LANES = 8;
const HOUR = 3_600_000;
const none = () => Promise.resolve(null);
const time = (value: string | number) => (typeof value === "number" ? (value < 1e12 ? value * 1000 : value) : Date.parse(value));

/** Counts per bucket over the last `span` milliseconds, newest bucket last. */
function histogram(times: number[], buckets: number, span: number, now = Date.now()): number[] {
  const counts = new Array(buckets).fill(0);
  for (const at of times) {
    const age = now - at;
    if (age < 0 || age >= span) continue;
    counts[buckets - 1 - Math.floor((age / span) * buckets)]++;
  }
  return counts;
}

function Spark({ counts, label }: { counts: number[]; label: string }) {
  const top = Math.max(1, ...counts);
  return (
    <span class="spark" role="img" aria-label={label}>
      {counts.map((count, i) => (
        <i key={i} style={{ opacity: count ? 0.35 + 0.65 * (count / top) : 1 }} class={count ? "on" : ""} />
      ))}
    </span>
  );
}

function Reading({ label, value, note, children }: { label: string; value: string; note: string; children?: preact.ComponentChildren }) {
  return (
    <div class="reading-cell">
      <span class="reading-label">{label}</span>
      <span class="reading-value">{value}</span>
      <span class="reading-note">{note}</span>
      {children}
    </div>
  );
}

const FILTERS = ["all", "webui", "ambient", "memory", "proactive"] as const;

export function Overview() {
  const store = useStore();
  const on = (name: string) => store.active(name);
  const affect = usePoll(() => (on("affect") ? get<Affect>("state/v1/affect") : none()), 6_000, [on("affect")]);
  const presence = usePoll(() => (on("presence") ? get<Snapshot>("state/v1/presence") : none()), 10_000, [on("presence")]);
  const observed = usePoll(
    () => (on("ambient") ? get<{ observations: Observation[] }>("state/v1/ambient?hours=24&limit=40") : none()),
    10_000,
    [on("ambient")],
  );
  const messages = usePoll(
    () => (on("memory") ? post<{ messages: Message[] }>("cli/v1/run/memory/recent", { limit: 120 }, true) : none()),
    6_000,
    [on("memory")],
  );
  const memos = usePoll(
    () =>
      on("memo")
        ? Promise.all([
            get<{ memos: Memo[] }>("state/v1/memo/list?status=active&limit=50"),
            get<{ memos: Memo[] }>("state/v1/memo/list?status=archived&limit=20"),
          ])
        : none(),
    20_000,
    [on("memo")],
  );
  const facts = usePoll(() => (on("evergreen") ? get<{ facts: Fact[] }>("state/v1/evergreen/facts?limit=100") : none()), 20_000, [on("evergreen")]);
  const ops = usePoll(operations, 4_000);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");

  const name = store.health?.companion || store.meta?.companion || "dusha";
  const loaded = store.plugins.filter((plugin) => plugin.loaded).length;
  const active = store.plugins.filter((plugin) => plugin.state === "active").length;
  const recent = [...(messages.data?.messages ?? [])].reverse();
  const now = Date.now();
  const dayOfMessages = recent.map((m) => time(m.occurred_at)).filter((at) => now - at < 24 * HOUR);
  const conversations = new Set(recent.filter((m) => now - time(m.occurred_at) < 24 * HOUR).map((m) => `${m.harness}/${m.external_conversation_id}`));
  const harnesses = [...new Set(recent.slice(0, 40).map((m) => m.harness))];
  const devices = presence.data?.devices ?? [];
  const up = devices.filter((d) => !["sleeping", "off", "unreachable"].includes(d.status)).length;
  const operationList: Operation[] = ops.data?.operations ?? [];
  const failures = operationList.filter((op) => op.error).length;

  const lanes: Lane[] = useMemo(() => {
    const mood = affect.data?.mood;
    if (mood) {
      return Object.entries(mood)
        .sort((a, b) => b[1] - a[1])
        .slice(0, LANES)
        .map(([dimension, value]) => ({ name: dimension, value }));
    }
    return store.plugins.slice(0, LANES).map((plugin) => ({
      name: plugin.name,
      value: plugin.state === "active" ? 0.5 : 0.06,
      hot: plugin.state === "failed" || plugin.state === "inactive" ? 1 : 0,
    }));
  }, [affect.data, store.plugins]);

  const activity = useMemo<Activity[]>(() => {
    const items: Activity[] = [];
    for (const op of operationList) {
      items.push({
        key: `op${op.id}`,
        at: op.at,
        source: "webui",
        tag: op.kind,
        text: `${op.label}${op.args ? ` ${op.args}` : ""}`,
        detail: op.error || `${op.ms} ms`,
        tone: op.error ? "bad" : undefined,
      });
    }
    for (const item of observed.data?.observations ?? []) {
      items.push({
        key: `amb${item.id}`,
        at: time(item.created_at),
        source: "ambient",
        tag: item.source,
        text: item.text.startsWith(`${item.device}:`) ? item.text : `${item.device}: ${item.text}`,
        tone: item.kind === "alert" ? "bad" : undefined,
      });
    }
    const [open, done] = memos.data ?? [];
    for (const memo of open?.memos ?? []) items.push({ key: `memo${memo.id}`, at: time(memo.created_at), source: "memory", tag: "memo", text: `noted: ${memo.text}` });
    for (const memo of done?.memos ?? []) {
      items.push({ key: `done${memo.id}`, at: time(memo.archived_at ?? memo.created_at), source: "memory", tag: "memo", text: `done: ${memo.text}`, detail: memo.reason });
    }
    for (const fact of facts.data?.facts ?? []) {
      items.push({ key: `fact${fact.fact_id}`, at: time(fact.created_at), source: "memory", tag: "fact", text: `${fact.revision > 1 ? "revised" : "remembered"} ${fact.key}`, detail: fact.text });
    }
    if (affect.data?.last_proactive_sent_at) {
      items.push({
        key: "proactive",
        at: time(affect.data.last_proactive_sent_at),
        source: "proactive",
        tag: "sent",
        text: "reached out on their own",
        detail: `${affect.data.unanswered_proactive} unanswered`,
        tone: "accent",
      });
    }
    return items.filter((item) => !Number.isNaN(item.at)).sort((a, b) => b.at - a.at);
  }, [operationList, observed.data, memos.data, facts.data, affect.data]);

  const shown = activity.filter((item) => filter === "all" || item.source === filter).slice(0, 18);
  const pulse = (recent[0]?.id ?? 0) + (operationList[0]?.id ?? 0) + (observed.data?.observations[0]?.id ?? 0);

  return (
    <div class="page">
      <header class="dash-head">
        <h1>{name}</h1>
        <p class="status">
          <span class={`dot ${store.online ? "ok live" : "bad"}`} />
          {store.online ? `${active} of ${loaded} plugins active` : "server unreachable"}
          {presence.data?.active_device ? ` · they're on ${presence.data.active_device}` : ""}
          {affect.data ? ` · last message ${ago(affect.data.last_user_message_at)}` : ""}
        </p>
      </header>

      <Plane
        lanes={lanes}
        pulse={pulse}
        label={affect.data ? "The strongest affect dimensions as lanes of bits, brighter where the mood runs higher" : "Plugins as lanes of bits"}
      />

      <div class="readout">
        <Reading label="messages · 24h" value={String(dayOfMessages.length)} note={`last ${ago(recent[0]?.occurred_at)}`}>
          <Spark counts={histogram(dayOfMessages, 24, 24 * HOUR, now)} label="Messages per hour over the last day" />
        </Reading>
        <Reading label="conversations" value={String(conversations.size)} note={harnesses.join(", ") || "none yet"} />
        <Reading
          label="remembered"
          value={String(facts.data?.facts.length ?? "—")}
          note={`facts · ${memos.data?.[0]?.memos.length ?? 0} memos open`}
        />
        <Reading
          label="devices"
          value={presence.data ? `${up}/${devices.length}` : "—"}
          note={presence.data ? (presence.data.active_device ? `they're on ${presence.data.active_device}` : "they're away") : "presence is off"}
        />
        <Reading
          label="proactive"
          value={affect.data?.last_proactive_sent_at ? ago(affect.data.last_proactive_sent_at).replace(" ago", "") : "never"}
          note={`${affect.data?.unanswered_proactive ?? 0} unanswered`}
        />
        <Reading label="operations" value={String(operationList.length)} note={failures ? `${failures} failed` : "from this webui"}>
          <Spark counts={histogram(operationList.map((op) => op.at), 24, HOUR, now)} label="Operations per 2.5 minutes over the last hour" />
        </Reading>
      </div>

      <div class="board">
        <section class="section">
          <div class="section-head">
            <h2>Messages</h2>
            <span class="aside">{on("memory") ? `newest first · ${recent.length} loaded` : "memory is off"}</span>
          </div>
          {recent.length ? (
            <ol class="messages">
              {recent.slice(0, 14).map((m) => (
                <li key={m.id} class={m.role}>
                  <span class="faint">{clock(m.occurred_at)}</span>
                  <div>
                    <div class="who">
                      <span class={m.role === "assistant" ? "accent-text" : ""}>{m.role === "assistant" ? name : m.role === "user" ? "them" : m.role}</span>
                      <span class="faint">
                        {m.harness} · {m.external_conversation_id}
                      </span>
                    </div>
                    <p>{m.text}</p>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <p class="empty">{on("memory") ? "No messages stored yet." : <><strong>memory is off,</strong> so there's no archive to read.</>}</p>
          )}
        </section>

        <section class="section">
          <div class="section-head">
            <h2>Activity</h2>
            <div class="chips" role="tablist" aria-label="Filter activity">
              {FILTERS.map((item) => (
                <button key={item} role="tab" aria-selected={filter === item} onClick={() => setFilter(item)}>
                  {item}
                </button>
              ))}
            </div>
          </div>
          {shown.length ? (
            <ol class="activity">
              {shown.map((item) => (
                <li key={item.key}>
                  <span class="faint">{clock(item.at)}</span>
                  <span class={`tag-text ${item.tone === "bad" ? "bad-text" : item.tone === "accent" ? "accent-text" : ""}`}>{item.tag}</span>
                  <div>
                    <p>{item.text}</p>
                    {item.detail && <p class={`faint small ${item.tone === "bad" ? "bad-text" : ""}`}>{item.detail}</p>}
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <p class="empty">
              {filter === "webui" ? "Nothing run from this WebUI yet. Commands, tool calls, and evaluations show up here." : "Nothing here yet."}
            </p>
          )}
        </section>
      </div>

      <section class="section">
        <div class="section-head">
          <h2>Wiring</h2>
          <span class="aside">solid: service · dotted: event · select a plugin to trace it</span>
        </div>
        <Wiring plugins={store.plugins} edges={store.edges} onPick={(plugin) => go("plugins", plugin)} />
      </section>
    </div>
  );
}

export function State({ state }: { state: string }) {
  const tone = state === "active" ? "ok" : state === "failed" || state === "inactive" ? "bad" : state === "disabled" ? "off" : "warn";
  return (
    <span class="status">
      <span class={`dot ${tone}`} />
      <span class={tone === "bad" ? "bad-text" : tone === "ok" ? "" : "faint"}>{state}</span>
    </span>
  );
}
