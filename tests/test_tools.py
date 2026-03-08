"""End-to-end tests for vxdb tools layer."""

import pytest

from vxdb.embedder import Embedder
from vxdb.storage import Storage
from vxdb.tools import Tools


@pytest.fixture(scope="module")
def embedder():
    return Embedder()


@pytest.fixture
def tools(tmp_path, embedder):
    storage = Storage(str(tmp_path), embedder.dimension)
    return Tools(storage, embedder)


@pytest.fixture
def tools_with_table(tools):
    tools.create_table("notes", {"title": "string", "content": "text:embed", "category": "string"})
    return tools


class TestCreateTable:
    def test_creates_table(self, tools):
        result = tools.create_table("notes", {"title": "string", "content": "text:embed"})
        assert result["table"] == "notes"
        assert result["embed_columns"] == ["content"]

    def test_duplicate_raises(self, tools):
        tools.create_table("notes", {"title": "string"})
        with pytest.raises(ValueError, match="already exists"):
            tools.create_table("notes", {"title": "string"})

    def test_invalid_type_raises(self, tools):
        with pytest.raises(ValueError, match="Invalid type"):
            tools.create_table("notes", {"title": "badtype"})


class TestInsert:
    def test_insert_rows(self, tools_with_table):
        result = tools_with_table.insert("notes", [
            {"title": "First", "content": "Hello world", "category": "dev"},
            {"title": "Second", "content": "Goodbye world", "category": "ops"},
        ])
        assert result["count"] == 2
        assert len(result["ids"]) == 2

    def test_insert_nonexistent_table(self, tools):
        with pytest.raises(ValueError, match="does not exist"):
            tools.insert("nope", [{"x": 1}])


class TestQuery:
    def test_select_all(self, tools_with_table):
        tools_with_table.insert("notes", [
            {"title": "A", "content": "Python programming", "category": "dev"},
            {"title": "B", "content": "Rust systems", "category": "dev"},
        ])
        result = tools_with_table.query("SELECT * FROM notes")
        assert result["count"] == 2
        for row in result["rows"]:
            assert "_id" in row
            assert "title" in row
            assert "_similarity" not in row
            # no internal columns
            assert not any(k.startswith("_vec_") for k in row)

    def test_select_columns(self, tools_with_table):
        tools_with_table.insert("notes", [
            {"title": "A", "content": "test", "category": "dev"},
        ])
        result = tools_with_table.query("SELECT title FROM notes")
        assert result["count"] == 1
        assert "title" in result["rows"][0]
        assert "_id" in result["rows"][0]  # _id always included

    def test_where_filter(self, tools_with_table):
        tools_with_table.insert("notes", [
            {"title": "A", "content": "test", "category": "dev"},
            {"title": "B", "content": "test", "category": "ops"},
        ])
        result = tools_with_table.query("SELECT * FROM notes WHERE category = 'dev'")
        assert result["count"] == 1
        assert result["rows"][0]["category"] == "dev"

    def test_near_search(self, tools_with_table):
        tools_with_table.insert("notes", [
            {"title": "ML paper", "content": "Deep learning and neural networks for image classification", "category": "ml"},
            {"title": "Recipe", "content": "How to bake chocolate chip cookies at home", "category": "food"},
            {"title": "AI news", "content": "Transformer models and attention mechanisms in NLP", "category": "ml"},
        ])
        result = tools_with_table.query(
            "SELECT * FROM notes WHERE NEAR(content, 'machine learning AI', 3)"
        )
        assert result["count"] == 3
        for row in result["rows"]:
            assert "_similarity" in row
            assert isinstance(row["_similarity"], float)

    def test_near_with_filter(self, tools_with_table):
        tools_with_table.insert("notes", [
            {"title": "ML paper", "content": "Deep learning neural networks", "category": "ml"},
            {"title": "Recipe", "content": "Baking cookies at home", "category": "food"},
            {"title": "AI news", "content": "Transformer attention mechanisms", "category": "ml"},
        ])
        result = tools_with_table.query(
            "SELECT * FROM notes WHERE category = 'ml' AND NEAR(content, 'deep learning', 5)"
        )
        # Should only return ml category rows
        for row in result["rows"]:
            assert row["category"] == "ml"
            assert "_similarity" in row

    def test_near_with_order_and_limit(self, tools_with_table):
        tools_with_table.insert("notes", [
            {"title": f"Note {i}", "content": f"Content about topic {i}", "category": "dev"}
            for i in range(5)
        ])
        result = tools_with_table.query(
            "SELECT * FROM notes WHERE NEAR(content, 'topic', 5) ORDER BY _similarity DESC LIMIT 2"
        )
        assert result["count"] == 2

    def test_nonexistent_table(self, tools):
        with pytest.raises(ValueError, match="does not exist"):
            tools.query("SELECT * FROM nope")

    def test_near_on_non_embed_column(self, tools_with_table):
        tools_with_table.insert("notes", [
            {"title": "A", "content": "test", "category": "dev"},
        ])
        with pytest.raises(ValueError, match="not a text:embed column"):
            tools_with_table.query("SELECT * FROM notes WHERE NEAR(title, 'test', 5)")

    def test_similarity_without_near_raises(self, tools_with_table):
        with pytest.raises(ValueError, match="_similarity"):
            tools_with_table.query("SELECT * FROM notes ORDER BY _similarity DESC")

    def test_empty_result(self, tools_with_table):
        result = tools_with_table.query("SELECT * FROM notes WHERE category = 'nonexistent'")
        assert result["rows"] == []
        assert result["count"] == 0


