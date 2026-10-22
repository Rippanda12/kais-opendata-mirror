"""Neighbours (Съседи) of parcels and of buildings, as KAIS lists them: objects whose outlines touch.

Computed in the native CRS (metres) with a small tolerance, because adjoining outlines in the
cadastre are not always drawn to the same millimetre. Parcels are compared with parcels, buildings
with buildings.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from shapely import STRtree, make_valid, union_all
from shapely.geometry import Polygon

from .dbf import iter_records
from .tiles import _member, _rings, _signed_area

TOLERANCE_M = 0.05


def _polygons(path: Path):
    zf = zipfile.ZipFile(path, metadata_encoding="cp866")
    dbf, shp = _member(zf, ".dbf"), _member(zf, ".shp")
    if not dbf or not shp:
        return
    _, records = iter_records(zf.open(dbf))
    for rec, rings in zip(records, _rings(zf.open(shp))):
        cad = rec.get("cadnum")
        if not rings or not cad:
            continue
        outers = []
        for ring in rings:  # clockwise rings are outer; holes don't matter for touching
            if len(ring) >= 3 and (_signed_area(ring) <= 0 or not outers):
                outers.append(Polygon(ring))
        if outers:
            yield cad, make_valid(outers[0]) if len(outers) == 1 else union_all([make_valid(p) for p in outers])


def neighbours(raw: Path, oblast: str, kind: str) -> dict[str, list[str]]:
    ids, geoms = [], []
    for z in sorted((raw / oblast).glob(f"*/{kind}.zip")):
        for cad, g in _polygons(z):
            ids.append(cad)
            geoms.append(g)
    tree = STRtree(geoms)
    out: dict[str, list[str]] = {}
    for i, g in enumerate(geoms):
        near = tree.query(g.buffer(TOLERANCE_M), predicate="intersects")
        out[ids[i]] = sorted(ids[j] for j in near if j != i and ids[j] != ids[i])
    return out


def build_neighbours(raw: Path, out: Path, oblast: str, log=print) -> Path | None:
    rows = {"cadnum": [], "kind": [], "neighbours": []}
    for kind in ("parcels", "buildings"):
        if not any((raw / oblast).glob(f"*/{kind}.zip")):
            continue
        found = neighbours(raw, oblast, kind)
        log(f"{kind}: neighbours for {len(found)} outlines")
        for cad, near in found.items():
            rows["cadnum"].append(cad)
            rows["kind"].append(kind[:-1])
            rows["neighbours"].append(near)
    if not rows["cadnum"]:
        return None
    table = pa.table(rows).sort_by("cadnum")
    target = out / f"neighbours-{oblast}.parquet"
    pq.write_table(table, target, compression="zstd", row_group_size=8192)
    return target
