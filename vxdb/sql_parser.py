"""Mini SQL parser for vxdb queries. Regex + state machine, no external deps."""

import re
from dataclasses import dataclass, field


@dataclass
class NearClause:
    column: str
    text: str
    k: int


@dataclass
class FilterClause:
    column: str
    op: str
    value: object


@dataclass
class OrderClause:
    column: str
    direction: str


@dataclass
class QueryAST:
    select: list[str] | str
    table: str
    where: list[FilterClause] = field(default_factory=list)
    near: NearClause | None = None
    order_by: list[OrderClause] = field(default_factory=list)
    limit: int | None = None


_KW = re.compile(r"\b(SELECT|FROM|WHERE|ORDER\s+BY|LIMIT)\b", re.IGNORECASE)
_NEAR_RE = re.compile(
    r"NEAR\s*\(\s*(\w+)\s*,\s*'((?:[^']|'')*?)'\s*,\s*(\d+)\s*\)", re.IGNORECASE
)
_OP_RE = re.compile(r"(!=|>=|<=|>|<|=)")
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _unescape(s: str) -> str:
    return s.replace("''", "'")


def _parse_value(raw: str) -> object:
    raw = raw.strip()
    if not raw:
        raise ValueError("Empty value in filter expression")
    if raw.startswith("'"):
        if not raw.endswith("'") or len(raw) < 2:
            raise ValueError(f"Unterminated string literal: {raw}")
        return _unescape(raw[1:-1])
    if raw.upper() == "TRUE":
        return True
    if raw.upper() == "FALSE":
        return False
    if _NUM_RE.match(raw):
        return float(raw) if "." in raw else int(raw)
    raise ValueError(f"Cannot parse value: {raw!r}")


def _split_respecting_quotes(s: str, sep: str = ",") -> list[str]:
    """Split on *sep* but not inside single-quoted strings."""
    parts, cur, in_q = [], [], False
    for ch in s:
        if ch == "'" :
            in_q = not in_q
            cur.append(ch)
        elif ch == sep and not in_q:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _parse_in_values(raw: str) -> list:
    m = re.search(r"\(\s*(.*?)\s*\)", raw)
    if not m:
        raise ValueError(f"Invalid IN list: {raw!r}")
    return [_parse_value(p.strip()) for p in _split_respecting_quotes(m.group(1))]


def _find_clause_spans(sql: str) -> dict[str, str]:
    matches = [(re.sub(r"\s+", " ", m.group(1).upper()), m.start()) for m in _KW.finditer(sql)]
    if not matches or matches[0][0] != "SELECT":
        raise ValueError("Query must start with SELECT")
    spans: dict[str, str] = {}
    for i, (kw, start) in enumerate(matches):
        kw_end = start + len(kw)
        if kw == "ORDER BY":
            kw_end = start + len(re.search(r"ORDER\s+BY", sql[start:], re.IGNORECASE).group())
        end = matches[i + 1][1] if i + 1 < len(matches) else len(sql)
        spans[kw] = sql[kw_end:end].strip()
    return spans


def _check_unterminated_strings(sql: str) -> None:
    in_q = False
    for i, ch in enumerate(sql):
        if ch == "'":
            if in_q:
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    continue  # escaped ''
                in_q = False
            else:
                in_q = True
    if in_q:
        raise ValueError("Unterminated string literal in query")


