<!-- status: locked -->
<!-- epic-slug: vxdb -->
# Tickets: vxdb

## Execution Order

```
T1: Project scaffold + dependencies          (no deps)
T2: Schema module + type mapping             (depends: T1)
T3: Embedding engine                         (depends: T1)
    ── T2 and T3 can run in parallel ──
T4: Storage engine                           (depends: T2, T3)
T5: SQL parser                               (depends: T2)
    ── T4 and T5 can run in parallel ──
T6: MCP tools + server                       (depends: T4, T5)
T7: CLI polish + SSE transport               (depends: T6)
```

---

### T1: Project scaffold + dependencies
**Specs**: tech-plan.md §File Layout, §Milestone 1
**Files**: `pyproject.toml`, `main.py`, `vxdb/__init__.py`, `vxdb/__main__.py`, `vxdb/server.py` (stub)
**Touch list**: project config, package structure, entry points
**Dependencies**: None
**Effort**: Small
**Parallel group**: —

**Description**:
Set up the Python package structure, add dependencies (fastmcp, lancedb, fastembed), create entry points (`[project.scripts]` + `__main__.py`), and verify imports work.

**Acceptance Criteria**:
- [ ] `uv run python -c "import fastmcp, lancedb, fastembed"` succeeds
- [ ] `uv run vxdb --help` runs without error (can be a stub that prints usage)
- [ ] `uv run python -m vxdb` runs the same entry point
- [ ] Package structure matches tech plan file layout (vxdb/ with __init__.py)

**Verification**:
- `uv run python -c "import fastmcp, lancedb, fastembed"`
- `uv run vxdb --help`

**Rollback**: Revert commit.

---

### T2: Schema module + type mapping
**Specs**: tech-plan.md §Data Model, §Type Mapping, §Component 7
**Files**: `vxdb/schema.py`, `tests/test_schema.py`
**Touch list**: schema module, tests
**Dependencies**: T1
**Effort**: Small
**Parallel group**: A (with T3)

**Description**:
Implement TableSchema/ColumnDef dataclasses, type mapping (vxdb types → Arrow types), schema validation, and JSON sidecar I/O (save/load to `{db_dir}/.vxdb/{table_name}.schema.json`).

**Acceptance Criteria**:
- [ ] `TableSchema` and `ColumnDef` dataclasses defined
- [ ] Type mapping: string→utf8, text→utf8, text:embed→utf8+vector, int→int64, float→float64, bool→bool
- [ ] Schema validation: rejects unknown types, requires at least one column
- [ ] Sidecar I/O: save schema to JSON, load it back, round-trips cleanly
- [ ] `text:embed` columns correctly identified in `embed_columns` list
- [ ] Unit tests pass for all of the above

**Verification**:
- `uv run pytest tests/test_schema.py -v`

**Rollback**: Revert commit.

---

### T3: Embedding engine
**Specs**: tech-plan.md §Component 5, §Milestone 3
**Files**: `vxdb/embedder.py`, `tests/test_embedder.py`
**Touch list**: embedder module, tests
**Dependencies**: T1
**Effort**: Small
**Parallel group**: A (with T2)

**Description**:
Wrap fastembed's TextEmbedding. Initialize with model name (default `BAAI/bge-small-en-v1.5`), expose `embed(text)` and `embed_batch(texts)`, detect dimension from model.

**Acceptance Criteria**:
- [ ] `Embedder("BAAI/bge-small-en-v1.5")` initializes without error
- [ ] `embed("hello")` returns a list of floats with correct dimension
- [ ] `embed_batch(["hello", "world"])` returns list of lists, correct dimension each
- [ ] `dimension` property returns the model's vector dimension
- [ ] Configurable model name (pass different model string)
- [ ] Tests pass

**Verification**:
- `uv run pytest tests/test_embedder.py -v`

**Rollback**: Revert commit.

---

### T4: Storage engine
**Specs**: tech-plan.md §Component 6, §Milestone 4, core-flows.md §Flows 1-6
**Files**: `vxdb/storage.py`, `tests/test_storage.py`
**Touch list**: storage module, tests
**Dependencies**: T2, T3
**Effort**: Medium
**Parallel group**: B (with T5)

**Description**:
Wrap LanceDB. Implement all storage operations: create_table (with schema + hidden vector columns), insert (with _id generation + vector attachment), search (vector + filter), filter (metadata only), update, delete, list_tables, drop_table. Manage schema sidecar files via the schema module.

**Acceptance Criteria**:
- [ ] `create_table` creates a LanceDB table with correct Arrow schema + saves sidecar
- [ ] Hidden `_vec_{col}` columns created for each `text:embed` column
- [ ] `insert` generates UUID `_id` per row, accepts pre-computed vectors, returns _ids
- [ ] `search` does vector similarity + optional WHERE filter, returns rows with `_similarity`
- [ ] `filter` does metadata-only query with column selection, ordering, limit
- [ ] `update` modifies matching rows (by where filter string)
- [ ] `delete` removes matching rows (by where filter string)
- [ ] `list_tables` returns all table names
- [ ] `drop_table` removes table + sidecar file
- [ ] Schema sidecar loaded on init (survives restart)
- [ ] All operations work in a temp directory (integration tests)

