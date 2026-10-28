// What every page reads: the server's plugins, commands, and tools, merged with what this process knows.
import { createContext } from "preact";
import { useContext, useEffect, useMemo, useState } from "preact/hooks";

import { type Manifest, type Meta, type PluginStatus, type Tool, get, mcp, meta, usePoll } from "./api";
import { type Edge, type PluginView, buildPlugins, wiring } from "./model";

export type Store = {
  meta?: Meta;
  online: boolean;
  error?: Error;
  health?: { companion: string; database: string; plugins: string[] };
  plugins: PluginView[];
  edges: Edge[];
  coreCommands: Manifest["commands"];
  coreTools: Tool[];
  manifest?: Manifest;
  active: (name: string) => boolean;
  refresh: () => void;
};

const Context = createContext<Store>(null as unknown as Store);

export const useStore = () => useContext(Context);

export function StoreProvider({ children }: { children: preact.ComponentChildren }) {
  const info = usePoll(meta, 60_000);
  const status = usePoll(() => get<Record<string, PluginStatus>>("state/v1/plugins"), 8_000);
  const manifest = usePoll(() => get<Manifest>("cli/v1/commands"), 30_000);
  const health = usePoll(() => get<Store["health"]>("health"), 8_000);
  const [tools, setTools] = useState<Tool[]>();

  useEffect(() => {
    if (!status.data) return;
    mcp<{ tools: Tool[] }>("tools/list", {}, true).then((result) => setTools(result.tools), () => setTools([]));
  }, [Object.keys(status.data ?? {}).join(), manifest.at]);

  const store = useMemo<Store>(() => {
    const built = buildPlugins(info.data, status.data, manifest.data, tools);
    const coreEvents = info.data?.coreEvents ?? [];
    return {
      meta: info.data,
      online: Boolean(status.data) && !status.error,
      error: status.error ?? info.error,
      health: health.data,
      plugins: built.plugins,
      edges: wiring(built.plugins, coreEvents),
      coreCommands: built.coreCommands,
      coreTools: built.coreTools,
      manifest: manifest.data,
      active: (name) => built.plugins.some((plugin) => plugin.name === name && plugin.state === "active"),
      refresh: () => {
        status.refresh();
        manifest.refresh();
        info.refresh();
        health.refresh();
      },
    };
  }, [info.data, info.error, status.data, status.error, manifest.data, tools, health.data]);

  return <Context.Provider value={store}>{children}</Context.Provider>;
}
