import { Fragment } from "preact";
import { useEffect, useRef } from "preact/hooks";

import { get, usePoll } from "../api";
import { go } from "../app";
import { Bits } from "../components/Bits";
import { Field } from "../components/Field";
import { Wiring } from "../components/Wiring";
import { ago, clock } from "../format";
import type { BitField } from "../gl/bitfield";
import { useStore } from "../store";

export type Affect = {
  base: Record<string, number>;
  mood: Record<string, number>;
  last_user_message_at: string | null;
  last_proactive_sent_at: string | null;
  unanswered_proactive: number;
};

export type Observation = { id: number; device: string; source: string; kind: string; text: string; at: string; created_at: number };

const COLS = 48;

/** The soul field: one ridge of bits per affect dimension, its height the mood, its motion a slow swell. */
function SoulField({ mood, pulse, copy }: { mood: Record<string, number> | null; pulse: number; copy: { current: HTMLElement | null } }) {
  const field = useRef<BitField | null>(null);
  const moodRef = useRef(mood);
  moodRef.current = mood;
  const rows = 16;

  useEffect(() => {
    const values = new Float32Array(COLS * rows);
    const started = performance.now();
    const timer = setInterval(() => {
      const t = (performance.now() - started) / 1000;
      const dims = Object.values(moodRef.current ?? {});
      for (let row = 0; row < rows; row++) {
        const level = dims.length ? dims[row % dims.length]! : 0.12;
        const speed = 0.35 + (row % 5) * 0.09;
        const freq = 0.16 + (row % 7) * 0.025;
        for (let col = 0; col < COLS; col++) {
          const swell = 0.5 + 0.5 * Math.sin(col * freq + t * speed + row * 1.7);
          const ripple = 0.5 + 0.5 * Math.sin(col * 0.61 - t * 1.3 + row);
          values[row * COLS + col] = level * (0.35 + 0.55 * swell + 0.1 * ripple) * 1.6;
        }
      }
      field.current?.set(values);
      // Keep the bits low under the name and state line while they're set over the field.
      const text = copy.current;
      const canvas = text?.parentElement?.querySelector("canvas");
      if (field.current && text && canvas && getComputedStyle(text).position === "absolute") {
        const a = text.getBoundingClientRect();
        const c = canvas.getBoundingClientRect();
        const pad = 20;
        field.current.clear({
          left: a.left - c.left - pad,
          top: a.top - c.top - pad,
          right: a.left - c.left + Math.min(a.width, 620) + pad,
          bottom: a.bottom - c.top + pad,
        });
      } else field.current?.clear(null);
    }, 120);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (pulse) field.current?.sweep(0, COLS - 1, 2200);
  }, [pulse]);

  return (
    <Field
      class="hero"
      label="Affect as a field of bits: one ridge per dimension, height is the current mood"
      options={{ cols: COLS, rows, maxHeight: 6, step: 0.5, size: 0.8, yaw: 0.95, pitch: 0.46, fill: 0.84, ease: 0.06 }}
      onReady={(f) => (field.current = f)}
    />
  );
}

export function Overview() {
  const store = useStore();
  const affectOn = store.active("affect");
  const ambientOn = store.active("ambient");
  const affect = usePoll(() => (affectOn ? get<Affect>("state/v1/affect") : Promise.resolve(null)), 6_000, [affectOn]);
  const recent = usePoll(
    () => (ambientOn ? get<{ observations: Observation[] }>("state/v1/ambient?hours=24&limit=8") : Promise.resolve(null)),
    10_000,
    [ambientOn],
  );
  const presence = usePoll(
    () => (store.active("presence") ? get<{ active_device: string | null }>("state/v1/presence") : Promise.resolve(null)),
    10_000,
    [store.active("presence")],
  );
  const latest = recent.data?.observations[0]?.id ?? 0;
  const name = store.health?.companion || store.meta?.companion || "dusha";
  const active = store.plugins.filter((plugin) => plugin.state === "active").length;
  const mood = affect.data?.mood ?? null;
  const dims = Object.entries(mood ?? {});
  const where = presence.data?.active_device;
  const copy = useRef<HTMLDivElement>(null);

  return (
    <div class="page">
      <section style={{ position: "relative" }}>
        <SoulField mood={mood} pulse={latest + active} copy={copy} />
        <div class="hero-legend" aria-hidden="true">
          <span>{dims.length ? `affect · ${dims.length} dimensions` : "plugins at rest"}</span>
          <span class="fine-pointer">move the pointer to tilt</span>
        </div>
        <div class="hero-copy" ref={copy}>
          <h1>{name}</h1>
          <p>
            {store.online ? `${active} of ${store.plugins.filter((p) => p.loaded).length} plugins active` : "server unreachable"}
            {where ? ` · she's on ${where}` : ""}
            {affect.data ? ` · last message ${ago(affect.data.last_user_message_at)}` : ""}
          </p>
        </div>
      </section>

      <div class="split">
        <section class="section">
          <div class="section-head">
            <h2>Plugins</h2>
            <a class="aside" href="#/plugins">all plugins</a>
          </div>
          <table class="table">
            <thead>
              <tr>
                <th>name</th>
                <th>origin</th>
                <th>state</th>
                <th class="num hide-sm">hears</th>
              </tr>
            </thead>
            <tbody>
              {store.plugins.map((plugin) => (
                <tr class="row" key={plugin.name} onClick={() => go("plugins", plugin.name)}>
                  <td>
                    <a href={`#/plugins/${plugin.name}`} style={{ textDecoration: "none" }}>
                      {plugin.name}
                    </a>
                  </td>
                  <td class={`origin ${plugin.origin === "local" ? "local" : ""}`}>{plugin.origin}</td>
                  <td>
                    <State state={plugin.state} />
                  </td>
                  <td class="num faint hide-sm">{plugin.listens.length || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section class="section">
          <div class="section-head">
            <h2>Mood</h2>
            <span class="aside">{affectOn ? "affect, live" : "affect is off"}</span>
          </div>
          {dims.length ? (
            <div class="mood">
              {dims
                .sort((a, b) => b[1] - a[1])
                .map(([dimension, value]) => (
                  <Fragment key={dimension}>
                    <span class="name">{dimension}</span>
                    <Bits value={value} mark={affect.data?.base[dimension]} />
                    <span class="num faint">{value.toFixed(2)}</span>
                  </Fragment>
                ))}
            </div>
          ) : (
            <p class="empty">
              {affectOn ? "Reading affect…" : <><strong>affect is off.</strong> Turn it on in config.json to see the mood here.</>}
            </p>
          )}
        </section>
      </div>

      <section class="section">
        <div class="section-head">
          <h2>Wiring</h2>
          <span class="aside">
            solid: service · dotted: event · select a plugin to trace it
          </span>
        </div>
        <Wiring plugins={store.plugins} edges={store.edges} onPick={(plugin) => go("plugins", plugin)} />
      </section>

      <div>

        <section class="section">
          <div class="section-head">
            <h2>Recently</h2>
            <a class="aside" href="#/ambient">ambient</a>
          </div>
          {recent.data?.observations.length ? (
            <ul class="feed">
              {recent.data.observations.map((item) => (
                <li key={item.id}>
                  <span class="faint">{item.at || clock(item.created_at)}</span>
                  <span class="who">{item.device}</span>
                  <span>
                    <span class="faint">{item.source} </span>
                    {item.text}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p class="empty">
              {ambientOn ? "Nothing observed in the last day." : <><strong>ambient isn't loaded.</strong> Its events land here once it is.</>}
            </p>
          )}
        </section>
      </div>
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
