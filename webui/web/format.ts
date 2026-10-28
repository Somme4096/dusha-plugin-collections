export function ago(when: string | number | null | undefined, now = Date.now()): string {
  if (when === null || when === undefined || when === "") return "never";
  const at = typeof when === "number" ? (when < 1e12 ? when * 1000 : when) : Date.parse(when);
  if (Number.isNaN(at)) return String(when);
  const seconds = Math.max(0, Math.round((now - at) / 1000));
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${String(minutes % 60).padStart(2, "0")}m ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h ago`;
}

export function clock(when: string | number | null | undefined): string {
  if (when === null || when === undefined || when === "") return "";
  const at = typeof when === "number" ? (when < 1e12 ? when * 1000 : when) : Date.parse(when);
  if (Number.isNaN(at)) return String(when);
  return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

export const fixed = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined || Number.isNaN(value) ? "—" : value.toFixed(digits);

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}
