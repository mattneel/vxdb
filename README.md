# vxdb

SQLite for vectors, over MCP.

Embedded vector database that any MCP-speaking agent can use. Server-side embeddings via [fastembed](https://github.com/qdrant/fastembed), file-based storage via [LanceDB](https://lancedb.github.io/lancedb/), exposed through [FastMCP](https://gofastmcp.com). Zero config for the consuming agent — it just talks to a database that happens to understand similarity.

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
# Create a table — text:embed columns get automatic embeddings
create_table("notes", {"title": "string", "content": "text:embed", "category": "string"})

# Insert — embeddings computed server-side, _id auto-generated
insert("notes", [
    {"title": "ML Paper", "content": "Transformer attention mechanisms in NLP", "category": "ml"},
    {"title": "Recipe", "content": "How to bake chocolate chip cookies", "category": "food"},
])

# Query with NEAR() — vector similarity as a WHERE predicate
query("SELECT * FROM notes WHERE category = 'ml' AND NEAR(content, 'deep learning', 5) ORDER BY _similarity DESC LIMIT 10")

# Update by _id or filter
update("notes", {"category": "archive"}, id="some-uuid")

# Delete, list, drop
delete("notes", where="category = 'archive'")
list_tables()
drop_table("notes")
```

## CLI

Also works as a direct CLI tool for agents without MCP (or humans):

```sh
# MCP server (default — no subcommand)
vxdb --db ./data
vxdb --db ./data serve --transport sse

# Create table
vxdb --db ./data create-table notes '{"title": "string", "content": "text:embed", "category": "string"}'

# Insert rows
vxdb --db ./data insert notes '[{"title": "ML Paper", "content": "Transformers for NLP", "category": "ml"}]'

# Query (with NEAR for vector search)
vxdb --db ./data query "SELECT * FROM notes WHERE NEAR(content, 'deep learning', 5) ORDER BY _similarity DESC"

# Update by id or filter
vxdb --db ./data update notes '{"category": "archive"}' --id some-uuid
vxdb --db ./data update notes '{"category": "archive"}' --where "category = 'old'"

# Delete
vxdb --db ./data delete notes --id some-uuid
vxdb --db ./data delete notes --where "category = 'archive'"

# List tables / drop
vxdb --db ./data tables
vxdb --db ./data drop-table notes

# Custom embedding model
vxdb --db ./data --embedding-model BAAI/bge-base-en-v1.5 query "SELECT * FROM notes"
```

All commands output JSON to stdout. Logs go to stderr.
