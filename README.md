# vxdb

SQLite for vectors, over MCP.

Embedded vector database that any MCP-speaking agent can use. Server-side embeddings via [fastembed](https://github.com/qdrant/fastembed), file-based storage via [LanceDB](https://lancedb.github.io/lancedb/), exposed through [FastMCP](https://gofastmcp.com). Zero config for the consuming agent — it just talks to a database that happens to understand similarity.

## Install

```
uvx --from git+https://github.com/mattneel/vxdb vxdb --db ./data
```

## Setup

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

## Usage

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

```
vxdb --db ./data                                    # stdio (default)
vxdb --db ./data --transport sse                    # HTTP/SSE server
vxdb --db ./data --embedding-model BAAI/bge-base-en-v1.5  # custom model
```
