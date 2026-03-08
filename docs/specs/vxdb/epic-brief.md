<!-- status: locked -->
<!-- epic-slug: vxdb -->
# Epic Brief: vxdb — Vectorized Database over MCP

## Problem

Agentic memory solutions today are bespoke — tightly coupled to specific frameworks or orchestrators. There's no embedded, zero-config, file-based vector database that any MCP-speaking agent can use out of the box. Agents that need persistent, searchable storage must either integrate framework-specific memory tools or manage their own vector DB infrastructure.

## Who's Affected

| Actor | Description |
|-------|-------------|
| **MCP client agent** | Any LLM agent (Claude Desktop, local vLLM, custom orchestrators) that speaks MCP and needs persistent structured storage with semantic search |
| **Host developer** | Developer configuring MCP servers for their agent setup — wants zero-config, drop-in |
| **vxdb itself (server)** | The MCP server process — owns embedding, storage, and query execution |

## Goals

- Expose a SQLite-mirrored tool surface over MCP: `create_table`, `insert`, `query`, `update`, `delete`, `list_tables`, `drop_table`
- Full SQL SELECT subset for queries: `SELECT columns FROM table WHERE ... ORDER BY ... LIMIT n`
- Vector similarity as a native predicate via `NEAR(column, 'text', k)` — usable in WHERE and ORDER BY
- Server-side embeddings via `fastembed` (ONNX-based) — agents send text, never think about vectors
- Embedded, file-based storage via LanceDB — no external services, no cloud dependency
- Hybrid search: vector similarity + typed metadata filtering in one query
- Framework-agnostic: any MCP client gets the same database substrate

## Non-Goals

- Not an "AI memory" product — no remember/recall/forget abstraction. It's a database; the agent decides what it's for
- No natural language query parsing — agents emit SQL-ish queries
- No cloud sync, replication, or multi-node
- No built-in reranking or summarization pass on results
- No authentication/authorization (single-user embedded model, like SQLite)

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Language | Python 3.14 | FastMCP is Python-native; fastest path to working MCP server |
| MCP framework | FastMCP | Best-in-class Python MCP server library |
| Storage engine | LanceDB | Embedded, Arrow-native, file-based, hybrid search, zero config |
| Embedding | `fastembed` (ONNX) | Lightweight, no PyTorch dependency, philosophically consistent with "embedded" story. sentence-transformers pulls 2GB+ of PyTorch for an 80MB model |
| Tool surface | SQLite-mirrored CRUD + vector predicate | LLMs already know SQL ops; maximizes composability |
| Query language | Full SQL SELECT subset with `NEAR()` | SELECT, columns, WHERE, ORDER BY, LIMIT. NEAR() as a function in WHERE/ORDER BY. LanceDB's Python API already supports column selection, filtering, and limit natively — mostly parsing and mapping |
| Schema typing | Simple types with `text:embed` convention | `{"name": "string", "count": "int", "content": "text:embed"}` — clean, obvious, no leaked Arrow implementation detail |
| Persistence model | One server = one DB directory (configured at startup) | Mirrors SQLite exactly. Second database = second server. Configuration is a path, not a routing layer |
| Transport | stdio default + `--transport sse` flag | stdio for standard MCP client subprocess launch. SSE costs almost nothing with FastMCP (flag flip) and unlocks shared/persistent server scenarios |

## Success Criteria

- `vxdb` runs as an MCP server via `uvx vxdb` or stdio transport
- An MCP client can: create a table with typed columns + `text:embed` column, insert rows, and query with `SELECT ... WHERE ... NEAR() ... ORDER BY ... LIMIT`
- Embeddings computed server-side transparently on insert and query via fastembed
- Data persists on disk across server restarts
- Query results return ranked rows with similarity scores when NEAR() is used
- Works with Claude Desktop, Claude Code, and any MCP-compatible client
- `--transport sse` flag starts an HTTP/SSE server instead of stdio

## Out of Scope

- Web UI / admin dashboard
- Multi-model embedding configuration (v1 uses fastembed default model)
- Index tuning / IVF-PQ configuration (LanceDB defaults for v1)
- Schema migration tooling
- Batch import from external sources
- Multi-database routing within a single server

## Context

The author built `ex_lancedb` — a production-grade Elixir NIF wrapper around LanceDB with schema DSL, batch insert, vector search with SQL filtering, and IVF-PQ indexing. That project validates the storage layer. vxdb takes the same core insight (LanceDB as embedded vector DB) and wraps it in MCP via Python FastMCP to make it universally accessible to any agent.

## Definitions

- **MCP**: Model Context Protocol — open standard for LLM tool servers
- **FastMCP**: Python library for building MCP servers with minimal boilerplate
- **LanceDB**: Embedded vector database built on Apache Arrow, file-based, zero-config
- **fastembed**: Lightweight ONNX-based embedding library — produces dense vectors without PyTorch
- **NEAR()**: vxdb's vector similarity predicate — `NEAR(column, 'search text', k)` returns k nearest neighbors by embedding similarity
- **`text:embed`**: Schema type annotation marking a text column for automatic embedding

## Kill Criteria / Stop Conditions

- fastembed can't produce quality embeddings for semantic search at acceptable latency (< 100ms per query embedding)
- LanceDB Python SDK doesn't support the filter expressiveness needed for the SQL SELECT subset
- FastMCP limitations prevent expressing the tool schemas needed (input validation, structured responses)
- The combined dependency footprint (fastembed + lancedb + fastmcp) exceeds ~500MB installed, undermining the "lightweight embedded" story
