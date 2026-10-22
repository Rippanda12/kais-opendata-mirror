"""DuckDB queries over the published Parquet (local folder or https base URL)."""
from __future__ import annotations

import re

import duckdb


def _result(rel):
    cols = [d[0] for d in rel.description]
    return cols, rel.fetchall()

UNIT_COLS = ("cadnum, entrance, flrnum, appnum, apptype, apparea, adjpart_sqm, adjpart_pct, "
             "adjpart, immaddr")


def _src(data: str, name: str, oblast: str) -> str:
    return f"{data.rstrip('/')}/{name}-{oblast.upper()}.parquet"


def by_cadnum(data: str, oblast: str, cadnum: str):
    cadnum = cadnum.strip()
    if not re.fullmatch(r"\d{5}(\.\d+){1,4}", cadnum):
        raise SystemExit("cadnum looks like 07079.602.802.1 (building) or 07079.602.802.1.5 (unit)")
    parts = cadnum.count(".") + 1
    where = "building = $1" if parts == 4 else "cadnum = $1" if parts == 5 else "starts_with(cadnum, $1 || '.')"
    con = duckdb.connect()
    return _result(con.execute(
        f"SELECT {UNIT_COLS} FROM read_parquet('{_src(data, 'units', oblast)}') WHERE {where} "
        "ORDER BY cadapp", [cadnum]
    ))


def by_address(data: str, oblast: str, text: str, limit: int = 50):
    words = [w for w in re.split(r"[\s,]+", text.lower()) if w]
    if not words:
        raise SystemExit("empty address")
    where = " AND ".join(f"q LIKE '%' || ${i + 1} || '%'" for i in range(len(words)))
    con = duckdb.connect()
    return _result(con.execute(
        f"SELECT building, address, blocks, entrances, units, units_with_adjpart "
        f"FROM read_parquet('{_src(data, 'search', oblast)}') WHERE {where} "
        f"ORDER BY address LIMIT {limit}", words
    ))
