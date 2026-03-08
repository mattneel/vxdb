"""Storage engine for vxdb — DuckDB + Lance extension for reads,
LanceDB Python API for writes (insert, update, delete, table lifecycle)."""

import uuid
from typing import Callable

import duckdb
import lancedb

from vxdb.schema import (
    TableSchema,
    build_arrow_schema,
    delete_schema,
    load_all_schemas,
    save_schema,
)

NAMESPACE = "lance_ns"


class Storage:
    def __init__(self, db_dir: str, vector_dim: int):
        self.db_dir = db_dir
        self.vector_dim = vector_dim

        # LanceDB for all writes + table lifecycle
        self.lancedb = lancedb.connect(db_dir)

        # DuckDB for reads (queries via lance_vector_search, lance_fts, plain SQL)
        self.conn = duckdb.connect()
        self.conn.execute("INSTALL lance FROM community")
        self.conn.execute("LOAD lance")
        self.conn.execute(f"ATTACH '{db_dir}' AS {NAMESPACE} (TYPE LANCE)")

        # Schema sidecars for text:embed tracking
        self.schemas: dict[str, TableSchema] = load_all_schemas(db_dir)

    def create_table(self, schema: TableSchema) -> None:
        """Create a new table with the given schema."""
        if schema.name in self.schemas:
            raise ValueError(f"Table {schema.name!r} already exists.")

        arrow_schema = build_arrow_schema(schema, self.vector_dim)
        self.lancedb.create_table(schema.name, schema=arrow_schema)
        save_schema(self.db_dir, schema)
        self.schemas[schema.name] = schema

    def insert(
        self,
        table: str,
        rows: list[dict],
        embed_fn: Callable[[list[str]], list[list[float]]],
    ) -> tuple[int, list[str]]:
        """Insert rows into a table, generating IDs and embeddings."""
        if table not in self.schemas:
            raise ValueError(f"Table {table!r} does not exist.")

        schema = self.schemas[table]
        ids: list[str] = []

        for row in rows:
            row_id = str(uuid.uuid4())
            row["_id"] = row_id
            ids.append(row_id)

        # Generate embeddings for each embed column
        for col in schema.embed_columns:
            texts = [row[col] for row in rows]
            vectors = embed_fn(texts)
            for row, vec in zip(rows, vectors):
                row[f"_vec_{col}"] = vec

        tbl = self.lancedb.open_table(table)
        tbl.add(rows)

        return len(rows), ids

    def execute(self, sql: str) -> list[dict]:
        """Execute a read query via DuckDB and return results as list of dicts.

        Strips internal _vec_* columns from results.
        """
        result = self.conn.execute(sql)
        if result.description is None:
            return []
        columns = [desc[0] for desc in result.description]
        rows = []
        for row in result.fetchall():
            d = dict(zip(columns, row))
            # Strip internal vector columns
            d = {k: v for k, v in d.items() if not k.startswith("_vec_")}
            rows.append(d)
        return rows

    def update(
        self,
        table: str,
        where: str,
        values: dict,
        embed_fn: Callable[[list[str]], list[list[float]]],
    ) -> int:
        """Update rows matching the filter with new values.

        Uses LanceDB Python API for writes (DuckDB Lance extension has bugs
        with vector column updates).
        """
        if table not in self.schemas:
            raise ValueError(f"Table {table!r} does not exist.")

        schema = self.schemas[table]
        tbl = self.lancedb.open_table(table)

        # Count affected rows before update
        count = tbl.count_rows(filter=where)

        if count == 0:
            return 0

        # Check if any embed columns are being updated — re-embed if so
        for col in schema.embed_columns:
            if col in values:
                vectors = embed_fn([values[col]])
                values[f"_vec_{col}"] = vectors[0]

        tbl.update(where=where, values=values)
        return count

    def delete(self, table: str, where: str) -> int:
        """Delete rows matching the filter.

        Uses LanceDB Python API for deletes (DuckDB Lance extension has a bug
        where DELETE removes all rows regardless of WHERE clause).
        """
        if table not in self.schemas:
            raise ValueError(f"Table {table!r} does not exist.")

        tbl = self.lancedb.open_table(table)
        count = tbl.count_rows(filter=where)
        if count > 0:
            tbl.delete(where)
        return count

    def list_tables(self) -> list[str]:
        """Return names of all tables."""
        return list(self.schemas.keys())

    def drop_table(self, name: str) -> None:
        """Drop a table and its schema sidecar."""
        if name not in self.schemas:
            raise ValueError(f"Table {name!r} does not exist.")

        self.lancedb.drop_table(name)
        delete_schema(self.db_dir, name)
        del self.schemas[name]
