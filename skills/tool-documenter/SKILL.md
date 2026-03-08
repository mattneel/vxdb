---
name: tool-documenter
description: Incrementally build operational knowledge about tools, APIs, and services — logging edge cases, undocumented behavior, and parameter gotchas in vxdb so any agent can retrieve them later.
---

# Tool Documenter

Build a shared operational knowledge base about tools, APIs, and services. When you discover something the docs don't tell you — an edge case, a gotcha, a workaround — log it. Before using a tool for anything non-trivial, query for what's already known.

## Table Schema

Table: `tool_docs`

| Column | Type | Description |
|---|---|---|
| tool_name | string | Tool, API, or service name (e.g. "gh cli", "duckdb", "fastmcp") |
| topic | text:embed | What aspect this covers (e.g. "creating pull requests with labels") |
| knowledge | text:embed | The actual operational knowledge — a clear instruction or warning |
| kind | string | One of: "gotcha", "usage", "limitation", "workaround", "example", "config" |
| verified | string | "true" or "false" — has this been confirmed to still work? |
| date | string | ISO date (e.g. "2026-03-08") |

Create the table on first use:

```
create_table("tool_docs", columns: tool_name:string, topic:text:embed, knowledge:text:embed, kind:string, verified:string, date:string)
```

## Workflows

### 1. Log on Discovery

When you encounter unexpected behavior, an undocumented requirement, or a useful trick — insert it immediately. Write knowledge as a self-contained instruction that would help someone hitting this for the first time.

```
insert("tool_docs", {
  tool_name: "gh cli",
  topic: "creating PRs with label assignment",
  knowledge: "gh pr create --label requires the label to already exist on the repo. It will not auto-create labels. Use gh label create first if needed.",
  kind: "gotcha",
  verified: "true",
  date: "2026-03-08"
})
```

Good entries are specific and actionable. Bad entries restate what the docs already say clearly.

### 2. Query Before Using a Tool

Before using a tool for a non-trivial operation, check for relevant operational knowledge.

**Targeted lookup** — filter by tool_name, semantic search on topic:

```
query("tool_docs", "vector column type requirements", where: "tool_name = 'lancedb'", columns: [topic, knowledge, kind])
```

**Broad discovery** — semantic search across all tools:

```
query("tool_docs", "authentication token expiration", columns: [tool_name, topic, knowledge])
```

**Find all gotchas for a tool before starting work:**

```
query("tool_docs", "common issues", where: "tool_name = 'docker' AND kind = 'gotcha'", columns: [topic, knowledge])
```

### 3. Update on Correction

If knowledge turns out to be wrong or outdated, update the entry or mark it unverified:

```
update("tool_docs", where: "tool_name = 'npm' AND topic LIKE '%peer dep%'", set: { verified: "false" })
```

If you discover the correct behavior, insert a new entry with the fix and mark the old one unverified rather than deleting it — the history of what was wrong is itself useful.

### 4. Cross-Tool Patterns

Use SQL-style queries to audit and maintain the knowledge base:

- Find the most-documented tools: query with `group by tool_name`
- Surface unverified entries for re-checking: `where: "verified = 'false'"`
- Find all workarounds (potential tech debt): `where: "kind = 'workaround'"`

## Triggers

**Do this:**
- You hit unexpected tool behavior and had to debug it
- User says "document this" or "remember this about X"
- User asks "what do I know about X tool"
- You're about to use a complex tool for the first time in a session
- You just finished debugging a tool-related issue

**Don't do this:**
- Documenting application code structure (use codebase-cartographer)
- Logging bugs in the user's project (use bug-archaeologist)
- Duplicating what official docs already explain clearly

## Writing Good Entries

The most valuable entries are the ones that cost you time to figure out. Prioritize:

1. **Gotchas** — things that silently fail or produce wrong results
2. **Workarounds** — non-obvious solutions to known limitations
3. **Parameter gotchas** — flags that don't work as expected, required but undocumented params
4. **Version-specific behavior** — things that changed between versions
5. **Interaction effects** — tool A behaves differently when used with tool B

Write each `knowledge` entry so it stands alone. Someone should be able to read just that field and know exactly what to do or avoid, without needing the rest of the row for context.

Keep entries atomic — one piece of knowledge per row. If you learned three things about a tool, that's three inserts.
