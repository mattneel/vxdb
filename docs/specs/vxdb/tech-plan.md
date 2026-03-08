<!-- status: locked -->
<!-- epic-slug: vxdb -->
# Tech Plan: vxdb

## Architecture Overview

```
MCP Client (any agent)
    │
    │ MCP protocol (stdio or SSE)
    │
    ▼
┌─────────────────────────────────┐
│  vxdb (FastMCP server)          │
│                                 │
│  ┌───────────┐  ┌────────────┐  │
│  │ SQL Parser │  │ fastembed  │  │
│  │ (custom   │  │ (ONNX      │  │
│  │  mini)    │  │  embedder) │  │
│  └─────┬─────┘  └─────┬──────┘  │
│        │              │          │
│        ▼              ▼          │
│  ┌──────────────────────────┐   │
│  │  LanceDB (embedded,     │   │
│  │  file-based storage)    │   │
│  └──────────────────────────┘   │
└─────────────────────────────────┘
         │
         ▼
    ./data/ (on-disk Lance files)
```

Three internal components: SQL parser, embedding engine, storage engine. The MCP tool handlers orchestrate between them.

## Design Decisions

| Decision | Choice | Rationale | Trade-off |
|----------|--------|-----------|-----------|
| SQL parser | Custom mini-parser (regex + state machine) | Grammar is tiny (SELECT/FROM/WHERE/ORDER BY/LIMIT + NEAR). sqlglot is 10MB+ solving a problem we don't have. If parser gets painful later, sqlglot is always there. | Must maintain parser; can't handle arbitrary SQL |
| Schema storage | Per-table JSON sidecar in `{db_dir}/.vxdb/` | Co-located with data — `cp -r` the db dir and everything comes with it. Inspectable, debuggable (`cat` it). Drop_table cleanup is trivial. | Extra file per table to manage |
| _id generation | UUID4 via `uuid` stdlib | Universally unique, no coordination needed, stdlib | Slightly larger than auto-increment but simpler |
| Vector column naming | `_vec_{column_name}` hidden column | Agents never see vectors; naming convention avoids collision | Convention-based, not enforced by LanceDB |
| Update/delete API | Both `where` and `id` params (mutually exclusive) | `where` is the general case, keeps language consistent with query. `id` is the 80% case (insert, get _id, later update). `id` desugars to `WHERE _id = '{id}'` internally. Error if neither provided. | Two code paths, but `id` is trivial sugar |
| Entry point | Both `[project.scripts]` and `__main__.py` | Scripts for `uvx vxdb` (public interface). `__main__.py` for `python -m vxdb` (free, 2-line file, helps during dev). | None — trivial to maintain |
| Result format | List of dicts with `_similarity` when NEAR() used | JSON-native, agents parse trivially | No streaming for large result sets in v1 |

## Non-Negotiables / Invariants

- Every `text:embed` column always has a corresponding up-to-date vector. No stale embeddings.
- `_similarity` column only exists in results when NEAR() is in the query. Parse error otherwise.
- One NEAR() per query. Clear error on multiple.
- One embedding model per server instance. Set at startup, immutable.
- Empty result set is `[]`, never an error.
- `_id` is auto-generated, always present, never user-writable on insert.

## Data Model

```
TableSchema {
    name:           str                  # table name
    columns:        dict[str, ColumnDef] # user-defined columns
    embed_columns:  list[str]            # columns with type "text:embed"
    created_at:     str                  # ISO timestamp
}

ColumnDef {
    name:       str         # column name
    type:       str         # "string" | "int" | "float" | "bool" | "text" | "text:embed"
    arrow_type: ArrowType   # mapped internally: string/text→utf8, int→int64, float→float64, bool→bool
}

# On-disk LanceDB table has:
#   _id:                  string (UUID4)
#   {user_columns}:       mapped Arrow types
#   _vec_{embed_col}:     vector[{dim}] (one per text:embed column, hidden from agent)

# Schema sidecar: {db_dir}/.vxdb/{table_name}.schema.json
```

### Type Mapping

| vxdb type | Arrow type | Notes |
|-----------|-----------|-------|
| `string` | `utf8` | General text, not embedded |
| `text` | `utf8` | Alias for string |
| `text:embed` | `utf8` + `vector[dim]` | Text column + auto-generated embedding vector |
| `int` | `int64` | |
| `float` | `float64` | |
| `bool` | `bool` | |

## Component Architecture

### 1. Entry Points (`main.py` + `vxdb/__main__.py`)
CLI argument parsing (`--db`, `--embedding-model`, `--transport`). Both call `vxdb.server:main`.

### 2. Server (`vxdb/server.py`)
FastMCP app definition, tool registration, server startup. Initializes embedder and storage, wires them into tool handlers.

### 3. Tools (`vxdb/tools.py`)
MCP tool handlers — one function per tool. Thin orchestration layer: validate input, call engine, format response. Tools:
- `create_table(name, schema)` → creates table
- `insert(table, rows)` → inserts rows with auto-embedding + _id
- `query(sql)` → parses SQL, executes against LanceDB
- `update(table, set, where=None, id=None)` → updates matching rows, recomputes embeddings if needed
- `delete(table, where=None, id=None)` → deletes matching rows
- `list_tables()` → returns table names
- `drop_table(name)` → drops table

### 4. SQL Parser (`vxdb/sql_parser.py`)
Custom mini-parser. Parses the SQL-ish query language into a structured AST:

