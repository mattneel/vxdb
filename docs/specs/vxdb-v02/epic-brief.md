<!-- status: locked -->
<!-- epic-slug: vxdb-v02 -->
# Epic Brief: vxdb v0.2 — DuckDB + Lance Rewrite

## Problem

vxdb v0.1 ships a 244-line custom SQL parser that supports a tiny subset of SQL (AND-only WHERE, no OR, no JOINs, no aggregations, no subqueries). Every new SQL feature requires parser work. Meanwhile, DuckDB's Lance extension provides a real SQL engine over Lance tables with native vector search (`lance_vector_search()`), full-text search (`lance_fts()`), and hybrid search (`lance_hybrid_search()`) as table functions. The custom parser is the wrong abstraction.

## Who's Affected

| Actor | Description |
|-------|-------------|
| **MCP client agent** | Gets real SQL instead of a toy subset — OR, JOINs, aggregations, subqueries all work |
| **Host developer** | Same zero-config setup, but more capable |
| **vxdb maintainer** | Kills the biggest maintenance liability (custom parser), replaces with battle-tested query engine |

## Goals

- Replace custom SQL parser + direct LanceDB Python API with DuckDB + Lance extension
- All reads AND writes through DuckDB SQL (INSERT, UPDATE, DELETE confirmed working on attached Lance tables)
- Keep the existing 7 MCP tools with identical signatures and behavior
- Add 1 new tool: `sql` — raw DuckDB SQL passthrough for power users (JOINs, aggregations, subqueries)
- Keep `query` tool single-table (predictable CRUD contract)
- Keep NEAR() as syntactic sugar — agents write `NEAR(column, 'text', k)`, vxdb rewrites to `lance_vector_search()` with server-side embedding
- Add SEARCH() as syntactic sugar for FTS — `SEARCH(column, 'text', k)` rewrites to `lance_fts()`
- Keep `_similarity` virtual column (mapped from `_distance` via `1/(1+distance)`)
- Keep `text:embed` schema convention and server-side embeddings via fastembed
- Unlock full SQL via the `sql` tool: OR, NOT, JOINs, GROUP BY, HAVING, subqueries, aggregations, DISTINCT, COUNT
- Keep all existing tests passing (adapted to new internals)
- Keep CLI subcommands working

## Non-Goals

- No schema migration tooling (still drop-and-recreate — separate epic)
- No concurrency model changes (DuckDB handles its own locking, still single-server)
- No embedding model changes or hot-swap
- No auth/permissions layer
- Agents never write `lance_vector_search()` or `lance_fts()` directly — NEAR()/SEARCH() only

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Query engine | DuckDB + Lance extension | Real SQL parser, real query planner, battle-tested. Eliminates 244-line custom parser |
| All I/O through DuckDB | INSERT/UPDATE/DELETE via DuckDB SQL on attached Lance tables | Verified: DuckDB Lance extension supports full DML (INSERT, UPDATE, DELETE, MERGE, TRUNCATE). One query engine, one connection, one code path |
| NEAR() handling | Syntactic sugar → rewrite to `lance_vector_search()` | Preserves clean agent API. Agents write NEAR(), vxdb extracts it, embeds text, rewrites FROM clause to table function |
| SEARCH() handling | Syntactic sugar → rewrite to `lance_fts()` | Parallel to NEAR(). SEARCH(column, 'text', k) → lance_fts(). Free capability from the extension |
| `query` tool scope | Single-table only | Predictable CRUD contract. Clear semantics, clear error surface |
| `sql` tool (new) | Raw DuckDB SQL passthrough, no rewriting | Power user escape hatch for JOINs, aggregations, subqueries. Two distinct contracts instead of one muddled one |
| `_similarity` | Computed as `1/(1+_distance)` from `lance_vector_search()` output | Backward compatible with v0.1 |
| Table access | `ATTACH db_dir AS lance_ns (TYPE LANCE)` | DuckDB's native Lance namespace model. Tables at `lance_ns.main.table_name` |
| Vector columns | `FLOAT[]` in DuckDB, hidden `_vec_{col}` convention preserved | Same as v0.1 but stored/queried via DuckDB |
| Schema sidecars | Keep JSON sidecars for `text:embed` tracking | DuckDB doesn't know which columns are "embeddable" — sidecars still needed |
| Dependencies | Add `duckdb` pip package, keep `lancedb` for table lifecycle | Lance extension installed at runtime via DuckDB's `INSTALL lance; LOAD lance;` |

## What Changes

| Module | v0.1 | v0.2 |
|--------|------|------|
| `sql_parser.py` | 244-line custom parser | **Deleted.** Replaced by ~50-line NEAR()/SEARCH() rewriter (`rewriter.py`) |
| `storage.py` | Direct LanceDB Python API (210 lines) | Thin DuckDB connection wrapper. ATTACH, execute SQL, return results |
| `embedder.py` | fastembed wrapper | **Unchanged** |
| `schema.py` | Schema sidecars + Arrow type mapping | Mostly unchanged. Arrow schema building may simplify |
| `tools.py` | Orchestrates parser → embedder → storage | Orchestrates rewriter → embedder → DuckDB |
| `server.py` | FastMCP server + CLI (7 tools) | Adds `sql` tool (8 tools total). Otherwise unchanged |
| `tests/` | 124 tests | Adapted to new internals, same coverage + new `sql` tool tests |

## Success Criteria

- All 7 existing MCP tools work identically from an agent's perspective
- New `sql` tool executes arbitrary DuckDB SQL and returns results
- `NEAR(column, 'text', k)` works as before (with `_similarity` scores)
- `SEARCH(column, 'text', k)` works for full-text search (with `_score`)
- `query` stays single-table; `sql` handles multi-table/analytical queries
- Existing test scenarios pass (adapted assertions where needed)
- CLI subcommands work as before (add `sql` subcommand)
- Cold start latency stays under 500ms

## Kill Criteria / Stop Conditions

- ~~DuckDB Lance extension doesn't support DML~~ **Confirmed: full DML supported (INSERT, UPDATE, DELETE, MERGE, TRUNCATE)**
- Lance extension has blocking bugs or is too immature
- Extension install/load adds >2s to cold start
- NEAR()/SEARCH() rewrite approach can't handle edge cases cleanly
