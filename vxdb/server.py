"""vxdb MCP server."""

import argparse
import os
import sys

from fastmcp import FastMCP

from vxdb.embedder import Embedder
from vxdb.storage import Storage
from vxdb.tools import Tools

INSTRUCTIONS = """\
Vectorized database over MCP. SQLite for vectors.

When working with tool results, write down any important information you might need later in your response, as the original tool result may be cleared later.

## Quick Start

1. Create a table — use "text:embed" type for columns you want to search semantically:
   create_table("notes", {"title": "string", "content": "text:embed", "category": "string"})

2. Insert rows — embeddings are computed automatically, _id is auto-generated:
   insert("notes", [{"title": "ML Paper", "content": "Neural networks for NLP", "category": "ml"}])

3. Query with SQL + NEAR() for vector search:
   query("SELECT * FROM notes WHERE category = 'ml' AND NEAR(content, 'deep learning', 5) ORDER BY _similarity DESC")

## Schema Types
string, text, int, float, bool, text:embed

## Two Query Tools

### query — single-table, with NEAR()/SEARCH() sugar
- NEAR(column, 'search text', k) — vector similarity search (embeds text server-side)
- SEARCH(column, 'search text', k) — full-text search (no embedding needed)
- _similarity — virtual column when NEAR() is used
- _score — virtual column when SEARCH() is used
- Full SQL: OR, NOT, GROUP BY, COUNT, DISTINCT, subqueries all work
- One NEAR() or one SEARCH() per query (not both)

### sql — raw DuckDB SQL, no rewriting
- For JOINs, cross-table queries, aggregations, analytics
- Tables referenced as lance_ns.main.<table_name>
- Use lance_vector_search(), lance_fts(), lance_hybrid_search() directly
- No NEAR()/SEARCH() sugar — this is the power user escape hatch

## Rules
- _similarity only exists when NEAR() is present. ORDER BY _similarity without NEAR() is an error.
- One NEAR() or one SEARCH() per query (not both). For hybrid, use sql tool with lance_hybrid_search().
- NEAR() column must be text:embed type.
- update/delete accept either "where" (filter string) or "id" (_id value), not both.
- Empty results return [], not an error.
"""

mcp = FastMCP("vxdb", instructions=INSTRUCTIONS)

_tools: Tools | None = None

