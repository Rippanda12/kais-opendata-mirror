"""Assemble the static site: site/index.html + data/*.parquet + national tiles + data/meta.json.

Per oblast: units/buildings/search Parquet are copied as they are (the page picks the oblast from
the EKATTE code in a cadastral id). Map tiles of all oblasts are joined into one buildings.pmtiles
and one parcels.pmtiles, so the map works across the whole country.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import duckdb

from . import ekatte

PAGE = Path(__file__).resolve().parent.parent / "site" / "index.html"


def _join_tiles(parts: list[Path], target: Path) -> bool:
    if not parts:
        target.unlink(missing_ok=True)
        return False
    if len(parts) == 1:
        shutil.copy(parts[0], target)
    else:
        subprocess.run(["tile-join", "-o", str(target), "--force", "--quiet", *map(str, parts)], check=True)
    return True


def build(out: Path, site: Path) -> dict:
    data = site / "data"
    data.mkdir(parents=True, exist_ok=True)
    shutil.copy(PAGE, site / "index.html")
    for old in data.glob("*-???.pmtiles"):  # per-oblast tiles from older builds
        old.unlink()
    codes = sorted(p.name[len("units-"):-len(".parquet")] for p in out.glob("units-*.parquet"))
    names = {s.oblast: s.oblast_name for s in ekatte.load(ekatte.download(ekatte.default_year()))}
    oblasts, dates, by_ekatte = [], [], {}
    for code in codes:
        for kind in ("units", "buildings", "search"):
            shutil.copy(out / f"{kind}-{code}.parquet", data / f"{kind}-{code}.parquet")
        near = out / f"neighbours-{code}.parquet"
        for extra in (near, out / f"parcels-{code}.parquet"):
            if extra.exists():
                shutil.copy(extra, data / extra.name)
        d, n = duckdb.sql(
            f"SELECT max(source_date)::VARCHAR, count(*) FROM read_parquet('{out / f'units-{code}.parquet'}')"
        ).fetchall()[0]
        lon, lat = duckdb.sql(
            f"SELECT median(lon), median(lat) FROM read_parquet('{out / f'buildings-{code}.parquet'}')"
        ).fetchall()[0]
        for (e,) in duckdb.sql(
            f"SELECT DISTINCT ekatte FROM read_parquet('{out / f'search-{code}.parquet'}') WHERE ekatte IS NOT NULL"
        ).fetchall():
            by_ekatte[e] = code
        dates.append(d)
        oblasts.append({"code": code, "name": names.get(code, code), "units": n,
                        "center": [round(lon, 5), round(lat, 5)] if lon is not None else None,
                        "parcels": (out / f"parcels-{code}.pmtiles").exists(), "neighbours": near.exists()})
    has_map = _join_tiles([out / f"buildings-{c}.pmtiles" for c in codes
                           if (out / f"buildings-{c}.pmtiles").exists()], data / "buildings.pmtiles")
    has_parcels = _join_tiles([out / f"parcels-{c}.pmtiles" for c in codes
                               if (out / f"parcels-{c}.pmtiles").exists()], data / "parcels.pmtiles")
    protected = out / "protected.pmtiles"
    if protected.exists():
        shutil.copy(protected, data / protected.name)
    dates = [d for d in dates if d]  # an oblast without units has no export date
    meta = {"source_date": max(dates) if dates else None, "oblasts": oblasts,
            "map": has_map, "parcels": has_parcels, "protected": protected.exists(), "ekatte": by_ekatte}
    (data / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
    return {k: v for k, v in meta.items() if k != "ekatte"} | {"settlements": len(by_ekatte)}
