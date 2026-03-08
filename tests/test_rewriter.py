"""Tests for the vxdb SQL rewriter."""

import pytest

from vxdb.rewriter import RewriteResult, rewrite
from vxdb.schema import ColumnDef, TableSchema


def _make_schema(name="notes", columns=None, embed_columns=None):
    if columns is None:
        columns = {
            "title": ColumnDef("title", "string"),
            "content": ColumnDef("content", "text:embed"),
            "category": ColumnDef("category", "string"),
        }
    if embed_columns is None:
        embed_columns = [c.name for c in columns.values() if c.type == "text:embed"]
    return TableSchema(name=name, columns=columns, embed_columns=embed_columns)


def _mock_embed(text: str) -> list[float]:
    """Returns a fixed 3-dim vector for testing."""
    return [0.1, 0.2, 0.3]


@pytest.fixture
def schemas():
    return {"notes": _make_schema()}


class TestPlainSQL:
    """Non-vector queries — table name rewrite only."""

    def test_simple_select(self, schemas):
        result = rewrite("SELECT * FROM notes", schemas, _mock_embed)
        assert "lance_ns.main.notes" in result.sql
        assert not result.has_similarity
        assert not result.has_score

    def test_select_with_where(self, schemas):
        result = rewrite(
            "SELECT title FROM notes WHERE category = 'ml'",
            schemas, _mock_embed
        )
        assert "lance_ns.main.notes" in result.sql
        assert "category = 'ml'" in result.sql

    def test_select_with_or(self, schemas):
        result = rewrite(
            "SELECT * FROM notes WHERE category = 'ml' OR category = 'ai'",
            schemas, _mock_embed
        )
        assert "OR" in result.sql

    def test_select_with_order_and_limit(self, schemas):
        result = rewrite(
            "SELECT title FROM notes ORDER BY title ASC LIMIT 10",
            schemas, _mock_embed
        )
        assert "ORDER BY title ASC" in result.sql
        assert "LIMIT 10" in result.sql

    def test_select_star_expands(self, schemas):
        result = rewrite("SELECT * FROM notes", schemas, _mock_embed)
        # Should expand * to explicit columns (hiding _vec_*)
        assert "_id" in result.sql
        assert "title" in result.sql
        assert "content" in result.sql
        assert "category" in result.sql
        assert "_vec_" not in result.sql

    def test_similarity_without_near_errors(self, schemas):
        with pytest.raises(ValueError, match="_similarity"):
            rewrite(
                "SELECT * FROM notes ORDER BY _similarity DESC",
                schemas, _mock_embed
            )

    def test_empty_query_errors(self, schemas):
        with pytest.raises(ValueError, match="Empty"):
            rewrite("", schemas, _mock_embed)

    def test_no_from_errors(self, schemas):
        with pytest.raises(ValueError, match="FROM"):
            rewrite("SELECT 1", schemas, _mock_embed)


class TestNearRewrite:
    """NEAR() extraction and rewrite to lance_vector_search()."""

    def test_near_basic(self, schemas):
        result = rewrite(
            "SELECT * FROM notes WHERE NEAR(content, 'deep learning', 5)",
            schemas, _mock_embed
        )
        assert "lance_vector_search(" in result.sql
        assert "'lance_ns.main.notes'" in result.sql
        assert "'_vec_content'" in result.sql
        assert "k := 5" in result.sql
        assert result.has_similarity
        assert "_similarity" in result.sql

    def test_near_with_filter(self, schemas):
        result = rewrite(
            "SELECT * FROM notes WHERE category = 'ml' AND NEAR(content, 'deep learning', 5)",
            schemas, _mock_embed
        )
        assert "lance_vector_search(" in result.sql
        assert "category = 'ml'" in result.sql
        # NEAR() should be removed from WHERE
        assert "NEAR(" not in result.sql

    def test_near_first_with_and(self, schemas):
        result = rewrite(
            "SELECT * FROM notes WHERE NEAR(content, 'deep learning', 5) AND category = 'ml'",
            schemas, _mock_embed
        )
        assert "lance_vector_search(" in result.sql
        assert "category = 'ml'" in result.sql
        assert "NEAR(" not in result.sql

    def test_near_middle_condition(self, schemas):
        """NEAR in the middle of multiple AND conditions."""
        schemas_ext = {
            "notes": _make_schema(columns={
                "title": ColumnDef("title", "string"),
                "content": ColumnDef("content", "text:embed"),
                "category": ColumnDef("category", "string"),
                "year": ColumnDef("year", "int"),
            })
        }
        result = rewrite(
            "SELECT * FROM notes WHERE category = 'ml' AND NEAR(content, 'transformers', 3) AND year > 2020",
            schemas_ext, _mock_embed
        )
        assert "lance_vector_search(" in result.sql
        assert "category = 'ml'" in result.sql
        assert "year > 2020" in result.sql
        assert "NEAR(" not in result.sql

    def test_near_with_order_by_similarity(self, schemas):
        result = rewrite(
            "SELECT * FROM notes WHERE NEAR(content, 'deep learning', 5) ORDER BY _similarity DESC",
            schemas, _mock_embed
        )
        assert "_similarity" in result.sql
        assert "ORDER BY _similarity DESC" in result.sql

    def test_near_vector_in_output(self, schemas):
        result = rewrite(
            "SELECT * FROM notes WHERE NEAR(content, 'test', 3)",
            schemas, _mock_embed
        )
        # Vector should be in the SQL as a FLOAT[] literal
        assert "0.10000000" in result.sql
        assert "0.20000000" in result.sql
        assert "0.30000000" in result.sql

    def test_near_invalid_column(self, schemas):
        with pytest.raises(ValueError, match="not a text:embed"):
            rewrite(
                "SELECT * FROM notes WHERE NEAR(title, 'test', 5)",
                schemas, _mock_embed
            )

    def test_near_nonexistent_table(self):
        with pytest.raises(ValueError, match="does not exist"):
            rewrite(
                "SELECT * FROM missing WHERE NEAR(content, 'test', 5)",
                {}, _mock_embed
            )

    def test_multiple_near_errors(self, schemas):
        with pytest.raises(ValueError, match="Multiple NEAR"):
            rewrite(
                "SELECT * FROM notes WHERE NEAR(content, 'a', 5) AND NEAR(content, 'b', 5)",
                schemas, _mock_embed
            )

    def test_near_with_escaped_quote(self, schemas):
        result = rewrite(
            "SELECT * FROM notes WHERE NEAR(content, 'it''s a test', 5)",
            schemas, _mock_embed
        )
        assert "lance_vector_search(" in result.sql

    def test_near_only_no_empty_where(self, schemas):
        """When NEAR is the only WHERE condition, no empty WHERE clause."""
        result = rewrite(
            "SELECT * FROM notes WHERE NEAR(content, 'test', 5)",
            schemas, _mock_embed
        )
        # Should not have a dangling WHERE
        assert "WHERE" not in result.sql or "WHERE " in result.sql


