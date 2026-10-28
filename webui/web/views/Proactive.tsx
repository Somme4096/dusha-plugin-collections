// The proactive pipeline as an instrument. Each poll of `proactive plan` becomes one row of bits across
// five stages, collect, gate, pick, queue, deliver, and history scrolls back into the field. When the plan
// changes, a pulse runs through the stages in order, as the engine would.
import { useEffect, useRef, useState } from "preact/hooks";

import { get, post, usePoll } from "../api";
import { Bits } from "../components/Bits";
import { Field } from "../components/Field";
import { Json } from "../components/Json";
import { ago } from "../format";
import type { BitField } from "../gl/bitfield";
import { useStore } from "../store";
import type { Affect } from "./Overview";

type Candidate = {
  source: string;
  reason: string;
  topic: string;
  kind: string;
  score: number;
  dedup_key: string;
  requires_presence: string;
  skipped?: string;
};

type Plan = { blocked: string | null; candidates: Candidate[]; chosen: Candidate | null };

const STAGES = ["collect", "gate", "pick", "queue", "deliver"] as const;
const BINS = 6;
const GAP = 1;
const COLS = STAGES.length * BINS + (STAGES.length - 1) * GAP;
const ROWS = 30;
const WAITING = "a message is already waiting for delivery";

const col = (stage: number, bin: number) => stage * (BINS + GAP) + bin;

function row(plan: Plan | null, affect: Affect | null, queued: boolean): { values: number[]; tones: number[] } {
  const values = new Array(COLS).fill(0);
  const tones = new Array(COLS).fill(0);
  if (!plan) return { values, tones };
  const sorted = [...plan.candidates].sort((a, b) => b.score - a.score);
  sorted.slice(0, BINS).forEach((item, i) => {
    values[col(0, i)] = 0.15 + item.score * 0.85;
    tones[col(0, i)] = item.skipped ? 0.55 : 0;
  });
  const waiting = plan.blocked === WAITING || queued;
  for (let i = 0; i < BINS; i++) {
    values[col(1, i)] = plan.blocked ? 0.22 : 0.55 + 0.1 * Math.sin(i);
    tones[col(1, i)] = plan.blocked && !waiting ? 1 : 0;
  }
  if (plan.chosen) {
    for (let i = 0; i < BINS; i++) values[col(2, i)] = plan.chosen.score * (1 - Math.abs(i - 2.5) / 3.2);
  }
  for (let i = 0; i < BINS; i++) values[col(3, i)] = waiting ? 0.9 - i * 0.04 : 0;
  const sent = affect?.last_proactive_sent_at ? (Date.now() - Date.parse(affect.last_proactive_sent_at)) / 3.6e6 : Infinity;
  const recent = Number.isFinite(sent) ? Math.exp(-sent / 3) : 0;
  const unanswered = affect?.unanswered_proactive ?? 0;
  for (let i = 0; i < BINS; i++) {
    values[col(4, i)] = recent * (1 - i * 0.12);
    if (i >= BINS - unanswered) {
      values[col(4, i)] = Math.max(values[col(4, i)], 0.5);
      tones[col(4, i)] = 0.8;
    }
  }
  return { values, tones };
}

// Synthetic plans for when nothing real is happening. Every one is labeled on screen.
const SOURCES: Candidate[] = [
  { source: "silence", reason: "longing", topic: "", kind: "talk", score: 0.55, dedup_key: "", requires_presence: "" },
  { source: "presence", reason: "came back", topic: "she's back on desktop after 3h away", kind: "talk", score: 0.6, dedup_key: "", requires_presence: "" },
  { source: "ambient", reason: "alert", topic: "desktop: disk at 92%", kind: "talk", score: 0.5, dedup_key: "", requires_presence: "active" },
  { source: "ambient", reason: "alert", topic: "cargo build failing for an hour", kind: "task", score: 0.45, dedup_key: "", requires_presence: "active" },
  { source: "memo", reason: "loose end", topic: "ask how the overhang went", kind: "talk", score: 0.35, dedup_key: "", requires_presence: "" },
];
const BLOCKS = ["cooling down", "quiet hours", WAITING, "daily cap reached", "too many unanswered messages"];
const SKIPS = ["already raised", "she talked just now", "she isn't at any device", "score below min_score"];

