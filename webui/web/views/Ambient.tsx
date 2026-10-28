// Ambient's metrics as a heat field, one band per device and metric, newest readings at the front, and its
// event log under it. Ambient reports summaries, not series, so the history is what this page has seen.
import { useEffect, useRef, useState } from "preact/hooks";

import { get, usePoll } from "../api";
import { Field } from "../components/Field";
import { Runner } from "../components/Runner";
import { clock, fixed } from "../format";
import type { BitField } from "../gl/bitfield";
import { useStore } from "../store";
import type { Observation } from "./Overview";

type Summary = { last: number; min: number; avg: number; max: number; n: number; at: string };
type Metrics = Record<string, Record<string, Summary>>;

const SHOWN = ["cpu.percent", "mem.percent", "swap.percent", "load.1", "net.rx_kbps", "net.tx_kbps", "battery.percent"];
const BAND = 4;
const ROWS = 24;
const MAX_BANDS = 12;

/** Every metric as 0..1, with net traffic on a log scale up to about 100 MB/s. */
function level(name: string, value: number): number {
  if (name.startsWith("net.")) return Math.min(1, Math.log10(1 + Math.max(0, value)) / 5);
  if (name.startsWith("load.")) return Math.min(1, value / 8);
  if (name.endsWith("percent") || name.startsWith("disk.percent")) return Math.min(1, Math.max(0, value / 100));
  return Math.min(1, value);
}

/** A metric's name for people: "disk % home" rather than "disk.percent:~". */
const NAMES: Record<string, string> = { "battery.plugged": "charger", "load.1": "load 1m", "uptime.hours": "uptime h" };

function label(name: string): string {
  if (NAMES[name]) return NAMES[name];
  const [base = name, mount] = name.split(":");
  const short = base.replace(".percent", " %").replace("net.", "").replace("_kbps", " kB/s");
  if (mount === undefined) return short;
  return `${short} ${mount === "~" ? "home" : mount === "/" ? "root" : mount.split("/").pop() || mount}`;
}

// ambient's own alert lines: cpu 85%, memory and disk 90%, battery 15%.
function hot(name: string, value: number): number {
  if (name === "battery.percent") return value <= 15 ? 1 : 0;
  if (name === "cpu.percent") return value >= 85 ? 1 : 0;
  if (name === "mem.percent" || name.startsWith("disk.percent")) return value >= 90 ? 1 : 0;
  return 0;
}

function bands(metrics: Metrics | undefined): { device: string; name: string }[] {
  const out: { device: string; name: string }[] = [];
  for (const [device, names] of Object.entries(metrics ?? {})) {
    for (const name of Object.keys(names).sort()) {
      if (SHOWN.includes(name) || name.startsWith("disk.percent")) out.push({ device, name });
    }
  }
  return out.slice(0, MAX_BANDS);
}

