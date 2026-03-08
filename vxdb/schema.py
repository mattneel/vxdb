"""Schema definitions and type mapping for vxdb tables."""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa

VALID_TYPES: set[str] = {"string", "int", "float", "bool", "text", "text:embed"}

TYPE_MAPPING: dict[str, pa.DataType] = {
    "string": pa.utf8(),
    "text": pa.utf8(),
    "text:embed": pa.utf8(),
    "int": pa.int64(),
    "float": pa.float64(),
    "bool": pa.bool_(),
}


@dataclass
class ColumnDef:
    name: str
    type: str


@dataclass
class TableSchema:
    name: str
    columns: dict[str, ColumnDef]
    embed_columns: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def validate_schema(name: str, schema_dict: dict[str, str]) -> TableSchema:
    if not name or not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError(f"Invalid table name: {name!r}. Must be non-empty, alphanumeric + underscores.")

    if not schema_dict:
        raise ValueError("Schema must have at least one column.")

    for col_name, col_type in schema_dict.items():
        if col_type not in VALID_TYPES:
            raise ValueError(f"Invalid type {col_type!r} for column {col_name!r}. Valid types: {VALID_TYPES}")

    columns = {col_name: ColumnDef(name=col_name, type=col_type) for col_name, col_type in schema_dict.items()}
    embed_columns = [col_name for col_name, col_type in schema_dict.items() if col_type == "text:embed"]

    return TableSchema(name=name, columns=columns, embed_columns=embed_columns)


def build_arrow_schema(table_schema: TableSchema, vector_dim: int) -> pa.Schema:
    fields: list[pa.Field] = [pa.field("_id", pa.utf8())]

    for col_name, col_def in table_schema.columns.items():
        fields.append(pa.field(col_name, TYPE_MAPPING[col_def.type]))

    for embed_col in table_schema.embed_columns:
        fields.append(pa.field(f"_vec_{embed_col}", pa.list_(pa.float32(), vector_dim)))

    return pa.schema(fields)


def save_schema(db_dir: str, schema: TableSchema) -> None:
    vxdb_dir = Path(db_dir) / ".vxdb"
    vxdb_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "name": schema.name,
        "columns": {col_name: col_def.type for col_name, col_def in schema.columns.items()},
        "created_at": schema.created_at,
    }

    sidecar = vxdb_dir / f"{schema.name}.schema.json"
    sidecar.write_text(json.dumps(data, indent=2))


def load_schema(db_dir: str, table_name: str) -> TableSchema:
    sidecar = Path(db_dir) / ".vxdb" / f"{table_name}.schema.json"
    if not sidecar.exists():
        raise FileNotFoundError(f"Schema sidecar not found: {sidecar}")

    data = json.loads(sidecar.read_text())
    columns = {col_name: ColumnDef(name=col_name, type=col_type) for col_name, col_type in data["columns"].items()}
    embed_columns = [col_name for col_name, col_type in data["columns"].items() if col_type == "text:embed"]

    return TableSchema(
        name=data["name"],
        columns=columns,
        embed_columns=embed_columns,
        created_at=data["created_at"],
    )


def load_all_schemas(db_dir: str) -> dict[str, TableSchema]:
    vxdb_dir = Path(db_dir) / ".vxdb"
    if not vxdb_dir.exists():
        return {}

    schemas: dict[str, TableSchema] = {}
    for sidecar in vxdb_dir.glob("*.schema.json"):
        table_name = sidecar.name.removesuffix(".schema.json")
        schemas[table_name] = load_schema(db_dir, table_name)

    return schemas


def delete_schema(db_dir: str, table_name: str) -> None:
    sidecar = Path(db_dir) / ".vxdb" / f"{table_name}.schema.json"
    sidecar.unlink(missing_ok=True)
