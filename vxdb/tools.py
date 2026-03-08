"""MCP tool handlers for vxdb."""

from vxdb.embedder import Embedder
from vxdb.schema import validate_schema
from vxdb.sql_parser import parse_query
from vxdb.storage import Storage


def _build_where(filters) -> str | None:
    if not filters:
        return None
    parts = []
    for f in filters:
        if f.op == "IN":
            vals = ", ".join(
                f"'{v}'" if isinstance(v, str) else str(v) for v in f.value
            )
            parts.append(f"{f.column} IN ({vals})")
        elif f.op == "LIKE":
            parts.append(f"{f.column} LIKE '{f.value}'")
        elif isinstance(f.value, str):
            parts.append(f"{f.column} {f.op} '{f.value}'")
        elif isinstance(f.value, bool):
            parts.append(f"{f.column} {f.op} {str(f.value).lower()}")
        else:
            parts.append(f"{f.column} {f.op} {f.value}")
    return " AND ".join(parts)


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
        ast = parse_query(sql)

        # Validate NEAR column is a text:embed column
        if ast.near:
            schema = self.storage.schemas.get(ast.table)
            if schema is None:
                raise ValueError(f"Table {ast.table!r} does not exist.")
            if ast.near.column not in schema.embed_columns:
                raise ValueError(
                    f"NEAR() column {ast.near.column!r} is not a text:embed column. "
                    f"Embed columns: {schema.embed_columns}"
                )

        columns = ast.select if isinstance(ast.select, list) else None
        where_str = _build_where(ast.where)

        if ast.near:
            vector = self.embedder.embed(ast.near.text)
            rows = self.storage.search(
                table=ast.table,
                vector=vector,
                vec_column=ast.near.column,
                where=where_str,
                columns=columns,
                k=ast.near.k,
                limit=ast.limit,
            )
            # Apply ORDER BY
            if ast.order_by:
                for oc in reversed(ast.order_by):
                    reverse = oc.direction == "DESC"
                    rows.sort(key=lambda r: r.get(oc.column, 0), reverse=reverse)
        else:
            order_by = [(oc.column, oc.direction) for oc in ast.order_by] if ast.order_by else None
            rows = self.storage.filter(
                table=ast.table,
                where=where_str,
                columns=columns,
                limit=ast.limit,
                order_by=order_by,
            )

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
