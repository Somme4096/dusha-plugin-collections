import { useEffect, useMemo, useRef, useState } from "preact/hooks";

import { Chevron, Moon, Search, Sun } from "./components/icons";
import { StoreProvider, useStore } from "./store";
import { Ambient } from "./views/Ambient";
import { Console } from "./views/Console";
import { Overview } from "./views/Overview";
import { Plugins } from "./views/Plugins";
import { Presence } from "./views/Presence";
import { Proactive } from "./views/Proactive";

export type Route = { page: string; arg: string };

function parse(): Route {
  const [page = "", ...rest] = location.hash.replace(/^#\/?/, "").split("/");
  return { page: page || "overview", arg: decodeURIComponent(rest.join("/")) };
}

export const go = (page: string, arg = "") => {
  location.hash = `#/${page}${arg ? `/${encodeURIComponent(arg)}` : ""}`;
};

function useRoute(): Route {
  const [route, setRoute] = useState(parse);
  useEffect(() => {
    const update = () => {
      setRoute(parse());
      window.scrollTo({ top: 0 });
    };
    addEventListener("hashchange", update);
    return () => removeEventListener("hashchange", update);
  }, []);
  return route;
}

function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState(document.documentElement.dataset.theme ?? "light");
  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)");
    const follow = () => {
      let saved = null;
      try {
        saved = localStorage.getItem("dusha-theme");
      } catch {}
      if (!saved) apply(media.matches ? "dark" : "light");
    };
    media.addEventListener("change", follow);
    return () => media.removeEventListener("change", follow);
  }, []);
  const apply = (next: string) => {
    document.documentElement.dataset.theme = next;
    setTheme(next);
  };
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    try {
      localStorage.setItem("dusha-theme", next);
    } catch {}
    apply(next);
  };
  return [theme, toggle];
}

const PAGES = [
  { page: "overview", label: "overview", short: "ov" },
  { page: "plugins", label: "plugins", short: "pl" },
  { page: "proactive", label: "proactive", short: "pr" },
  { page: "presence", label: "presence", short: "pe" },
  { page: "ambient", label: "ambient", short: "am" },
  { page: "console", label: "console", short: "co" },
];

function useRail(): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem("dusha-rail") === "collapsed";
    } catch {
      return false;
    }
  });
  const toggle = () =>
    setCollapsed((was) => {
      try {
        localStorage.setItem("dusha-rail", was ? "open" : "collapsed");
      } catch {}
      return !was;
    });
  return [collapsed, toggle];
}

