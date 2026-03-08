---
name: learning-log
description: "Persistent learning memory using vxdb for semantic retrieval. Tracks gotchas, patterns, tool quirks, domain knowledge, and user preferences across sessions — scaling past MEMORY.md's ~200-line limit. Triggers: 'remember that...', 'I keep forgetting...', 'log this', 'learning log', session starts on projects with existing learning logs, or when the agent discovers something non-obvious (unexpected behavior, workaround, undocumented quirk). NOT for: temporary task context, active work state, things that belong in CLAUDE.md or project docs."
---

# Learning Log

Scalable long-term memory backed by vxdb. Replaces flat-file memory with semantic search over thousands of entries, filterable by category and project.

## Why Not MEMORY.md

- MEMORY.md tops out at ~200 lines before it becomes noise
- Retrieval is positional (you read top-to-bottom), not semantic
- No filtering by category, project, or relevance
- Learning Log uses NEAR() vector search to surface the right insight at the right time

## Table Setup

On first use, check if the `learnings` table exists. If not, create it:

```
mcp__vxdb__list_tables → check for "learnings"

mcp__vxdb__create_table:
  name: learnings
  schema:
    insight:   "text:embed"
    category:  "string"      # gotcha | convention | preference | domain | tool-tip
    project:   "string"      # project name or "global"
    context:   "string"      # what you were doing when you learned this
    date:      "string"      # YYYY-MM-DD
```

## Categories

| Category | What Goes Here | Example |
|----------|---------------|---------|
| `gotcha` | Surprising behavior, footguns, silent failures | "pyarrow 15+ silently drops null columns on parquet round-trip" |
| `convention` | Team/project patterns, naming rules, architecture decisions | "This repo uses Result types instead of exceptions for all DB ops" |
| `preference` | User's stated preferences for code style, tooling, workflow | "User prefers explicit imports over star imports in Python" |
| `domain` | Domain-specific knowledge relevant to the work | "HNSW index build time is O(n log n); query is O(log n)" |
| `tool-tip` | CLI flags, tool quirks, config tricks | "zig build -Doptimize=ReleaseSafe still includes safety checks — use ReleaseFast for benchmarks" |

## Logging a Learning

When you encounter something worth remembering:

1. **Write a self-contained insight.** Months from now, the insight text must stand alone. Bad: "the flag fixed it". Good: "Pass --no-verify to npm publish when the OTP prompt hangs in CI — it skips the interactive 2FA check."

2. **Deduplicate before inserting.** Query for similar existing entries:
   ```
   mcp__vxdb__query:
     sql: SELECT * FROM learnings WHERE NEAR(insight, '<new insight text>', 3)
   ```
   If any result has `_similarity > 0.85`, skip the insert. If similarity is 0.70-0.85, consider whether the new insight adds meaningful detail — update the existing row if so.

3. **Insert with full metadata:**
   ```
   mcp__vxdb__insert:
     table: learnings
     rows:
       - insight: "<self-contained statement>"
         category: "<gotcha|convention|preference|domain|tool-tip>"
         project: "<project-name or global>"
         context: "<what triggered this learning>"
         date: "<YYYY-MM-DD>"
   ```

## Proactive Recall

### Session Start

When beginning work on a project, pull relevant context:

```
mcp__vxdb__query:
  sql: SELECT * FROM learnings WHERE project = '<project>' ORDER BY date DESC LIMIT 10
```

Also run a semantic search against the task description:

```
mcp__vxdb__query:
  sql: SELECT * FROM learnings WHERE NEAR(insight, '<task description>', 5)
```

Surface any results with `_similarity > 0.5` to the user as "things I remember that might be relevant."

### Mid-Session

When something unexpected happens (build failure, weird API behavior, confusing error):
1. Search learnings first: `NEAR(insight, '<description of what happened>', 3)`
2. If a match exists, apply it immediately and mention "I've seen this before"
3. If no match and the resolution is non-obvious, log it

### Domain Triggers

When entering a familiar domain (e.g., working with Docker, Zig allocators, SQL migrations), pull domain-relevant learnings:

```
mcp__vxdb__query:
  sql: SELECT * FROM learnings WHERE NEAR(insight, '<domain keyword>', 5) AND category = 'domain'
```

## Maintenance

### Periodic Cleanup

When the user asks, or roughly every 20-30 sessions on a project:

1. **Find stale entries:** Query by project, sort by date, flag anything older than 6 months for review
2. **Find contradictions:** NEAR() search pairs of insights in the same category — if two entries are similar but say different things, surface them for resolution
3. **Merge duplicates:** If two entries cover the same ground, keep the better-written one, delete the other

```
# Find potential duplicates in a category
mcp__vxdb__query:
  sql: SELECT * FROM learnings WHERE category = 'gotcha' AND NEAR(insight, '<existing insight>', 5)
```

Delete with `mcp__vxdb__delete` using the `id` of the inferior entry. Update with `mcp__vxdb__update` if an existing entry needs correction.

## User Interaction Patterns

- **"Remember that..."** → Extract the insight, categorize it, deduplicate, insert
- **"I keep forgetting..."** → Log it AND surface it immediately so they have it now
- **"What do you know about X?"** → NEAR() search against X, return top results
- **"Log this"** → Summarize what just happened as a self-contained insight, insert
- **"Clean up learnings"** → Run maintenance workflow
- **"What have I learned about [project]?"** → Filter by project, return grouped by category

## Principles

**Write for your future self.** Every insight should be a complete thought. Include the "why" and the "when this matters," not just the "what."

**Search before you store.** Duplicates degrade retrieval quality. Always deduplicate.

**Global vs project-scoped.** If a learning applies everywhere (e.g., a git quirk), use `project: "global"`. If it's specific to a codebase, scope it.

**Don't log the obvious.** If it's in the docs and easy to find, it doesn't need a learning entry. Log the things that cost you time to figure out.

**Prefer updating over appending.** If you learn more about an existing topic, update the existing entry to be more complete rather than adding a second one.
