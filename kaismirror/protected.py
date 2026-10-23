"""Protected areas in Bulgaria from the European Environment Agency (open data, reuse with attribution):
Natura 2000 sites (Habitats and Birds Directives) and nationally designated areas (parks, reserves,
protected sites, natural landmarks) and areas of potential significant flood risk (Floods Directive).
Written as one protected.pmtiles with layers "natura", "national" and "floods".
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from .ekatte import USER_AGENT

EEA = "https://bio.discomap.eea.europa.eu/arcgis/rest/services/ProtectedSites"
NATURA = f"{EEA}/Natura2000Sites/MapServer"          # layer 0: Habitats Directive, 1: Birds Directive
NATIONAL = f"{EEA}/NatDAv24_Dyna_WM/MapServer/4"     # nationally designated areas, large scale
# Areas of potential significant flood risk (EU Floods Directive, 2019 cycle): 2 = polygons, 1 = river lines.
FLOODS = "https://water.discomap.eea.europa.eu/arcgis/rest/services/FloodsDirective/Floods2019_RiskZone_WM/MapServer"
# The IUCN category says which Bulgarian category (Закон за защитените територии) a site is.
IUCN_BG = {"Ia": "резерват", "Ib": "резерват", "II": "национален парк", "III": "природна забележителност",
           "IV": "поддържан резерват", "V": "природен парк", "VI": "защитена местност"}


def _query(url: str, where: str, fields: str) -> list[dict]:
    params = urllib.parse.urlencode({
        "where": where, "outFields": fields, "outSR": 4326, "f": "geojson",
        "maxAllowableOffset": 0.00005,  # ~5 m: plenty for zones drawn over a cadastral map
    })
    req = urllib.request.Request(f"{url}/query?{params}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)["features"]


def download() -> tuple[list[dict], list[dict], list[dict]]:
    natura = []
    for layer, kind in ((0, "местообитания"), (1, "птици")):
        for f in _query(f"{NATURA}/{layer}", "MS='BG'", "SITECODE,SITENAME,SITETYPE,Area_ha"):
            p = f["properties"]
            f["properties"] = {"code": p["SITECODE"], "name": p["SITENAME"], "kind": kind,
                               "ha": round(p.get("Area_ha") or 0)}
            natura.append(f)
    national = []
    for f in _query(NATIONAL, "countryCode='BG'", "siteName,iucnCategory,siteArea,legalFoundationDate"):
        p = f["properties"]
        f["properties"] = {"name": p["siteName"], "cat": IUCN_BG.get(p.get("iucnCategory") or "", "защитена територия"),
                           "iucn": p.get("iucnCategory") or "", "ha": round(p.get("siteArea") or 0),
                           "year": p.get("legalFoundationDate")}
        national.append(f)
    floods = []
    for layer in (2, 1):
        for f in _query(f"{FLOODS}/{layer}", "countryCode='BG'", "thematicIdIdentifier,sizeValue,sizeUom"):
            p = f["properties"]
            f["properties"] = {"code": p.get("thematicIdIdentifier") or "", "size": p.get("sizeValue"),
                               "uom": p.get("sizeUom") or ""}
            floods.append(f)
    return natura, national, floods


def build_protected(out: Path, log=print) -> Path:
    if not shutil.which("tippecanoe"):
        raise SystemExit("tippecanoe not found")
    out.mkdir(parents=True, exist_ok=True)
    natura, national, floods = download()
    log(f"Natura 2000: {len(natura)} sites, national: {len(national)} areas, flood risk: {len(floods)}")
    paths = []
    for name, feats in (("natura", natura), ("national", national), ("floods", floods)):
        p = out / f".{name}.geojson"
        p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False))
        paths.append((name, p))
    target = out / "protected.pmtiles"
    cmd = ["tippecanoe", "-o", str(target), "--force", "--quiet", "-Z5", "-z14",
           "--coalesce-densest-as-needed", "--attribution", "EEA: Natura 2000, Nationally designated areas, Floods Directive"]
    for name, p in paths:
        cmd += ["-L", f"{name}:{p}"]
    subprocess.run(cmd, check=True)
    for _, p in paths:
        p.unlink()
    return target