GUIDE = """\
# vxdb Guide

Vectorized database over MCP. SQLite for vectors. Powered by DuckDB + Lance.

## Concepts

vxdb is an embedded vector database exposed as MCP tools. You talk to it like a SQL \
database, but it understands semantic similarity via the NEAR() function and full-text \
search via the SEARCH() function. Embeddings are computed in-process — you send text, \
it handles vectorization.

## Tables & Schema

Create tables with typed columns:

    create_table("documents", {
        "title": "string",
        "body": "text:embed",
        "source": "string",
        "priority": "int",
        "score": "float",
        "archived": "bool"
    })

### Types
| Type        | Description                                          |
|-------------|------------------------------------------------------|
| string      | UTF-8 text, not embedded                             |
| text        | Alias for string                                     |
| text:embed  | Text that gets automatically embedded for NEAR()     |
| int         | 64-bit integer                                       |
| float       | 64-bit float                                         |
| bool        | Boolean                                              |

A table can have multiple text:embed columns. Each gets its own vector index.

## Inserting Data

    insert("documents", [
        {"title": "Attention Is All You Need", "body": "We propose a new architecture...", "source": "arxiv", "priority": 1, "score": 9.5, "archived": false},
        {"title": "Cookie Recipe", "body": "Preheat oven to 350F...", "source": "blog", "priority": 3, "score": 7.0, "archived": false}
    ])

- _id (UUID) is auto-generated for every row. Returned in the response.
- Embeddings for text:embed columns are computed automatically on insert.
- Rows must match the table schema.

## Querying — Two Tools

### query tool (single-table, with sugar)

Full SQL with NEAR()/SEARCH() syntactic sugar. Operates on one table.

Metadata-only queries:

    query("SELECT * FROM documents")
    query("SELECT title, source FROM documents WHERE priority <= 2")
    query("SELECT * FROM documents WHERE source = 'arxiv' OR source = 'blog' ORDER BY score DESC LIMIT 5")
    query("SELECT category, COUNT(*) AS cnt FROM documents GROUP BY category")

Vector similarity search with NEAR():

    query("SELECT * FROM documents WHERE NEAR(body, 'transformer architecture attention', 10)")
    query("SELECT title FROM documents WHERE source = 'arxiv' AND NEAR(body, 'language models', 5) ORDER BY _similarity DESC LIMIT 3")

Full-text search with SEARCH():

    query("SELECT * FROM documents WHERE SEARCH(body, 'neural network architecture', 5)")
    query("SELECT title FROM documents WHERE source = 'arxiv' AND SEARCH(body, 'attention', 3) ORDER BY _score DESC")

NEAR(column, 'search text', k):
- column: must be a text:embed column
- search text: natural language query (embedded at query time)
- k: number of nearest neighbors to return
- _similarity: virtual column (0-1, higher = more similar)

SEARCH(column, 'search text', k):
- column: any text column
- search text: full-text search query
- k: number of results to return
- _score: virtual column (higher = more relevant)

Rules:
- One NEAR() or one SEARCH() per query (not both)
- _similarity only exists with NEAR(), _score only exists with SEARCH()
- Full SQL supported: OR, NOT, GROUP BY, HAVING, DISTINCT, subqueries

### sql tool (raw DuckDB SQL, no rewriting)

For JOINs, cross-table queries, aggregations, and analytics. No NEAR()/SEARCH() sugar — \
use DuckDB's native lance_vector_search(), lance_fts(), lance_hybrid_search() directly.

Tables are referenced as lance_ns.main.<table_name>.

    sql("SELECT COUNT(*) FROM lance_ns.main.documents")
    sql("SELECT n.title, c.name FROM lance_ns.main.notes n JOIN lance_ns.main.categories c ON n.cat_id = c._id")
    sql("SELECT category, COUNT(*) FROM lance_ns.main.documents GROUP BY category ORDER BY COUNT(*) DESC")

## Updating

By _id (most common — get _id from insert or query, then update):

    update("documents", {"priority": 1, "archived": true}, id="some-uuid-here")

By filter:

    update("documents", {"archived": true}, where="source = 'blog'")

- If you update a text:embed column, its embedding is automatically recomputed.
- Provide either "where" or "id", not both.

## Deleting

    delete("documents", id="some-uuid-here")
    delete("documents", where="archived = true")

## Housekeeping

    list_tables()          -- returns {"tables": ["documents", "notes"]}
    drop_table("documents") -- destructive, cannot be undone

## Common Patterns

### Semantic memory
    create_table("memory", {"content": "text:embed", "context": "string", "timestamp": "string"})
    insert("memory", [{"content": "User prefers dark mode", "context": "preferences", "timestamp": "2025-01-01T00:00:00Z"}])
    query("SELECT * FROM memory WHERE NEAR(content, 'UI preferences', 5)")

### Document index with full-text search
    create_table("docs", {"title": "string", "chunk": "text:embed", "path": "string", "page": "int"})
    query("SELECT * FROM docs WHERE SEARCH(chunk, 'installation guide', 10) ORDER BY _score DESC")

### Structured logs with semantic search
    create_table("logs", {"message": "text:embed", "level": "string", "service": "string"})
    query("SELECT * FROM logs WHERE level = 'error' AND NEAR(message, 'connection timeout', 10)")

### Cross-table analytics (via sql tool)
    sql("SELECT l.message, s.name FROM lance_ns.main.logs l JOIN lance_ns.main.services s ON l.service = s._id WHERE l.level = 'error'")

## Advanced Patterns

These patterns go beyond basic CRUD. They exploit the fact that you can create tables \
on the fly, embed your own outputs, and JOIN across your own emergent schema.

### Self-organizing memory

You don't need a pre-designed schema. Create tables as you discover you need them:

    create_table("context", {"content": "text:embed", "source": "string", "timestamp": "string"})
    create_table("decisions", {"choice": "text:embed", "rationale": "string", "domain": "string"})
    create_table("preferences", {"preference": "text:embed", "confidence": "string"})

The schema is an emergent artifact of your workflow. Use the sql tool to query across \
your own tables:

    sql("SELECT d.choice, c.content FROM lance_ns.main.decisions d JOIN lance_ns.main.context c ON d.domain = c.source WHERE d.domain = 'architecture'")

### Semantic caching

Before making an expensive tool call, check if you've seen something similar:

    query("SELECT * FROM cache WHERE NEAR(query, 'current request description', 3) ORDER BY _similarity DESC LIMIT 1")

If _similarity > 0.9, use the cached result. Evict stale entries:

    delete("cache", where="timestamp < '2025-01-01'")

Semantic dedup: find and remove entries that are near-duplicates of newer ones.

### Progressive knowledge distillation

Build a hierarchy from raw material to conclusions:

    insert("knowledge", [{"content": "raw text from source...", "level": "raw", "source": "paper.pdf", "parent": ""}])
    -- later, after reading several raw chunks:
    insert("knowledge", [{"content": "Synthesized summary of findings...", "level": "summary", "source": "synthesis", "parent": "raw-ids"}])
    -- later still:
    insert("knowledge", [{"content": "Key conclusion: ...", "level": "conclusion", "source": "distillation", "parent": "summary-ids"}])

NEAR() across levels traces provenance: "what raw chunks informed this conclusion?"

    query("SELECT * FROM knowledge WHERE level = 'raw' AND NEAR(content, 'the conclusion text', 10)")

### Multi-agent shared state

Two agents pointed at the same vxdb instance (sequentially) can leave each other \
semantically-queryable messages. Agent A inserts findings:

    insert("shared", [{"content": "Found that API X rate-limits at 100/min", "agent": "researcher", "task": "api-survey"}])

Agent B queries for relevance to its task:

    query("SELECT * FROM shared WHERE NEAR(content, 'API rate limits and throttling', 5)")

Use source/agent columns for agent-scoped namespacing within shared storage.

### Reflexive self-evaluation

Log your own outputs with outcome metadata:

    insert("outputs", [{"task": "code review", "output": "text:embed summary of what I produced", "feedback": "accepted", "confidence": "high"}])

When facing a similar task, query for past performance:

    query("SELECT * FROM outputs WHERE NEAR(output, 'current task description', 5) ORDER BY _similarity DESC")

This builds few-shot retrieval from lived experience.

## CLI (non-MCP)

vxdb also works as a direct CLI tool. All commands output JSON to stdout:

    vxdb --db ./data create-table notes '{"title": "string", "content": "text:embed"}'
    vxdb --db ./data insert notes '[{"title": "Hello", "content": "World"}]'
    vxdb --db ./data query "SELECT * FROM notes WHERE NEAR(content, 'greeting', 5)"
    vxdb --db ./data sql "SELECT COUNT(*) FROM lance_ns.main.notes"
    vxdb --db ./data update notes '{"title": "Updated"}' --id <uuid>
    vxdb --db ./data delete notes --where "title = 'old'"
    vxdb --db ./data tables
    vxdb --db ./data drop-table notes
"""


