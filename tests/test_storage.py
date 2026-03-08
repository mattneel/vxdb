"""Tests for vxdb.storage module (DuckDB reads + LanceDB writes)."""

import pytest

from vxdb.embedder import Embedder
from vxdb.schema import validate_schema
from vxdb.storage import NAMESPACE, Storage


@pytest.fixture(scope="module")
def embedder():
    return Embedder()


@pytest.fixture
def storage(tmp_path, embedder):
    return Storage(str(tmp_path), embedder.dimension)


def _make_notes_schema():
    return validate_schema("notes", {
        "title": "string",
        "body": "text:embed",
        "category": "string",
    })


def _make_simple_schema():
    return validate_schema("items", {
        "name": "string",
        "count": "int",
    })


class TestCreateTable:
    def test_creates_table(self, storage):
        schema = _make_notes_schema()
        storage.create_table(schema)
        assert "notes" in storage.list_tables()
        assert "notes" in storage.schemas

    def test_duplicate_raises(self, storage):
        schema = _make_notes_schema()
        storage.create_table(schema)
        with pytest.raises(ValueError, match="already exists"):
            storage.create_table(schema)

    def test_duckdb_sees_table(self, storage):
        """Table created via LanceDB API is visible to DuckDB."""
        storage.create_table(_make_notes_schema())
        result = storage.conn.execute(
            f"SELECT COUNT(*) FROM {NAMESPACE}.main.notes"
        ).fetchone()
        assert result[0] == 0


class TestInsert:
    def test_insert_returns_count_and_ids(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        count, ids = storage.insert("notes", [
            {"title": "First", "body": "Hello world", "category": "test"},
            {"title": "Second", "body": "Goodbye world", "category": "test"},
        ], embedder.embed_batch)
        assert count == 2
        assert len(ids) == 2
        assert all(isinstance(i, str) for i in ids)
        assert ids[0] != ids[1]

    def test_insert_visible_to_duckdb(self, storage, embedder):
        """Rows inserted via LanceDB tbl.add() are queryable via DuckDB."""
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "Test", "body": "Test body", "category": "x"},
        ], embedder.embed_batch)
        rows = storage.execute(f"SELECT title FROM {NAMESPACE}.main.notes")
        assert len(rows) == 1
        assert rows[0]["title"] == "Test"

    def test_insert_nonexistent_table(self, storage, embedder):
        with pytest.raises(ValueError, match="does not exist"):
            storage.insert("nope", [{"x": 1}], embedder.embed_batch)


class TestExecute:
    """Test execute() for SQL queries via DuckDB."""

    def test_select_all(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "A", "body": "aaa", "category": "x"},
            {"title": "B", "body": "bbb", "category": "y"},
        ], embedder.embed_batch)
        rows = storage.execute(
            f"SELECT title, category FROM {NAMESPACE}.main.notes ORDER BY title"
        )
        assert len(rows) == 2
        assert rows[0]["title"] == "A"
        assert rows[1]["title"] == "B"

    def test_select_with_where(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "A", "body": "aaa", "category": "x"},
            {"title": "B", "body": "bbb", "category": "y"},
        ], embedder.embed_batch)
        rows = storage.execute(
            f"SELECT title FROM {NAMESPACE}.main.notes WHERE category = 'x'"
        )
        assert len(rows) == 1
        assert rows[0]["title"] == "A"

    def test_or_query(self, storage, embedder):
        """OR support — new with DuckDB."""
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "A", "body": "aaa", "category": "x"},
            {"title": "B", "body": "bbb", "category": "y"},
            {"title": "C", "body": "ccc", "category": "z"},
        ], embedder.embed_batch)
        rows = storage.execute(
            f"SELECT title FROM {NAMESPACE}.main.notes "
            f"WHERE category = 'x' OR category = 'z' ORDER BY title"
        )
        assert len(rows) == 2
        assert rows[0]["title"] == "A"
        assert rows[1]["title"] == "C"

    def test_count(self, storage, embedder):
        """COUNT(*) — new with DuckDB."""
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "A", "body": "aaa", "category": "x"},
            {"title": "B", "body": "bbb", "category": "x"},
        ], embedder.embed_batch)
        rows = storage.execute(
            f"SELECT COUNT(*) AS cnt FROM {NAMESPACE}.main.notes"
        )
        assert rows[0]["cnt"] == 2

    def test_strips_vec_columns(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "A", "body": "aaa", "category": "x"},
        ], embedder.embed_batch)
        rows = storage.execute(f"SELECT * FROM {NAMESPACE}.main.notes")
        assert len(rows) == 1
        for key in rows[0]:
            assert not key.startswith("_vec_")

    def test_vector_search(self, storage, embedder):
        """lance_vector_search() works through execute()."""
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "Cats", "body": "Cats are fluffy animals", "category": "pets"},
            {"title": "Python", "body": "Python is a programming language", "category": "dev"},
        ], embedder.embed_batch)

        query_vec = embedder.embed("fluffy animals")
        vec_str = "[" + ",".join(f"{v:.8f}" for v in query_vec) + "]::FLOAT[]"

        rows = storage.execute(f"""
            SELECT title, 1.0/(1.0+_distance) AS _similarity
            FROM lance_vector_search(
                '{NAMESPACE}.main.notes',
                '_vec_body',
                {vec_str},
                k := 2
            )
            ORDER BY _similarity DESC
        """)
        assert len(rows) == 2
        assert rows[0]["title"] == "Cats"
        assert rows[0]["_similarity"] > rows[1]["_similarity"]


class TestUpdate:
    def test_update_metadata(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        _, ids = storage.insert("notes", [
            {"title": "Old Title", "body": "Some body text", "category": "draft"},
        ], embedder.embed_batch)

        row_id = ids[0]
        count = storage.update("notes", f"_id = '{row_id}'", {"title": "New Title"}, embedder.embed_batch)
        assert count == 1

        rows = storage.execute(
            f"SELECT title FROM {NAMESPACE}.main.notes WHERE _id = '{row_id}'"
        )
        assert len(rows) == 1
        assert rows[0]["title"] == "New Title"

    def test_update_embed_column(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "ML Note", "body": "Machine learning basics", "category": "tech"},
            {"title": "Cooking", "body": "How to bake bread", "category": "food"},
        ], embedder.embed_batch)

        count = storage.update(
            "notes",
            "title = 'ML Note'",
            {"body": "How to cook pasta and make sauce"},
            embedder.embed_batch,
        )
        assert count == 1

        # Verify via vector search that the embedding was updated
        query_vec = embedder.embed("cooking recipes pasta")
        vec_str = "[" + ",".join(f"{v:.8f}" for v in query_vec) + "]::FLOAT[]"
        rows = storage.execute(f"""
            SELECT title, 1.0/(1.0+_distance) AS _similarity
            FROM lance_vector_search(
                '{NAMESPACE}.main.notes',
                '_vec_body',
                {vec_str},
                k := 2
            )
            ORDER BY _similarity DESC
        """)
        titles = [r["title"] for r in rows]
        assert "ML Note" in titles

    def test_update_no_matches(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "Only", "body": "Only row", "category": "x"},
        ], embedder.embed_batch)
        count = storage.update("notes", "title = 'Nonexistent'", {"category": "y"}, embedder.embed_batch)
        assert count == 0

    def test_update_nonexistent_table(self, storage, embedder):
        with pytest.raises(ValueError, match="does not exist"):
            storage.update("nope", "_id = 'x'", {"a": 1}, embedder.embed_batch)


class TestDelete:
    def test_delete_rows(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "Keep", "body": "Keep this", "category": "a"},
            {"title": "Remove", "body": "Remove this", "category": "b"},
            {"title": "Also Remove", "body": "Also remove", "category": "b"},
        ], embedder.embed_batch)

        count = storage.delete("notes", "category = 'b'")
        assert count == 2

        remaining = storage.execute(
            f"SELECT title FROM {NAMESPACE}.main.notes"
        )
        assert len(remaining) == 1
        assert remaining[0]["title"] == "Keep"

    def test_delete_no_matches(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "Stay", "body": "Staying", "category": "x"},
        ], embedder.embed_batch)
        count = storage.delete("notes", "category = 'nonexistent'")
        assert count == 0

    def test_delete_nonexistent_table(self, storage):
        with pytest.raises(ValueError, match="does not exist"):
            storage.delete("nope", "_id = 'x'")


class TestDropTable:
    def test_drop_table(self, storage, embedder):
        schema = _make_notes_schema()
        storage.create_table(schema)
        storage.insert("notes", [
            {"title": "X", "body": "X body", "category": "x"},
        ], embedder.embed_batch)
        assert "notes" in storage.list_tables()
        storage.drop_table("notes")
        assert "notes" not in storage.list_tables()
        assert "notes" not in storage.schemas

    def test_drop_nonexistent_raises(self, storage):
        with pytest.raises(ValueError, match="does not exist"):
            storage.drop_table("ghost")


class TestListTables:
    def test_list_empty(self, storage):
        assert storage.list_tables() == []

    def test_list_multiple(self, storage):
        storage.create_table(_make_notes_schema())
        storage.create_table(_make_simple_schema())
        tables = storage.list_tables()
        assert set(tables) == {"notes", "items"}
