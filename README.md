# vxdb

A local vector store with a SQL interface, exposed over MCP.

In-process embeddings via [fastembed](https://github.com/qdrant/fastembed), query engine via [DuckDB](https://duckdb.org/) + [Lance extension](https://lancedb.github.io/lance/integrations/duckdb.html), storage via [LanceDB](https://lancedb.github.io/lancedb/), exposed through [FastMCP](https://gofastmcp.com). Zero config for the client agent — it just talks to a database that happens to understand similarity.

## Why vxdb

Most agentic memory solutions are either too coupled (framework-specific) or too heavy (external server, cloud dependency, API keys). vxdb is the boring middle ground: an embedded database that lives on disk next to your agent, speaks MCP, and handles embeddings internally.

| | vxdb | sqlite-vec | Chroma | Qdrant | LanceDB direct |
|---|---|---|---|---|---|
| Embedded (no server) | Yes | Yes | No (client-server) | No (client-server) | Yes |
| MCP native | Yes | No | No | No | No |
| In-process embeddings | Yes | No (BYO vectors) | Yes | No (BYO vectors) | No (BYO vectors) |
| Single-table semantic SQL | Yes (`query` tool) | No | No | No | No |
| Raw SQL access | Yes (`sql` tool, DuckDB) | Yes (SQLite ext) | No (Python API) | No (REST/gRPC) | No (Python API) |
| Vector + full-text search | Yes | Limited | Yes | Yes | Yes |
| JOINs, aggregations | Yes (via `sql` tool) | Yes | No | No | No |
| Zero config | Yes | Yes | No (needs setup) | No (needs setup) | Yes |
| Install | `uvx` one-liner | pip + compile | pip + server | Docker / pip + server | pip |
| Framework coupling | None (MCP) | SQLite | Langchain-adjacent | Framework-agnostic | Python-only |

**vxdb exists because:** an agent should be able to `create_table`, `insert`, and `query` with semantic search without the developer configuring anything beyond a one-line MCP server entry.

## Install

```
uvx --from git+https://github.com/mattneel/vxdb vxdb --db ./data
```

## MCP Setup

```json
{
  "mcpServers": {
    "vxdb": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/mattneel/vxdb", "vxdb", "--db", "./data"]
    }
  }
}
```

## MCP Tools

Eight tools — seven for CRUD, one for raw SQL:

```python
create_table("notes", {"title": "string", "content": "text:embed", "category": "string"})

insert("notes", [
    {"title": "ML Paper", "content": "Transformer attention mechanisms in NLP", "category": "ml"},
    {"title": "Recipe", "content": "How to bake chocolate chip cookies", "category": "food"},
])

# query — single-table, with NEAR()/SEARCH() sugar
query("SELECT * FROM notes WHERE category = 'ml' AND NEAR(content, 'deep learning', 5) ORDER BY _similarity DESC LIMIT 10")
query("SELECT * FROM notes WHERE SEARCH(content, 'neural networks', 5) ORDER BY _score DESC")
query("SELECT DISTINCT category FROM notes ORDER BY category")
query("SELECT category, COUNT(*) AS cnt FROM notes GROUP BY category")

# sql — raw DuckDB SQL for JOINs, cross-table queries, analytics
sql("SELECT n.title, c.name FROM lance_ns.main.notes n JOIN lance_ns.main.categories c ON n.cat_id = c._id")

update("notes", {"category": "archive"}, id="some-uuid")
delete("notes", where="category = 'archive'")
list_tables()
drop_table("notes")
```

## CLI

Also works as a direct CLI tool for agents without MCP:

```sh
# MCP server (default — no subcommand)
vxdb --db ./data
vxdb --db ./data serve --transport sse

# CRUD
vxdb --db ./data create-table notes '{"title": "string", "content": "text:embed", "category": "string"}'
vxdb --db ./data insert notes '[{"title": "ML Paper", "content": "Transformers for NLP", "category": "ml"}]'
vxdb --db ./data query "SELECT * FROM notes WHERE NEAR(content, 'deep learning', 5) ORDER BY _similarity DESC"
vxdb --db ./data query "SELECT * FROM notes WHERE SEARCH(content, 'neural networks', 5) ORDER BY _score DESC"
vxdb --db ./data sql "SELECT COUNT(*) FROM lance_ns.main.notes"
vxdb --db ./data update notes '{"category": "archive"}' --id some-uuid
vxdb --db ./data delete notes --where "category = 'archive'"
vxdb --db ./data tables
vxdb --db ./data drop-table notes
```

All commands output JSON to stdout. Logs go to stderr.

## Query Tools

vxdb has two query tools. Use `query` for single-table work with semantic search sugar, and `sql` for anything more complex.

### `query` — single-table, with NEAR()/SEARCH()

SQL `SELECT` queries over a single table, with `NEAR()` and `SEARCH()` syntactic sugar.

```sql
-- Metadata queries (full SQL: OR, GROUP BY, DISTINCT, COUNT, subqueries)
SELECT * FROM notes
SELECT title, source FROM notes WHERE priority <= 2
SELECT * FROM notes WHERE source = 'arxiv' OR source = 'blog' ORDER BY score DESC LIMIT 5
SELECT category, COUNT(*) AS cnt FROM notes GROUP BY category
SELECT DISTINCT category FROM notes

-- Vector similarity search
SELECT * FROM notes WHERE NEAR(content, 'transformer architecture', 10)
SELECT title FROM notes WHERE source = 'arxiv' AND NEAR(content, 'language models', 5) ORDER BY _similarity DESC

-- Full-text search
SELECT * FROM notes WHERE SEARCH(content, 'neural network architecture', 5)
SELECT title FROM notes WHERE SEARCH(content, 'attention', 3) ORDER BY _score DESC
```

**NEAR(column, 'text', k)** — semantic similarity search:
- `column` must be a `text:embed` column
- `text` is embedded in-process at query time
- `k` nearest neighbors returned
- `_similarity` virtual column (0-1, higher = more similar)

**SEARCH(column, 'text', k)** — full-text search:
- `column` can be any text column
- Keyword-based search via Lance FTS
- `k` results returned
- `_score` virtual column (higher = more relevant)

Rules:
- One `NEAR()` or one `SEARCH()` per query (not both)
- `_similarity` only exists with `NEAR()`, `_score` only exists with `SEARCH()`
- For hybrid (vector + FTS), use the `sql` tool with `lance_hybrid_search()`

### `sql` — raw DuckDB SQL

For JOINs, cross-table queries, aggregations, and analytics. No `NEAR()`/`SEARCH()` sugar — use DuckDB's native Lance table functions directly.

Tables are referenced as `lance_ns.main.<table_name>`.

```sql
SELECT COUNT(*) FROM lance_ns.main.notes
SELECT n.title, c.name FROM lance_ns.main.notes n JOIN lance_ns.main.categories c ON n.cat_id = c._id
SELECT category, COUNT(*) FROM lance_ns.main.notes GROUP BY category ORDER BY COUNT(*) DESC
```

## Architecture

```
Agent ──MCP──▶ FastMCP ──▶ Tools ──▶ rewriter ──▶ DuckDB (reads)
                                  └──▶ LanceDB Python API (writes)
                                                    │
                                              Lance files on disk
```

**Reads** go through DuckDB with the Lance extension. DuckDB provides the SQL parser, query planner, and execution engine. The rewriter transforms `NEAR()`/`SEARCH()` sugar into DuckDB-native `lance_vector_search()`/`lance_fts()` table functions.

**Writes** (insert, update, delete, table lifecycle) go through the LanceDB Python API for reliability and performance.

**Schema sidecars**: Each table's schema (column names, types, which columns are `text:embed`) is stored as a JSON file alongside the Lance data in the `--db` directory. These sidecars are the source of truth for vxdb's type system — if a sidecar is deleted, vxdb loses track of the table even though the Lance data still exists on disk.

### Schema types

| Type | Description |
|------|-------------|
| `string` | UTF-8 text |
| `text` | Alias for string |
| `text:embed` | Text + auto-embedded vector for NEAR() |
| `int` | 64-bit integer |
| `float` | 64-bit float |
| `bool` | Boolean |

### What NEAR() actually does

1. The search text is embedded using the server's model (default: `BAAI/bge-small-en-v1.5`, 384 dimensions).
2. The rewriter transforms `NEAR(col, 'text', k)` into a `lance_vector_search()` call with the embedded vector.
3. DuckDB executes the query, including any additional WHERE filters, ORDER BY, and LIMIT.
4. Each result gets a `_similarity` score: `1 / (1 + distance)`, where distance is L2. Higher = more similar.

## Demo

End-to-end: create, insert, query, update, persist, reopen.

```sh
$ vxdb --db ./demo create-table docs \
    '{"title": "string", "body": "text:embed", "source": "string", "year": "int"}' 2>/dev/null
{"table": "docs", "columns": {"title": "string", "body": "text:embed", "source": "string", "year": "int"}, "embed_columns": ["body"]}

$ vxdb --db ./demo insert docs '[
    {"title": "Attention Is All You Need", "body": "We propose a new simple network architecture, the Transformer, based solely on attention mechanisms", "source": "arxiv", "year": 2017},
    {"title": "BERT", "body": "We introduce a new language representation model called BERT, designed to pre-train deep bidirectional representations", "source": "arxiv", "year": 2018},
    {"title": "GPT-2", "body": "Language models are unsupervised multitask learners that can generate coherent paragraphs of text", "source": "openai", "year": 2019},
    {"title": "Scaling Laws", "body": "Performance of neural language models depends strongly on scale: model size, dataset size, and compute", "source": "arxiv", "year": 2020},
    {"title": "Cookie Recipe", "body": "Preheat oven to 350F, mix flour sugar and butter, add chocolate chips, bake for 12 minutes", "source": "blog", "year": 2021}
]' 2>/dev/null
{"count": 5, "ids": ["...", "...", "...", "...", "..."]}

# Semantic search — NEAR finds papers about transformers, not cookies
$ vxdb --db ./demo query \
    "SELECT title, year FROM docs WHERE NEAR(body, 'transformer architecture attention', 3) ORDER BY _similarity DESC" 2>/dev/null
{"rows": [
    {"_id": "...", "title": "Attention Is All You Need", "year": 2017, "_similarity": 0.72},
    {"_id": "...", "title": "BERT", "year": 2018, "_similarity": 0.61},
    {"_id": "...", "title": "GPT-2", "year": 2019, "_similarity": 0.57}
], "count": 3}

# Full-text search — keyword matching
$ vxdb --db ./demo query \
    "SELECT title FROM docs WHERE SEARCH(body, 'neural language model', 3) ORDER BY _score DESC" 2>/dev/null
{"rows": [
    {"_id": "...", "title": "Scaling Laws", "_score": 4.2},
    {"_id": "...", "title": "BERT", "_score": 3.1},
    {"_id": "...", "title": "GPT-2", "_score": 2.8}
], "count": 3}

# Hybrid search — vector + metadata filter
$ vxdb --db ./demo query \
    "SELECT title FROM docs WHERE source = 'arxiv' AND NEAR(body, 'scaling compute', 5) ORDER BY _similarity DESC" 2>/dev/null
{"rows": [
    {"_id": "...", "title": "Scaling Laws", "_similarity": 0.68},
    {"_id": "...", "title": "Attention Is All You Need", "_similarity": 0.44},
    {"_id": "...", "title": "BERT", "_similarity": 0.41}
], "count": 3}

# Full SQL — aggregations, OR, DISTINCT
$ vxdb --db ./demo query "SELECT source, COUNT(*) AS cnt FROM docs GROUP BY source ORDER BY cnt DESC" 2>/dev/null
{"rows": [
    {"source": "arxiv", "cnt": 3},
    {"source": "blog", "cnt": 1},
    {"source": "openai", "cnt": 1}
], "count": 3}

# Raw DuckDB SQL — cross-table JOINs via sql tool
$ vxdb --db ./demo sql "SELECT COUNT(*) AS total FROM lance_ns.main.docs" 2>/dev/null
{"rows": [{"total": 5}], "count": 1}

# Filter-only query (no vector search, no _similarity)
$ vxdb --db ./demo query "SELECT title, year FROM docs WHERE year >= 2019 ORDER BY year DESC" 2>/dev/null
{"rows": [
    {"_id": "...", "title": "Cookie Recipe", "year": 2021},
    {"_id": "...", "title": "Scaling Laws", "year": 2020},
    {"_id": "...", "title": "GPT-2", "year": 2019}
], "count": 3}

# Update — re-embeds automatically when text:embed column changes
$ vxdb --db ./demo update docs '{"source": "classic"}' --where "year < 2019" 2>/dev/null
{"count": 2}

# Persistence — reopen the same --db directory, data is still there
$ vxdb --db ./demo tables 2>/dev/null
{"tables": ["docs"]}
$ vxdb --db ./demo query "SELECT title FROM docs" 2>/dev/null
{"rows": [...all 5 docs...], "count": 5}
```

## Performance

Measured on a single core (x86_64, WSL2), default model (`BAAI/bge-small-en-v1.5`, 384 dims), Python 3.14. All query numbers are warm-cache averages over 10 runs. LanceDB defaults (no custom ANN index config).

| Operation | Latency |
|-----------|---------|
| Cold start (model load) | ~200ms |
| Embed single text | ~2ms |
| Insert 1 row (with embedding) | ~7ms |
| Insert 100 rows (batch) | ~98ms (~1ms/row) |
| Insert 1000 rows (batch) | ~1.1s (~1.1ms/row) |
| NEAR() query (1K rows) | ~16ms |
| SEARCH() query (1K rows) | ~10ms |
| Filter-only query (1K rows) | ~3ms |
| Aggregation query (1K rows) | ~8ms |
| Raw `sql` COUNT (1K rows) | ~1ms |

Insert is dominated by embedding time. Batch inserts amortize well. Query latency is stable up to low thousands of rows with default LanceDB indexing. Not benchmarked beyond 10K rows — expect degradation at scale without index tuning (which vxdb doesn't expose).

## Limits and Non-Goals

### What vxdb is

- A **local, single-process, file-based** vector store
- A **convenience layer** that handles embeddings so agents don't have to
- **Good enough** for agent memory, document search, semantic caching at small-to-medium local scale (low tens of thousands of rows)

### What vxdb is not

- **Not a production database.** No transactions, no WAL, no crash recovery guarantees beyond what LanceDB provides. If the process dies mid-write, data may be partially written.
- **Not concurrent.** One process at a time. No connection pooling, no multi-writer. Running two vxdb instances on the same `--db` directory is unsupported and may corrupt data.
- **No schema migrations.** Changing a table's schema means drop and recreate. Column types are fixed at creation.
- **No index tuning.** Vector search uses LanceDB's default flat/IVF strategy. No knobs for IVF-PQ, HNSW, or custom index parameters.
- **No embedding model hot-swap.** The model is chosen at server start. Changing models means re-embedding all data (the stored vectors become meaningless with a different model).
- **No auth.** Anyone who can reach the MCP server can read/write everything. Same trust model as SQLite.

### Failure modes

| Scenario | What happens |
|----------|-------------|
| Two processes open same `--db` | Unsupported. May corrupt data. Don't do this. |
| Process killed mid-insert | Partial write possible. LanceDB uses append-only storage, so existing committed data should remain intact. The incomplete batch is lost. |
| Disk full | LanceDB write fails. Error returned to agent. Existing data intact. |
| Embedding model download fails | Server won't start. fastembed caches models in `~/.cache/fastembed/`. |
| Schema sidecar deleted | Table exists in LanceDB but vxdb doesn't know its schema. `list_tables` won't show it. Recreate the sidecar or drop/recreate the table. |

### Embedding re-computation

- **On insert:** all `text:embed` columns are embedded automatically.
- **On update:** if you update a `text:embed` column, its vector is recomputed. If you update a non-embed column, vectors are untouched.
- **On model change:** existing vectors are NOT re-embedded. If you change `--embedding-model`, old vectors and new query vectors will be from different models. Results will be semantically meaningless. Drop and reinsert.

### What "SQLite for vectors" means (and doesn't)

The comparison to SQLite is about **deployment model**, not feature parity:
- Embedded, not client-server
- File-based, not cloud
- Zero config, not infrastructure
- Single-user, not multi-tenant

It does **not** mean: ACID transactions, mature query optimizer, decades of battle-testing, or the SQLite test suite. Set expectations accordingly.
