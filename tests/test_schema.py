"""Tests for vxdb.schema module."""

import pyarrow as pa
import pytest

from vxdb.schema import (
    build_arrow_schema,
    delete_schema,
    load_all_schemas,
    load_schema,
    save_schema,
    validate_schema,
)


class TestValidateSchema:
    def test_valid_schema_all_types(self):
        schema = validate_schema("my_table", {
            "title": "string",
            "body": "text",
            "content": "text:embed",
            "count": "int",
            "score": "float",
            "active": "bool",
        })
        assert schema.name == "my_table"
        assert len(schema.columns) == 6
        assert schema.columns["title"].type == "string"
        assert schema.columns["body"].type == "text"
        assert schema.columns["content"].type == "text:embed"
        assert schema.columns["count"].type == "int"
        assert schema.columns["score"].type == "float"
        assert schema.columns["active"].type == "bool"

    def test_embed_columns_detected(self):
        schema = validate_schema("t", {
            "a": "text:embed",
            "b": "string",
            "c": "text:embed",
        })
        assert schema.embed_columns == ["a", "c"]

    def test_no_embed_columns(self):
        schema = validate_schema("t", {"a": "string"})
        assert schema.embed_columns == []

    def test_invalid_type_rejected(self):
        with pytest.raises(ValueError, match="Invalid type"):
            validate_schema("t", {"a": "varchar"})

    def test_empty_schema_rejected(self):
        with pytest.raises(ValueError, match="at least one column"):
            validate_schema("t", {})

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="Invalid table name"):
            validate_schema("", {"a": "string"})

    def test_invalid_name_with_spaces(self):
        with pytest.raises(ValueError, match="Invalid table name"):
            validate_schema("my table", {"a": "string"})

    def test_invalid_name_with_special_chars(self):
        with pytest.raises(ValueError, match="Invalid table name"):
            validate_schema("my-table", {"a": "string"})

    def test_valid_name_with_underscores(self):
        schema = validate_schema("my_table_2", {"a": "string"})
        assert schema.name == "my_table_2"

    def test_created_at_is_set(self):
        schema = validate_schema("t", {"a": "string"})
        assert schema.created_at is not None
        assert len(schema.created_at) > 0


class TestBuildArrowSchema:
    def test_basic_schema(self):
        ts = validate_schema("t", {"title": "string", "count": "int"})
        arrow = build_arrow_schema(ts, vector_dim=384)

        assert arrow.field("_id").type == pa.utf8()
        assert arrow.field("title").type == pa.utf8()
        assert arrow.field("count").type == pa.int64()

    def test_id_is_first_column(self):
        ts = validate_schema("t", {"a": "string"})
        arrow = build_arrow_schema(ts, vector_dim=384)
        assert arrow.field(0).name == "_id"

    def test_embed_produces_vec_column(self):
        ts = validate_schema("t", {"content": "text:embed"})
        arrow = build_arrow_schema(ts, vector_dim=384)

        assert arrow.field("content").type == pa.utf8()
        vec_field = arrow.field("_vec_content")
        assert vec_field.type == pa.list_(pa.float32(), 384)

    def test_multiple_embed_columns(self):
        ts = validate_schema("t", {"a": "text:embed", "b": "text:embed"})
        arrow = build_arrow_schema(ts, vector_dim=128)

        assert "_vec_a" in arrow.names
        assert "_vec_b" in arrow.names

    def test_all_type_mappings(self):
        ts = validate_schema("t", {
            "s": "string",
            "t": "text",
            "e": "text:embed",
            "i": "int",
            "f": "float",
            "b": "bool",
        })
        arrow = build_arrow_schema(ts, vector_dim=64)

        assert arrow.field("s").type == pa.utf8()
        assert arrow.field("t").type == pa.utf8()
        assert arrow.field("e").type == pa.utf8()
        assert arrow.field("i").type == pa.int64()
        assert arrow.field("f").type == pa.float64()
        assert arrow.field("b").type == pa.bool_()


class TestSidecarRoundTrip:
    def test_save_and_load(self, tmp_path):
        schema = validate_schema("notes", {"title": "string", "body": "text:embed"})
        save_schema(str(tmp_path), schema)
        loaded = load_schema(str(tmp_path), "notes")

        assert loaded.name == schema.name
        assert loaded.created_at == schema.created_at
        assert loaded.embed_columns == ["body"]
        assert loaded.columns["title"].type == "string"
        assert loaded.columns["body"].type == "text:embed"

    def test_load_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_schema(str(tmp_path), "nonexistent")

    def test_load_all_schemas(self, tmp_path):
        s1 = validate_schema("alpha", {"a": "string"})
        s2 = validate_schema("beta", {"b": "int", "c": "text:embed"})
        save_schema(str(tmp_path), s1)
        save_schema(str(tmp_path), s2)

        all_schemas = load_all_schemas(str(tmp_path))
        assert set(all_schemas.keys()) == {"alpha", "beta"}
        assert all_schemas["alpha"].columns["a"].type == "string"
        assert all_schemas["beta"].embed_columns == ["c"]

    def test_load_all_schemas_empty(self, tmp_path):
        result = load_all_schemas(str(tmp_path))
        assert result == {}

    def test_delete_schema(self, tmp_path):
        schema = validate_schema("doomed", {"x": "float"})
        save_schema(str(tmp_path), schema)

        loaded = load_schema(str(tmp_path), "doomed")
        assert loaded.name == "doomed"

        delete_schema(str(tmp_path), "doomed")

        with pytest.raises(FileNotFoundError):
            load_schema(str(tmp_path), "doomed")

    def test_delete_nonexistent_is_noop(self, tmp_path):
        delete_schema(str(tmp_path), "ghost")
