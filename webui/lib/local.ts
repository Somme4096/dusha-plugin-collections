// Reads the plugins that live in a directory, the way dusha finds them: one subdirectory per plugin with a
// pyproject.toml holding [tool.dusha]. The server only reports what it loaded and what each plugin listens
// for. The source also says what a plugin emits and asks, so the wiring map can draw both ends.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

export type LocalPlugin = {
  name: string;
  package: string;
  description: string;
  summary: string;
  entry: string;
  inject: string[];
  file: string;
  lines: number;
  emits: string[];
  collects: string[];
  listens: string[];
  tools: string[];
  groups: string[];
  routes: { method: string; path: string }[];
  intervals: number;
};

const unique = (items: Iterable<string>) => [...new Set(items)].sort();

function all(source: string, pattern: RegExp, group = 1): string[] {
  return [...source.matchAll(pattern)].map((match) => match[group]!).filter(Boolean);
}

// The README's plugin table: | [name](name) | what it does |
function summaries(dir: string): Record<string, string> {
  const readme = join(dir, "README.md");
  if (!existsSync(readme)) return {};
  const found: Record<string, string> = {};
  for (const match of readFileSync(readme, "utf8").matchAll(/^\|\s*\[([\w-]+)\]\([^)]*\)\s*\|\s*(.+?)\s*\|\s*$/gm)) {
    found[match[1]!] = match[2]!;
  }
  return found;
}

function source(dir: string, entry: string): { file: string; text: string } | null {
  for (const candidate of [`${entry}.py`, join(entry, "__init__.py")]) {
    const path = join(dir, candidate);
    if (existsSync(path)) return { file: candidate, text: readFileSync(path, "utf8") };
  }
  return null;
}

export function scan(dir: string): LocalPlugin[] {
  if (!existsSync(dir) || !statSync(dir).isDirectory()) return [];
  const blurbs = summaries(dir);
  const plugins: LocalPlugin[] = [];
  for (const name of readdirSync(dir).sort()) {
    const project = join(dir, name, "pyproject.toml");
    if (!existsSync(project)) continue;
    let toml: Record<string, any>;
    try {
      toml = Bun.TOML.parse(readFileSync(project, "utf8")) as Record<string, any>;
    } catch {
      continue;
    }
    const dusha = toml.tool?.dusha;
    if (!dusha) continue;
    const entry = String(dusha.entry ?? name);
    const code = source(join(dir, name), entry);
    const text = code?.text ?? "";
    plugins.push({
      name,
      package: String(toml.project?.name ?? name),
      description: String(toml.project?.description ?? ""),
      summary: blurbs[name] ?? blurbs[name.replace(/_/g, "-")] ?? "",
      entry,
      inject: Array.isArray(dusha.inject) ? dusha.inject.map(String) : [],
      file: code ? join(name, code.file) : "",
      lines: text ? text.split("\n").length : 0,
      emits: unique(all(text, /\.emit\(\s*"([^"]+)"/g)),
      collects: unique(all(text, /\.collect\(\s*"([^"]+)"/g)),
      listens: unique(all(text, /ctx\.on\(\s*"([^"]+)"/g)),
      tools: unique(all(text, /\.tool\(\s*"([^"]+)"/g)),
      groups: unique([...all(text, /\.group\(\s*"([^"]+)"/g), ...all(text, /Group\(\s*ctx\.cli,\s*"([^"]+)"/g)]),
      routes: [...text.matchAll(/@router\.(get|post|put|delete)\(\s*"([^"]+)"/g)].map((match) => ({
        method: match[1]!.toUpperCase(),
        path: match[2]!,
      })),
      intervals: all(text, /ctx\.interval\(/g, 0).length,
    });
  }
  return plugins;
}
