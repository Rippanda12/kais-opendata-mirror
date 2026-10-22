"""Assemble the static site: site/index.html + data/*.parquet + data/meta.json."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb

from . import ekatte

PAGE = Path(__file__).resolve().parent.parent / "site" / "index.html"


def build(out: Path, site: Path) -> dict:
    data = site / "data"
    data.mkdir(parents=True, exist_ok=True)
    shutil.copy(PAGE, site / "index.html")
    codes = sorted(p.name[len("units-"):-len(".parquet")] for p in out.glob("units-*.parquet"))
    names = {s.oblast: s.oblast_name for s in ekatte.load(ekatte.download(ekatte.default_year()))}
    oblasts, dates = [], []
    for code in codes:
        for kind in ("units", "buildings", "search"):
            shutil.copy(out / f"{kind}-{code}.parquet", data / f"{kind}-{code}.parquet")
        d, n = duckdb.sql(
            f"SELECT max(source_date)::VARCHAR, count(*) FROM read_parquet('{out / f'units-{code}.parquet'}')"
        ).fetchall()[0]
        dates.append(d)
        oblasts.append({"code": code, "name": names.get(code, code), "units": n})
    meta = {"source_date": max(dates) if dates else None, "oblasts": oblasts}
    (data / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta
