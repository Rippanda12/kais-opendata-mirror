"""Settlement list from the public EKATTE register of the National Statistical Institute (NSI).

KAIS stores its open data under `област X/община Y/<вид> <име> (<ЕКАТТЕ>)/`, so the list of
settlements (not KAIS's own folder listing) tells us which paths to try.
"""
from __future__ import annotations

import io
import json
import urllib.request
import zipfile
from dataclasses import dataclass

EKATTE_URL = "https://www.nsi.bg/nrnm/ekatte/archive/json/Ekatte-{year}-json.zip/download"
USER_AGENT = "kais-opendata-mirror (+https://github.com/Rippanda12/kais-opendata-mirror)"


@dataclass(frozen=True)
class Settlement:
    ekatte: str
    kind: str  # "гр.", "с.", "ман."
    name: str
    oblast: str  # code, e.g. BGS
    oblast_name: str  # e.g. Бургас
    obshtina: str  # code, e.g. BGS04
    obshtina_name: str

    @property
    def folder(self) -> str:
        return (
            f"област {self.oblast_name}/община {self.obshtina_name}/"
            f"{self.kind} {self.name} ({self.ekatte})"
        )


def default_year() -> int:
    """NSI publishes the archive for year N at the start of N+1."""
    import datetime as dt
    return dt.date.today().year - 1


def download(year: int) -> bytes:
    req = urllib.request.Request(EKATTE_URL.format(year=year), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def load(data: bytes) -> list[Settlement]:
    zf = zipfile.ZipFile(io.BytesIO(data))
    atte = json.load(zf.open("ek_atte.json"))
    oblasts = {o["oblast"]: o["name"] for o in json.load(zf.open("ek_obl.json")) if "oblast" in o}
    obshtini = {o["obshtina"]: o["name"] for o in json.load(zf.open("ek_obst.json")) if "obshtina" in o}
    out = []
    for a in atte:
        if not a.get("oblast") or not a.get("ekatte") or a["ekatte"] == "00000":
            continue
        out.append(
            Settlement(
                ekatte=a["ekatte"],
                kind=a["t_v_m"],
                name=a["name"],
                oblast=a["oblast"],
                oblast_name=oblasts.get(a["oblast"], a["oblast"]),
                obshtina=a["obshtina"],
                obshtina_name=obshtini.get(a["obshtina"], a.get("obshtina_name", "")),
            )
        )
    return sorted(out, key=lambda s: (s.oblast, s.obshtina, s.ekatte))


def settlements(year: int, oblasts: list[str] | None = None, only: str | None = None) -> list[Settlement]:
    """All settlements, or only those in the given oblast codes (BGS, VAR, SOF, …).

    only="towns" keeps the towns („гр.“: nearly all apartments and ideal parts),
    only="villages" everything else, so a region can be fetched towns first and villages later.
    """
    items = load(download(year))
    if oblasts:
        wanted = {o.upper() for o in oblasts}
        items = [s for s in items if s.oblast in wanted]
    if only == "towns":
        items = [s for s in items if s.kind == "гр."]
    elif only == "villages":
        items = [s for s in items if s.kind != "гр."]
    return items
