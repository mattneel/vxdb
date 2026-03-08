"""Tests for vxdb.storage module."""

import pytest

from vxdb.embedder import Embedder
from vxdb.schema import validate_schema
from vxdb.storage import Storage


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

    def test_insert_nonexistent_table(self, storage, embedder):
        with pytest.raises(ValueError, match="does not exist"):
            storage.insert("nope", [{"x": 1}], embedder.embed_batch)


class TestSearch:
    def test_search_returns_results(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "Cats", "body": "Cats are fluffy animals", "category": "pets"},
            {"title": "Dogs", "body": "Dogs are loyal companions", "category": "pets"},
            {"title": "Python", "body": "Python is a programming language", "category": "dev"},
        ], embedder.embed_batch)

        query_vec = embedder.embed("fluffy pets")
        results = storage.search("notes", query_vec, "body", k=3)

        assert len(results) > 0
        assert "_similarity" in results[0]
        assert results[0]["_similarity"] > 0
        # No internal columns exposed
        for row in results:
            assert all(not k.startswith("_vec_") for k in row.keys())
            assert "vector" not in row
        # Similarity is between 0 and 1
        for row in results:
            assert 0 < row["_similarity"] <= 1.0

    def test_search_ordering(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "Cats", "body": "Cats are fluffy animals", "category": "pets"},
            {"title": "Python", "body": "Python is a programming language", "category": "dev"},
        ], embedder.embed_batch)

        query_vec = embedder.embed("fluffy cats")
        results = storage.search("notes", query_vec, "body", k=2)

        assert len(results) == 2
        # Most similar should be first (highest similarity)
        assert results[0]["_similarity"] >= results[1]["_similarity"]
        assert results[0]["title"] == "Cats"

    def test_search_with_where_filter(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "Cats", "body": "Cats are fluffy animals", "category": "pets"},
            {"title": "Dogs", "body": "Dogs are loyal companions", "category": "pets"},
            {"title": "Python", "body": "Python is a programming language", "category": "dev"},
        ], embedder.embed_batch)

        query_vec = embedder.embed("animals")
        results = storage.search("notes", query_vec, "body", where="category = 'dev'", k=10)

        assert len(results) == 1
        assert results[0]["title"] == "Python"

    def test_search_with_column_selection(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "Cats", "body": "Cats are fluffy animals", "category": "pets"},
        ], embedder.embed_batch)

        query_vec = embedder.embed("cats")
        results = storage.search("notes", query_vec, "body", columns=["title"], k=1)

        assert len(results) == 1
        assert "title" in results[0]
        assert "_id" in results[0]
        assert "_similarity" in results[0]
        # category should not be present since we only selected title
        assert "category" not in results[0]

    def test_search_nonexistent_table(self, storage, embedder):
        with pytest.raises(ValueError, match="does not exist"):
            storage.search("nope", [0.0] * 384, "body", k=1)


class TestFilter:
    def test_filter_returns_rows(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "Cats", "body": "Cats are fluffy", "category": "pets"},
            {"title": "Dogs", "body": "Dogs are loyal", "category": "pets"},
            {"title": "Python", "body": "Python is great", "category": "dev"},
        ], embedder.embed_batch)

        results = storage.filter("notes", where="category = 'pets'")

        assert len(results) == 2
        titles = {r["title"] for r in results}
        assert titles == {"Cats", "Dogs"}

    def test_filter_no_similarity(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "Cats", "body": "Cats are fluffy", "category": "pets"},
        ], embedder.embed_batch)

        results = storage.filter("notes")

        assert len(results) == 1
        assert "_similarity" not in results[0]

    def test_filter_no_vec_columns(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "Cats", "body": "Cats are fluffy", "category": "pets"},
        ], embedder.embed_batch)

        results = storage.filter("notes")

        for row in results:
            assert all(not k.startswith("_vec_") for k in row.keys())

    def test_filter_with_columns(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "Cats", "body": "Cats are fluffy", "category": "pets"},
        ], embedder.embed_batch)

        results = storage.filter("notes", columns=["title"])

        assert len(results) == 1
        assert "title" in results[0]
        assert "_id" in results[0]
        assert "category" not in results[0]

    def test_filter_with_limit(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "A", "body": "aaa", "category": "x"},
            {"title": "B", "body": "bbb", "category": "x"},
            {"title": "C", "body": "ccc", "category": "x"},
        ], embedder.embed_batch)

        results = storage.filter("notes", limit=2)
        assert len(results) == 2

    def test_filter_nonexistent_table(self, storage):
        with pytest.raises(ValueError, match="does not exist"):
            storage.filter("nope")


class TestUpdate:
    def test_update_metadata(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        _, ids = storage.insert("notes", [
            {"title": "Old Title", "body": "Some body text", "category": "draft"},
        ], embedder.embed_batch)

        row_id = ids[0]
        count = storage.update("notes", f"_id = '{row_id}'", {"title": "New Title"}, embedder.embed_batch)

        assert count == 1

        results = storage.filter("notes", where=f"_id = '{row_id}'")
        assert len(results) == 1
        assert results[0]["title"] == "New Title"

    def test_update_embed_column(self, storage, embedder):
        storage.create_table(_make_notes_schema())
        storage.insert("notes", [
            {"title": "ML Note", "body": "Machine learning basics", "category": "tech"},
            {"title": "Cooking", "body": "How to bake bread", "category": "food"},
        ], embedder.embed_batch)

        # Update the embed column to something very different
        count = storage.update(
            "notes",
            "title = 'ML Note'",
            {"body": "How to cook pasta and make sauce"},
            embedder.embed_batch,
        )
        assert count == 1

        # Search for cooking-related content — the updated row should now match
        query_vec = embedder.embed("cooking recipes pasta")
        results = storage.search("notes", query_vec, "body", k=2)

        # The ML Note (now about cooking) should appear with high relevance
        titles = [r["title"] for r in results]
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

        remaining = storage.filter("notes")
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
