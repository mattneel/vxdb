---
name: vxdb
description: "Agent infrastructure built on vxdb — semantic KV store over MCP. Use this skill whenever you need persistent state beyond the context window: working memory, caching, debugging state, knowledge accumulation, cross-agent coordination, or any structured data with semantic search. Triggers: any task where you're losing context to compression, repeating expensive operations, debugging in circles, building knowledge across sessions, or coordinating with other agents. Also triggers on: 'remember', 'cache', 'what did I learn', 'have I seen this before', 'log this'. This skill teaches you to build your own infrastructure from three primitives: tables (create_table), semantic search (NEAR/SEARCH), and relational queries (sql tool)."
---

# vxdb — Agent Infrastructure Toolkit

You have three primitives. Everything else is a composition.

| Primitive | What it does |
|-----------|-------------|
| `create_table` / `insert` / `update` / `delete` | Semantic KV store. Any schema. text:embed columns get vector indexes. |
| `query` with `NEAR()` / `SEARCH()` | Semantic retrieval. "Find things like X." Single-table. |
| `sql` | Full DuckDB SQL. JOINs across tables, aggregations, analytics. |

Tables are cheap. Create them as you discover you need them. The schema is an emergent
artifact of your workflow, not an upfront design. Use the `sql` tool to JOIN across
your own emergent schema — that's what makes this compositional, not just "multiple
vector stores."

## Pattern 1: Working Memory

**Problem**: You read 50 files, context compresses, you forget what you learned.

**Solution**: Extract facts from every significant read into a session-scoped table.

```
create_table("wm", {
    "key": "string",
    "facts": "text:embed",
    "source": "string",
    "timestamp": "string"
})
```

After reading a file or running a command, extract the important bits:

```
insert("wm", [{"key": "auth_module", "facts": "JWT via verify_token(), 24h expiry hardcoded line 45, exports AuthMiddleware, depends on cryptography", "source": "vxdb/auth.py", "timestamp": "..."}])
```

Before re-reading a file, check working memory first:

```
query("SELECT * FROM wm WHERE NEAR(facts, 'authentication token validation', 3)")
```

If similarity > 0.7, you already know this. Don't re-read the file.

**Lifecycle**: Drop the table at session end, or keep it for continuity. Your call.

## Pattern 2: Semantic Cache

**Problem**: You're about to make an expensive call (web search, API, large computation)
that you might have already made.

**Solution**: NEAR() before every expensive operation.

```
create_table("cache", {
    "query": "text:embed",
    "result": "string",
    "source": "string",
    "timestamp": "string"
})
```

Before making the call:

```
query("SELECT * FROM cache WHERE NEAR(query, 'the thing you are about to look up', 1) ORDER BY _similarity DESC LIMIT 1")
```

If `_similarity > 0.85`, use the cached result. Tell the user it's from cache.

After making the call:

```
insert("cache", [{"query": "what you looked up", "result": "what you got back", "source": "web_search", "timestamp": "..."}])
```

**Eviction**: `delete("cache", where="timestamp < '2025-01-01'")`. Semantic dedup:
query for entries with > 0.9 similarity to newer ones and delete the older.

## Pattern 3: Hypothesis-Driven Debugging

**Problem**: You go in circles. Try fix A, fails. Try fix B, fails. Try fix A again.

**Solution**: Track every hypothesis in a table. Check before retrying.

```
create_table("debug", {
    "hypothesis": "text:embed",
    "test": "string",
    "result": "string",
    "status": "string",
    "timestamp": "string"
})
```

Before trying a fix:

```
query("SELECT * FROM debug WHERE NEAR(hypothesis, 'what you are about to try', 3)")
```

If `_similarity > 0.8`, you already tried this. Don't retry — try something else.

After testing:

```
insert("debug", [{"hypothesis": "wrong port in config", "test": "grep PORT .env", "result": "5433 but postgres on 5432", "status": "confirmed", "timestamp": "..."}])
```

**Force structure**: State your hypothesis before running the test. Predict the outcome.
Record whether prediction matched. This prevents "random command, hope for the best."

Pattern: always check the obvious things first (is the service running? right branch?
saved the file? cache stale?). Insert those as hypotheses with status="untested" at
session start.

Drop the table when the bug is fixed.

## Pattern 4: Knowledge Distillation

**Problem**: You need to build understanding from a large corpus — docs, code, research.

**Solution**: Insert at levels, trace provenance.

```
create_table("knowledge", {
    "content": "text:embed",
    "level": "string",
    "source": "string",
    "parent_ids": "string",
    "topic": "string"
})
```

1. **Ingest**: Read sources, insert as `level="raw"` with source paths.
2. **Synthesize**: NEAR() to find related raw chunks. Summarize. Insert as `level="summary"` with `parent_ids` pointing to raw _ids.
3. **Distill**: NEAR() across summaries. Extract conclusions. Insert as `level="conclusion"`.

Trace provenance in either direction:

```
query("SELECT * FROM knowledge WHERE level = 'raw' AND NEAR(content, 'the conclusion you want to trace', 10)")
```

## Pattern 5: Decision Log

**Problem**: You made a decision 3 sessions ago. Now a similar question comes up.

**Solution**: Two text:embed columns — search by problem OR by solution.

```
create_table("decisions", {
    "context": "text:embed",
    "decision": "text:embed",
    "alternatives": "string",
    "rationale": "string",
    "domain": "string",
    "status": "string",
    "date": "string"
})
```

Before a design discussion, search for prior decisions:

```
query("SELECT * FROM decisions WHERE NEAR(context, 'the problem being discussed', 5)")
```

When a decision supersedes an old one, update the old one's status:

```
update("decisions", {"status": "superseded"}, id="old-decision-id")
```

## Pattern 6: Self-Evaluation

**Problem**: You keep making the same kind of mistake on the same kind of task.

**Solution**: Log approach + outcome, query before similar tasks.

```
create_table("evals", {
    "task": "text:embed",
    "approach": "text:embed",
    "outcome": "string",
    "feedback": "string",
    "task_type": "string",
    "date": "string"
})
```

Before starting a task:

```
query("SELECT * FROM evals WHERE NEAR(task, 'current task description', 5) ORDER BY _similarity DESC")
```

Filter to successes: what approach worked for tasks like this?

```
query("SELECT approach, outcome FROM evals WHERE outcome = 'success' AND NEAR(task, 'current task description', 5)")
```

Calibration check via sql:

```
sql("SELECT task_type, outcome, COUNT(*) AS cnt FROM lance_ns.main.evals GROUP BY task_type, outcome ORDER BY task_type")
```

## Pattern 7: Shared Blackboard (Multi-Agent)

**Problem**: Agent A discovers something Agent B needs, but they run sequentially.

**Solution**: Shared table with agent-scoped namespacing.

```
create_table("blackboard", {
    "content": "text:embed",
    "agent": "string",
    "task": "string",
    "kind": "string",
    "timestamp": "string"
})
```

Agent A writes findings:

```
insert("blackboard", [{"content": "API rate-limits at 100/min, returns 429 with Retry-After header", "agent": "researcher", "task": "api-survey", "kind": "finding", "timestamp": "..."}])
```

Agent B queries for relevance:

```
query("SELECT * FROM blackboard WHERE NEAR(content, 'rate limiting and throttling', 5)")
```

Filter by agent or task to scope:

```
query("SELECT * FROM blackboard WHERE agent = 'researcher' AND NEAR(content, 'API constraints', 5)")
```

## Pattern 8: Tool Documentation

**Problem**: You discover a gotcha with a tool. Next session, you've forgotten it.

**Solution**: Log operational knowledge, query before using the tool.

```
create_table("tool_docs", {
    "tool": "string",
    "knowledge": "text:embed",
    "kind": "string",
    "date": "string"
})
```

After discovering a gotcha:

```
insert("tool_docs", [{"tool": "duckdb-lance", "knowledge": "DELETE via DuckDB SQL deletes ALL rows regardless of WHERE clause. Use LanceDB Python API tbl.delete() instead.", "kind": "gotcha", "date": "..."}])
```

Before using a tool for something non-trivial:

```
query("SELECT * FROM tool_docs WHERE tool = 'duckdb-lance' AND NEAR(knowledge, 'deleting rows with a filter', 3)")
```

## Pattern 9: Bug Archaeology

**Problem**: Same class of bug keeps recurring. Each time you debug from scratch.

**Solution**: Dual text:embed — search by symptoms OR by root cause.

```
create_table("bugs", {
    "symptoms": "text:embed",
    "root_cause": "text:embed",
    "fix": "string",
    "component": "string",
    "date": "string"
})
```

When you see a bug:

```
query("SELECT * FROM bugs WHERE NEAR(symptoms, 'connection drops after 30s behind nginx', 5)")
```

When you have a theory:

```
query("SELECT * FROM bugs WHERE NEAR(root_cause, 'proxy timeout shorter than heartbeat interval', 5)")
```

## Composition: The Emergent Schema

The real power is JOINing across these tables. You create them as needed. Over time,
your schema looks like:

```
wm          — what I know right now (session)
cache       — what I've already looked up (session/persistent)
debug       — what I've tried (session)
knowledge   — what I've learned from sources (persistent)
decisions   — what I've decided (persistent)
evals       — how I've performed (persistent)
blackboard  — what other agents found (persistent)
tool_docs   — operational knowledge about tools (persistent)
bugs        — failure patterns (persistent)
```

Cross-table queries via `sql`:

```
sql("SELECT d.decision, k.content FROM lance_ns.main.decisions d JOIN lance_ns.main.knowledge k ON d.domain = k.topic WHERE d.status = 'active'")
```

"What decisions did I make that were informed by knowledge from source X?"

```
sql("SELECT d.decision, d.rationale FROM lance_ns.main.decisions d JOIN lance_ns.main.knowledge k ON d.domain = k.topic WHERE k.source = 'architecture-doc.md'")
```

## Lifecycle Rules

- **Session tables** (wm, debug, cache): create at start, drop at end. Or keep for continuity.
- **Persistent tables** (decisions, knowledge, evals, bugs, tool_docs): create once, accumulate forever.
- **Deduplication**: Before inserting, NEAR() with k=3. If similarity > 0.85, skip or update instead.
- **Eviction**: Delete by timestamp, or by semantic redundancy (older entry similar to newer one).
- **Schema evolution**: Need a new column? Create a new table. Tables are cheap. JOIN later.

## Principles

**Tables are free. Create them when you need them.** Don't pre-design a grand schema.
Discover what you need as you work.

**NEAR() before everything expensive.** Check cache, check working memory, check bugs,
check tool docs. The cost of a NEAR() query is ~16ms. The cost of re-doing work is minutes.

**Write for your future self.** Every insert should be a self-contained statement that
makes sense without the original context. "verify_token() uses JWT with 24h expiry"
not "the token thing has a hardcoded value."

**Search before you store.** Duplicates degrade retrieval quality. Always deduplicate.

**The sql tool is the unlock.** Single-table NEAR() is useful. Cross-table JOINs across
your own emergent schema is where it gets compositional. "What decisions were informed
by what knowledge?" is a JOIN, not a vector search.
