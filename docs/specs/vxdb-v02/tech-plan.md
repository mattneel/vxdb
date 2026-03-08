<!-- status: locked -->
# Tech Plan: vxdb v0.2 — DuckDB + Lance Rewrite

## Architecture Overview

v0.1 architecture: `FastMCP → sql_parser → embedder → LanceDB Python API → disk`

v0.2 architecture: `FastMCP → rewriter → embedder → DuckDB (Lance extension) → disk`

The custom SQL parser (244 lines) is deleted. DuckDB becomes the single query engine for all reads and writes. The rewriter is a ~50-line module that extracts `NEAR()` and `SEARCH()` syntactic sugar, embeds text via fastembed, and rewrites to `lance_vector_search()` / `lance_fts()` table functions before handing the SQL to DuckDB.

```
Agent SQL: SELECT * FROM notes WHERE category = 'ml' AND NEAR(content, 'deep learning', 5) ORDER BY _similarity DESC
                                                    ↓
Rewriter extracts NEAR(content, 'deep learning', 5) → embeds text → [0.12, 0.45, ...]
                                                    ↓
DuckDB SQL: SELECT *, 1.0/(1.0+_distance) AS _similarity
            FROM lance_vector_search('lance_ns.main.notes', '_vec_content', [0.12, 0.45, ...]::FLOAT[], k := 5)
            WHERE category = 'ml'
            ORDER BY _similarity DESC
```

LanceDB Python API is retained for table lifecycle (create_table, drop_table) since it owns the on-disk format. All data reads and writes go through DuckDB.

## Design Decisions