export function Ambient() {
  const store = useStore();
  const on = store.active("ambient");
  const [device, setDevice] = useState("");
  const [source, setSource] = useState("");
  const [hours, setHours] = useState(6);
  const field = useRef<BitField | null>(null);
  const metrics = usePoll(() => (on ? get<{ metrics: Metrics }>("state/v1/ambient/metrics?hours=0.05") : Promise.resolve(null)), 5_000, [on]);
  const wide = usePoll(() => (on ? get<{ metrics: Metrics }>("state/v1/ambient/metrics?hours=1") : Promise.resolve(null)), 30_000, [on]);
  const query = new URLSearchParams({ device, source, hours: String(hours), limit: "80" });
  const log = usePoll(
    () => (on ? get<{ observations: Observation[] }>(`state/v1/ambient?${query}`) : Promise.resolve(null)),
    8_000,
    [on, device, source, hours],
  );

  const layout = bands(wide.data?.metrics ?? metrics.data?.metrics);
  const cols = Math.max(1, layout.length) * BAND;
  const seeded = useRef(false);

  useEffect(() => {
    const data = metrics.data?.metrics;
    if (!field.current || !data || !layout.length) return;
    const pushRow = (pick: (s: Summary) => number, from: Metrics = data) => {
      const values: number[] = [];
      const tones: number[] = [];
      for (const band of layout) {
        const summary = from[band.device]?.[band.name];
        const value = summary ? pick(summary) : 0;
        for (let i = 0; i < BAND; i++) {
          values.push(i === BAND - 1 ? 0 : Math.max(0.04, level(band.name, value)));
          tones.push(hot(band.name, value));
        }
      }
      field.current!.push(values, tones);
    };
    if (!seeded.current) {
      // Start the field from the last hour's spread so it isn't empty: min, avg, max, avg.
      const spread = wide.data?.metrics;
      if (spread) {
        for (let i = 0; i < ROWS - 1; i++) {
          const phase = i % 4;
          pushRow((s) => (phase === 0 ? s.min : phase === 2 ? s.max : s.avg), spread);
        }
      }
      seeded.current = true;
    }
    pushRow((s) => s.last);
  }, [metrics.at, layout.length]);

  const devices = Object.keys(wide.data?.metrics ?? {});
  const sources = [...new Set(log.data?.observations.map((item) => item.source) ?? [])];
  const plugin = store.plugins.find((item) => item.name === "ambient");

  if (!on) {
    return (
      <div class="page">
        <div class="page-head">
          <div>
            <h1>Ambient</h1>
          </div>
        </div>
        <p class="empty">
          <strong>ambient isn't active on this server.</strong> {plugin?.error || "Load it to see metrics and events from every device."}
        </p>
      </div>
    );
  }

  return (
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Ambient</h1>
          <p>
            Metrics from every device, one band each, newest at the front. Bits turn hot past an alert line. The field
            starts from the last hour's min, average, and max, then adds a row every five seconds.
          </p>
        </div>
      </div>

      {layout.length ? (
        <>
          <Field
            key={cols}
            class="metric-field"
            label="Metrics per device as a heat field over time"
            options={{ cols, rows: ROWS, maxHeight: 4, step: 0.3, size: 0.82, yaw: 0.2, pitch: 0.52, fill: 0.94, margin: 2.2, ease: 0.1, sway: 0.04 }}
            onReady={(f) => {
              field.current = f;
              seeded.current = false;
            }}
            pins={layout.map((band, i) => ({
              label: `${devices.length > 1 ? `${band.device} ` : ""}${label(band.name)}`,
              col: i * BAND + (BAND - 2) / 2,
              row: ROWS + 0.4,
            }))}
          />
        </>
      ) : (
        <p class="empty">
          No metrics yet. Ambient samples with psutil every <code>sample_seconds</code>; without psutil it records
          events only.
        </p>
      )}

      {Object.entries(wide.data?.metrics ?? {}).map(([name, values]) => (
        <section class="section" key={name}>
          <div class="section-head">
            <h2>{name}</h2>
            <span class="aside">last hour · last / range</span>
          </div>
          <div class="metric-grid">
            {Object.entries(values)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([metric, summary]) => {
                const current = metrics.data?.metrics[name]?.[metric]?.last ?? summary.last;
                return (
                  <div class="metric" key={metric}>
                    <div class="name" title={metric}>{label(metric)}</div>
                    {metric === "battery.plugged" ? (
                      <div class="value">{current ? "plugged in" : "on battery"}</div>
                    ) : (
                      <>
                        <div class={`value${hot(metric, current) ? " bad-text" : ""}`}>{fixed(current, metric.startsWith("load") ? 2 : 1)}</div>
                        <div class="range">
                          {fixed(summary.min, 1)} – {fixed(summary.max, 1)} · avg {fixed(summary.avg, 1)}
                        </div>
                      </>
                    )}
                  </div>
                );
              })}
          </div>
        </section>
      ))}

      <section class="section">
        <div class="section-head">
          <h2>Events</h2>
          <div class="filters">
            <select class="input" value={device} onChange={(e) => setDevice(e.currentTarget.value)} aria-label="Device">
              <option value="">every device</option>
              {devices.map((name) => (
                <option key={name}>{name}</option>
              ))}
            </select>
            <select class="input" value={source} onChange={(e) => setSource(e.currentTarget.value)} aria-label="Source">
              <option value="">every source</option>
              {[...new Set([...sources, source].filter(Boolean))].map((name) => (
                <option key={name}>{name}</option>
              ))}
            </select>
            <select class="input" value={hours} onChange={(e) => setHours(Number(e.currentTarget.value))} aria-label="Window">
              {[1, 6, 24, 72, 168].map((h) => (
                <option key={h} value={h}>
                  {h < 24 ? `${h}h` : `${h / 24}d`}
                </option>
              ))}
            </select>
          </div>
        </div>
        {log.data?.observations.length ? (
          <ul class="feed">
            {log.data.observations.map((item) => (
              <li key={item.id}>
                <span class="faint">{item.at || clock(item.created_at)}</span>
                <span class="who">{item.device}</span>
                <span>
                  <span class={item.kind === "alert" ? "bad-text" : "faint"}>{item.source} </span>
                  {item.text}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p class="empty">Nothing in this window.</p>
        )}
      </section>

      {plugin && plugin.commands.length > 0 && (
        <section class="section">
          <div class="section-head">
            <h2>Commands</h2>
          </div>
          {plugin.commands.map((command) => (
            <Runner key={command.name} kind="command" item={command} />
          ))}
        </section>
      )}
    </div>
  );
}
