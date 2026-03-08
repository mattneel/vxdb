<!-- status: locked -->
# Tickets: vxdb v0.2 — DuckDB + Lance Rewrite

## Execution Order

```
T1: DuckDB + Lance PoC (no deps)
T2: Rewriter module (no deps — pure string transform + embed, no storage needed)
    ↕ parallel
T3: Storage rewrite (depends: T1 confirmed working)
T4: Tools integration (depends: T2, T3)
T5: `sql` tool (depends: T3)
T6: Server + CLI + docs (depends: T4, T5)
```

Parallel group A: T2 + T3 (independent — rewriter is pure logic, storage is I/O layer)

---

### T1: DuckDB + Lance extension proof-of-concept
**Specs**: tech-plan.md §Architecture Overview, §Risks
**Files**: (throwaway script, not committed — or `tests/test_duckdb_lance.py`)
**Touch list**: none (PoC only)
**Dependencies**: None
**Effort**: Small

**Description**:
Validate that DuckDB + Lance extension works for the vxdb use case. Write a standalone test/script that:
1. `INSTALL lance; LOAD lance;`
2. `ATTACH` a temp directory as a Lance namespace
3. `CREATE TABLE` with string + FLOAT[] columns via DuckDB
4. `INSERT INTO` rows including vector arrays serialized as `[0.1, 0.2, ...]::FLOAT[]`
5. `SELECT` rows back
6. `UPDATE` a row
7. `DELETE` a row
8. `lance_vector_search()` with a query vector — verify `_distance` is returned
9. `lance_fts()` with a text query — verify `_score` is returned
10. Test insert perf: 100 rows with 384-dim vectors as SQL literals. If >2x slower than LanceDB `tbl.add()`, flag for fallback to LanceDB Python API for inserts.

**Acceptance Criteria**:
- [ ] All 9 operations succeed without error
- [ ] `lance_vector_search()` returns rows with `_distance` column
- [ ] `lance_fts()` returns rows with `_score` column
- [ ] Insert perf is acceptable (or fallback path identified)
- [ ] DuckDB ATTACH sees tables created by LanceDB Python API (and vice versa)

**Verification**:
- `pytest tests/test_duckdb_lance.py -v`

**Rollback**: Delete test file. No production code touched.

---

### T2: Rewriter module
**Specs**: tech-plan.md §Component Architecture → rewriter.py
**Files**: `vxdb/rewriter.py`, `tests/test_rewriter.py`
**Touch list**: rewriter (new)
**Dependencies**: None (pure string transformation + embedder call)
**Effort**: Medium
**Parallel group**: A (with T3)

**Description**:
Create the NEAR()/SEARCH() rewriter that replaces the custom SQL parser. This module:
1. Extracts `NEAR(column, 'text', k)` from WHERE clause via regex
2. Extracts `SEARCH(column, 'text', k)` from WHERE clause via regex
3. Validates: column is `text:embed` (checked against schema dict), max one NEAR(), max one SEARCH(), no NEAR+SEARCH combo
4. Embeds text via embedder
5. Rewrites FROM clause to `lance_vector_search()` or `lance_fts()` table function
6. Moves remaining WHERE conditions after the table function
7. Handles `_similarity` → adds `1.0/(1.0+_distance) AS _similarity` to SELECT, handles ORDER BY _similarity
8. Handles `_score` passthrough from lance_fts()
9. For non-NEAR/non-SEARCH queries, rewrites table name to `lance_ns.main.{table}`
10. Strips `_vec_*` columns from `SELECT *` by expanding to explicit column list (using schema)

**Acceptance Criteria**:
- [ ] NEAR() extracted and rewritten to lance_vector_search() with embedded vector
- [ ] SEARCH() extracted and rewritten to lance_fts()
- [ ] Remaining WHERE conditions preserved
- [ ] `_similarity` computed inline, ORDER BY works
- [ ] Non-vector queries rewrite table name only
- [ ] Error on multiple NEAR(), multiple SEARCH(), or NEAR+SEARCH combo
- [ ] Error on NEAR()/SEARCH() column that isn't text:embed
- [ ] Error on _similarity without NEAR()
- [ ] SELECT * expands to explicit columns (hiding _vec_* columns)
- [ ] Unit tests cover: simple NEAR, NEAR+WHERE, SEARCH, no-vector, edge cases

**Verification**:
- `pytest tests/test_rewriter.py -v`

**Rollback**: Delete new files. No existing code modified.

---

### T3: Storage rewrite
**Specs**: tech-plan.md §Component Architecture → storage.py
**Files**: `vxdb/storage.py`, `tests/test_storage.py`, `pyproject.toml`
**Touch list**: storage, deps
**Dependencies**: T1 (confirms DuckDB+Lance works)
**Effort**: Medium
**Parallel group**: A (with T2)

**Description**:
Rewrite storage.py from LanceDB Python API to DuckDB connection wrapper:
1. Init: `duckdb.connect()`, `INSTALL lance`, `LOAD lance`, `ATTACH db_dir AS lance_ns (TYPE LANCE)`
2. Keep LanceDB connection for `create_table` and `drop_table` (owns on-disk format)
3. `execute(sql)` method: runs SQL via DuckDB, returns list of dicts with `_vec_*` columns stripped
4. `insert()`: generates UUIDs, computes embeddings via embed_fn, builds INSERT SQL with vector literals. If T1 flagged perf issues, use LanceDB `tbl.add()` instead.
5. `update()`: builds UPDATE SQL. Re-embeds text:embed columns if changed.
6. `delete()`: builds DELETE SQL.
7. Remove old methods: `search()`, `filter()` — replaced by `execute()`.
8. Add `duckdb` to pyproject.toml dependencies.