function synthetic(step: number): Plan {
  const random = (n: number) => Math.abs(Math.sin(step * 12.9898 + n * 78.233) * 43758.5453) % 1;
  if (random(1) < 0.28) return { blocked: BLOCKS[Math.floor(random(2) * BLOCKS.length)]!, candidates: [], chosen: null };
  const candidates = SOURCES.filter((_, i) => random(i + 3) > 0.35).map((item, i) => ({
    ...item,
    dedup_key: `${item.source}:${step}`,
    score: Math.min(1, Math.max(0.1, item.score + (random(i + 9) - 0.5) * 0.4)),
    skipped: random(i + 20) < 0.35 ? SKIPS[Math.floor(random(i + 30) * SKIPS.length)] : "",
  }));
  const eligible = candidates.filter((item) => !item.skipped);
  const chosen = eligible.length ? eligible.reduce((a, b) => (b.score > a.score ? b : a)) : null;
  return { blocked: null, candidates, chosen };
}

const signature = (plan: Plan | null) =>
  plan ? JSON.stringify([plan.blocked, plan.chosen?.dedup_key, plan.candidates.map((c) => [c.dedup_key, c.skipped])]) : "";

export function Proactive({ initial }: { initial: string }) {
  const store = useStore();
  const provider = store.plugins.find((plugin) => plugin.state === "active" && plugin.provides.includes("proactive"));
  const planned = Boolean(provider?.commands.some((command) => command.group === "proactive" && command.name === "plan"));
  const [simulate, setSimulate] = useState(initial === "simulate");
  const [lit, setLit] = useState(-1);
  const [evaluated, setEvaluated] = useState<{ event: any; at: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const field = useRef<BitField | null>(null);
  const step = useRef(0);
  const last = useRef("");

  const real = usePoll(
    () => (planned ? post<Plan>("cli/v1/run/proactive/plan") : Promise.resolve(null)),
    simulate ? 0 : 3_000,
    [planned, simulate],
  );
  const affect = usePoll(() => (store.active("affect") ? get<Affect>("state/v1/affect") : Promise.resolve(null)), 6_000, [store.active("affect")]);
  const [fake, setFake] = useState<Plan | null>(null);

  useEffect(() => {
    if (!simulate) return;
    const tick = () => setFake(synthetic(++step.current));
    tick();
    const timer = setInterval(tick, 2_400);
    return () => clearInterval(timer);
  }, [simulate]);

  const plan = simulate ? fake : real.data ?? null;
  const queued = Boolean(evaluated?.event && Date.now() - evaluated.at < 60_000);

  // One row per reading, and a run through the stages when the plan changes.
  useEffect(() => {
    if (!field.current) return;
    const { values, tones } = row(plan, affect.data ?? null, queued);
    field.current.push(values, tones);
    const now = signature(plan);
    if (now !== last.current) {
      last.current = now;
      field.current.sweep(0, COLS - 1, 1500);
      const timers = STAGES.map((_, i) => setTimeout(() => setLit(i), i * 300));
      timers.push(setTimeout(() => setLit(-1), STAGES.length * 300 + 400));
      return () => timers.forEach(clearTimeout);
    }
  }, [real.at, fake]);

  const evaluate = async () => {
    setBusy(true);
    try {
      const result = await post<{ event: any }>("state/v1/proactive/evaluate");
      setEvaluated({ event: result.event, at: Date.now() });
      real.refresh();
      field.current?.sweep(0, COLS - 1, 1500);
    } catch (error) {
      setEvaluated({ event: { error: (error as Error).message }, at: Date.now() });
    } finally {
      setBusy(false);
    }
  };

  const waiting = plan?.blocked === WAITING || queued;
  const eligible = plan?.candidates.filter((item) => !item.skipped) ?? [];
  const unanswered = affect.data?.unanswered_proactive ?? 0;

  const readings = [
    {
      reading: plan ? `${plan.candidates.length} reason${plan.candidates.length === 1 ? "" : "s"}` : "—",
      note: plan?.candidates.length
        ? [...new Set(plan.candidates.map((c) => c.source))].join(", ")
        : plan?.blocked
          ? "not asked while the gate is shut"
          : "nobody has a reason right now",
    },
    {
      reading: plan ? (plan.blocked ? "shut" : "open") : "—",
      note: plan?.blocked ?? "quiet hours, cooldown, caps, and presence all allow it",
      blocked: Boolean(plan?.blocked) && !waiting,
    },
    {
      reading: plan?.chosen ? plan.chosen.score.toFixed(2) : "none",
      note: plan?.chosen ? plan.chosen.topic || plan.chosen.reason : eligible.length ? "" : "no eligible reason",
    },
    {
      reading: waiting ? "waiting" : "empty",
      note: waiting ? "one event is pending until a consumer leases it" : "consumers poll /state/v1/proactive/events",
    },
    {
      reading: affect.data?.last_proactive_sent_at ? ago(affect.data.last_proactive_sent_at) : "never",
      note: `${unanswered} unanswered`,
    },
  ];

  return (
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Proactive</h1>
          <p>
            Why the companion would or wouldn't speak up right now. Every few seconds the WebUI asks{" "}
            <code>proactive plan</code>, which decides without queueing anything, and adds a row to the field.
          </p>
        </div>
        <div class="toolbar">
          <label class="check">
            <input type="checkbox" checked={simulate} onChange={(e) => setSimulate(e.currentTarget.checked)} />
            simulate
          </label>
          <button
            class="btn primary"
            onClick={evaluate}
            disabled={busy || simulate || !provider}
            title="Runs one real evaluation. It queues a message when one is due."
          >
            {busy ? "Evaluating…" : "Evaluate now"}
          </button>
        </div>
      </div>

      {!provider && (
        <p class="empty">
          <strong>Nothing provides proactive.</strong> Enable the built-in <code>proactive</code> or load{" "}
          <code>better_proactive</code>. The simulation still runs.
        </p>
      )}
      {provider && !planned && !simulate && (
        <p class="empty">
          <strong>{provider.name} has no plan view.</strong> Only better_proactive explains its decisions, so the field
          below shows delivery alone. Load better_proactive with the built-in disabled to see every stage.
        </p>
      )}

      <Field
        class="flow-field"
        label="Proactive stages left to right, time front to back"
        options={{ cols: COLS, rows: ROWS, maxHeight: 4.2, step: 0.35, size: 0.8, yaw: 0.14, pitch: 0.44, fill: 0.94, margin: 2.2, ease: 0.12, sway: 0.04 }}
        onReady={(f) => (field.current = f)}
        pins={[
          ...STAGES.map((stage, i) => ({ label: stage, col: col(i, (BINS - 1) / 2), row: ROWS + 0.4, class: lit === i ? "lit" : "" })),
          { label: "now", col: -1.2, row: ROWS - 1, class: "time" },
          { label: `${Math.round((ROWS * 3) / 60 * 10) / 10} min ago`, col: -1.2, row: 0, class: "time" },
        ]}
      >
        {simulate && <span class="synthetic">synthetic data</span>}
      </Field>

      <div class="stages" role="list">
        {STAGES.map((stage, i) => (
          <div
            key={stage}
            role="listitem"
            class={`stage${lit === i ? " lit" : ""}${readings[i]!.blocked ? " blocked" : ""}`}
          >
            <h3>
              <span>{stage}</span>
              <span>{i + 1}/5</span>
            </h3>
            <div class="reading">{readings[i]!.reading}</div>
            <div class="note">{readings[i]!.note}</div>
          </div>
        ))}
      </div>

      <section class="section">
        <div class="section-head">
          <h2>Candidates</h2>
          <span class="aside">
            {simulate ? "synthetic" : real.error ? real.error.message : real.at ? `read ${ago(real.at)}` : "reading…"}
          </span>
        </div>
        {plan?.candidates.length ? (
          <table class="table candidates">
            <thead>
              <tr>
                <th>source</th>
                <th>why</th>
                <th class="hide-sm">kind</th>
                <th>score</th>
                <th>verdict</th>
              </tr>
            </thead>
            <tbody>
              {[...plan.candidates]
                .sort((a, b) => b.score - a.score)
                .map((item) => {
                  const chosen = plan.chosen?.dedup_key === item.dedup_key && plan.chosen.source === item.source;
                  return (
                    <tr key={`${item.source}${item.dedup_key}`}>
                      <td>{item.source}</td>
                      <td>
                        {item.topic || item.reason}
                        {item.topic && <span class="faint"> · {item.reason}</span>}
                      </td>
                      <td class="faint hide-sm">{item.kind}</td>
                      <td class="score">
                        <span class="scorebar">
                          <Bits value={item.score} count={10} />
                          <span class="faint">{item.score.toFixed(2)}</span>
                        </span>
                      </td>
                      <td class={chosen ? "accent-text" : "faint"}>{chosen ? "chosen" : item.skipped || "eligible"}</td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        ) : (
          <p class="empty">
            {plan?.blocked
              ? `The gate is shut (${plan.blocked}), so no plugin is asked for reasons.`
              : "No plugin offers a reason right now. Presence offers one when she comes back after a long absence, and ambient offers one for each open alert."}
          </p>
        )}
      </section>

      {evaluated && (
        <section class="section">
          <div class="section-head">
            <h2>Last evaluation</h2>
            <span class="aside">{ago(evaluated.at)}</span>
          </div>
          {evaluated.event ? <Json value={evaluated.event} /> : <p class="empty">Nothing was due, so nothing was queued.</p>}
        </section>
      )}
    </div>
  );
}
