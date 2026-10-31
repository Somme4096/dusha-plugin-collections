// What dusha ships in core (dusha/service.py BUILTINS). The server reports their state but not what they
// are, so the words come from dusha's README.
export type Builtin = { name: string; description: string; tools: string[]; routes: string[] };

export const BUILTINS: Builtin[] = [
  {
    name: "identity",
    description: "The identity text, identity.md or a file you name, at the head of every injection.",
    tools: [],
    routes: [],
  },
  {
    name: "memory",
    description: "Every message in SQLite with FTS5, plus optional embedding search. Recent turns and search hits go into context.",
    tools: ["memory_search", "memory_get"],
    routes: ["POST /state/v1/memory/search", "GET /state/v1/memory/{message_id}"],
  },
  {
    name: "evergreen",
    description: "Long-term facts with revisions, edited from the CLI or by the companion's tools. No automatic extraction.",
    tools: ["evergreen_list", "evergreen_remember", "evergreen_revise", "evergreen_forget"],
    routes: ["GET /state/v1/evergreen/facts", "POST /state/v1/evergreen/facts"],
  },
  {
    name: "memo",
    description: "Short notes the agent leaves for itself, such as a loose end to raise later.",
    tools: ["memo_add", "memo_list", "memo_done"],
    routes: ["POST /state/v1/memo/add", "GET /state/v1/memo/list"],
  },
  {
    name: "affect",
    description: "A deterministic emotion engine. Values per dimension shift with each user message and drift while they're silent.",
    tools: ["affect_status"],
    routes: ["GET /state/v1/affect"],
  },
  {
    name: "proactive",
    description: "Speaks up after silence, gated by quiet hours, cooldown, and caps, through a leased event queue.",
    tools: [],
    routes: ["POST /state/v1/proactive/evaluate", "GET /state/v1/proactive/events"],
  },
];

// Events the gateway itself emits to plugins, rather than another plugin.
export const CORE_EVENTS = ["message", "context", "decide"];
