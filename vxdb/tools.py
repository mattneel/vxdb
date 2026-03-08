"""MCP tool handlers for vxdb."""

from vxdb.embedder import Embedder
from vxdb.rewriter import rewrite
from vxdb.schema import validate_schema
from vxdb.storage import NAMESPACE, Storage


class Tools:
    def __init__(self, storage: Storage, embedder: Embedder):
        self.storage = storage
        self.embedder = embedder

    def create_table(self, name: str, schema: dict[str, str]) -> dict:
        table_schema = validate_schema(name, schema)
        self.storage.create_table(table_schema)
        return {
            "table": name,
            "columns": schema,
            "embed_columns": table_schema.embed_columns,
        }

    def insert(self, table: str, rows: list[dict]) -> dict:
        count, ids = self.storage.insert(table, rows, self.embedder.embed_batch)
        return {"count": count, "ids": ids}

    def query(self, sql: str) -> dict:
        result = rewrite(
            sql,
            schemas=self.storage.schemas,
            embed_fn=self.embedder.embed,
            namespace=NAMESPACE,
        )
        rows = self.storage.execute(result.sql)
        return {"rows": rows, "count": len(rows)}

    def sql(self, sql: str) -> dict:
        """Execute raw DuckDB SQL. No NEAR()/SEARCH() rewriting.

        Table names must use the full namespace: lance_ns.main.<table>
        Use lance_vector_search(), lance_fts(), lance_hybrid_search() directly.
        """
        rows = self.storage.execute(sql)
        return {"rows": rows, "count": len(rows)}

    def update(self, table: str, set_values: dict, where: str | None = None, id: str | None = None) -> dict:
        if not where and not id:
            raise ValueError("Must provide either 'where' or 'id' parameter.")
        if where and id:
            raise ValueError("Cannot provide both 'where' and 'id'. Use one or the other.")

        filter_str = f"_id = '{id}'" if id else where
        count = self.storage.update(table, filter_str, set_values, self.embedder.embed_batch)
        return {"count": count}

    def delete(self, table: str, where: str | None = None, id: str | None = None) -> dict:
        if not where and not id:
            raise ValueError("Must provide either 'where' or 'id' parameter.")
        if where and id:
            raise ValueError("Cannot provide both 'where' and 'id'. Use one or the other.")

        filter_str = f"_id = '{id}'" if id else where
        count = self.storage.delete(table, filter_str)
        return {"count": count}

    def list_tables(self) -> dict:
        tables = self.storage.list_tables()
        return {"tables": tables}

    def drop_table(self, name: str) -> dict:
        self.storage.drop_table(name)
        return {"dropped": name}
