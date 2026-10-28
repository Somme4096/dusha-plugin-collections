// An arc diagram of how plugins reach each other. Plugins stack in a column. Injected services stay in a
// tight lane next to it as solid arcs, and events sweep wide to the right as dotted arcs, from the plugin
// that emits or asks to the one that listens.
import { useEffect, useRef, useState } from "preact/hooks";

import { CORE, type Edge, type PluginView } from "../model";

const ROW = 30;
const LABEL = 150;
const LANE = 56;

export function Wiring({ plugins, edges, onPick }: { plugins: PluginView[]; edges: Edge[]; onPick: (name: string) => void }) {
  const [focus, setFocus] = useState<string | null>(null);
  const [width, setWidth] = useState(640);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!box.current) return;
    // Resize on the next frame, and only when the width really changed, so the SVG's own size never
    // feeds back into the observer within one layout pass.
    let frame = 0;
    const watch = new ResizeObserver(([entry]) => {
      if (!entry) return;
      cancelAnimationFrame(frame);
      const next = Math.max(320, Math.floor(entry.contentRect.width));
      frame = requestAnimationFrame(() => setWidth((old) => (Math.abs(old - next) > 1 ? next : old)));
    });
    watch.observe(box.current);
    return () => {
      cancelAnimationFrame(frame);
      watch.disconnect();
    };
  }, []);

  const names = [CORE, ...plugins.map((plugin) => plugin.name)];
  const index = new Map(names.map((name, i) => [name, i]));
  const state = new Map(plugins.map((plugin) => [plugin.name, plugin.state]));
  const y = (name: string) => (index.get(name) ?? 0) * ROW + ROW / 2;
  const x = LABEL + 22;
  const height = names.length * ROW;
  const tallest = Math.max(ROW, ...edges.filter((edge) => edge.kind === "event").map((edge) => Math.abs(y(edge.from) - y(edge.to))));
  const room = Math.max(60, width - x - LANE - 120);
  const hot = (edge: Edge) => focus !== null && (edge.from === focus || edge.to === focus);
  const near = new Set(edges.filter(hot).flatMap((edge) => [edge.from, edge.to]));

  return (
    <div ref={box}>
      <svg
        class={`wiring${focus ? " focus" : ""}`}
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        role="img"
        aria-label="How the plugins wire together"
        onMouseLeave={() => setFocus(null)}
      >
        {edges.map((edge, i) => {
          const a = y(edge.from);
          const b = y(edge.to);
          const span = Math.abs(b - a);
          const bend = edge.kind === "service" ? Math.min(LANE, 14 + span * 0.25) : LANE + 16 + (span / tallest) * room;
          return (
            <g key={i}>
              <path
                class={`edge ${edge.kind}${hot(edge) ? " hot" : ""}`}
                d={`M${x} ${a} C${x + bend * 1.33} ${a}, ${x + bend * 1.33} ${b}, ${x + 5} ${b}`}
              />
              <text class={`label${hot(edge) ? " hot" : ""}`} x={x + bend + 6} y={(a + b) / 2 + 3}>
                {edge.label}
              </text>
            </g>
          );
        })}
        {names.map((name) => {
          const off = name !== CORE && state.get(name) !== "active";
          return (
            <g
              key={name}
              class={`node${name === CORE ? " core" : ""}${off ? " off" : ""}${near.has(name) || focus === name ? " hot" : ""}`}
              onMouseEnter={() => setFocus(name)}
              onFocus={() => setFocus(name)}
              onBlur={() => setFocus(null)}
              onClick={() => name !== CORE && onPick(name)}
              tabIndex={0}
              role="button"
              aria-label={name}
            >
              <text x={LABEL} y={y(name) + 4} text-anchor="end">
                {name}
              </text>
              <rect x={LABEL + 10} y={y(name) - 4} width="8" height="8" />
            </g>
          );
        })}
      </svg>
    </div>
  );
}
