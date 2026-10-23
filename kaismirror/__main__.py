"""python -m kaismirror {settlements,fetch,convert,query} …"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import ekatte


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kaismirror")
    p.add_argument("--ekatte-year", type=int, default=ekatte.default_year(),
                   help="edition of the NSI EKATTE archive (default: last year)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("settlements", help="print the settlement folders that will be tried")
    s.add_argument("oblast", nargs="*")
    s.add_argument("--only", choices=["towns", "villages"])

    ob = sub.add_parser("oblasts", help="print oblast codes as JSON (for a CI matrix)")
    ob.add_argument("--exclude", default="", help="space or comma separated codes to leave out")

    f = sub.add_parser("fetch", help="download zips")
    f.add_argument("oblast", nargs="+", help="oblast codes, e.g. BGS VAR, or ALL")
    f.add_argument("--raw", type=Path, default=Path("data/raw"))
    f.add_argument("--workers", type=int, default=1)
    f.add_argument("--delay", type=float, default=5.0, help="seconds between requests per worker")
    f.add_argument("--only", choices=["towns", "villages"], help="towns („гр.“) or the rest; default both")
    f.add_argument("--kinds", default="buildings,units", help="comma list of buildings,units,parcels")
    f.add_argument("--ekatte", help="comma list of EKATTE codes to limit the run to")

    c = sub.add_parser("convert", help="zips -> Parquet")
    c.add_argument("oblast", nargs="+")
    c.add_argument("--raw", type=Path, default=Path("data/raw"))
    c.add_argument("--out", type=Path, default=Path("data/out"))
    c.add_argument("--geometry", action="store_true", help="also give units lon/lat (buildings always have them)")

    m = sub.add_parser("tiles", help="building outlines -> PMTiles (needs tippecanoe)")
    m.add_argument("oblast", nargs="+")
    m.add_argument("--kinds", default="buildings,parcels", help="which layers (parcels only where fetched)")
    m.add_argument("--raw", type=Path, default=Path("data/raw"))
    m.add_argument("--out", type=Path, default=Path("data/out"))

    pr = sub.add_parser("protected", help="Natura 2000 and national protected areas (EEA) -> protected.pmtiles")
    pr.add_argument("--out", type=Path, default=Path("data/out"))

    w = sub.add_parser("site", help="assemble the static site (page + Parquet + meta.json)")
    w.add_argument("--out", type=Path, default=Path("data/out"))
    w.add_argument("--site", type=Path, default=Path("data/site"))

    q = sub.add_parser("query", help="query local or remote Parquet with DuckDB")
    q.add_argument("--data", default="data/out", help="folder or base URL holding the Parquet files")
    q.add_argument("--oblast", default="BGS")
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--cadnum", help="building (4 parts) or unit (5 parts) identifier")
    g.add_argument("--address", help="words that must all appear, e.g. 'чайка бл. 10 вх. А'")

    a = p.parse_args(argv)
    if a.cmd == "settlements":
        for st in ekatte.settlements(a.ekatte_year, a.oblast or None, a.only):
            print(st.folder)
    elif a.cmd == "oblasts":
        skip = {c.upper() for c in a.exclude.replace(",", " ").split()}
        print(json.dumps(sorted({s.oblast for s in ekatte.settlements(a.ekatte_year)} - skip)))
    elif a.cmd == "fetch":
        from .fetch import Forbidden, fetch
        obl = None if a.oblast == ["ALL"] else a.oblast
        try:
            places = ekatte.settlements(a.ekatte_year, obl, a.only)
            if a.ekatte:
                wanted = set(a.ekatte.split(","))
                places = [s for s in places if s.ekatte in wanted]
            man = fetch(places, a.raw, kinds=tuple(a.kinds.split(",")), workers=a.workers, delay=a.delay)
        except Forbidden as e:
            print(f"403 from KAIS, stopping: {e}", file=sys.stderr)
            return 2
        stats = {}
        for m in man:
            stats[m["status"]] = stats.get(m["status"], 0) + 1
        print(json.dumps(stats))
        return 1 if stats.get("error") else 0
    elif a.cmd == "convert":
        from .convert import convert_oblast
        for o in a.oblast:
            print(o, convert_oblast(a.raw, a.out, o.upper(), geometry=a.geometry))
    elif a.cmd == "tiles":
        from .neighbours import build_neighbours
        from .tiles import build_tiles
        for o in a.oblast:
            for kind in a.kinds.split(","):
                print(build_tiles(a.raw, a.out, o.upper(), kind))
            print(build_neighbours(a.raw, a.out, o.upper()))
    elif a.cmd == "protected":
        from .protected import build_protected
        print(build_protected(a.out))
    elif a.cmd == "site":
        from .site import build
        print(json.dumps(build(a.out, a.site), ensure_ascii=False))
    elif a.cmd == "query":
        from .query import by_address, by_cadnum
        cols, rows = by_cadnum(a.data, a.oblast, a.cadnum) if a.cadnum else by_address(a.data, a.oblast, a.address)
        if not rows:
            print("no match")
        for r in rows:
            print(" | ".join("" if v is None else str(v) for v in r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
