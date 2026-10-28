import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { BitField, type FieldOptions } from "../gl/bitfield";

/** A label pinned to a cell, kept in place as the camera moves. */
export type Pin = { label: string; col: number; row: number; height?: number; class?: string };

type Props = {
  options: FieldOptions;
  onReady: (field: BitField) => void;
  class?: string;
  label: string;
  pins?: Pin[];
  children?: ComponentChildren;
};

export function Field({ options, onReady, class: name = "", label, pins = [], children }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const spans = useRef<(HTMLSpanElement | null)[]>([]);
  const pinned = useRef(pins);
  pinned.current = pins;
  const [failed, setFailed] = useState("");

  useEffect(() => {
    if (!canvas.current) return;
    let field: BitField;
    try {
      field = new BitField(canvas.current, options);
    } catch (error) {
      setFailed((error as Error).message);
      return;
    }
    const off = field.onFrame(() => {
      pinned.current.forEach((pin, i) => {
        const span = spans.current[i];
        if (!span) return;
        const { x, y } = field.project(pin.col, pin.row, pin.height ?? 0);
        span.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
      });
    });
    onReady(field);
    return () => {
      off();
      field.destroy();
    };
  }, [options.cols, options.rows]);

  return (
    <div class={`field ${name}`}>
      {failed ? (
        <p class="empty" style={{ padding: "24px" }}>
          The visualizer needs WebGL2 ({failed}). Everything else on this page still works.
        </p>
      ) : (
        <canvas ref={canvas} role="img" aria-label={label} />
      )}
      {!failed &&
        pins.map((pin, i) => (
          <span key={pin.label} class={`pin ${pin.class ?? ""}`} ref={(el) => {
            spans.current[i] = el;
          }} aria-hidden="true">
            <span>{pin.label}</span>
          </span>
        ))}
      {children}
    </div>
  );
}