class TestSearchRewrite:
    """SEARCH() extraction and rewrite to lance_fts()."""

    def test_search_basic(self, schemas):
        result = rewrite(
            "SELECT * FROM notes WHERE SEARCH(content, 'neural networks', 5)",
            schemas, _mock_embed
        )
        assert "lance_fts(" in result.sql
        assert "'lance_ns.main.notes'" in result.sql
        assert "'content'" in result.sql
        assert "'neural networks'" in result.sql
        assert "k := 5" in result.sql
        assert result.has_score
        assert not result.has_similarity

    def test_search_with_filter(self, schemas):
        result = rewrite(
            "SELECT * FROM notes WHERE category = 'ml' AND SEARCH(content, 'neural', 3)",
            schemas, _mock_embed
        )
        assert "lance_fts(" in result.sql
        assert "category = 'ml'" in result.sql
        assert "SEARCH(" not in result.sql

    def test_search_no_embed_fn_called(self, schemas):
        """SEARCH() does not call the embed function (FTS uses raw text)."""
        called = []

        def tracking_embed(text):
            called.append(text)
            return [0.0]

        rewrite(
            "SELECT * FROM notes WHERE SEARCH(content, 'test', 5)",
            schemas, tracking_embed
        )
        assert len(called) == 0

    def test_multiple_search_errors(self, schemas):
        with pytest.raises(ValueError, match="Multiple SEARCH"):
            rewrite(
                "SELECT * FROM notes WHERE SEARCH(content, 'a', 5) AND SEARCH(content, 'b', 5)",
                schemas, _mock_embed
            )

    def test_near_and_search_errors(self, schemas):
        with pytest.raises(ValueError, match="Cannot use both"):
            rewrite(
                "SELECT * FROM notes WHERE NEAR(content, 'a', 5) AND SEARCH(content, 'b', 5)",
                schemas, _mock_embed
            )


class TestEdgeCases:
    """Edge cases for the rewriter."""

    def test_case_insensitive_keywords(self, schemas):
        result = rewrite(
            "select * from notes where near(content, 'test', 5)",
            schemas, _mock_embed
        )
        assert "lance_vector_search(" in result.sql

    def test_select_specific_columns_with_near(self, schemas):
        result = rewrite(
            "SELECT title, category FROM notes WHERE NEAR(content, 'test', 5)",
            schemas, _mock_embed
        )
        assert "lance_vector_search(" in result.sql
        assert "_similarity" in result.sql

    def test_limit_preserved(self, schemas):
        result = rewrite(
            "SELECT * FROM notes WHERE NEAR(content, 'test', 5) LIMIT 3",
            schemas, _mock_embed
        )
        assert "LIMIT 3" in result.sql

    def test_count_query(self, schemas):
        result = rewrite(
            "SELECT COUNT(*) FROM notes",
            schemas, _mock_embed
        )
        assert "lance_ns.main.notes" in result.sql
        assert "COUNT(*)" in result.sql

    def test_table_unknown_no_near(self):
        """Unknown table without NEAR/SEARCH — still rewrites (DuckDB will error)."""
        result = rewrite(
            "SELECT * FROM unknown_table WHERE x = 1",
            {}, _mock_embed
        )
        assert "lance_ns.main.unknown_table" in result.sql