class TestUpdate:
    def test_update_by_id(self, tools_with_table):
        insert_result = tools_with_table.insert("notes", [
            {"title": "Old", "content": "test content", "category": "dev"},
        ])
        row_id = insert_result["ids"][0]
        result = tools_with_table.update("notes", {"title": "New"}, id=row_id)
        assert result["count"] == 1

        # Verify update
        query_result = tools_with_table.query(f"SELECT * FROM notes WHERE _id = '{row_id}'")
        assert query_result["rows"][0]["title"] == "New"

    def test_update_by_where(self, tools_with_table):
        tools_with_table.insert("notes", [
            {"title": "A", "content": "test", "category": "dev"},
            {"title": "B", "content": "test", "category": "dev"},
        ])
        result = tools_with_table.update("notes", {"category": "updated"}, where="category = 'dev'")
        assert result["count"] == 2

    def test_update_no_params_raises(self, tools_with_table):
        with pytest.raises(ValueError, match="Must provide"):
            tools_with_table.update("notes", {"title": "x"})

    def test_update_both_params_raises(self, tools_with_table):
        with pytest.raises(ValueError, match="Cannot provide both"):
            tools_with_table.update("notes", {"title": "x"}, where="x = 1", id="abc")


class TestDelete:
    def test_delete_by_id(self, tools_with_table):
        insert_result = tools_with_table.insert("notes", [
            {"title": "Delete me", "content": "test", "category": "dev"},
        ])
        row_id = insert_result["ids"][0]
        result = tools_with_table.delete("notes", id=row_id)
        assert result["count"] == 1

    def test_delete_by_where(self, tools_with_table):
        tools_with_table.insert("notes", [
            {"title": "A", "content": "test", "category": "trash"},
            {"title": "B", "content": "test", "category": "trash"},
        ])
        result = tools_with_table.delete("notes", where="category = 'trash'")
        assert result["count"] == 2

    def test_delete_no_params_raises(self, tools_with_table):
        with pytest.raises(ValueError, match="Must provide"):
            tools_with_table.delete("notes")


class TestListTables:
    def test_empty(self, tools):
        assert tools.list_tables() == {"tables": []}

    def test_after_create(self, tools):
        tools.create_table("a", {"x": "string"})
        tools.create_table("b", {"x": "string"})
        result = tools.list_tables()
        assert sorted(result["tables"]) == ["a", "b"]


class TestDropTable:
    def test_drop(self, tools):
        tools.create_table("temp", {"x": "string"})
        result = tools.drop_table("temp")
        assert result["dropped"] == "temp"
        assert tools.list_tables() == {"tables": []}

    def test_drop_nonexistent_raises(self, tools):
        with pytest.raises(ValueError, match="does not exist"):
            tools.drop_table("nope")
