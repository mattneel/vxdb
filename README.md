# vxdb

A local vector store with a SQL-like interface, exposed over MCP.

Server-side embeddings via [fastembed](https://github.com/qdrant/fastembed), file-based storage via [LanceDB](https://lancedb.github.io/lancedb/), exposed through [FastMCP](https://gofastmcp.com). Zero config for the consuming agent — it just talks to a database that happens to understand similarity.

## Why vxdb

Most agentic memory solutions are either too coupled (framework-specific) or too heavy (external server, cloud dependency, API keys). vxdb is the boring middle ground: an embedded database that lives on disk next to your agent, speaks MCP, and handles embeddings internally.

| | vxdb | sqlite-vec | Chroma | Qdrant | LanceDB direct |
|---|---|---|---|---|---|
| Embedded (no server) | Yes | Yes | No (client-server) | No (client-server) | Yes |
| MCP native | Yes | No | No | No | No |
| Server-side embeddings | Yes | No (BYO vectors) | Yes | No (BYO vectors) | No (BYO vectors) |
| SQL-like query language | Yes | Yes (SQLite ext) | No (Python API) | No (REST/gRPC) | No (Python API) |
| Hybrid search (vector + filter) | Yes | Limited | Yes | Yes | Yes |
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

Seven tools, mirroring SQLite's surface:

```python
create_table("notes", {"title": "string", "content": "text:embed", "category": "string"})

insert("notes", [
    {"title": "ML Paper", "content": "Transformer attention mechanisms in NLP", "category": "ml"},
    {"title": "Recipe", "content": "How to bake chocolate chip cookies", "category": "food"},
])

query("SELECT * FROM notes WHERE category = 'ml' AND NEAR(content, 'deep learning', 5) ORDER BY _similarity DESC LIMIT 10")

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
vxdb --db ./data update notes '{"category": "archive"}' --id some-uuid
vxdb --db ./data delete notes --where "category = 'archive'"
vxdb --db ./data tables
vxdb --db ./data drop-table notes
```

All commands output JSON to stdout. Logs go to stderr.

## Query Dialect

vxdb implements a subset of SQL. This is the complete grammar — anything not listed here is not supported.

### Grammar

```
query     = SELECT columns FROM table [WHERE filters] [ORDER BY ordering] [LIMIT n]
columns   = "*" | col1, col2, ...
filters   = condition [AND condition]*
condition = column op value | NEAR(column, 'text', k)
op        = "=" | "!=" | ">" | "<" | ">=" | "<=" | "IN" | "LIKE"
value     = 'string' | number | true | false | (val, val, ...)  -- for IN
ordering  = column [ASC|DESC] [, column [ASC|DESC]]*
```

### Rules

- **Keywords** are case-insensitive. Column names and table names are case-sensitive.
- **Strings** are single-quoted. Escape quotes by doubling: `'it''s'`.
- **AND** is the only logical combinator. No OR, no NOT, no parenthesized groups.
- **NEAR(column, 'text', k)** is valid only in WHERE. It embeds `text` at query time and returns the `k` nearest rows by cosine similarity.
- **`_similarity`** is a virtual column that exists in results only when NEAR() is present. You can ORDER BY it. Using `_similarity` without NEAR() is a parse error.
- **One NEAR() per query.** Multiple NEAR() clauses are a parse error.
- **NEAR() column must be `text:embed` type.** Using NEAR() on a string/int/etc column is an error.
- **No JOINs, no subqueries, no GROUP BY, no HAVING, no UNION, no aliases.** One table per query.
- **No arithmetic expressions.** `WHERE count > 5` works. `WHERE count + 1 > 5` does not.
- **IN** takes a parenthesized list: `WHERE category IN ('a', 'b', 'c')`.
- **LIKE** uses `%` wildcards: `WHERE title LIKE '%neural%'`.
- **Empty results return `[]`**, not an error. "No matching data" is a valid answer.

### What NEAR() actually does

1. The search text is embedded using the server's model (default: `BAAI/bge-small-en-v1.5`, 384 dimensions).
2. LanceDB performs an approximate nearest-neighbor search on the vector column, returning `k` candidates.
3. Metadata filters (other WHERE clauses) are applied to narrow results.
4. Each result gets a `_similarity` score: `1 / (1 + distance)`, where distance is L2. Higher = more similar.
5. Results are returned as JSON with all requested columns + `_similarity`.

### Schema types

| Type | Description | Arrow mapping |
|------|-------------|---------------|
| `string` | UTF-8 text | utf8 |
| `text` | Alias for string | utf8 |
| `text:embed` | Text + auto-embedded vector | utf8 + vector[dim] |
| `int` | Integer | int64 |
| `float` | Floating point | float64 |
| `bool` | Boolean | bool |

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

# Hybrid search — vector + metadata filter
$ vxdb --db ./demo query \
    "SELECT title FROM docs WHERE source = 'arxiv' AND NEAR(body, 'scaling compute', 5) ORDER BY _similarity DESC" 2>/dev/null
{"rows": [
    {"_id": "...", "title": "Scaling Laws", "_similarity": 0.68},
    {"_id": "...", "title": "Attention Is All You Need", "_similarity": 0.44},
    {"_id": "...", "title": "BERT", "_similarity": 0.41}
], "count": 3}

# Filter-only query (no vector search, no _similarity)
$ vxdb --db ./demo query "SELECT title, year FROM docs WHERE year >= 2019 ORDER BY year DESC" 2>/dev/null
{"rows": [
    {"_id": "...", "title": "Cookie Recipe", "year": 2021},
    {"_id": "...", "title": "Scaling Laws", "year": 2020},
    {"_id": "...", "title": "GPT-2", "year": 2019}
], "count": 3}

# Update — re-embeds automatically when text:embed column changes
$ vxdb --db ./demo update docs '{"source": "classic"}' --where "year < 2019" 2>/dev/null
{"count": 1}

# Persistence — reopen the same --db directory, data is still there
$ vxdb --db ./demo tables 2>/dev/null
{"tables": ["docs"]}
$ vxdb --db ./demo query "SELECT title FROM docs" 2>/dev/null
{"rows": [...all 5 docs...], "count": 5}
```

## Performance

Measured on a single core (x86_64, WSL2), default model (`BAAI/bge-small-en-v1.5`, 384 dims), Python 3.14:

| Operation | Latency |
|-----------|---------|
| Cold start (model load) | ~160ms |
| Embed single text | ~3ms |
| Insert 1 row (with embedding) | ~14ms |
| Insert 100 rows (batch) | ~153ms (~1.5ms/row) |
| Insert 1000 rows (batch) | ~1.5s (~1.5ms/row) |
| NEAR() query (1K rows) | ~14ms |
| Filter-only query (1K rows) | ~4ms |

Insert is dominated by embedding time. Batch inserts amortize well. Query latency is stable up to low thousands of rows. Performance will degrade on larger tables — there's no index tuning in v1 (LanceDB defaults only). For tables under 100K rows, everything should feel instant over MCP.

## Limits and Non-Goals

Be explicit about what this is and isn't.

### What vxdb is

- A **local, single-process, file-based** vector store
- A **convenience layer** that handles embeddings so agents don't have to
- **Good enough** for agent memory, document search, semantic caching up to ~100K rows

### What vxdb is not

- **Not a production database.** No transactions, no WAL, no crash recovery guarantees beyond what LanceDB provides. If the process dies mid-write, data may be partially written.
- **Not concurrent.** One process at a time. No connection pooling, no multi-writer. Running two vxdb instances on the same `--db` directory will corrupt data.
- **Not a full SQL engine.** No JOINs, no subqueries, no OR, no GROUP BY. See [Query Dialect](#query-dialect) for the exact surface.
- **No schema migrations.** Changing a table's schema means drop and recreate. Column types are fixed at creation.
- **No index tuning.** Vector search uses LanceDB's default flat/IVF strategy. No knobs for IVF-PQ, HNSW, or custom index parameters in v1.
- **No embedding model hot-swap.** The model is chosen at server start. Changing models means re-embedding all data (the stored vectors become meaningless with a different model).
- **No auth.** Anyone who can reach the MCP server can read/write everything. Same trust model as SQLite.

### Failure modes

| Scenario | What happens |
|----------|-------------|
| Two processes open same `--db` | Undefined behavior. Likely corruption. Don't do this. |
| Process killed mid-insert | Partial write possible. LanceDB uses append-only storage, so existing data is safe. The incomplete batch is lost. |
| Disk full | LanceDB write fails. Error returned to agent. Existing data intact. |
| Embedding model download fails | Server won't start. fastembed caches models in `~/.cache/fastembed/`. |
| Query references nonexistent column | Parse error returned (if NEAR column) or LanceDB filter error (if WHERE column). |
| Schema sidecar deleted | Table exists in LanceDB but vxdb doesn't know its schema. `list_tables` won't show it. Recreate the sidecar or drop/recreate the table. |

### Embedding re-computation

- **On insert:** all `text:embed` columns are embedded automatically.
- **On update:** if you update a `text:embed` column, its vector is recomputed. If you update a non-embed column, vectors are untouched.
- **On model change:** existing vectors are NOT re-embedded. If you change `--embedding-model`, old vectors and new query vectors will be from different models. Results will be garbage. Drop and reinsert.

### What "SQLite for vectors" means (and doesn't)

The comparison to SQLite is about **deployment model**, not feature parity:
- Embedded, not client-server
- File-based, not cloud
- Zero config, not infrastructure
- Single-user, not multi-tenant

It does **not** mean: ACID transactions, mature query optimizer, decades of battle-testing, or the SQLite test suite. vxdb is a v0.1 with a 244-line SQL parser. Set expectations accordingly.
