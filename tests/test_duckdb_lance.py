"""DuckDB + Lance extension proof-of-concept tests.

Validates that DuckDB's Lance extension supports the full vxdb use case:
ATTACH, CREATE TABLE, INSERT, SELECT, UPDATE, DELETE, lance_vector_search(), lance_fts().
"""

import time
import uuid

import duckdb
import lancedb
import pyarrow as pa
import pytest


@pytest.fixture
def lance_dir(tmp_path):
    """Create a temp directory for Lance data."""
    return str(tmp_path / "lance_data")


@pytest.fixture
def conn(lance_dir):
    """DuckDB connection with Lance extension attached."""
    c = duckdb.connect()
    c.execute("INSTALL lance FROM community")
    c.execute("LOAD lance")
    c.execute(f"ATTACH '{lance_dir}' AS lance_ns (TYPE LANCE)")
    return c


class TestDuckDBLanceBasics:
    """Basic CRUD operations via DuckDB on Lance tables."""

    def test_create_table(self, conn):
        conn.execute("""
            CREATE TABLE lance_ns.main.test_basic AS
            SELECT 'hello' AS title, 'world' AS body
        """)
        result = conn.execute("SELECT * FROM lance_ns.main.test_basic").fetchall()
        assert len(result) == 1
        assert result[0] == ("hello", "world")

    def test_create_table_with_schema(self, conn):
        """Create an empty table with explicit schema, then insert."""
        conn.execute("""
            CREATE TABLE lance_ns.main.typed (
                _id VARCHAR,
                title VARCHAR,
                score DOUBLE,
                count BIGINT,
                active BOOLEAN
            )
        """)
        conn.execute("""
            INSERT INTO lance_ns.main.typed
            VALUES ('id1', 'test', 3.14, 42, true)
        """)
        result = conn.execute("SELECT * FROM lance_ns.main.typed").fetchall()
        assert len(result) == 1
        assert result[0] == ("id1", "test", 3.14, 42, True)

    def test_insert(self, conn):
        conn.execute("""
            CREATE TABLE lance_ns.main.test_insert (
                _id VARCHAR,
                name VARCHAR,
                value BIGINT
            )
        """)
        conn.execute("""
            INSERT INTO lance_ns.main.test_insert VALUES
            ('a', 'alice', 10),
            ('b', 'bob', 20),
            ('c', 'carol', 30)
        """)
        result = conn.execute(
            "SELECT name, value FROM lance_ns.main.test_insert ORDER BY value"
        ).fetchall()
        assert result == [("alice", 10), ("bob", 20), ("carol", 30)]

    def test_select_with_where(self, conn):
        conn.execute("""
            CREATE TABLE lance_ns.main.test_where AS
            SELECT * FROM (VALUES
                ('a', 'alice', 10),
                ('b', 'bob', 20),
                ('c', 'carol', 30)
            ) AS t(_id, name, value)
        """)
        result = conn.execute(
            "SELECT name FROM lance_ns.main.test_where WHERE value > 15"
        ).fetchall()
        names = [r[0] for r in result]
        assert set(names) == {"bob", "carol"}

    def test_update(self, conn):
        conn.execute("""
            CREATE TABLE lance_ns.main.test_update AS
            SELECT * FROM (VALUES
                ('a', 'alice', 10),
                ('b', 'bob', 20)
            ) AS t(_id, name, value)
        """)
        conn.execute(
            "UPDATE lance_ns.main.test_update SET value = 99 WHERE _id = 'a'"
        )
        result = conn.execute(
            "SELECT value FROM lance_ns.main.test_update WHERE _id = 'a'"
        ).fetchall()
        assert result[0][0] == 99

    def test_delete(self, conn):
        conn.execute("""
            CREATE TABLE lance_ns.main.test_delete AS
            SELECT * FROM (VALUES
                ('a', 'alice', 10),
                ('b', 'bob', 20),
                ('c', 'carol', 30)
            ) AS t(_id, name, value)
        """)
        conn.execute(
            "DELETE FROM lance_ns.main.test_delete WHERE _id = 'b'"
        )
        result = conn.execute(
            "SELECT _id FROM lance_ns.main.test_delete ORDER BY _id"
        ).fetchall()
        ids = [r[0] for r in result]
        assert ids == ["a", "c"]

    def test_or_in_where(self, conn):
        """OR support — something v0.1 can't do."""
        conn.execute("""
            CREATE TABLE lance_ns.main.test_or AS
            SELECT * FROM (VALUES
                ('a', 'alice', 'dev'),
                ('b', 'bob', 'ops'),
                ('c', 'carol', 'dev'),
                ('d', 'dave', 'sales')
            ) AS t(_id, name, dept)
        """)
        result = conn.execute("""
            SELECT name FROM lance_ns.main.test_or
            WHERE dept = 'dev' OR dept = 'sales'
            ORDER BY name
        """).fetchall()
        names = [r[0] for r in result]
        assert names == ["alice", "carol", "dave"]

    def test_count_aggregate(self, conn):
        """COUNT(*) — something v0.1 can't do."""
        conn.execute("""
            CREATE TABLE lance_ns.main.test_count AS
            SELECT * FROM (VALUES ('a'), ('b'), ('c')) AS t(name)
        """)
        result = conn.execute(
            "SELECT COUNT(*) FROM lance_ns.main.test_count"
        ).fetchone()
        assert result[0] == 3

    def test_group_by(self, conn):
        """GROUP BY — something v0.1 can't do."""
        conn.execute("""
            CREATE TABLE lance_ns.main.test_group AS
            SELECT * FROM (VALUES
                ('dev', 1), ('dev', 2), ('ops', 3)
            ) AS t(dept, val)
        """)
        result = conn.execute("""
            SELECT dept, COUNT(*) as cnt
            FROM lance_ns.main.test_group
            GROUP BY dept
            ORDER BY dept
        """).fetchall()
        assert result == [("dev", 2), ("ops", 1)]