@mcp.resource("vxdb://guide")
def get_guide() -> str:
    """Complete usage guide for vxdb — schema types, query syntax, NEAR(), and common patterns."""
    return GUIDE


def _get_tools() -> Tools:
    if _tools is None:
        raise RuntimeError("vxdb server not initialized. Call main() first.")
    return _tools


@mcp.tool()
def create_table(name: str, schema: dict[str, str]) -> dict:
    """Create a new table.

    Args:
        name: Table name (alphanumeric + underscores).
        schema: Column definitions. Keys are column names, values are types:
            "string", "text", "int", "float", "bool", "text:embed".
            Use "text:embed" for columns that should be automatically embedded
            for vector similarity search.

    Returns:
        Table name, columns, and which columns are embedded.
    """
    return _get_tools().create_table(name, schema)


@mcp.tool()
def insert(table: str, rows: list[dict]) -> dict:
    """Insert rows into a table.

    Embeddings are computed automatically for text:embed columns.
    A unique _id is generated for each row.

    Args:
        table: Table name.
        rows: List of row dicts matching the table schema.

    Returns:
        Count of rows inserted and their _id values.
    """
    return _get_tools().insert(table, rows)


@mcp.tool()
def query(sql: str) -> dict:
    """Query a table using SQL-like syntax with optional vector search.

    Supports: SELECT columns FROM table WHERE filters ORDER BY col LIMIT n

    Vector search via NEAR() in WHERE clause:
        NEAR(column, 'search text', k) — finds k nearest neighbors by similarity.
        column must be a text:embed column.
        _similarity is a virtual column available in results when NEAR() is used.

    Examples:
        SELECT * FROM notes
        SELECT title FROM notes WHERE category = 'dev' LIMIT 10
        SELECT * FROM notes WHERE NEAR(content, 'machine learning', 5)
        SELECT * FROM notes WHERE category = 'dev' AND NEAR(content, 'ML', 10) ORDER BY _similarity DESC

    Args:
        sql: SQL-like query string.

    Returns:
        Matching rows and count.
    """
    return _get_tools().query(sql)


