"""Building outlines -> WGS84 GeoJSON lines -> PMTiles (vector tiles in one file) with tippecanoe.

KAIS ships the polygons in BGS2005 (Lambert conformal conic, the `.prj` in each zip); web maps want
WGS84. The tiles carry only what the map shows: cadastral id, building number, floors, units.
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path
from typing import Iterator

from pyproj import CRS, Transformer

from .dbf import iter_records


def _member(zf: zipfile.ZipFile, ext: str) -> str | None:
    return next((n for n in zf.namelist() if n.lower().endswith(ext)), None)


def transformer_for(zf: zipfile.ZipFile) -> Transformer:
    prj = _member(zf, ".prj")
    src = CRS.from_wkt(zf.read(prj).decode("ascii")) if prj else CRS.from_epsg(7801)
    return Transformer.from_crs(src, 4326, always_xy=True)


def _rings(f) -> Iterator[list[list[tuple[float, float]]] | None]:
    """Rings of each record of a polygon .shp stream (None for a null shape)."""
    f.read(100)
    while head := f.read(8):
        if len(head) < 8:
            return
        _, words = struct.unpack(">ii", head)
        content = f.read(words * 2)
        if struct.unpack("<i", content[:4])[0] == 0:
            yield None
            continue
        nparts, npoints = struct.unpack("<ii", content[36:44])
        parts = list(struct.unpack(f"<{nparts}i", content[44 : 44 + 4 * nparts])) + [npoints]
        base = 44 + 4 * nparts
        pts = struct.unpack(f"<{2 * npoints}d", content[base : base + 16 * npoints])
        yield [[(pts[2 * i], pts[2 * i + 1]) for i in range(a, b)] for a, b in zip(parts, parts[1:])]


def _signed_area(ring) -> float:
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:]))


def _geometry(rings, tr: Transformer) -> dict:
    """Shapefile: clockwise rings are outer, counter-clockwise ones are holes of the last outer."""
    polys = []
    for ring in rings:
        xs, ys = tr.transform([p[0] for p in ring], [p[1] for p in ring])
        coords = [[round(x, 7), round(y, 7)] for x, y in zip(xs, ys)]
        if _signed_area(ring) <= 0 or not polys:
            polys.append([coords])
        else:
            polys[-1].append(coords)
    if len(polys) == 1:
        return {"type": "Polygon", "coordinates": polys[0]}
    return {"type": "MultiPolygon", "coordinates": polys}


def features(path: Path) -> Iterator[dict]:
    zf = zipfile.ZipFile(path, metadata_encoding="cp866")
    dbf, shp = _member(zf, ".dbf"), _member(zf, ".shp")
    if not dbf or not shp:
        return
    tr = transformer_for(zf)
    _, records = iter_records(zf.open(dbf))
    for rec, rings in zip(records, _rings(zf.open(shp))):
        if not rings:
            continue
        cad = rec.get("cadnum") or ""
        yield {
            "type": "Feature",
            "properties": {
                "c": cad,
                "n": cad.rsplit(".", 1)[-1],
                "f": rec.get("flrcount") or 0,
                "u": rec.get("appcount") or 0,
            },
            "geometry": _geometry(rings, tr),
        }


def build_tiles(raw: Path, out: Path, oblast: str, log=print) -> Path:
    if not shutil.which("tippecanoe"):
        raise SystemExit("tippecanoe not found (brew install tippecanoe / build felt/tippecanoe)")
    out.mkdir(parents=True, exist_ok=True)
    seq = out / f".buildings-{oblast}.geojsonl"
    n = 0
    with open(seq, "w") as f:
        for z in sorted((raw / oblast).glob("*/buildings.zip")):
            for feat in features(z):
                f.write(json.dumps(feat, ensure_ascii=False, separators=(",", ":")) + "\n")
                n += 1
    log(f"{n} building outlines")
    target = out / f"buildings-{oblast}.pmtiles"
    subprocess.run(
        ["tippecanoe", "-o", str(target), "--force", "--quiet", "-l", "buildings",
         "-Z12", "-z16", "--drop-smallest-as-needed", "--extend-zooms-if-still-dropping",
         "--attribution", "АГКК / КАИС — Отворени данни", str(seq)],
        check=True,
    )
    seq.unlink()
    return target