class TestVectorSearch:
    """lance_vector_search() table function."""

    def test_vector_search_returns_distance(self, conn):
        conn.execute("""
            CREATE TABLE lance_ns.main.test_vec AS
            SELECT * FROM (VALUES
                ('dog', [0.9, 0.1, 0.0]::FLOAT[]),
                ('cat', [0.8, 0.2, 0.1]::FLOAT[]),
                ('car', [0.1, 0.1, 0.9]::FLOAT[])
            ) AS t(name, vector)
        """)
        result = conn.execute("""
            SELECT name, _distance
            FROM lance_vector_search(
                'lance_ns.main.test_vec',
                'vector',
                [0.85, 0.15, 0.05]::FLOAT[],
                k := 2
            )
            ORDER BY _distance ASC
        """).fetchall()
        assert len(result) == 2
        # dog and cat should be closest to [0.85, 0.15, 0.05]
        names = [r[0] for r in result]
        assert "dog" in names
        assert "cat" in names
        # _distance should be a float >= 0
        for row in result:
            assert isinstance(row[1], float)
            assert row[1] >= 0

    def test_vector_search_with_filter(self, conn):
        """Vector search + WHERE filter — hybrid search."""
        conn.execute("""
            CREATE TABLE lance_ns.main.test_vec_filter AS
            SELECT * FROM (VALUES
                ('dog', 'animal', [0.9, 0.1, 0.0]::FLOAT[]),
                ('cat', 'animal', [0.8, 0.2, 0.1]::FLOAT[]),
                ('car', 'vehicle', [0.1, 0.1, 0.9]::FLOAT[]),
                ('bus', 'vehicle', [0.2, 0.1, 0.8]::FLOAT[])
            ) AS t(name, category, vector)
        """)
        result = conn.execute("""
            SELECT name, _distance
            FROM lance_vector_search(
                'lance_ns.main.test_vec_filter',
                'vector',
                [0.85, 0.15, 0.05]::FLOAT[],
                k := 3
            )
            WHERE category = 'animal'
            ORDER BY _distance ASC
        """).fetchall()
        names = [r[0] for r in result]
        # Only animals returned
        assert all(n in ("dog", "cat") for n in names)

    def test_similarity_computation(self, conn):
        """Verify 1/(1+_distance) can be computed inline."""
        conn.execute("""
            CREATE TABLE lance_ns.main.test_sim AS
            SELECT * FROM (VALUES
                ('a', [1.0, 0.0]::FLOAT[]),
                ('b', [0.0, 1.0]::FLOAT[])
            ) AS t(name, vector)
        """)
        result = conn.execute("""
            SELECT name, 1.0/(1.0+_distance) AS _similarity
            FROM lance_vector_search(
                'lance_ns.main.test_sim',
                'vector',
                [1.0, 0.0]::FLOAT[],
                k := 2
            )
            ORDER BY _similarity DESC
        """).fetchall()
        assert len(result) == 2
        # First result should be 'a' with higher similarity
        assert result[0][0] == "a"
        assert result[0][1] > result[1][1]