@mcp.tool()
def update(table: str, set: dict, where: str | None = None, id: str | None = None) -> dict:
    """Update rows in a table.

    Provide either 'where' (filter string) or 'id' (_id value), not both.
    If a text:embed column is updated, its embedding is automatically recomputed.

    Args:
        table: Table name.
        set: Column values to update.
        where: SQL-like filter string (e.g. "category = 'old'").
        id: Specific row _id to update.

    Returns:
        Count of rows updated.
    """
    return _get_tools().update(table, set, where=where, id=id)


@mcp.tool()
def delete(table: str, where: str | None = None, id: str | None = None) -> dict:
    """Delete rows from a table.

    Provide either 'where' (filter string) or 'id' (_id value), not both.

    Args:
        table: Table name.
        where: SQL-like filter string (e.g. "status = 'archived'").
        id: Specific row _id to delete.

    Returns:
        Count of rows deleted.
    """
    return _get_tools().delete(table, where=where, id=id)


@mcp.tool()
def list_tables() -> dict:
    """List all tables in the database.

    Returns:
        List of table names.
    """
    return _get_tools().list_tables()


@mcp.tool()
def drop_table(name: str) -> dict:
    """Drop a table and delete all its data.

    This is destructive and cannot be undone.

    Args:
        name: Table name to drop.

    Returns:
        Name of the dropped table.
    """
    return _get_tools().drop_table(name)


@mcp.tool()
def sql(sql: str) -> dict:
    """Execute raw DuckDB SQL. No NEAR()/SEARCH() rewriting.

    Use this for JOINs, cross-table queries, aggregations, and analytics.
    Tables are referenced as lance_ns.main.<table_name>.
    For vector search, use lance_vector_search() directly.
    For full-text search, use lance_fts() directly.

    Examples:
        SELECT COUNT(*) FROM lance_ns.main.notes
        SELECT n.title, c.name FROM lance_ns.main.notes n JOIN lance_ns.main.categories c ON n.cat_id = c._id
        SELECT category, COUNT(*) FROM lance_ns.main.notes GROUP BY category

    Args:
        sql: Raw DuckDB SQL string.

    Returns:
        Result rows and count.
    """
    return _get_tools().sql(sql)