```
QueryAST {
    select:     list[str] | "*"
    table:      str
    where:      list[FilterClause]     # metadata filters
    near:       NearClause | None      # vector search
    order_by:   list[OrderClause]
    limit:      int | None
}

NearClause {
    column:     str         # must be a text:embed column
    text:       str         # search text (will be embedded)
    k:          int         # number of nearest neighbors
}

FilterClause {
    column:     str
    op:         str         # =, !=, >, <, >=, <=, IN, LIKE
    value:      any
}

OrderClause {
    column:     str         # can be "_similarity" (only when NEAR present)
    direction:  str         # ASC | DESC
}
```

Validation rules:
- `_similarity` in ORDER BY requires NEAR() in WHERE → parse error otherwise
- Multiple NEAR() → parse error
- NEAR() column must be `text:embed` type → error otherwise
- Referenced columns must exist in table schema

### 5. Embedding Engine (`vxdb/embedder.py`)
Wraps fastembed. Initialized once at startup with the chosen model.

```
Embedder {
    model:      TextEmbedding          # fastembed model instance
    dimension:  int                    # vector dimension (e.g. 384 for bge-small-en-v1.5)

    embed(text: str) -> list[float]
    embed_batch(texts: list[str]) -> list[list[float]]
}
```

### 6. Storage Engine (`vxdb/storage.py`)
Wraps LanceDB. Manages connection, table operations, schema persistence.

```
Storage {
    db:         lancedb.DBConnection   # LanceDB connection
    db_dir:     str                    # path to database directory
    schemas:    dict[str, TableSchema] # loaded from sidecar files

    create_table(schema: TableSchema, dim: int) -> None
    insert(table: str, rows: list[dict]) -> list[str]  # returns _ids
    search(table: str, vector: list[float], where: str, columns: list[str], k: int, limit: int) -> list[dict]
    filter(table: str, where: str, columns: list[str], limit: int, order_by: list) -> list[dict]
    update(table: str, where: str, values: dict) -> int
    delete(table: str, where: str) -> int
    list_tables() -> list[str]
    drop_table(name: str) -> None
}
```

### 7. Schema Manager (`vxdb/schema.py`)
Handles type mapping, schema validation, sidecar file I/O.

## File Layout

```
vxdb/
├── pyproject.toml          # deps: fastmcp, lancedb, fastembed
├── main.py                 # CLI entry: parse args, start server
├── vxdb/
│   ├── __init__.py
│   ├── __main__.py         # python -m vxdb support (2 lines)
│   ├── server.py           # FastMCP app, tool registration
│   ├── tools.py            # MCP tool handler functions
│   ├── sql_parser.py       # SQL-ish query parser
│   ├── embedder.py         # fastembed wrapper
│   ├── storage.py          # LanceDB wrapper
│   └── schema.py           # Type mapping, schema sidecar I/O
├── tests/
│   ├── test_sql_parser.py  # Parser unit tests
│   ├── test_embedder.py    # Embedding tests
│   ├── test_storage.py     # Storage integration tests
│   └── test_tools.py       # End-to-end tool tests
└── docs/
    └── specs/              # (planning artifacts)
```

## Milestone Sequencing

| # | Milestone | Gate |
|---|-----------|------|
| 1 | Project scaffold + deps | `uv run python -c "import fastmcp, lancedb, fastembed"` succeeds |
| 2 | Schema + type mapping | Unit tests pass for schema parsing, type mapping, sidecar I/O |
| 3 | Embedding engine | `embedder.embed("hello")` returns correct-dimension vector |
| 4 | Storage engine | Create table, insert, search, filter, update, delete all work against LanceDB |
| 5 | SQL parser | Parses SELECT/WHERE/NEAR/ORDER BY/LIMIT, rejects invalid queries |
| 6 | MCP tools + server | All 7 tools work end-to-end via FastMCP, stdio transport |
| 7 | SSE transport + CLI polish | `--transport sse`, `--embedding-model`, `--db` flags work |

## Performance & Resource Budgets

- **Cold start** (model loading): < 5s for bge-small-en-v1.5
- **Embed latency**: < 50ms per query embedding (single text)
- **Query latency**: < 200ms for NEAR() + filter on tables < 100K rows
- **Insert latency**: < 100ms per row (dominated by embedding), batch amortizes
- **Installed size**: Target < 500MB total (fastembed ~100MB + model ~50MB + lancedb ~50MB + fastmcp ~5MB)

## Testing Strategy

- **Unit tests**: SQL parser (most critical — lots of edge cases), schema type mapping, embedder initialization
- **Integration tests**: Storage engine against real LanceDB (temp directories), end-to-end tool calls via FastMCP test client
- **Smoke test**: Start server, create table, insert, query with NEAR(), verify results

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| SQL parser doesn't handle agent-generated SQL edge cases | High | Medium | Start minimal, add cases as real agents hit them |
| fastembed model download on first run surprises users | Medium | Low | Document it. Consider pre-download hook. |
| LanceDB Python API changes between versions | Low | Medium | Pin version in pyproject.toml |
| Combined dep size exceeds 500MB target | Medium | Medium | Measure early (Milestone 1). CPU ONNX only. |
| LanceDB update/delete API limitations | Medium | Medium | Validate in Milestone 4 before building tools layer |

## Open Questions

- Exact fastembed default model dimensions (expected 384 for bge-small-en-v1.5) — validate in Milestone 3
- LanceDB's Python delete API — confirm it supports WHERE-style filtering
- FastMCP structured error responses — how to return typed errors vs string messages
