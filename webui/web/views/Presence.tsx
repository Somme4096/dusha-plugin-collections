import { useRef } from "preact/hooks";

import { get, usePoll } from "../api";
import { Runner } from "../components/Runner";
import { ago, duration } from "../format";
import { useStore } from "../store";

type Device = {
  device: string;
  status: string;
  since: string | null;
  user: string | null;
  idle_seconds: number | null;
  last_seen: string | null;
  last_active_at: string | null;
  url: string;
  capabilities: string[];
  local: boolean;
};

type Snapshot = { local: string; active_device: string | null; last_active_device: string | null; devices: Device[] };

const HISTORY = 48;

const tone = (status: string) =>
  status === "active" ? "ok" : status === "idle" ? "warn" : status === "locked" || status === "awake" ? "" : "bad";

export function Presence() {
  const store = useStore();
  const on = store.active("presence");
  const history = useRef(new Map<string, string[]>());
  const snapshot = usePoll(() => (on ? get<Snapshot>("state/v1/presence") : Promise.resolve(null)), 4_000, [on]);

  if (snapshot.data) {
    for (const device of snapshot.data.devices) {
      const line = history.current.get(device.device) ?? [];
      if (snapshot.at !== (line as any).at) {
        line.push(device.status);
        (line as any).at = snapshot.at;
        if (line.length > HISTORY) line.shift();
      }
      history.current.set(device.device, line);
    }
  }

  const plugin = store.plugins.find((item) => item.name === "presence");

  return (
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Presence</h1>
          <p>
            Every machine that sends heartbeats here, and whether she's at it. The strip under each device fills from
            the right with one bit per reading while this page is open.
          </p>
        </div>
        {snapshot.data && (
          <dl class="facts small">
            <dt>active on</dt>
            <dd>{snapshot.data.active_device ?? "no device"}</dd>
            <dt>last used</dt>
            <dd>{snapshot.data.last_active_device ?? "—"}</dd>
          </dl>
        )}
      </div>

      {!on ? (
        <p class="empty">
          <strong>presence isn't active on this server.</strong> {plugin?.error || "Load it to see devices here."}
        </p>
      ) : !snapshot.data ? (
        <p class="faint">{snapshot.error?.message ?? "Reading the registry…"}</p>
      ) : (
        <div class="devices">
          {snapshot.data.devices.map((device) => {
            const line = history.current.get(device.device) ?? [];
            const pad = Array.from({ length: HISTORY - line.length }, () => "");
            return (
              <article class="device" key={device.device}>
                <h3>
                  <span class="status">
                    <span class={`dot ${tone(device.status)}${device.status === "active" ? " live" : ""}`} />
                    {device.device}
                  </span>
                  <span class="faint small">
                    {device.local ? "this server" : ""}
                    {device.device === snapshot.data!.active_device ? " · she's here" : ""}
                  </span>
                </h3>
                <div class={`big ${tone(device.status)}-text`}>{device.status}</div>
                <div class="faint small" style={{ marginTop: "6px" }}>since {ago(device.since)}</div>
                <div class="timeline" aria-label={`${device.device} recent states`}>
                  {[...pad, ...line].map((status, i) => (
                    <i key={i} class={status} title={status} />
                  ))}
                </div>
                <dl class="facts">
                  <dt>user</dt>
                  <dd>{device.user ?? "—"}</dd>
                  <dt>idle</dt>
                  <dd>{duration(device.idle_seconds)}</dd>
                  <dt>heard</dt>
                  <dd>{ago(device.last_seen)}</dd>
                  <dt>can</dt>
                  <dd>{device.capabilities.join(", ") || "—"}</dd>
                  <dt>url</dt>
                  <dd class="faint">{device.url}</dd>
                </dl>
              </article>
            );
          })}
        </div>
      )}

      {plugin && plugin.commands.length > 0 && (
        <section class="section">
          <div class="section-head">
            <h2>Commands</h2>
            <span class="aside">presence · relay</span>
          </div>
          {plugin.commands.map((command) => (
            <Runner key={command.name} kind="command" item={command} />
          ))}
          {store.plugins
            .find((item) => item.name === "relay" && item.state === "active")
            ?.commands.map((command) => <Runner key={`relay-${command.name}`} kind="command" item={command} />)}
        </section>
      )}
    </div>
  );
}
