---
name: semantic-cache
description: "Uses vxdb as a semantic cache for expensive operations (web fetches, API calls, large computations). Before making a costly call, silently checks if a semantically similar request was already fulfilled. Returns cached results on hit, stores new results on miss, and manages eviction by time, similarity, and size. Triggers: proactively before any expensive tool call, 'cache this', 'have I looked this up before', repeated similar queries in a session. NOT for: fast local operations (file reads, grep, git), one-off queries the user explicitly wants fresh."
---

# Semantic Cache

Transparent caching layer backed by vxdb. Turns expensive, slow tool calls into instant retrievals when the same (or similar enough) question has been asked before.

## Table Setup

On first use, check if the `semantic_cache` table exists. If not, create it:

```
mcp__vxdb__list_tables -> check for "semantic_cache"

mcp__vxdb__create_table:
  name: semantic_cache
  schema:
    query:     "text:embed"   # the request/question — embedded for similarity search
    result:    "string"       # the cached response (stored as-is, not embedded)
    source:    "string"       # what produced this: "web_search", "web_fetch", "api_call", etc.
    timestamp: "string"       # ISO 8601 datetime of when the result was cached
    ttl:       "string"       # expiration policy: "short" (hours), "medium" (days), "long" (weeks)
```

## Cache Lookup (Before Every Expensive Call)

Before invoking WebSearch, WebFetch, or any external API tool, silently check the cache:

```
mcp__vxdb__query:
  sql: SELECT * FROM semantic_cache WHERE NEAR(query, '<the request about to be made>', 3)
```

**Decision logic:**

| `_similarity` | Action |
|---|---|
| >= 0.85 | **Cache hit.** Return the cached `result`. Tell the user: "From cache ({timestamp}):" followed by the result. Do NOT make the external call. |
| 0.75 - 0.84 | **Fuzzy match.** Only use if the user has asked for relaxed matching ("close enough", "roughly", "have I seen something like this"). Otherwise treat as a miss. |
| < 0.75 | **Cache miss.** Proceed with the expensive call. |

Also check freshness — even on a similarity hit, treat it as a miss if:
- `ttl = "short"` and `timestamp` is older than 6 hours
- `ttl = "medium"` and `timestamp` is older than 3 days
- `ttl = "long"` and `timestamp` is older than 2 weeks

## Storing Results (After a Cache Miss)

After making the expensive call and getting a result:

```
mcp__vxdb__insert:
  table: semantic_cache
  rows:
    - query: "<the original request, written as a clear question or statement>"
      result: "<the response, trimmed to essential content — not raw HTML dumps>"
      source: "<tool name: web_search, web_fetch, api_call, computation>"
      timestamp: "<current ISO 8601 datetime>"
      ttl: "<short|medium|long>"
```

**TTL assignment rules:**
- `short` — time-sensitive data: stock prices, weather, live scores, "latest" anything
- `medium` — moderately stable: documentation lookups, API references, news articles
- `long` — stable knowledge: concepts, tutorials, historical facts, language references

**Write the `query` field as a clean, searchable statement.** Not the raw URL or garbled search string. Example: "How does Raft leader election work?" not "raft leader election site:github.com filetype:pdf".

**Trim the `result` field.** Store the useful answer, not the full page dump. If the response is over 2000 characters, summarize it while preserving key facts.

## Eviction

### On Insert (Automatic)

After every insert, check the table size:

```
mcp__vxdb__query:
  sql: SELECT COUNT(*) as count FROM semantic_cache
```

If count > 500 rows, run cleanup:

1. **Time-based eviction** — delete expired entries:
   ```
   mcp__vxdb__query:
     sql: SELECT _id, ttl, timestamp FROM semantic_cache ORDER BY timestamp ASC LIMIT 50
   ```
   Check each entry against its TTL policy. Delete any that have expired using `mcp__vxdb__delete`.

2. **Semantic dedup** — for the newly inserted entry, check if older near-duplicates exist:
   ```
   mcp__vxdb__query:
     sql: SELECT * FROM semantic_cache WHERE NEAR(query, '<newly cached query>', 5)
   ```
   If any older entry has `_similarity > 0.90`, delete the older one (the new entry supersedes it).

3. **Size-based eviction** — if still over 500 after steps 1-2, delete the oldest entries until under 450:
   ```
   mcp__vxdb__query:
     sql: SELECT _id FROM semantic_cache ORDER BY timestamp ASC LIMIT 50
   ```
   Delete these rows with `mcp__vxdb__delete`.

### On Explicit Command

When the user says "clean cache", "clear cache", or "cache maintenance":

1. Report current row count.
2. Run all three eviction steps above regardless of count.
3. Report how many entries were removed and how many remain.

## What to Cache vs. What Not To

**Cache these (proactively, silently):**
- WebSearch results
- WebFetch page content
- External API responses
- Expensive computations that took significant time
- Anything the user says "cache this" or "remember this result" about

**Do NOT cache:**
- Local file reads (fast, always fresh)
- Grep/glob results (fast, change with code edits)
- Git operations (inherently stateful)
- Anything the user says "get me fresh" or "look this up again" about

## User Interaction Patterns

- **"Have I looked this up before?"** — Run the NEAR() query and report matches with timestamps
- **"Cache this"** — Store the most recent expensive result with user-chosen TTL (default: medium)
- **"Clear/clean cache"** — Run full eviction, report stats
- **"Get fresh results for X"** — Skip cache, make the call, then update the cache entry
- **Repeated similar queries in one session** — After the second similar query, mention that caching is available

## Principles

**Be invisible on hits.** The user shouldn't have to think about caching. Just silently check and return cached results with a brief "(from cache, {timestamp})" note.

**Be honest about staleness.** Always show the timestamp. Never present stale data as current without flagging it.

**Cache the answer, not the noise.** A cached result should be a clean, useful response — not a raw data dump that needs re-processing every time.

**Prefer precision over recall.** A 0.85 threshold means the query must be genuinely similar, not just topically adjacent. "How does Python GIL work?" and "Python performance tips" are NOT a cache hit.

**Evict aggressively, store selectively.** A small cache with high-quality entries beats a large cache full of stale junk.
