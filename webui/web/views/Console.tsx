// `dusha` in the browser: the same command list, parsed the way click would, sent to the same routes.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";

import { type Command, run } from "../api";
import { Json } from "../components/Json";
import { cliLine, parseLine } from "../model";
import { useStore } from "../store";

type Entry = { line: string; ok: boolean; value: unknown; ms: number };

const usage = (command: Command) =>
  [
    "dusha",
    command.group,
    command.name,
    ...command.args.map((arg) => {
      const name = arg.option ? `--${arg.name.replace(/_/g, "-")}${arg.type === "bool" ? "" : ` ${arg.type}`}` : arg.name;
      const shown = arg.repeated ? `${name}…` : name;
      return arg.required ? shown : `[${shown}]`;
    }),
  ]
    .filter(Boolean)
    .join(" ");

export function Console({ initial }: { initial: string }) {
  const store = useStore();
  const commands = store.manifest?.commands ?? [];
  const [line, setLine] = useState(initial ? `${initial} ` : "");
  const [log, setLog] = useState<Entry[]>([]);
  const [index, setIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [past, setPast] = useState<string[]>([]);
  const [cursor, setCursor] = useState(-1);
  const input = useRef<HTMLInputElement>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => input.current?.focus(), []);
  useEffect(() => end.current?.scrollIntoView({ block: "nearest" }), [log.length]);

  const parsed = parseLine(line, commands);
  const suggestions = useMemo(() => {
    const typed = line.replace(/^dusha\s+/, "").trimStart();
    if (parsed.command && /\s/.test(typed.slice(`${parsed.command.group} ${parsed.command.name}`.trim().length))) return [];
    return commands
      .map((command) => ({ command, text: [command.group, command.name].filter(Boolean).join(" ") }))
      .filter(({ text }) => text.startsWith(typed.trim()) && text !== typed.trim())
      .slice(0, 12);
  }, [line, commands]);

  const submit = async () => {
    const text = line.trim();
    if (!text) return;
    if (text === "clear") {
      setLog([]);
      setLine("");
      return;
    }
    setPast((items) => [...items.filter((item) => item !== text), text]);
    setCursor(-1);
    if (text === "help" || !parsed.command) {
      const value = text === "help"
        ? commands.map(usage)
        : parsed.error || "type a command, help for the list";
      setLog((items) => [...items, { line: text, ok: text === "help", value, ms: 0 }]);
      setLine("");
      return;
    }
    if (parsed.error) {
      setLog((items) => [...items, { line: text, ok: false, value: `${parsed.error}\nusage: ${usage(parsed.command!)}`, ms: 0 }]);
      return;
    }
    setBusy(true);
    const started = performance.now();
    try {
      const value = await run(parsed.command, parsed.values);
      setLog((items) => [...items, { line: cliLine(parsed.command!, parsed.values), ok: true, value, ms: performance.now() - started }]);
      setLine("");
    } catch (error) {
      setLog((items) => [...items, { line: text, ok: false, value: (error as Error).message, ms: performance.now() - started }]);
    } finally {
      setBusy(false);
    }
  };

  const complete = (text: string) => {
    setLine(`${text} `);
    setIndex(0);
    input.current?.focus();
  };

  return (
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Console</h1>
          <p>
            Type what you'd type after <code>dusha</code>. Tab completes, ↑ recalls, <code>help</code> lists every
            command, <code>clear</code> wipes the log. Commands come from the server, so plugins add their own.
          </p>
        </div>
      </div>
      <div class="console">
        <div class="console-log" aria-live="polite">
          {!log.length && (
            <p class="faint">
              {commands.length} commands available. Try <code>presence status</code> or <code>proactive plan</code>.
            </p>
          )}
          {log.map((entry, i) => (
            <div class="console-entry" key={i}>
              <div class="line">
                <b>$</b> {entry.line}
                {entry.ms ? <span class="faint small"> · {Math.round(entry.ms)} ms</span> : null}
              </div>
              {entry.ok ? (
                typeof entry.value === "string" ? <pre class="json">{entry.value}</pre> : <Json value={entry.value} />
              ) : (
                <pre class="json bad-text">{String(entry.value)}</pre>
              )}
            </div>
          ))}
          <div ref={end} />
        </div>
        <div class="console-input">
          {suggestions.length > 0 && line.trim() && (
            <ul class="suggest" role="listbox">
              {suggestions.map(({ command, text }, i) => (
                <li key={text} role="option" aria-selected={i === index} onMouseDown={(e) => (e.preventDefault(), complete(text))}>
                  <span>{text}</span>
                  <span>{command.help}</span>
                </li>
              ))}
            </ul>
          )}
          <span class="prompt">dusha</span>
          <input
            ref={input}
            value={line}
            spellcheck={false}
            autocomplete="off"
            aria-label="Command"
            placeholder={busy ? "running…" : "presence status"}
            onInput={(e) => {
              setLine(e.currentTarget.value);
              setIndex(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "Tab" && suggestions.length) {
                e.preventDefault();
                complete(suggestions[index]!.text);
              } else if (e.key === "ArrowDown" && suggestions.length) {
                e.preventDefault();
                setIndex((i) => (i + 1) % suggestions.length);
              } else if (e.key === "ArrowUp" && suggestions.length && line.trim()) {
                e.preventDefault();
                setIndex((i) => (i - 1 + suggestions.length) % suggestions.length);
              } else if (e.key === "ArrowUp" && past.length) {
                e.preventDefault();
                const next = cursor < 0 ? past.length - 1 : Math.max(0, cursor - 1);
                setCursor(next);
                setLine(past[next]!);
              } else if (e.key === "ArrowDown" && cursor >= 0) {
                e.preventDefault();
                const next = cursor + 1;
                setCursor(next >= past.length ? -1 : next);
                setLine(next >= past.length ? "" : past[next]!);
              } else if (e.key === "Enter") {
                e.preventDefault();
                if (!busy) submit();
              }
            }}
          />
          {parsed.command && <span class="faint small hide-sm">{usage(parsed.command)}</span>}
        </div>
      </div>
    </div>
  );
}
