// A value as a row of square bits, the 2D sibling of the 3D field.
export function Bits({ value, count = 32, mark }: { value: number; count?: number; mark?: number }) {
  const lit = Math.round(Math.max(0, Math.min(1, value)) * count);
  const marked = mark === undefined ? -1 : Math.round(Math.max(0, Math.min(1, mark)) * count) - 1;
  return (
    <span class="bits" style={{ gridTemplateColumns: `repeat(${count}, 1fr)` }} aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <i key={i} class={i < lit ? "on" : i === marked ? "mark" : ""} />
      ))}
    </span>
  );
}
