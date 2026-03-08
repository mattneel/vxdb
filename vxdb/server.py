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

## Query Language
SQL subset: SELECT columns FROM table WHERE filters ORDER BY col ASC/DESC LIMIT n
- NEAR(column, 'search text', k) in WHERE — vector similarity search, returns k nearest neighbors
- _similarity — virtual column in results when NEAR() is used, available for ORDER BY
- Operators: =, !=, >, <, >=, <=, IN, LIKE
- Multiple filters with AND

## Rules
- _similarity only exists when NEAR() is present. ORDER BY _similarity without NEAR() is an error.
- One NEAR() per query.
- NEAR() column must be text:embed type.
- update/delete accept either "where" (filter string) or "id" (_id value), not both.
- Empty results return [], not an error.
"""

mcp = FastMCP("vxdb", instructions=INSTRUCTIONS)

_tools: Tools | None = None

GUIDE = """\
# vxdb Guide

Vectorized database over MCP. SQLite for vectors.

## Concepts

vxdb is an embedded vector database exposed as MCP tools. You talk to it like a SQL \
database, but it understands semantic similarity via the NEAR() function. Embeddings \
are computed server-side — you send text, it handles vectorization.

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
| Type        | Description                                        |
|-------------|----------------------------------------------------|
| string      | UTF-8 text, not embedded                           |
| text        | Alias for string                                   |
| text:embed  | Text that gets automatically embedded for NEAR()   |
| int         | 64-bit integer                                     |
| float       | 64-bit float                                       |
| bool        | Boolean                                            |

A table can have multiple text:embed columns. Each gets its own vector index.

## Inserting Data

    insert("documents", [
        {"title": "Attention Is All You Need", "body": "We propose a new architecture...", "source": "arxiv", "priority": 1, "score": 9.5, "archived": false},
        {"title": "Cookie Recipe", "body": "Preheat oven to 350F...", "source": "blog", "priority": 3, "score": 7.0, "archived": false}
    ])

- _id (UUID) is auto-generated for every row. Returned in the response.
- Embeddings for text:embed columns are computed automatically on insert.
- Rows must match the table schema.

## Querying

Full SQL subset: SELECT / FROM / WHERE / ORDER BY / LIMIT

### Metadata-only queries (no vector search):

    query("SELECT * FROM documents")
    query("SELECT title, source FROM documents WHERE priority <= 2")
    query("SELECT * FROM documents WHERE source IN ('arxiv', 'blog') ORDER BY score DESC LIMIT 5")

### Vector similarity search with NEAR():

    query("SELECT * FROM documents WHERE NEAR(body, 'transformer architecture attention', 10)")
    query("SELECT title FROM documents WHERE source = 'arxiv' AND NEAR(body, 'language models', 5) ORDER BY _similarity DESC LIMIT 3")

NEAR(column, 'search text', k):
- column: must be a text:embed column
- search text: natural language query (embedded at query time)
- k: number of nearest neighbors to return

### _similarity
- Virtual column that appears in results ONLY when NEAR() is in the query.
- Float between 0 and 1 (higher = more similar).
- Can be used in ORDER BY: ORDER BY _similarity DESC
- Using _similarity without NEAR() is a parse error.

### Operators
=, !=, >, <, >=, <=, IN ('a', 'b'), LIKE '%pattern%'

Combine filters with AND:
    WHERE category = 'ml' AND priority > 2 AND NEAR(body, 'search', 5)

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

### Document index
    create_table("docs", {"title": "string", "chunk": "text:embed", "path": "string", "page": "int"})

### Structured logs with semantic search
    create_table("logs", {"message": "text:embed", "level": "string", "service": "string"})
    query("SELECT * FROM logs WHERE level = 'error' AND NEAR(message, 'connection timeout', 10)")
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


def _init(db_dir: str, embedding_model: str) -> None:
    global _tools
    os.makedirs(db_dir, exist_ok=True)
    print(f"vxdb: loading embedding model {embedding_model}...", file=sys.stderr)
    embedder = Embedder(model_name=embedding_model)
    print(f"vxdb: model loaded (dim={embedder.dimension})", file=sys.stderr)
    storage = Storage(db_dir, embedder.dimension)
    print(f"vxdb: connected to {db_dir} ({len(storage.list_tables())} tables)", file=sys.stderr)
    _tools = Tools(storage, embedder)


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
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )

    args = parser.parse_args()
    _init(args.db, args.embedding_model)
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