**Verification**:
- `uv run pytest tests/test_storage.py -v`

**Rollback**: Revert commit.

---

### T5: SQL parser
**Specs**: tech-plan.md §Component 4, §Milestone 5, core-flows.md §Flow 3
**Files**: `vxdb/sql_parser.py`, `tests/test_sql_parser.py`
**Touch list**: parser module, tests
**Dependencies**: T2 (needs schema types for validation)
**Effort**: Medium
**Parallel group**: B (with T4)

**Description**:
Custom mini-parser for the SQL-ish query language. Parse `SELECT columns FROM table WHERE filters AND NEAR(col, 'text', k) ORDER BY col ASC/DESC LIMIT n` into QueryAST. Implement all validation rules.

**Acceptance Criteria**:
- [ ] Parses: `SELECT * FROM table`
- [ ] Parses: `SELECT col1, col2 FROM table`
- [ ] Parses: `SELECT * FROM table WHERE col = 'value'`
- [ ] Parses: `SELECT * FROM table WHERE col > 5 AND col2 = 'x'`
- [ ] Parses: `SELECT * FROM table WHERE NEAR(content, 'search text', 10)`
- [ ] Parses: `SELECT * FROM table WHERE category = 'dev' AND NEAR(content, 'search', 5) ORDER BY _similarity DESC LIMIT 20`
- [ ] Parses operators: `=`, `!=`, `>`, `<`, `>=`, `<=`, `IN`, `LIKE`
- [ ] Parses ORDER BY with ASC/DESC (default ASC)
- [ ] Parses LIMIT
- [ ] Error: `_similarity` in ORDER BY without NEAR()
- [ ] Error: multiple NEAR() clauses
- [ ] Error: invalid SQL syntax
- [ ] Error: NEAR() on non-text:embed column (requires schema context)
- [ ] Returns structured QueryAST dataclass
- [ ] Unit tests for all of the above

**Verification**:
- `uv run pytest tests/test_sql_parser.py -v`

**Rollback**: Revert commit.

---

### T6: MCP tools + server
**Specs**: tech-plan.md §Components 2-3, §Milestone 6, core-flows.md (all flows), epic-brief.md §Goals
**Files**: `vxdb/server.py`, `vxdb/tools.py`, `tests/test_tools.py`
**Touch list**: server, tools, tests
**Dependencies**: T4, T5
**Effort**: Medium

**Description**:
Wire everything together. Define FastMCP app with 7 tools (create_table, insert, query, update, delete, list_tables, drop_table). Tool handlers: validate input, call parser/embedder/storage, format JSON responses. Server init: parse `--db` and `--embedding-model` args, create Embedder + Storage, register tools, run via stdio.

**Acceptance Criteria**:
- [ ] `create_table` tool: accepts name + schema dict, creates table, returns confirmation
- [ ] `insert` tool: accepts table + rows, returns count + _ids
- [ ] `query` tool: accepts SQL string, returns rows (with _similarity when NEAR used)
- [ ] `update` tool: accepts table + set + (where OR id), returns count updated
- [ ] `delete` tool: accepts table + (where OR id), returns count deleted
- [ ] `list_tables` tool: returns table names
- [ ] `drop_table` tool: drops table, returns confirmation
- [ ] Errors return clear messages (table not found, parse error, schema mismatch, etc.)
- [ ] Server starts via stdio transport with `--db` flag
- [ ] End-to-end test: create table → insert → query with NEAR() → verify results

**Verification**:
- `uv run pytest tests/test_tools.py -v`
- Manual: `echo '{}' | uv run vxdb --db /tmp/test_vxdb` starts without error

**Rollback**: Revert commit.

---

### T7: CLI polish + SSE transport
**Specs**: tech-plan.md §Milestone 7, epic-brief.md §Design Decisions (transport)
**Files**: `main.py`, `vxdb/server.py`, `vxdb/__main__.py`
**Touch list**: entry points, server config
**Dependencies**: T6
**Effort**: Small

**Description**:
Add `--transport sse` flag (FastMCP flag flip). Polish `--help` output. Ensure `--embedding-model` flag works (pass to Embedder). Verify `uvx vxdb` works as expected.

**Acceptance Criteria**:
- [ ] `vxdb --db ./data` starts in stdio mode (default)
- [ ] `vxdb --db ./data --transport sse` starts HTTP/SSE server
- [ ] `vxdb --db ./data --embedding-model BAAI/bge-base-en-v1.5` uses specified model
- [ ] `vxdb --help` shows all flags with descriptions
- [ ] `uvx vxdb --db ./data` works (package is installable + runnable)

**Verification**:
- `uv run vxdb --help`
- `uv run vxdb --db /tmp/test --transport sse` (starts, ctrl-c)

**Rollback**: Revert commit.
