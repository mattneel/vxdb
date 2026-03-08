"""SQL rewriter for vxdb. Extracts NEAR()/SEARCH() sugar, embeds text,
rewrites to DuckDB lance_vector_search()/lance_fts() table functions."""

import re
from dataclasses import dataclass

# Match NEAR(column, 'text', k) or SEARCH(column, 'text', k)
# Handles escaped quotes ('') inside the text argument.
_NEAR_RE = re.compile(
    r"\bNEAR\s*\(\s*(\w+)\s*,\s*'((?:[^']|'')*?)'\s*,\s*(\d+)\s*\)",
    re.IGNORECASE,
)
_SEARCH_RE = re.compile(
    r"\bSEARCH\s*\(\s*(\w+)\s*,\s*'((?:[^']|'')*?)'\s*,\s*(\d+)\s*\)",
    re.IGNORECASE,
)

# Match FROM <table> (single word, no namespace prefix)
_FROM_RE = re.compile(r"\bFROM\s+(\w+)\b", re.IGNORECASE)

# Match SELECT ... FROM to find column list
_SELECT_RE = re.compile(r"\bSELECT\s+(.*?)\s+FROM\b", re.IGNORECASE | re.DOTALL)

# Match ORDER BY _similarity
_ORDER_SIM_RE = re.compile(r"\b_similarity\b", re.IGNORECASE)

NAMESPACE = "lance_ns"


@dataclass
class RewriteResult:
    sql: str
    has_similarity: bool
    has_score: bool


def _unescape(s: str) -> str:
    return s.replace("''", "'")


def _format_vector(vec: list[float]) -> str:
    """Format a vector as a DuckDB FLOAT[] literal."""
    floats = ",".join(f"{v:.8f}" for v in vec)
    return f"[{floats}]::FLOAT[]"


def _expand_select_star(sql: str, schema_columns: list[str]) -> str:
    """If SELECT *, expand to explicit column list (hiding _vec_* columns)."""
    m = _SELECT_RE.search(sql)
    if not m:
        return sql
    cols_str = m.group(1).strip()
    if cols_str != "*":
        return sql
    # Expand * to explicit columns: _id + user columns (no _vec_*)
    expanded = ", ".join(["_id"] + schema_columns)
    return sql[:m.start(1)] + expanded + sql[m.end(1):]


