import { useEffect, useRef, useState } from "preact/hooks";

import { BitPlane } from "../gl/bitplane";

export type Lane = { name: string; value: number; hot?: number };

/** Lanes of bits seen from above, each labeled at its left edge with its name and reading. */
export function Plane({ lanes, pulse, label }: { lanes: Lane[]; pulse: number; label: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const plane = useRef<BitPlane | null>(null);
  const [centers, setCenters] = useState<number[]>([]);
  const [failed, setFailed] = useState("");
  const count = Math.max(1, lanes.length);

  useEffect(() => {
    if (!canvas.current) return;
    try {
      plane.current = new BitPlane(canvas.current, { lanes: count, rowsPerLane: count > 6 ? 2 : 3 });
    } catch (error) {
      setFailed((error as Error).message);
      return;
    }
    const off = plane.current.onLayout(setCenters);
    return () => {
      off();
      plane.current?.destroy();
      plane.current = null;
    };
  }, [count]);

  useEffect(() => {
    plane.current?.set(
      lanes.map((lane) => lane.value),
      lanes.map((lane) => lane.hot ?? 0),
    );
  }, [lanes.map((lane) => `${lane.name}${lane.value.toFixed(3)}`).join()]);

  useEffect(() => {
    if (pulse) plane.current?.sweep();
  }, [pulse]);

  return (
    <div class="plane">
      <ol class="plane-lanes" aria-label="Lanes">
        {lanes.map((lane, i) => (
          <li key={lane.name} style={{ top: `${centers[i] ?? 0}px`, opacity: centers.length ? 1 : 0 }}>
            <span>{lane.name}</span>
            <span class="faint">{lane.value.toFixed(2)}</span>
          </li>
        ))}
      </ol>
      {failed ? (
        <p class="empty">The plane needs a canvas ({failed}).</p>
      ) : (
        <canvas ref={canvas} role="img" aria-label={label} />
      )}
    </div>
  );
}