**Acceptance Criteria**:
- [ ] Storage inits with DuckDB + Lance extension + ATTACH
- [ ] create_table creates Lance table via LanceDB API (DuckDB can see it)
- [ ] execute() runs arbitrary SQL and returns clean dicts
- [ ] insert() generates UUIDs, embeds, inserts via DuckDB (or LanceDB fallback)
- [ ] update() works via DuckDB, re-embeds when needed
- [ ] delete() works via DuckDB
- [ ] drop_table() uses LanceDB API + cleans sidecar
- [ ] `_vec_*` columns never appear in execute() results
- [ ] Integration tests pass

**Verification**:
- `pytest tests/test_storage.py -v`

**Rollback**: `git checkout -- vxdb/storage.py tests/test_storage.py pyproject.toml`

---

### T4: Tools integration
**Specs**: tech-plan.md §Component Architecture → tools.py
**Files**: `vxdb/tools.py`, `tests/test_tools.py`
**Touch list**: tools, tests
**Dependencies**: T2 (rewriter), T3 (storage)
**Effort**: Medium

**Description**:
Wire the rewriter and new storage into tools.py:
1. `query()`: call rewriter to transform agent SQL → DuckDB SQL, call `storage.execute()`, return `{rows, count}`
2. `insert()`: call `storage.insert()` (which now goes through DuckDB)
3. `update()`: build filter string, call `storage.update()`
4. `delete()`: build filter string, call `storage.delete()`
5. Remove: `_build_where()` helper, `parse_query` import
6. Adapt all existing test_tools.py tests to work with new internals (same assertions, different setup)
7. Add new test cases: OR in WHERE, COUNT(*), DISTINCT (things that now work for free)

**Acceptance Criteria**:
- [ ] All existing tool test scenarios pass (adapted)
- [ ] query() with NEAR() returns results with _similarity
- [ ] query() with SEARCH() returns results with _score
- [ ] query() with OR in WHERE works
- [ ] query() with COUNT(*) works
- [ ] insert/update/delete work through DuckDB
- [ ] Error cases preserved: nonexistent table, invalid NEAR column, etc.

**Verification**:
- `pytest tests/test_tools.py -v`

**Rollback**: `git checkout -- vxdb/tools.py tests/test_tools.py`

---

### T5: `sql` tool
**Specs**: epic-brief.md §Goals, tech-plan.md §Design Decisions
**Files**: `vxdb/tools.py`, `vxdb/server.py`, `tests/test_tools.py`
**Touch list**: tools, server
**Dependencies**: T3 (storage — needs execute())
**Effort**: Small

**Description**:
Add the raw SQL passthrough tool:
1. `Tools.sql(sql)`: calls `storage.execute(sql)` directly, no rewriting. Returns `{rows, count}`.
2. Add `sql` MCP tool in server.py with docstring explaining it's raw DuckDB SQL
3. Add `sql` CLI subcommand
4. Tests: JOIN across two tables, aggregation (COUNT, GROUP BY), subquery

**Acceptance Criteria**:
- [ ] `sql` tool executes arbitrary DuckDB SQL
- [ ] JOIN across two vxdb tables works
- [ ] Aggregations (COUNT, SUM, GROUP BY) work
- [ ] `lance_vector_search()` works directly in sql tool
- [ ] `lance_fts()` works directly in sql tool
- [ ] Errors returned cleanly for invalid SQL
- [ ] CLI `vxdb --db ./data sql "SELECT ..."` works

**Verification**:
- `pytest tests/test_tools.py -v -k sql`

**Rollback**: Revert additions to tools.py, server.py.

---

### T6: Server, CLI, docs, cleanup
**Specs**: All specs
**Files**: `vxdb/server.py`, `vxdb/sql_parser.py` (delete), `tests/test_sql_parser.py` (delete), `README.md`
**Touch list**: server, docs, cleanup
**Dependencies**: T4, T5
**Effort**: Medium

**Description**:
1. Update INSTRUCTIONS string in server.py: add SEARCH() syntax, mention `sql` tool, note that full SQL (OR, JOINs, etc.) is now supported via `sql` tool
2. Update GUIDE resource: add SEARCH() examples, `sql` tool usage, new capabilities
3. Delete `vxdb/sql_parser.py`
4. Delete `tests/test_sql_parser.py`
5. Update README.md:
   - Architecture section: FastMCP → DuckDB + Lance
   - Query dialect: note full SQL support via `sql` tool, NEAR() and SEARCH() sugar in `query` tool
   - Add `sql` to MCP Tools and CLI sections
   - Update comparison table if relevant
   - Update performance numbers after benchmarking
6. Bump version to 0.2.0 in pyproject.toml
7. Run full test suite, verify clean

**Acceptance Criteria**:
- [ ] sql_parser.py deleted
- [ ] test_sql_parser.py deleted
- [ ] INSTRUCTIONS updated with SEARCH(), sql tool
- [ ] GUIDE resource updated
- [ ] README updated
- [ ] Version bumped to 0.2.0
- [ ] `pytest` — all tests green
- [ ] No references to sql_parser remain in codebase

**Verification**:
- `pytest -v`
- `grep -r "sql_parser" vxdb/ tests/` returns nothing

**Rollback**: `git revert`