class TestFullTextSearch:
    """lance_fts() table function."""

    def test_fts_returns_score(self, conn):
        conn.execute("""
            CREATE TABLE lance_ns.main.test_fts AS
            SELECT * FROM (VALUES
                ('Neural networks for deep learning'),
                ('Baking chocolate chip cookies'),
                ('Transformer attention mechanisms')
            ) AS t(content)
        """)
        result = conn.execute("""
            SELECT content, _score
            FROM lance_fts(
                'lance_ns.main.test_fts',
                'content',
                'neural network deep learning',
                k := 2
            )
            ORDER BY _score DESC
        """).fetchall()
        assert len(result) >= 1
        # _score should be a float
        for row in result:
            assert isinstance(row[1], float)


class TestCrossEngineCompat:
    """Verify DuckDB can see tables created by LanceDB Python API and vice versa."""

    def test_duckdb_sees_lancedb_table(self, lance_dir, conn):
        """Create table via LanceDB Python API, query via DuckDB."""
        db = lancedb.connect(lance_dir)
        schema = pa.schema([
            pa.field("_id", pa.utf8()),
            pa.field("title", pa.utf8()),
            pa.field("_vec_content", pa.list_(pa.float32(), 3)),
        ])
        db.create_table("cross_test", schema=schema)
        tbl = db.open_table("cross_test")
        tbl.add([
            {"_id": "x1", "title": "hello", "_vec_content": [0.1, 0.2, 0.3]},
            {"_id": "x2", "title": "world", "_vec_content": [0.4, 0.5, 0.6]},
        ])

        # DuckDB should see this table
        result = conn.execute(
            "SELECT _id, title FROM lance_ns.main.cross_test ORDER BY _id"
        ).fetchall()
        assert result == [("x1", "hello"), ("x2", "world")]

    def test_vector_search_on_lancedb_table(self, lance_dir, conn):
        """Create via LanceDB, vector search via DuckDB."""
        db = lancedb.connect(lance_dir)
        schema = pa.schema([
            pa.field("name", pa.utf8()),
            pa.field("vec", pa.list_(pa.float32(), 3)),
        ])
        db.create_table("vec_cross", schema=schema)
        tbl = db.open_table("vec_cross")
        tbl.add([
            {"name": "close", "vec": [0.9, 0.1, 0.0]},
            {"name": "far", "vec": [0.0, 0.0, 1.0]},
        ])

        result = conn.execute("""
            SELECT name, _distance
            FROM lance_vector_search(
                'lance_ns.main.vec_cross',
                'vec',
                [1.0, 0.0, 0.0]::FLOAT[],
                k := 1
            )
        """).fetchall()
        assert result[0][0] == "close"


