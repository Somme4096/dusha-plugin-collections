// Pretty JSON with its tokens colored by kind. Built as nodes, never as HTML strings.
import type { JSX } from "preact";

function render(value: unknown, indent: string, out: JSX.Element[], key = 0): number {
  const push = (cls: string, text: string) => out.push(<span key={key++} class={cls}>{text}</span>);
  if (value === null || value === undefined) push("z", "null");
  else if (typeof value === "string") push("s", JSON.stringify(value));
  else if (typeof value === "number") push("n", String(value));
  else if (typeof value === "boolean") push("b", String(value));
  else if (Array.isArray(value)) {
    if (!value.length) push("p", "[]");
    else {
      push("p", "[\n");
      value.forEach((item, index) => {
        push("p", indent + "  ");
        key = render(item, indent + "  ", out, key);
        push("p", index < value.length - 1 ? ",\n" : "\n");
      });
      push("p", indent + "]");
    }
  } else if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (!entries.length) push("p", "{}");
    else {
      push("p", "{\n");
      entries.forEach(([name, item], index) => {
        push("p", indent + "  ");
        push("k", JSON.stringify(name));
        push("p", ": ");
        key = render(item, indent + "  ", out, key);
        push("p", index < entries.length - 1 ? ",\n" : "\n");
      });
      push("p", indent + "}");
    }
  } else push("s", String(value));
  return key;
}

export function Json({ value }: { value: unknown }) {
  const out: JSX.Element[] = [];
  render(value, "", out);
  return <pre class="json">{out}</pre>;
}