function Rail({
  route,
  theme,
  toggle,
  collapsed,
  toggleRail,
  openPalette,
}: {
  route: Route;
  theme: string;
  toggle: () => void;
  collapsed: boolean;
  toggleRail: () => void;
  openPalette: () => void;
}) {
  const store = useStore();
  const counts: Record<string, string> = {
    plugins: `${store.plugins.filter((p) => p.state === "active").length}/${store.plugins.length}`,
  };
  const name = store.health?.companion || store.meta?.companion;
  return (
    <aside class="rail">
      <div class="rail-head">
        <a class="brand" href="#/" title="dusha" style={{ textDecoration: "none" }}>
          <span class="brand-mark" aria-hidden="true" />
          <span class="rail-word">dusha</span>
          {name && <span class="brand-name rail-word">{name}</span>}
        </a>
        <button
          class="btn ghost rail-toggle"
          onClick={toggleRail}
          aria-label={collapsed ? "Expand the sidebar" : "Collapse the sidebar"}
          aria-expanded={!collapsed}
          title={collapsed ? "Expand the sidebar" : "Collapse the sidebar"}
        >
          <span class="rail-flip">
            <Chevron size={12} />
          </span>
        </button>
      </div>
      <nav class="nav" aria-label="Sections">
        <span class="nav-label rail-word">companion</span>
        {PAGES.map(({ page, label, short }) => (
          <a key={page} href={`#/${page}`} aria-label={label} aria-current={route.page === page ? "page" : undefined} title={label}>
            <span class="rail-word">{label}</span>
            <span class="rail-short" aria-hidden="true">
              {short}
            </span>
            {counts[page] && <span class="count">{counts[page]}</span>}
          </a>
        ))}
      </nav>
      <div class="rail-foot">
        <button class="btn ghost" onClick={openPalette} aria-label="Open the command palette" title="Jump (⌘K)">
          <Search size={12} /> <span class="rail-word">jump</span> <kbd class="rail-word">⌘K</kbd>
        </button>
        <button class="btn ghost" onClick={toggle} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
          {theme === "dark" ? <Sun size={12} /> : <Moon size={12} />} <span class="rail-word">{theme === "dark" ? "light" : "dark"}</span>
        </button>
        <span class="status server" title={store.meta?.server}>
          <span class={`dot ${store.online ? "ok live" : "bad"}`} />
          <span class="rail-word">{store.meta?.server.replace(/^https?:\/\//, "") ?? "…"}</span>
        </span>
      </div>
    </aside>
  );
}

type Jump = { label: string; kind: string; act: () => void };

function Palette({ close }: { close: () => void }) {
  const store = useStore();
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);

  const items = useMemo<Jump[]>(() => {
    const all: Jump[] = [
      ...PAGES.map(({ page, label }) => ({ label, kind: "page", act: () => go(page) })),
      ...store.plugins.map((plugin) => ({ label: plugin.name, kind: `plugin · ${plugin.origin}`, act: () => go("plugins", plugin.name) })),
      ...(store.manifest?.commands ?? []).map((command) => {
        const line = [command.group, command.name].filter(Boolean).join(" ");
        return { label: line, kind: "command", act: () => go("console", line) };
      }),
    ];
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return all.filter((item) => words.every((word) => `${item.label} ${item.kind}`.toLowerCase().includes(word))).slice(0, 40);
  }, [query, store.plugins, store.manifest]);

  const pick = (item?: Jump) => {
    if (!item) return;
    item.act();
    close();
  };

  return (
    <div class="palette-backdrop" onClick={close}>
      <div class="palette" role="dialog" aria-label="Jump to" onClick={(e) => e.stopPropagation()}>
        <input
          ref={input}
          value={query}
          placeholder="Jump to a page, plugin, or command"
          aria-label="Search"
          onInput={(e) => {
            setQuery(e.currentTarget.value);
            setIndex(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") close();
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setIndex((i) => Math.min(items.length - 1, i + 1));
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setIndex((i) => Math.max(0, i - 1));
            }
            if (e.key === "Enter") pick(items[index]);
          }}
        />
        <ul role="listbox">
          {items.map((item, i) => (
            <li key={`${item.kind}${item.label}`} role="option" aria-selected={i === index} onMouseEnter={() => setIndex(i)} onClick={() => pick(item)}>
              <span>{item.label}</span>
              <span class="kind">{item.kind}</span>
            </li>
          ))}
          {!items.length && <li class="faint">Nothing matches.</li>}
        </ul>
      </div>
    </div>
  );
}

function Topbar({ route }: { route: Route }) {
  const store = useStore();
  const active = store.plugins.filter((plugin) => plugin.state === "active").length;
  return (
    <header class="topbar">
      <div class="crumbs">
        <span>{store.health?.companion || store.meta?.companion || "dusha"}</span>
        <span>/</span>
        <span>{route.page}</span>
        {route.arg && route.page === "plugins" && (
          <>
            <span>/</span>
            <span>{route.arg}</span>
          </>
        )}
      </div>
      <div class="topbar-right">
        {store.online ? (
          <span class="status">
            <span class="dot ok live" /> {active} plugins active · database {store.health?.database ?? "…"}
          </span>
        ) : (
          <span class="status bad-text">
            <span class="dot bad" /> server unreachable
          </span>
        )}
      </div>
    </header>
  );
}

function Offline() {
  const store = useStore();
  if (store.online || !store.error) return null;
  return (
    <div class="banner" role="alert">
      <b>Can't reach the server.</b>
      <span>
        {store.error.message}. Start it with <code>dusha serve</code>, or point the WebUI elsewhere with{" "}
        <code>--url</code> or <code>--companion</code>. Retrying every few seconds.
      </span>
    </div>
  );
}

function Page({ route }: { route: Route }) {
  switch (route.page) {
    case "plugins":
      return <Plugins selected={route.arg} />;
    case "proactive":
      return <Proactive initial={route.arg} />;
    case "presence":
      return <Presence />;
    case "ambient":
      return <Ambient />;
    case "console":
      return <Console initial={route.arg} />;
    default:
      return <Overview />;
  }
}

function Shell() {
  const route = useRoute();
  const [theme, toggle] = useTheme();
  const [palette, setPalette] = useState(false);
  const [railCollapsed, toggleRail] = useRail();
  const store = useStore();

  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette((open) => !open);
      }
    };
    addEventListener("keydown", key);
    return () => removeEventListener("keydown", key);
  }, []);

  useEffect(() => {
    const name = store.health?.companion || store.meta?.companion;
    document.title = [route.page === "overview" ? "" : route.page, name, "dusha"].filter(Boolean).join(" · ");
  }, [route.page, store.health, store.meta]);

  return (
    <div class={`shell${railCollapsed ? " rail-collapsed" : ""}`}>
      <Rail
        route={route}
        theme={theme}
        toggle={toggle}
        collapsed={railCollapsed}
        toggleRail={toggleRail}
        openPalette={() => setPalette(true)}
      />
      <main class="main">
        <Topbar route={route} />
        <Offline />
        <Page key={route.page} route={route} />
      </main>
      {palette && <Palette close={() => setPalette(false)} />}
    </div>
  );
}

export function App() {
  return (
    <StoreProvider>
      <Shell />
    </StoreProvider>
  );
}