def _init(db_dir: str, embedding_model: str) -> None:
    global _tools
    os.makedirs(db_dir, exist_ok=True)
    print(f"vxdb: loading embedding model {embedding_model}...", file=sys.stderr)
    embedder = Embedder(model_name=embedding_model)
    print(f"vxdb: model loaded (dim={embedder.dimension})", file=sys.stderr)
    storage = Storage(db_dir, embedder.dimension)
    print(f"vxdb: connected to {db_dir} ({len(storage.list_tables())} tables)", file=sys.stderr)
    _tools = Tools(storage, embedder)


def _json_out(data):
    import json
    print(json.dumps(data, indent=2))


def main():
    parser = argparse.ArgumentParser(
        prog="vxdb",
        description="Vectorized database over MCP. SQLite for vectors.",
    )
    parser.add_argument("--db", required=True, help="Path to database directory")
    parser.add_argument(
        "--embedding-model",
        default="BAAI/bge-small-en-v1.5",
        help="Embedding model name (default: BAAI/bge-small-en-v1.5)",
    )

    sub = parser.add_subparsers(dest="command")

    # MCP server (default when no subcommand)
    serve_cmd = sub.add_parser("serve", help="Start MCP server (default)")
    serve_cmd.add_argument(
        "--transport", choices=["stdio", "sse"], default="stdio",
        help="MCP transport (default: stdio)",
    )

    # CLI subcommands
    ct = sub.add_parser("create-table", help="Create a table")
    ct.add_argument("name", help="Table name")
    ct.add_argument("schema", help='Column schema as JSON: \'{"col": "type", ...}\'')

    ins = sub.add_parser("insert", help="Insert rows")
    ins.add_argument("table", help="Table name")
    ins.add_argument("rows", help='Rows as JSON array: \'[{"col": "val"}, ...]\'')

    q = sub.add_parser("query", help="Query with SQL (NEAR/SEARCH sugar)")
    q.add_argument("sql", help="SQL query string")

    raw = sub.add_parser("sql", help="Raw DuckDB SQL (no rewriting)")
    raw.add_argument("sql", help="Raw DuckDB SQL string")

    up = sub.add_parser("update", help="Update rows")
    up.add_argument("table", help="Table name")
    up.add_argument("set", help='Values as JSON: \'{"col": "val"}\'')
    up_target = up.add_mutually_exclusive_group(required=True)
    up_target.add_argument("--where", help="Filter string")
    up_target.add_argument("--id", help="Row _id")

    dl = sub.add_parser("delete", help="Delete rows")
    dl.add_argument("table", help="Table name")
    dl_target = dl.add_mutually_exclusive_group(required=True)
    dl_target.add_argument("--where", help="Filter string")
    dl_target.add_argument("--id", help="Row _id")

    sub.add_parser("tables", help="List tables")

    dt = sub.add_parser("drop-table", help="Drop a table")
    dt.add_argument("name", help="Table name")

    args = parser.parse_args()

    # No subcommand or "serve" → MCP server
    if args.command is None or args.command == "serve":
        transport = getattr(args, "transport", "stdio")
        _init(args.db, args.embedding_model)
        mcp.run(transport=transport)
        return

    # CLI mode
    import json
    _init(args.db, args.embedding_model)
    tools = _get_tools()

    match args.command:
        case "create-table":
            _json_out(tools.create_table(args.name, json.loads(args.schema)))
        case "insert":
            _json_out(tools.insert(args.table, json.loads(args.rows)))
        case "query":
            _json_out(tools.query(args.sql))
        case "sql":
            _json_out(tools.sql(args.sql))
        case "update":
            _json_out(tools.update(args.table, json.loads(args.set), where=args.where, id=args.id))
        case "delete":
            _json_out(tools.delete(args.table, where=args.where, id=args.id))
        case "tables":
            _json_out(tools.list_tables())
        case "drop-table":
            _json_out(tools.drop_table(args.name))


if __name__ == "__main__":
    main()
