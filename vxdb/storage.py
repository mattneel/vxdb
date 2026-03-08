"""Storage engine wrapping LanceDB for vxdb."""

import uuid
from typing import Callable

import lancedb
import pyarrow as pa

from vxdb.schema import (
    TableSchema,
    build_arrow_schema,
    delete_schema,
    load_all_schemas,
    save_schema,
)


class Storage:
    def __init__(self, db_dir: str, vector_dim: int):
        self.db_dir = db_dir
        self.vector_dim = vector_dim
        self.db = lancedb.connect(db_dir)
        self.schemas: dict[str, TableSchema] = load_all_schemas(db_dir)

    def create_table(self, schema: TableSchema) -> None:
        """Create a new table with the given schema."""
        if schema.name in self.schemas:
            raise ValueError(f"Table {schema.name!r} already exists.")

        arrow_schema = build_arrow_schema(schema, self.vector_dim)
        self.db.create_table(schema.name, schema=arrow_schema)
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

        tbl = self.db.open_table(table)
        tbl.add(rows)

        return len(rows), ids

    def search(
        self,
        table: str,
        vector: list[float],
        vec_column: str,
        where: str | None = None,
        columns: list[str] | None = None,
        k: int = 10,
        limit: int | None = None,
    ) -> list[dict]:
        """Vector similarity search."""
        if table not in self.schemas:
            raise ValueError(f"Table {table!r} does not exist.")

        tbl = self.db.open_table(table)
        query = tbl.search(vector, vector_column_name=f"_vec_{vec_column}")

        if where:
            query = query.where(where)

        if columns:
            # Always include _id in selected columns
            select_cols = list(set(["_id"] + columns))
            query = query.select(select_cols)

        query = query.limit(k if limit is None else limit)
        results = query.to_list()

        # Clean results: remove internal columns, convert _distance to _similarity
        cleaned = []
        for row in results:
            clean_row = {}
            for key, value in row.items():
                if key.startswith("_vec_") or key == "vector":
                    continue
                if key == "_distance":
                    clean_row["_similarity"] = 1.0 / (1.0 + value)
                else:
                    clean_row[key] = value
            cleaned.append(clean_row)

        return cleaned

    def filter(
        self,
        table: str,
        where: str | None = None,
        columns: list[str] | None = None,
        limit: int | None = None,
        order_by: list[tuple[str, str]] | None = None,
    ) -> list[dict]:
        """Metadata-only query (no vector search)."""
        if table not in self.schemas:
            raise ValueError(f"Table {table!r} does not exist.")

        tbl = self.db.open_table(table)
        query = tbl.search()

        if where:
            query = query.where(where, prefilter=True)

        if columns:
            select_cols = list(set(["_id"] + columns))
            query = query.select(select_cols)

        # Use a generous default limit for non-vector scans
        query = query.limit(limit if limit is not None else 10_000)
        rows = query.to_list()

        # Strip internal _vec_ columns and _distance (artifact of search())
        cleaned = []
        for row in rows:
            clean_row = {
                k: v
                for k, v in row.items()
                if not k.startswith("_vec_") and k != "_distance" and k != "vector"
            }
            cleaned.append(clean_row)

        # Apply ordering if specified
        if order_by:
            for col, direction in reversed(order_by):
                reverse = direction.upper() == "DESC"
                cleaned.sort(key=lambda r: r.get(col, ""), reverse=reverse)

        return cleaned

    def update(
        self,
        table: str,
        where: str,
        values: dict,
        embed_fn: Callable[[list[str]], list[list[float]]],
    ) -> int:
        """Update rows matching the filter with new values."""
        if table not in self.schemas:
            raise ValueError(f"Table {table!r} does not exist.")

        schema = self.schemas[table]
        tbl = self.db.open_table(table)

        # Count affected rows before update
        count = tbl.count_rows(filter=where)

        if count == 0:
            return 0

        # Check if any embed columns are being updated
        embed_updates: dict[str, str] = {}
        for col in schema.embed_columns:
            if col in values:
                embed_updates[col] = values[col]

        # If embed columns are updated, recompute vectors
        if embed_updates:
            # Re-embed and include vectors in the update
            for col, text in embed_updates.items():
                vectors = embed_fn([text])
                values[f"_vec_{col}"] = vectors[0]

        tbl.update(where=where, values=values)
        return count

    def delete(self, table: str, where: str) -> int:
        """Delete rows matching the filter."""
        if table not in self.schemas:
            raise ValueError(f"Table {table!r} does not exist.")

        tbl = self.db.open_table(table)
        count = tbl.count_rows(filter=where)
        tbl.delete(where)
        return count

    def list_tables(self) -> list[str]:
        """Return names of all tables."""
        return list(self.schemas.keys())

    def drop_table(self, name: str) -> None:
        """Drop a table and its schema sidecar."""
        if name not in self.schemas:
            raise ValueError(f"Table {name!r} does not exist.")

        self.db.drop_table(name)
        delete_schema(self.db_dir, name)
        del self.schemas[name]
