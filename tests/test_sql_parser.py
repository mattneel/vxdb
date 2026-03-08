"""Tests for vxdb.sql_parser module."""

import pytest

from vxdb.sql_parser import (
    FilterClause,
    NearClause,
    OrderClause,
    QueryAST,
    parse_query,
)


class TestBasicParsing:
    def test_select_star_from_table(self):
        ast = parse_query("SELECT * FROM notes")
        assert ast.select == "*"
        assert ast.table == "notes"
        assert ast.where == []
        assert ast.near is None
        assert ast.order_by == []
        assert ast.limit is None

    def test_select_columns(self):
        ast = parse_query("SELECT col1, col2 FROM table1")
        assert ast.select == ["col1", "col2"]
        assert ast.table == "table1"

    def test_select_columns_no_spaces(self):
        ast = parse_query("SELECT col1,col2 FROM table1")
        assert ast.select == ["col1", "col2"]

    def test_case_insensitive_keywords(self):
        ast = parse_query("select * from TABLE")
        assert ast.select == "*"
        assert ast.table == "TABLE"

    def test_mixed_case_keywords(self):
        ast = parse_query("Select col1 From my_table")
        assert ast.select == ["col1"]
        assert ast.table == "my_table"

    def test_extra_whitespace(self):
        ast = parse_query("  SELECT   *   FROM   notes  ")
        assert ast.select == "*"
        assert ast.table == "notes"


class TestWhereFilters:
    def test_equals_string(self):
        ast = parse_query("SELECT * FROM t WHERE col = 'value'")
        assert len(ast.where) == 1
        assert ast.where[0] == FilterClause("col", "=", "value")

    def test_not_equals_string(self):
        ast = parse_query("SELECT * FROM t WHERE col != 'value'")
        assert ast.where[0] == FilterClause("col", "!=", "value")

    def test_greater_than_int(self):
        ast = parse_query("SELECT * FROM t WHERE col > 5")
        assert ast.where[0] == FilterClause("col", ">", 5)

    def test_greater_equal_float(self):
        ast = parse_query("SELECT * FROM t WHERE col >= 5.5")
        assert ast.where[0] == FilterClause("col", ">=", 5.5)

    def test_less_than_int(self):
        ast = parse_query("SELECT * FROM t WHERE col < 10")
        assert ast.where[0] == FilterClause("col", "<", 10)

    def test_less_equal_int(self):
        ast = parse_query("SELECT * FROM t WHERE col <= 10")
        assert ast.where[0] == FilterClause("col", "<=", 10)

    def test_like_pattern(self):
        ast = parse_query("SELECT * FROM t WHERE col LIKE '%pattern%'")
        assert ast.where[0] == FilterClause("col", "LIKE", "%pattern%")

    def test_in_strings(self):
        ast = parse_query("SELECT * FROM t WHERE col IN ('a', 'b', 'c')")
        assert ast.where[0] == FilterClause("col", "IN", ["a", "b", "c"])

    def test_in_numbers(self):
        ast = parse_query("SELECT * FROM t WHERE col IN (1, 2, 3)")
        assert ast.where[0] == FilterClause("col", "IN", [1, 2, 3])

    def test_equals_true(self):
        ast = parse_query("SELECT * FROM t WHERE col = true")
        assert ast.where[0] == FilterClause("col", "=", True)

    def test_equals_false(self):
        ast = parse_query("SELECT * FROM t WHERE col = false")
        assert ast.where[0] == FilterClause("col", "=", False)

    def test_boolean_case_insensitive(self):
        ast = parse_query("SELECT * FROM t WHERE col = TRUE")
        assert ast.where[0] == FilterClause("col", "=", True)

    def test_multiple_conditions(self):
        ast = parse_query("SELECT * FROM t WHERE col1 = 'a' AND col2 > 5")
        assert len(ast.where) == 2
        assert ast.where[0] == FilterClause("col1", "=", "a")
        assert ast.where[1] == FilterClause("col2", ">", 5)


class TestNearClause:
    def test_near_only(self):
        ast = parse_query("SELECT * FROM t WHERE NEAR(content, 'search text', 5)")
        assert ast.near == NearClause("content", "search text", 5)
        assert ast.where == []

    def test_near_with_filter(self):
        ast = parse_query(
            "SELECT * FROM t WHERE category = 'dev' AND NEAR(content, 'search', 10)"
        )
        assert len(ast.where) == 1
        assert ast.where[0] == FilterClause("category", "=", "dev")
        assert ast.near == NearClause("content", "search", 10)

    def test_near_with_escaped_quotes(self):
        ast = parse_query(
            "SELECT * FROM t WHERE NEAR(content, 'text with ''quotes''', 5)"
        )
        assert ast.near == NearClause("content", "text with 'quotes'", 5)

    def test_near_filter_before_and_after(self):
        ast = parse_query(
            "SELECT * FROM t WHERE a = 1 AND NEAR(col, 'txt', 3) AND b = 2"
        )
        assert ast.near == NearClause("col", "txt", 3)
        assert len(ast.where) == 2
        assert ast.where[0] == FilterClause("a", "=", 1)
        assert ast.where[1] == FilterClause("b", "=", 2)