| Decision | Choice | Rationale | Trade-off |
|----------|--------|-----------|-----------|
| Query engine | DuckDB + Lance extension | Real SQL engine, eliminates custom parser. Full SQL: OR, JOINs, aggregations, subqueries | Adds ~20MB dependency |
| NEAR() rewrite strategy | Regex extraction → FROM clause rewrite | Simple, no parser needed. NEAR() is always at top-level WHERE, never nested | Can't handle NEAR() inside subqueries (acceptable — single-table `query` tool) |
| SEARCH() rewrite strategy | Same regex pattern as NEAR() → lance_fts() | Parallel implementation, same rewrite mechanics | Same nesting limitation |
| Table writes | DuckDB INSERT/UPDATE/DELETE on attached Lance tables. Try A (all DuckDB), keep LanceDB `tbl.add()` as fallback if vector serialization perf is bad | One code path, one connection. Verified: full DML supported. 384 floats as SQL literals is ugly but probably fine | Fallback to LanceDB Python API for inserts if needed |
| Table lifecycle | LanceDB Python API (create_table, drop_table) | LanceDB owns the on-disk format and schema creation | Two engines for different operations |
| Connection model | Single DuckDB connection, ATTACH on init. INSTALL + LOAD lance on every startup (idempotent) | Simple, mirrors v0.1's single-process model. No "is it installed?" detection logic | First run downloads extension (~one-time cost) |
| `_similarity` computation | Inline SQL: `1.0/(1.0+_distance) AS _similarity` | DuckDB computes it, no post-processing needed | Column rename in SELECT |
| `_score` for FTS | Pass through `_score` from `lance_fts()` directly | No transformation needed — higher = more relevant | Different semantics from `_similarity` (that's fine) |
| `sql` tool | Raw passthrough, no rewriting | Clear contract: "you write DuckDB SQL, we execute it" | Agents must know DuckDB syntax for JOINs/analytics |

## Non-Negotiables / Invariants

- **NEAR() contract**: Agents never see `lance_vector_search()`. NEAR(col, 'text', k) is the only vector search syntax in the `query` tool.
- **SEARCH() contract**: Same — agents never see `lance_fts()`. SEARCH(col, 'text', k) is the FTS syntax.
- **`_similarity` semantics**: Always `1/(1+_distance)`, always present when NEAR() is used, never present otherwise.
- **`_id` semantics**: Auto-generated UUID on insert, always present in results.
- **Hidden vectors**: `_vec_*` columns never appear in query results.
- **Schema sidecars**: Still the source of truth for `text:embed` column tracking.
- **Single-table `query`**: The `query` tool rejects multi-table SQL. Use `sql` tool for that.
- **`sql` tool is raw**: No NEAR()/SEARCH() rewriting in the `sql` tool. Agents use DuckDB-native syntax.

## Data Model

No new data structures. Modified structures:

```
# DELETED: NearClause, FilterClause, OrderClause, QueryAST (from sql_parser.py)

# NEW: RewriteResult (from rewriter.py)
RewriteResult {
    sql: str              # Rewritten SQL ready for DuckDB execution
    has_similarity: bool  # Whether to post-process _distance → _similarity
    has_score: bool       # Whether lance_fts() _score is present
}

# UNCHANGED: TableSchema, ColumnDef (from schema.py)
# UNCHANGED: Embedder (from embedder.py)
```

## Component Architecture

### `rewriter.py` (NEW — replaces `sql_parser.py`)

**Responsibilities**: Extract NEAR()/SEARCH() from agent SQL, embed text, rewrite to DuckDB-native SQL with lance table functions.

**Key design**:
- Regex-based extraction of `NEAR(column, 'text', k)` and `SEARCH(column, 'text', k)` from WHERE clause
- Looks up schema sidecar to find `_vec_{column}` name for NEAR()
- Calls embedder to convert text → vector
- Rewrites `FROM table` → `FROM lance_vector_search(...)` or `FROM lance_fts(...)`
- Moves non-NEAR/SEARCH WHERE conditions to a standard WHERE after the table function
- Adds `1.0/(1.0+_distance) AS _similarity` to SELECT when NEAR() present
- Rewrites `ORDER BY _similarity` → `ORDER BY _distance` (inverted direction) or keeps `_similarity` if using computed column
- If no NEAR()/SEARCH(), passes SQL through with table name rewritten to `lance_ns.main.{table}`

**Validation**:
- NEAR() column must be `text:embed` (checked against schema sidecar)
- One NEAR() per query (regex finds all matches, errors on >1)
- One SEARCH() per query
- Cannot have both NEAR() and SEARCH() (use `lance_hybrid_search()` via `sql` tool for that)

### `storage.py` (REWRITTEN — thin DuckDB wrapper)

**Responsibilities**: DuckDB connection management, Lance extension init, SQL execution, result cleaning.

```python
class Storage:
    def __init__(self, db_dir: str, vector_dim: int):
        self.db_dir = db_dir
        self.vector_dim = vector_dim
        self.conn = duckdb.connect()        # In-memory DuckDB
        self.conn.execute("INSTALL lance")
        self.conn.execute("LOAD lance")
        self.conn.execute(f"ATTACH '{db_dir}' AS lance_ns (TYPE LANCE)")
        self.schemas = load_all_schemas(db_dir)  # Still needed for text:embed tracking
        self.lancedb = lancedb.connect(db_dir)   # For create_table/drop_table

    def create_table(self, schema: TableSchema) -> None:
        # Use LanceDB Python API (owns on-disk format)
        arrow_schema = build_arrow_schema(schema, self.vector_dim)
        self.lancedb.create_table(schema.name, schema=arrow_schema)
        save_schema(self.db_dir, schema)
        self.schemas[schema.name] = schema

    def execute(self, sql: str) -> list[dict]:
        # Execute rewritten SQL via DuckDB, return cleaned results
        result = self.conn.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = []
        for row in result.fetchall():
            d = dict(zip(columns, row))
            # Strip _vec_* columns from results
            d = {k: v for k, v in d.items() if not k.startswith("_vec_")}
            rows.append(d)
        return rows

    def insert(self, table: str, rows: list[dict], embed_fn) -> tuple[int, list[str]]:
        # Generate UUIDs, compute embeddings, INSERT via DuckDB
        ...

    def drop_table(self, name: str) -> None:
        # Use LanceDB Python API + cleanup sidecar
        self.lancedb.drop_table(name)
        delete_schema(self.db_dir, name)
        del self.schemas[name]
```

**Key change**: `search()` and `filter()` methods are replaced by a single `execute()`. The rewriter produces complete DuckDB SQL; storage just runs it.

### `tools.py` (MODIFIED)

**Responsibilities**: Same as v0.1 — orchestration between rewriter, embedder, storage.

**Changes**:
- `query()`: Calls rewriter instead of parser. Rewriter returns ready-to-execute SQL. Calls `storage.execute()`.
- `insert()`: Builds DuckDB INSERT statement with embedded vectors. Calls `storage.execute()`.
- `update()`: Builds DuckDB UPDATE statement. Re-embeds if text:embed column changed.
- `delete()`: Builds DuckDB DELETE statement.
- `sql()` (NEW): Raw passthrough to `storage.execute()`. No rewriting.

### `embedder.py` (UNCHANGED)

### `schema.py` (MINOR CHANGES)

- `build_arrow_schema()` still needed for `create_table` via LanceDB API
- May add DuckDB type mapping if needed for CREATE TABLE via DuckDB (but v0.2 uses LanceDB for creates)

### `server.py` (MINOR ADDITIONS)

- Add `sql` MCP tool definition
- Add `sql` CLI subcommand
- Update INSTRUCTIONS and GUIDE strings for new capabilities (OR, JOINs via `sql` tool, SEARCH())

## File Changes

### New Files
- `vxdb/rewriter.py` — NEAR()/SEARCH() extraction + SQL rewrite (~80 lines)
- `tests/test_rewriter.py` — Rewriter unit tests

### Modified Files
- `vxdb/storage.py` — Rewritten to DuckDB wrapper (~120 lines, down from 210)
- `vxdb/tools.py` — Use rewriter instead of parser, add `sql()` method
- `vxdb/server.py` — Add `sql` tool + CLI subcommand, update docs strings
- `vxdb/schema.py` — Minor: may simplify Arrow schema building
- `pyproject.toml` — Add `duckdb` dependency
- `tests/test_storage.py` — Adapted for DuckDB-backed storage
- `tests/test_tools.py` — Adapted, add `sql` tool tests
- `README.md` — Update architecture, query dialect, add `sql` tool docs

### Deleted Files
- `vxdb/sql_parser.py` — Replaced by rewriter + DuckDB
- `tests/test_sql_parser.py` — Replaced by test_rewriter.py

## Milestone Sequencing

| # | Milestone | Gate |
|---|-----------|------|
| 1 | DuckDB + Lance extension proof-of-concept | ATTACH works, SELECT/INSERT/UPDATE/DELETE on Lance table, lance_vector_search() returns results |
| 2 | Rewriter module | NEAR() extracted, text embedded, SQL rewritten to lance_vector_search(). SEARCH() → lance_fts(). Unit tests pass |
| 3 | Storage rewrite | DuckDB connection wrapper, execute(), create_table/drop_table via LanceDB, insert via DuckDB |
| 4 | Tools integration | query() uses rewriter + storage.execute(). insert/update/delete through DuckDB. All existing tool tests pass |
| 5 | `sql` tool | New tool + CLI subcommand. Raw SQL passthrough working |
| 6 | Docs + cleanup | README, INSTRUCTIONS, GUIDE updated. sql_parser.py deleted. All tests green |

## Testing Strategy

- **Layer 1 — Unit tests**: `test_rewriter.py` — NEAR()/SEARCH() extraction, SQL rewriting edge cases, error handling
- **Layer 2 — Integration tests**: `test_storage.py` — DuckDB connection, ATTACH, CRUD via SQL, lance_vector_search() results
- **Layer 3 — End-to-end**: `test_tools.py` — Full MCP tool surface, same scenarios as v0.1 + new capabilities (OR, COUNT, sql tool)

Existing test scenarios from v0.1 are preserved as regression tests. Parser-specific tests (`test_sql_parser.py`) are deleted since DuckDB handles parsing.

## Performance & Resource Budgets

| Metric | v0.1 | v0.2 Target |
|--------|------|-------------|
| Cold start | ~160ms (model load) | <500ms (model + DuckDB + Lance extension) |
| Query (no vector) | ~4ms | <10ms (DuckDB overhead acceptable) |
| NEAR() query | ~14ms | <20ms |
| Insert 1 row | ~14ms | <20ms |
| Insert 100 rows | ~153ms | <200ms |

DuckDB adds overhead per query (connection, parsing, planning) but removes Python-side sorting and filtering. Net should be comparable or faster for complex queries.

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Lance extension doesn't support INSERT/UPDATE/DELETE | Low (verified in docs) | High | Milestone 1 gates this. Fallback: keep LanceDB Python API for writes |
| Extension install/load slow on first run | Medium | Medium | Cache extension. Document first-run latency |
| NEAR() rewrite misses edge cases | Medium | Medium | Comprehensive test suite for rewriter. Keep rewrite simple (no nested NEAR) |
| DuckDB ↔ LanceDB file format conflict | Low | High | Both use Lance format. DuckDB ATTACHes the same directory LanceDB writes to |
| `_vec_*` columns leak into results | Low | Medium | Strip in storage.execute(). Test explicitly |

## Open Questions

- Does `INSTALL lance` download on first run or is it bundled? If download, need to handle offline scenarios.
- Does DuckDB's Lance ATTACH handle schema changes made by LanceDB Python API (create_table) without re-ATTACH?
- Can we use `CREATE TABLE lance_ns.main.{name} ...` via DuckDB instead of LanceDB API for table creation? Would simplify to one engine for everything. (Nice-to-have, not blocking.)