def rewrite(
    sql: str,
    schemas: dict,
    embed_fn: callable,
    namespace: str = NAMESPACE,
) -> RewriteResult:
    """Rewrite agent SQL into DuckDB-native SQL.

    - Extracts NEAR(column, 'text', k) → lance_vector_search() with embedded vector
    - Extracts SEARCH(column, 'text', k) → lance_fts()
    - Rewrites FROM table → FROM lance_ns.main.table
    - Expands SELECT * to hide _vec_* columns
    - Adds _similarity computation when NEAR() is present

    Args:
        sql: Agent SQL string (may contain NEAR/SEARCH sugar).
        schemas: Dict of table_name → TableSchema (for embed column validation).
        embed_fn: Callable that takes a string and returns a list[float] vector.
        namespace: DuckDB namespace for Lance tables.

    Returns:
        RewriteResult with rewritten SQL and flags.
    """
    sql = sql.strip()
    if not sql:
        raise ValueError("Empty query")

    # Find all NEAR() and SEARCH() calls
    near_matches = list(_NEAR_RE.finditer(sql))
    search_matches = list(_SEARCH_RE.finditer(sql))

    if len(near_matches) > 1:
        raise ValueError("Multiple NEAR() clauses are not allowed")
    if len(search_matches) > 1:
        raise ValueError("Multiple SEARCH() clauses are not allowed")
    if near_matches and search_matches:
        raise ValueError(
            "Cannot use both NEAR() and SEARCH() in the same query. "
            "Use the sql tool with lance_hybrid_search() for combined vector + FTS."
        )

    # Find the table name from FROM clause
    from_match = _FROM_RE.search(sql)
    if not from_match:
        raise ValueError("Query must contain a FROM clause")

    table = from_match.group(1)
    full_table = f"{namespace}.main.{table}"

    # Get schema for the table (needed for validation and SELECT * expansion)
    schema = schemas.get(table)

    has_near = bool(near_matches)
    has_search = bool(search_matches)

    if has_near:
        near_m = near_matches[0]
        column = near_m.group(1)
        text = _unescape(near_m.group(2))
        k = int(near_m.group(3))

        # Validate column is text:embed
        if schema is None:
            raise ValueError(f"Table {table!r} does not exist.")
        if column not in schema.embed_columns:
            raise ValueError(
                f"NEAR() column {column!r} is not a text:embed column. "
                f"Embed columns: {schema.embed_columns}"
            )

        vec_column = f"_vec_{column}"
        vector = embed_fn(text)
        vec_literal = _format_vector(vector)

        # Build the lance_vector_search() FROM clause
        lvs_from = (
            f"lance_vector_search('{full_table}', '{vec_column}', "
            f"{vec_literal}, k := {k})"
        )

        # Remove the NEAR() from the SQL
        rewritten = sql[:near_m.start()] + sql[near_m.end():]

        # Clean up leftover AND (NEAR might be first, middle, or last condition)
        # Remove "AND" that's now dangling
        rewritten = re.sub(r'\bAND\s+AND\b', 'AND', rewritten, flags=re.IGNORECASE)
        rewritten = re.sub(r'\bWHERE\s+AND\b', 'WHERE', rewritten, flags=re.IGNORECASE)
        # Remove trailing AND before ORDER BY/LIMIT/end
        rewritten = re.sub(r'\bAND\s*(?=ORDER\b|LIMIT\b|$)', '', rewritten, flags=re.IGNORECASE)
        # Remove empty WHERE clause
        rewritten = re.sub(r'\bWHERE\s*(?=ORDER\b|LIMIT\b|$)', '', rewritten, flags=re.IGNORECASE)
        rewritten = re.sub(r'\bWHERE\s*$', '', rewritten, flags=re.IGNORECASE)

        # Replace FROM table with FROM lance_vector_search(...)
        rewritten = re.sub(
            r'\bFROM\s+' + re.escape(table) + r'\b',
            f'FROM {lvs_from}',
            rewritten,
            count=1,
            flags=re.IGNORECASE,
        )

        # Expand SELECT * to hide _vec_* columns, add _similarity
        if schema:
            schema_cols = list(schema.columns.keys())
            rewritten = _expand_select_star(rewritten, schema_cols)

        # Add _similarity to SELECT
        select_m = _SELECT_RE.search(rewritten)
        if select_m:
            cols = select_m.group(1).strip()
            new_cols = f"{cols}, 1.0/(1.0+_distance) AS _similarity"
            rewritten = rewritten[:select_m.start(1)] + new_cols + rewritten[select_m.end(1):]

        rewritten = rewritten.strip()
        return RewriteResult(sql=rewritten, has_similarity=True, has_score=False)

    elif has_search:
        search_m = search_matches[0]
        column = search_m.group(1)
        text = _unescape(search_m.group(2))
        k = int(search_m.group(3))

        # Build the lance_fts() FROM clause
        fts_from = (
            f"lance_fts('{full_table}', '{column}', "
            f"'{text}', k := {k})"
        )

        # Remove the SEARCH() from the SQL
        rewritten = sql[:search_m.start()] + sql[search_m.end():]

        # Same AND cleanup as NEAR()
        rewritten = re.sub(r'\bAND\s+AND\b', 'AND', rewritten, flags=re.IGNORECASE)
        rewritten = re.sub(r'\bWHERE\s+AND\b', 'WHERE', rewritten, flags=re.IGNORECASE)
        rewritten = re.sub(r'\bAND\s*(?=ORDER\b|LIMIT\b|$)', '', rewritten, flags=re.IGNORECASE)
        rewritten = re.sub(r'\bWHERE\s*(?=ORDER\b|LIMIT\b|$)', '', rewritten, flags=re.IGNORECASE)
        rewritten = re.sub(r'\bWHERE\s*$', '', rewritten, flags=re.IGNORECASE)

        # Replace FROM table with FROM lance_fts(...)
        rewritten = re.sub(
            r'\bFROM\s+' + re.escape(table) + r'\b',
            f'FROM {fts_from}',
            rewritten,
            count=1,
            flags=re.IGNORECASE,
        )

        # Expand SELECT * to hide _vec_* columns, add _score
        if schema:
            schema_cols = list(schema.columns.keys())
            rewritten = _expand_select_star(rewritten, schema_cols)

        # Add _score to SELECT
        select_m = _SELECT_RE.search(rewritten)
        if select_m:
            cols = select_m.group(1).strip()
            new_cols = f"{cols}, _score"
            rewritten = rewritten[:select_m.start(1)] + new_cols + rewritten[select_m.end(1):]

        rewritten = rewritten.strip()
        return RewriteResult(sql=rewritten, has_similarity=False, has_score=True)

    else:
        # No NEAR() or SEARCH() — plain SQL passthrough with table name rewrite
        # Check for _similarity without NEAR()
        if _ORDER_SIM_RE.search(sql):
            raise ValueError("Cannot use _similarity without a NEAR() clause")

        rewritten = re.sub(
            r'\bFROM\s+' + re.escape(table) + r'\b',
            f'FROM {full_table}',
            sql,
            count=1,
            flags=re.IGNORECASE,
        )

        # Expand SELECT * to hide _vec_* columns
        if schema:
            schema_cols = list(schema.columns.keys())
            rewritten = _expand_select_star(rewritten, schema_cols)

        rewritten = rewritten.strip()
        return RewriteResult(sql=rewritten, has_similarity=False, has_score=False)