def _split_and_conditions(where_str: str) -> list[str]:
    """Split WHERE content on AND, respecting quotes and parentheses."""
    parts, cur, depth, in_q, i = [], [], 0, False, 0
    while i < len(where_str):
        ch = where_str[i]
        if ch == "'" and not in_q:
            in_q = True; cur.append(ch); i += 1
        elif ch == "'" and in_q:
            cur.append(ch)
            if i + 1 < len(where_str) and where_str[i + 1] == "'":
                cur.append("'"); i += 2
            else:
                in_q = False; i += 1
        elif not in_q and ch == "(":
            depth += 1; cur.append(ch); i += 1
        elif not in_q and ch == ")":
            depth -= 1; cur.append(ch); i += 1
        elif (not in_q and depth == 0
              and where_str[i:i+3].upper() == "AND"
              and (i == 0 or not where_str[i-1].isalnum())
              and (i+3 >= len(where_str) or not where_str[i+3].isalnum())):
            parts.append("".join(cur).strip()); cur = []; i += 3
        else:
            cur.append(ch); i += 1
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_filter(cond: str) -> FilterClause:
    cond = cond.strip()
    # IN operator
    m = re.match(r"(\w+)\s+IN\s*(\(.*\))", cond, re.IGNORECASE)
    if m:
        return FilterClause(m.group(1), "IN", _parse_in_values(m.group(2)))
    # LIKE operator
    m = re.match(r"(\w+)\s+LIKE\s+(.*)", cond, re.IGNORECASE)
    if m:
        return FilterClause(m.group(1), "LIKE", _parse_value(m.group(2).strip()))
    # Standard comparison
    op_m = _OP_RE.search(cond)
    if not op_m:
        raise ValueError(f"Invalid filter expression (no valid operator): {cond!r}")
    col = cond[:op_m.start()].strip()
    if not col:
        raise ValueError(f"Missing column name in filter: {cond!r}")
    return FilterClause(col, op_m.group(1), _parse_value(cond[op_m.end():].strip()))


def _parse_select(s: str) -> list[str] | str:
    s = s.strip()
    if not s:
        raise ValueError("Empty SELECT clause")
    if s == "*":
        return "*"
    cols = [c.strip() for c in s.split(",")]
    if any(not c for c in cols):
        raise ValueError("Empty column name in SELECT")
    return cols


def _parse_order_by(s: str) -> list[OrderClause]:
    if not s.strip():
        return []
    clauses: list[OrderClause] = []
    for part in s.split(","):
        tokens = part.split()
        if not tokens:
            continue
        direction = "ASC"
        if len(tokens) > 1:
            d = tokens[1].upper()
            if d not in ("ASC", "DESC"):
                raise ValueError(f"Invalid ORDER BY direction: {tokens[1]!r}")
            direction = d
        clauses.append(OrderClause(tokens[0], direction))
    return clauses


def parse_query(sql: str) -> QueryAST:
    """Parse a SQL-ish query string into a QueryAST. Raises ValueError on invalid syntax."""
    if not sql or not sql.strip():
        raise ValueError("Empty query")
    sql = sql.strip()
    _check_unterminated_strings(sql)

    spans = _find_clause_spans(sql)
    if "SELECT" not in spans:
        raise ValueError("Query must contain SELECT")
    if "FROM" not in spans:
        raise ValueError("Query must contain FROM")

    select = _parse_select(spans["SELECT"])

    table = spans["FROM"].split()[0] if spans["FROM"] else ""
    if not table or not re.fullmatch(r"[A-Za-z0-9_]+", table):
        raise ValueError(f"Invalid table name: {table!r}")

    where_filters: list[FilterClause] = []
    near: NearClause | None = None
    if "WHERE" in spans:
        for cond in _split_and_conditions(spans["WHERE"]):
            if not cond:
                continue
            m = _NEAR_RE.match(cond)
            if m:
                if near is not None:
                    raise ValueError("Multiple NEAR() clauses are not allowed")
                near = NearClause(m.group(1), _unescape(m.group(2)), int(m.group(3)))
            else:
                where_filters.append(_parse_filter(cond))

    order_by = _parse_order_by(spans["ORDER BY"]) if "ORDER BY" in spans else []

    limit: int | None = None
    if "LIMIT" in spans:
        raw = spans["LIMIT"].strip()
        if not raw.isdigit():
            raise ValueError(f"LIMIT must be a positive integer, got: {raw!r}")
        limit = int(raw)

    for oc in order_by:
        if oc.column == "_similarity" and near is None:
            raise ValueError("Cannot ORDER BY _similarity without a NEAR() clause in WHERE")

    return QueryAST(select=select, table=table, where=where_filters,
                    near=near, order_by=order_by, limit=limit)