class TestOrderBy:
    def test_default_asc(self):
        ast = parse_query("SELECT * FROM t ORDER BY col1")
        assert ast.order_by == [OrderClause("col1", "ASC")]

    def test_explicit_desc(self):
        ast = parse_query("SELECT * FROM t ORDER BY col1 DESC")
        assert ast.order_by == [OrderClause("col1", "DESC")]

    def test_explicit_asc(self):
        ast = parse_query("SELECT * FROM t ORDER BY col1 ASC")
        assert ast.order_by == [OrderClause("col1", "ASC")]

    def test_multiple_order_clauses(self):
        ast = parse_query("SELECT * FROM t ORDER BY col1 ASC, col2 DESC")
        assert ast.order_by == [
            OrderClause("col1", "ASC"),
            OrderClause("col2", "DESC"),
        ]

    def test_similarity_with_near(self):
        ast = parse_query(
            "SELECT * FROM t WHERE NEAR(content, 'q', 5) ORDER BY _similarity DESC"
        )
        assert ast.order_by == [OrderClause("_similarity", "DESC")]
        assert ast.near is not None


class TestLimit:
    def test_limit(self):
        ast = parse_query("SELECT * FROM t LIMIT 10")
        assert ast.limit == 10

    def test_limit_with_order(self):
        ast = parse_query("SELECT * FROM t ORDER BY col1 LIMIT 5")
        assert ast.limit == 5
        assert ast.order_by == [OrderClause("col1", "ASC")]


class TestFullQueries:
    def test_complex_query(self):
        sql = (
            "SELECT title, content FROM notes "
            "WHERE category = 'dev' AND NEAR(content, 'rendering pipelines', 5) "
            "ORDER BY _similarity DESC LIMIT 20"
        )
        ast = parse_query(sql)
        assert ast.select == ["title", "content"]
        assert ast.table == "notes"
        assert len(ast.where) == 1
        assert ast.where[0] == FilterClause("category", "=", "dev")
        assert ast.near == NearClause("content", "rendering pipelines", 5)
        assert ast.order_by == [OrderClause("_similarity", "DESC")]
        assert ast.limit == 20

    def test_select_star_with_where_and_limit(self):
        ast = parse_query("SELECT * FROM items WHERE active = true LIMIT 50")
        assert ast.select == "*"
        assert ast.table == "items"
        assert ast.where == [FilterClause("active", "=", True)]
        assert ast.limit == 50

    def test_near_only_query(self):
        ast = parse_query(
            "SELECT * FROM docs WHERE NEAR(body, 'machine learning', 10) "
            "ORDER BY _similarity DESC"
        )
        assert ast.near == NearClause("body", "machine learning", 10)
        assert ast.where == []
        assert ast.order_by == [OrderClause("_similarity", "DESC")]


class TestErrorCases:
    def test_empty_string(self):
        with pytest.raises(ValueError, match="Empty query"):
            parse_query("")

    def test_whitespace_only(self):
        with pytest.raises(ValueError, match="Empty query"):
            parse_query("   ")

    def test_no_select(self):
        with pytest.raises(ValueError, match="SELECT"):
            parse_query("FROM table1")

    def test_no_from(self):
        with pytest.raises(ValueError, match="FROM"):
            parse_query("SELECT *")

    def test_similarity_without_near(self):
        with pytest.raises(ValueError, match="_similarity.*NEAR"):
            parse_query("SELECT * FROM t ORDER BY _similarity DESC")

    def test_two_near_clauses(self):
        with pytest.raises(ValueError, match="[Mm]ultiple NEAR"):
            parse_query(
                "SELECT * FROM t WHERE NEAR(a, 'x', 1) AND NEAR(b, 'y', 2)"
            )

    def test_invalid_operator(self):
        with pytest.raises(ValueError, match="[Nn]o valid operator"):
            parse_query("SELECT * FROM t WHERE col ~ 'val'")

    def test_unterminated_string(self):
        with pytest.raises(ValueError, match="[Uu]nterminated string"):
            parse_query("SELECT * FROM t WHERE col = 'unterminated")

    def test_unterminated_string_in_near(self):
        with pytest.raises(ValueError, match="[Uu]nterminated string"):
            parse_query("SELECT * FROM t WHERE NEAR(col, 'open, 5)")