class TestInsertPerformance:
    """Benchmark: INSERT 100 rows with 384-dim vectors via DuckDB SQL."""

    def test_insert_100_rows_384dim(self, conn):
        """Insert 100 rows with 384-dim vectors. Track timing."""
        import random

        random.seed(42)
        dim = 384

        # Create table with fixed-size vector column
        conn.execute(f"""
            CREATE TABLE lance_ns.main.perf_test (
                _id VARCHAR,
                title VARCHAR,
                _vec_content FLOAT[{dim}]
            )
        """)

        # Build INSERT statement with 100 rows of 384-dim vectors
        rows = []
        for i in range(100):
            row_id = str(uuid.uuid4())
            vec = [random.random() for _ in range(dim)]
            vec_str = "[" + ",".join(f"{v:.6f}" for v in vec) + "]::FLOAT[]"
            rows.append(f"('{row_id}', 'title_{i}', {vec_str})")

        values_str = ",\n".join(rows)
        sql = f"INSERT INTO lance_ns.main.perf_test VALUES {values_str}"

        start = time.perf_counter()
        conn.execute(sql)
        elapsed_duckdb = time.perf_counter() - start

        # Verify all rows inserted
        count = conn.execute(
            "SELECT COUNT(*) FROM lance_ns.main.perf_test"
        ).fetchone()[0]
        assert count == 100

        # Report timing (for manual review — not a pass/fail threshold)
        print(f"\nDuckDB INSERT 100 rows × 384-dim: {elapsed_duckdb*1000:.1f}ms")

        # Soft threshold: should complete in under 5 seconds
        # If this fails, flag for LanceDB tbl.add() fallback
        assert elapsed_duckdb < 5.0, (
            f"DuckDB INSERT too slow ({elapsed_duckdb:.1f}s). "
            "Consider LanceDB tbl.add() fallback for inserts."
        )

    def test_insert_perf_vs_lancedb(self, lance_dir, conn):
        """Compare DuckDB INSERT vs LanceDB tbl.add() for 100 rows × 384-dim."""
        import random

        random.seed(42)
        dim = 384

        # -- LanceDB path --
        db = lancedb.connect(lance_dir)
        schema = pa.schema([
            pa.field("_id", pa.utf8()),
            pa.field("title", pa.utf8()),
            pa.field("_vec", pa.list_(pa.float32(), dim)),
        ])
        db.create_table("perf_lance", schema=schema)
        tbl = db.open_table("perf_lance")

        lance_rows = []
        for i in range(100):
            lance_rows.append({
                "_id": str(uuid.uuid4()),
                "title": f"title_{i}",
                "_vec": [random.random() for _ in range(dim)],
            })

        start = time.perf_counter()
        tbl.add(lance_rows)
        elapsed_lance = time.perf_counter() - start

        # -- DuckDB path --
        conn.execute(f"""
            CREATE TABLE lance_ns.main.perf_duck (
                _id VARCHAR,
                title VARCHAR,
                _vec FLOAT[{dim}]
            )
        """)

        random.seed(42)  # Reset for same data
        rows = []
        for i in range(100):
            row_id = str(uuid.uuid4())
            vec = [random.random() for _ in range(dim)]
            vec_str = "[" + ",".join(f"{v:.6f}" for v in vec) + "]::FLOAT[]"
            rows.append(f"('{row_id}', 'title_{i}', {vec_str})")

        values_str = ",\n".join(rows)
        sql = f"INSERT INTO lance_ns.main.perf_duck VALUES {values_str}"

        start = time.perf_counter()
        conn.execute(sql)
        elapsed_duck = time.perf_counter() - start

        ratio = elapsed_duck / elapsed_lance if elapsed_lance > 0 else float("inf")
        print(f"\nLanceDB tbl.add(): {elapsed_lance*1000:.1f}ms")
        print(f"DuckDB INSERT:     {elapsed_duck*1000:.1f}ms")
        print(f"Ratio (DuckDB/LanceDB): {ratio:.1f}x")

        # If DuckDB is >3x slower, flag it (but don't fail — just info)
        if ratio > 3.0:
            print("WARNING: DuckDB INSERT significantly slower. Consider LanceDB fallback.")
