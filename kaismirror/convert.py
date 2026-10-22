"""Turn downloaded zips into one Parquet file per oblast and dataset.

Output (in out/):
  units-<OBL>.parquet      every independent unit (самостоятелен обект), sorted by building
  buildings-<OBL>.parquet  every building (сграда), sorted by cadnum
  search-<OBL>.parquet     one row per building with its address, for address search

Sorting by cadastral number plus small row groups lets DuckDB (also DuckDB-wasm in the browser)
read only the few kilobytes it needs for one building over HTTP range requests.
"""
from __future__ import annotations

import datetime as dt
import re
import struct
import zipfile
from pathlib import Path
from typing import Iterator

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .dbf import iter_records
from .tiles import transformer_for

ROW_GROUP = 8192

_N = r"(\d+(?:[.,]\d+)?)"
_M2 = r"(?:кв\.?\s*м\.?|м2|m2)"
_PCT = re.compile(_N + r"\s*%")
# area of the ideal parts: "12,40 кв.м (2,15%) идеални…", "4,22 кв.м ид.ч.", "2,564% (15,13кв.м.)",
# "2.526% ид.ч. … = 8.28м2". A bare "изба 3,00 кв.м." is the cellar, not the ideal parts.
_SQM = [
    re.compile(_N + r"\s*" + _M2 + r"\s*\(?\s*" + _N + r"\s*%"),  # sqm (pct%)
    re.compile(_N + r"\s*" + _M2 + r"\s*(?:идеални|ид\.)", re.IGNORECASE),
    re.compile(r"%\s*\(\s*" + _N + r"\s*" + _M2),
    re.compile(r"%[^=;]{0,40}=\s*" + _N + r"\s*" + _M2),
]


def _clean(text: str) -> str:
    """Fix typing slips that are unambiguous: Cyrillic о for 0, '0 ,970', '0994%' for 0,994%."""
    text = re.sub(r"(?<![\w.,])[оО](?=[.,]\d)", "0", text)
    text = re.sub(r"(\d)\s+([.,])\s*(\d)", r"\1\2\3", text)
    return re.sub(r"(?<![\d.,])0(\d+)\s*%", r"0.\1%", text)


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def parse_adjpart(text: str | None) -> tuple[float | None, float | None]:
    """'12,40 кв.м (2,15%) идеални части…' or '1.129% (7.14кв.м.) …' -> (sqm, percent).

    Only values the text clearly states as ideal parts; a percentage above 100 is a typo in the
    register and gives None (the raw text stays in `adjpart`).
    """
    if not text:
        return None, None
    text = _clean(text)
    m = _PCT.search(text)
    pct = _num(m.group(1)) if m else None
    if pct is not None and pct > 100:
        pct = None
    sqm = None
    for rx in _SQM:
        if m := rx.search(text):
            sqm = _num(m.group(1))
            break
    return sqm, pct


def _members(zf: zipfile.ZipFile, ext: str) -> str | None:
    return next((n for n in zf.namelist() if n.lower().endswith(ext)), None)


def _centroids(f) -> Iterator[tuple[float | None, float | None]]:
    """Bounding-box centres of the shapes in a .shp stream, in the file's own CRS (.prj)."""
    f.read(100)
    while head := f.read(8):
        if len(head) < 8:
            return
        _, words = struct.unpack(">ii", head)
        content = f.read(words * 2)
        if struct.unpack("<i", content[:4])[0] == 0:
            yield None, None
            continue
        xmin, ymin, xmax, ymax = struct.unpack("<4d", content[4:36])
        yield (xmin + xmax) / 2, (ymin + ymax) / 2


UNIT_FIELDS = [
    ("cadnum", pa.string()), ("cadreg", pa.int64()), ("cadimm", pa.int64()),
    ("cadbuild", pa.int64()), ("cadapp", pa.int64()), ("ekatte", pa.string()),
    ("ekattefn", pa.string()), ("immaddr", pa.string()), ("quarname", pa.string()),
    ("regname", pa.string()), ("strename", pa.string()), ("strnum", pa.string()),
    ("blocknum", pa.string()), ("entrance", pa.string()), ("flrnum", pa.string()),
    ("addrflr", pa.string()), ("flrcount", pa.int64()), ("appnum", pa.string()),
    ("appcode", pa.string()), ("apptype", pa.string()), ("apparea", pa.float64()),
    ("AREA", pa.float64()), ("PERIM", pa.float64()), ("adjpart", pa.string()),
    ("propcode", pa.string()), ("proptype", pa.string()), ("validate", pa.string()),
]
BUILDING_FIELDS = [
    ("cadnum", pa.string()), ("cadreg", pa.int64()), ("cadimm", pa.int64()),
    ("cadbuild", pa.int64()), ("ekatte", pa.string()), ("ekattefn", pa.string()),
    ("immaddr", pa.string()), ("quarname", pa.string()), ("regname", pa.string()),
    ("strename", pa.string()), ("strnum", pa.string()), ("funccode", pa.string()),
    ("functype", pa.string()), ("flrcount", pa.int64()), ("appcount", pa.int64()),
    ("AREA", pa.float64()), ("PERIM", pa.float64()), ("propcode", pa.string()),
    ("proptype", pa.string()), ("oldident", pa.string()), ("validate", pa.string()),
]


