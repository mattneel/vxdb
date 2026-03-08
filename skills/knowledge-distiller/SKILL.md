---
name: knowledge-distiller
description: "Progressive knowledge distillation through hierarchical levels using vxdb. Ingests raw material (documents, code, research), synthesizes related chunks into summaries, and distills summaries into key conclusions — with provenance tracing across all levels via parent_ids and NEAR(). Triggers: 'distill this', 'summarize what we know about X', 'what's the conclusion on X', 'build a knowledge base from these files', working with large document sets. NOT for: simple single-document reads, quick summaries that fit in one response."
---

# Knowledge Distiller

Hierarchical knowledge compression backed by vxdb. Raw material flows upward through three levels — raw, summary, conclusion — with every derived entry linked back to its sources via parent_ids.

## Table Setup

One table handles all levels. Check if it exists first, then create:

```
mcp__vxdb__list_tables → check for "knowledge"

mcp__vxdb__create_table:
  name: knowledge
  schema:
    content:    "text:embed"   # the actual text at any level
    level:      "string"       # "raw" | "summary" | "conclusion"
    source:     "string"       # file path, URL, or "synthesis" / "distillation"
    parent_ids: "string"       # comma-separated _ids of parent entries (empty for raw)
    topic:      "string"       # subject area tag
    timestamp:  "string"       # ISO 8601 date
```

## Phase 1: Ingest

Read source material and chunk it into meaningful units. Each chunk becomes a level="raw" entry.

**Chunking guidelines:**
- Code: one function or logical block per chunk. Include the file path as context.
- Documents: one paragraph or section per chunk. Keep enough context to stand alone.
- Research: one finding or claim per chunk. Include the source.

```
mcp__vxdb__insert:
  table: knowledge
  rows:
    - content: "The HNSW algorithm builds a multi-layer graph where each layer is a navigable small-world network. Search starts at the top layer and descends, using greedy routing at each level."
      level: "raw"
      source: "papers/hnsw-2018.pdf"
      parent_ids: ""
      topic: "vector-indexing"
      timestamp: "2026-03-08"
```

Insert in small batches (1-5 rows) as you read. Don't wait until you've read everything — early entries improve NEAR() queries for later chunking decisions.

## Phase 2: Synthesize

After ingesting raw material for a topic, query for related chunks and synthesize them.

**Step 1 — Find related raw chunks:**
```
mcp__vxdb__query:
  sql: SELECT _id, content, source FROM knowledge
       WHERE level = 'raw' AND NEAR(content, '<topic description>', 10)
```

**Step 2 — Write a synthesis.** Combine 3-7 related raw chunks into a single summary that captures the essential points. Don't just concatenate — distill.

**Step 3 — Insert as summary with provenance:**
```
mcp__vxdb__insert:
  table: knowledge
  rows:
    - content: "HNSW builds a layered navigable small-world graph for approximate nearest neighbor search. Construction is O(n log n), queries are O(log n). Key tuning parameters are M (connections per node) and efConstruction (build-time search width). Higher M improves recall but increases memory linearly."
      level: "summary"
      source: "synthesis"
      parent_ids: "raw_id_1,raw_id_2,raw_id_3"
      topic: "vector-indexing"
      timestamp: "2026-03-08"
```

Repeat for each topic cluster. A single raw chunk can appear in multiple summaries' parent_ids if it's relevant to multiple themes.

## Phase 3: Distill

Query summaries and extract the key conclusions.

**Step 1 — Pull summaries for a topic:**
```
mcp__vxdb__query:
  sql: SELECT _id, content FROM knowledge
       WHERE level = 'summary' AND NEAR(content, '<broad topic>', 10)
```

**Step 2 — Write a conclusion.** This should be a crisp, actionable statement — the kind of thing you'd put in an executive summary or a decision document.

**Step 3 — Insert as conclusion:**
```
mcp__vxdb__insert:
  table: knowledge
  rows:
    - content: "For datasets under 1M vectors, HNSW with M=16 and efConstruction=200 provides the best recall/speed tradeoff. Beyond 1M, consider IVF-PQ for memory savings at the cost of ~5% recall."
      level: "conclusion"
      source: "distillation"
      parent_ids: "summary_id_1,summary_id_2"
      topic: "vector-indexing"
      timestamp: "2026-03-08"
```

## Phase 4: Query & Trace

### Search within a level

```
mcp__vxdb__query:
  sql: SELECT content, source FROM knowledge
       WHERE level = 'conclusion' AND NEAR(content, 'which vector index to use', 5)
```

### Trace provenance downward

Given a conclusion, find what informed it:

```
# Get the conclusion's parent summaries
mcp__vxdb__query:
  sql: SELECT _id, content, parent_ids FROM knowledge WHERE _id IN ('summary_id_1', 'summary_id_2')

# Then get the raw material behind those summaries
mcp__vxdb__query:
  sql: SELECT content, source FROM knowledge WHERE _id IN ('raw_id_1', 'raw_id_2', 'raw_id_3')
```

### Cross-level semantic search

Find raw material related to a conclusion without following parent_ids:

```
mcp__vxdb__query:
  sql: SELECT content, level, source FROM knowledge
       WHERE NEAR(content, '<conclusion text>', 10)
       ORDER BY _similarity DESC
```

This surfaces related entries across all levels, useful for discovering raw material that wasn't included in the original synthesis path.

## Phase 5: Refresh

When source material changes:

1. **Identify stale raws:** Query by source path to find entries from the changed file.
2. **Delete stale entries:** Remove outdated raw entries with `mcp__vxdb__delete`.
3. **Re-ingest:** Read the updated source, insert new raw chunks.
4. **Re-synthesize upward:** Find summaries whose parent_ids reference deleted raws. Re-run synthesis for those topics.
5. **Re-distill:** If summaries changed, re-evaluate conclusions in the same topic.

```
# Find entries from a changed source
mcp__vxdb__query:
  sql: SELECT _id FROM knowledge WHERE source = 'src/index.ts' AND level = 'raw'

# Find summaries that depend on those entries
mcp__vxdb__query:
  sql: SELECT _id, content, parent_ids FROM knowledge WHERE level = 'summary'
# Then filter in-agent for summaries whose parent_ids overlap with deleted raws
```

## Principles

**Chunk for comprehension, not for size.** A raw chunk should make sense on its own. Too small and you lose context; too large and synthesis becomes copy-paste.

**Summaries are synthesis, not concatenation.** If your summary reads like bullet points glued together, rewrite it as a coherent paragraph that draws connections.

**Conclusions are actionable.** A conclusion should answer "so what?" — what decision does this inform, what should we do differently, what's the key insight.

**Provenance is non-negotiable.** Always populate parent_ids. The ability to trace "why do we believe this conclusion" back to raw sources is the core value of this workflow.

**Refresh bottom-up.** When sources change, start at the raw level and propagate upward. Never edit a conclusion directly without checking whether its supporting evidence still holds.
