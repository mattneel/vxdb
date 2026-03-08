"""End-to-end tests for vxdb tools layer."""

import pytest

from vxdb.embedder import Embedder
from vxdb.storage import NAMESPACE, Storage
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
            assert not any(k.startswith("_vec_") for k in row)

    def test_select_columns(self, tools_with_table):
        tools_with_table.insert("notes", [
            {"title": "A", "content": "test", "category": "dev"},
        ])
        result = tools_with_table.query("SELECT title FROM notes")
        assert result["count"] == 1
        assert "title" in result["rows"][0]

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

    # --- New v0.2 capabilities ---

    def test_or_in_where(self, tools_with_table):
        """OR support — new with DuckDB."""
        tools_with_table.insert("notes", [
            {"title": "A", "content": "test", "category": "dev"},
            {"title": "B", "content": "test", "category": "ops"},
            {"title": "C", "content": "test", "category": "sales"},
        ])
        result = tools_with_table.query(
            "SELECT * FROM notes WHERE category = 'dev' OR category = 'sales' ORDER BY title"
        )
        assert result["count"] == 2
        titles = [r["title"] for r in result["rows"]]
        assert titles == ["A", "C"]

    def test_count_aggregate(self, tools_with_table):
        """COUNT(*) — new with DuckDB."""
        tools_with_table.insert("notes", [
            {"title": "A", "content": "test", "category": "dev"},
            {"title": "B", "content": "test", "category": "dev"},
        ])
        result = tools_with_table.query("SELECT COUNT(*) AS cnt FROM notes")
        assert result["rows"][0]["cnt"] == 2

    def test_distinct(self, tools_with_table):
        """DISTINCT — new with DuckDB."""
        tools_with_table.insert("notes", [
            {"title": "A", "content": "test", "category": "dev"},
            {"title": "B", "content": "test", "category": "dev"},
            {"title": "C", "content": "test", "category": "ops"},
        ])
        result = tools_with_table.query("SELECT DISTINCT category FROM notes ORDER BY category")
        assert result["count"] == 2
        cats = [r["category"] for r in result["rows"]]
        assert cats == ["dev", "ops"]

    def test_search_fts(self, tools_with_table):
        """SEARCH() full-text search — new with DuckDB+Lance."""
        tools_with_table.insert("notes", [
            {"title": "Neural Networks", "content": "Deep neural networks for image classification", "category": "ml"},
            {"title": "Cookies", "content": "Baking chocolate chip cookies recipe", "category": "food"},
        ])
        result = tools_with_table.query(
            "SELECT * FROM notes WHERE SEARCH(content, 'neural networks deep learning', 2)"
        )
        assert result["count"] >= 1
        for row in result["rows"]:
            assert "_score" in row


class TestSql:
    """Tests for the raw sql() tool."""

    def test_basic_query(self, tools_with_table):
        tools_with_table.insert("notes", [
            {"title": "A", "content": "test", "category": "dev"},
        ])
        result = tools_with_table.sql(f"SELECT title FROM {NAMESPACE}.main.notes")
        assert result["count"] == 1
        assert result["rows"][0]["title"] == "A"

    def test_count(self, tools_with_table):
        tools_with_table.insert("notes", [
            {"title": "A", "content": "test", "category": "dev"},
            {"title": "B", "content": "test", "category": "ops"},
        ])
        result = tools_with_table.sql(f"SELECT COUNT(*) AS cnt FROM {NAMESPACE}.main.notes")
        assert result["rows"][0]["cnt"] == 2

    def test_join_across_tables(self, tools):
        """JOIN across two vxdb tables — new with DuckDB."""
        tools.create_table("authors", {"name": "string", "code": "string"})
        tools.create_table("books", {"title": "string", "author_code": "string", "summary": "text:embed"})
        tools.insert("authors", [
            {"name": "Alice", "code": "A"},
            {"name": "Bob", "code": "B"},
        ])
        tools.insert("books", [
            {"title": "Book 1", "author_code": "A", "summary": "A great book"},
            {"title": "Book 2", "author_code": "B", "summary": "Another book"},
        ])
        result = tools.sql(
            f"SELECT b.title, a.name FROM {NAMESPACE}.main.books b "
            f"JOIN {NAMESPACE}.main.authors a ON b.author_code = a.code "
            f"ORDER BY b.title"
        )
        assert result["count"] == 2
        assert result["rows"][0]["title"] == "Book 1"
        assert result["rows"][0]["name"] == "Alice"

    def test_group_by(self, tools_with_table):
        tools_with_table.insert("notes", [
            {"title": "A", "content": "test", "category": "dev"},
            {"title": "B", "content": "test", "category": "dev"},
            {"title": "C", "content": "test", "category": "ops"},
        ])
        result = tools_with_table.sql(
            f"SELECT category, COUNT(*) AS cnt FROM {NAMESPACE}.main.notes GROUP BY category ORDER BY category"
        )
        assert result["count"] == 2
        assert result["rows"][0] == {"category": "dev", "cnt": 2}
        assert result["rows"][1] == {"category": "ops", "cnt": 1}


class TestUpdate:
    def test_update_by_id(self, tools_with_table):
        insert_result = tools_with_table.insert("notes", [
            {"title": "Old", "content": "test content", "category": "dev"},
        ])
        row_id = insert_result["ids"][0]
        result = tools_with_table.update("notes", {"title": "New"}, id=row_id)
        assert result["count"] == 1

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