def _schema(fields, kind: str, geometry: bool) -> pa.Schema:
    cols = [(n.lower(), t) for n, t in fields]
    if kind == "units":
        cols += [("building", pa.string()), ("adjpart_sqm", pa.float64()), ("adjpart_pct", pa.float64())]
    cols.append(("source_date", pa.date32()))
    if geometry or kind == "buildings":
        cols += [("lon", pa.float64()), ("lat", pa.float64())]
    return pa.schema(cols)


def _rows(path: Path, kind: str, fields, geometry: bool) -> Iterator[dict]:
    zf = zipfile.ZipFile(path, metadata_encoding="cp866")
    dbf_name = _members(zf, ".dbf")
    if not dbf_name:
        return
    source_date = dt.date(*zf.getinfo(dbf_name).date_time[:3])
    cpg = _members(zf, ".cpg")
    encoding = zf.read(cpg).decode("ascii").strip() if cpg else "utf-8"
    _, records = iter_records(zf.open(dbf_name), encoding=encoding if encoding.upper() != "UTF-8" else "utf-8")
    shp = _members(zf, ".shp") if (geometry or kind == "buildings") else None
    cents = _centroids(zf.open(shp)) if shp else None
    to_wgs84 = transformer_for(zf) if shp else None
    for rec in records:
        row = {n.lower(): rec.get(n) for n, _ in fields}
        for n, t in fields:
            v = row[n.lower()]
            if v is not None and t == pa.string() and not isinstance(v, str):
                row[n.lower()] = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
            elif v is not None and t == pa.int64() and not isinstance(v, int):
                row[n.lower()] = int(v) if isinstance(v, float) else None
            elif isinstance(v, float) and t == pa.float64():
                row[n.lower()] = round(v, 2 if n == "apparea" else 3)
        if kind == "units":
            cad = row["cadnum"] or ""
            row["building"] = ".".join(cad.split(".")[:4]) if cad.count(".") >= 4 else None
            row["adjpart_sqm"], row["adjpart_pct"] = parse_adjpart(row["adjpart"])
        row["source_date"] = source_date
        if cents is not None:
            x, y = next(cents, (None, None))
            row["lon"], row["lat"] = (
                [round(v, 7) for v in to_wgs84.transform(x, y)] if x is not None else (None, None)
            )
        yield row


def convert_oblast(raw: Path, out: Path, oblast: str, geometry: bool = False, log=print) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for kind, fields, order in (
        ("units", UNIT_FIELDS, "building, cadapp, cadnum"),
        ("buildings", BUILDING_FIELDS, "cadnum"),
    ):
        schema = _schema(fields, kind, geometry)
        tmp = out / f".{kind}-{oblast}.tmp.parquet"
        n = 0
        with pq.ParquetWriter(tmp, schema) as w:
            for z in sorted((raw / oblast).glob(f"*/{kind}.zip")):
                batch = []
                for row in _rows(z, kind, fields, geometry):
                    batch.append(row)
                    if len(batch) >= 50_000:
                        w.write_table(pa.Table.from_pylist(batch, schema))
                        n += len(batch)
                        batch = []
                if batch:
                    w.write_table(pa.Table.from_pylist(batch, schema))
                    n += len(batch)
                log(f"{kind:9} {z.parent.name}: {n} rows so far")
        final = out / f"{kind}-{oblast}.parquet"
        duckdb.sql(
            f"COPY (SELECT * FROM read_parquet('{tmp}') ORDER BY {order}) TO '{final}' "
            f"(FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP})"
        )
        tmp.unlink()
        counts[kind] = n
    _search_index(out, oblast)
    return counts


def _search_index(out: Path, oblast: str) -> None:
    """One row per building: address text (building's own or its first unit's without floor/apartment)."""
    units = out / f"units-{oblast}.parquet"
    buildings = out / f"buildings-{oblast}.parquet"
    duckdb.sql(f"""
        COPY (
          WITH u AS (
            SELECT building,
                   any_value(ekatte) AS ekatte, any_value(ekattefn) AS ekattefn,
                   min(regexp_replace(immaddr, ',\\s*(ет|ап)\\..*$', '')) AS unit_addr,
                   string_agg(DISTINCT entrance, ',' ORDER BY entrance) AS entrances,
                   string_agg(DISTINCT blocknum, ',' ORDER BY blocknum) AS blocks,
                   count(*) AS units,
                   count(adjpart_pct) AS units_with_adjpart
            FROM read_parquet('{units}') WHERE building IS NOT NULL GROUP BY building
          )
          SELECT coalesce(b.cadnum, u.building) AS building,
                 coalesce(b.ekatte, u.ekatte) AS ekatte,
                 coalesce(b.ekattefn, u.ekattefn) AS ekattefn,
                 coalesce(u.unit_addr, b.immaddr) AS address,
                 u.entrances, u.blocks, coalesce(u.units, 0) AS units,
                 coalesce(u.units_with_adjpart, 0) AS units_with_adjpart,
                 b.functype, b.flrcount, b.lon, b.lat,
                 lower(concat_ws(' ', coalesce(u.unit_addr, b.immaddr), b.immaddr,
                       'бл. ' || u.blocks, 'вх. ' || u.entrances)) AS q
          FROM read_parquet('{buildings}') b
          FULL OUTER JOIN u ON u.building = b.cadnum
          ORDER BY building
        ) TO '{out / f"search-{oblast}.parquet"}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP})
    """)
