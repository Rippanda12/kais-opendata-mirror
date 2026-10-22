# kais-opendata-mirror

An unofficial mirror of the open data from the Bulgarian cadastre (Агенция по геодезия, картография и
кадастър, **АГКК**, portal **КАИС**). It covers buildings (сгради) and independent units
(самостоятелни обекти: apartments, shops, garages, cellars), with their areas, **ideal parts of the
common areas** (идеални части), addresses, entrance, floor and cadastral identifiers. The data is
published as Parquet and can be queried from a browser, DuckDB, Python or any HTTP client.

- **Query page:** https://rippanda12.github.io/kais-opendata-mirror/ (e.g. `?oblast=BGS&cadnum=07079.602.802.1`)
- **Files:** [latest release](https://github.com/Rippanda12/kais-opendata-mirror/releases/latest)
- **Coverage:** област Бургас (BGS) for now; see [the country plan](#rest-of-the-country).

## Source and license

The data comes from **АГКК, КАИС портал → „Отворени данни“**
(https://kais.cadastre.bg/bg/OpenData). KAIS publishes one folder per settlement, and anyone can
download the zips without registering.

Reuse terms, as checked in October 2026:

- The Отворени данни page shows no license.
- The portal's [Общи условия](https://kais.cadastre.bg/bg/Terms) apply to registered *clients*
  using the paid *services and справки* (Тарифа № 14). Clause II.3 limits data from those services
  to the client's own needs. The open-data files are free, need no registration, and are not one of
  those services.
- The datasets are not on data.egov.bg, so no portal license applies to them.
- Under the Bulgarian Access to Public Information Act (ЗДОИ, chapter IV), information that public
  bodies publish as open data may be reused freely for any purpose.

This mirror therefore republishes the files with attribution. It does not claim to be official. As
АГКК says of its own data: **no guarantee of completeness or accuracy.** For legal purposes, use a
certified справка from КАИС. The mirror never logs in to KAIS.

**Left out on purpose:** the ownership files (собственост ПИ / сгради / СОС). They contain company
names, stable hashed identifiers of private persons, and notary deed numbers. Land parcels
(поземлени имоти) and geometry are also left out, to keep the mirror small; see the plan below.

The code is MIT licensed. The data belongs to АГКК; please credit „Източник: АГКК, КАИС — Отворени данни“.

## Files

There is one set of files per oblast (the three-letter EKATTE code: BGS, VAR, SOF, …). Each set is
attached to every release and also served by GitHub Pages under `/data/`.

| File | Contents |
|---|---|
| `units-<OBL>.parquet` | every independent unit, sorted by building |
| `buildings-<OBL>.parquet` | every building, sorted by `cadnum` |
| `search-<OBL>.parquet` | one row per building: address text, blocks, entrances, unit count (for address search) |
| `manifest-<OBL>.json` | which settlements had which file (release only) |

`units` columns are the KAIS attributes in lower case, plus a few derived ones:

| column | meaning |
|---|---|
| `cadnum` | unit id `EKATTE.region.parcel.building.unit`, e.g. `07079.602.802.1.5` |
| `building` | derived: the first four parts, e.g. `07079.602.802.1` |
| `cadreg`, `cadimm`, `cadbuild`, `cadapp` | the parts of `cadnum` as numbers |
| `ekatte`, `ekattefn` | settlement code and name |
| `immaddr` | full address as in the register |
| `quarname`, `regname`, `strename`, `strnum`, `blocknum`, `entrance` | address parts (often only partly filled; `immaddr` is the reliable one) |
| `flrnum`, `addrflr`, `flrcount` | floor, address floor, floors in the building |
| `appnum`, `appcode`, `apptype` | unit number, type code, type |
| `apparea` | area of the unit per the register, m² |
| `area`, `perim` | area/perimeter of the polygon, m |
| `adjpart` | **raw text** of the ideal parts, e.g. `13,09 кв.м (2,89%) идеални части от общите части на сградата` |
| `adjpart_sqm`, `adjpart_pct` | derived: m² and % parsed from `adjpart`, empty when the text doesn't clearly state them |
| `propcode`, `proptype` | kind of property (частна, държавна, общинска …); no owners |
| `validate` | the order that approved the cadastral map |
| `source_date` | date of the KAIS export |

### About `adjpart`

The register's text is free-form and sometimes mistyped. The parser:

- takes the percentage before `%`;
- takes m² only when the text ties it to the ideal parts (`… кв.м (x%) …`, `… кв.м ид.ч.`,
  `x% (… кв.м)`, `x% … = … м2`), so `изба 3,00 кв.м` is not mistaken for them;
- fixes only unambiguous slips (`0994%` → 0.994, a Cyrillic `о.911%`, `0 ,970%`);
- leaves any percentage above 100 empty.

In Burgas city, 57,687 of 107,571 units with text get a percentage. **The percentages of one
building do not always add up to 100 %.** Cellars and attics often carry their own share next to
the apartment's, and some buildings were entered unit by unit over many years. Treat the numbers as
what the register says, not as a checked distribution.

## Query

### In the browser

On the [query page](https://rippanda12.github.io/kais-opendata-mirror/), pick the oblast and enter
a building id (`07079.602.802.1`) or a unit id, or search an address
(`славейков бл. 129`, `изгрев бл. 35 вх. 2`). The page runs DuckDB-wasm and reads only the parts
of the Parquet it needs, using HTTP range requests against GitHub Pages. There is no server.

### DuckDB / Python (server side)

Release assets support range requests, so DuckDB reads them directly. Browsers can't use these URLs
because of CORS; use the Pages URLs `https://rippanda12.github.io/kais-opendata-mirror/data/…` there.

```sql
-- duckdb
INSTALL httpfs; LOAD httpfs;
SELECT cadnum, entrance, flrnum, appnum, apparea, adjpart_sqm, adjpart_pct, adjpart
FROM 'https://github.com/Rippanda12/kais-opendata-mirror/releases/latest/download/units-BGS.parquet'
WHERE building = '07079.602.802.1' ORDER BY cadapp;
```

```bash
pip install -e .
python -m kaismirror query --data https://rippanda12.github.io/kais-opendata-mirror/data --cadnum 07079.602.802.1
python -m kaismirror query --data https://rippanda12.github.io/kais-opendata-mirror/data --address "славейков бл. 129"
```

The EKATTE code in the first five digits of `cadnum` tells you the settlement. Map it to an oblast
with the NSI register, or check the `ekatte` column of `search-<OBL>.parquet`.

## Build it yourself

```bash
python -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/python -m kaismirror settlements BGS     # folders that will be tried
.venv/bin/python -m kaismirror fetch BGS           # → data/raw/BGS/<ekatte>/{buildings,units}.zip
.venv/bin/python -m kaismirror convert BGS         # → data/out/*.parquet   (--geometry adds bbox centres)
.venv/bin/python -m kaismirror site                # → data/site (page + Parquet + meta.json)
```

How it works:

- **Settlement list:** comes from the NSI's public EKATTE register
  (`nsi.bg/nrnm/ekatte/archive`), not from KAIS's folder listing. The listing is a form POST, and
  the mirror doesn't script it.
- **Downloads:** go through the public link the Отворени данни page uses:
  `https://kais.cadastre.bg/bg/OpenData/Download?path=<област X/община Y/гр. Z (EKATTE)/сгради.zip>`.
  A missing file returns 404 and is skipped.
- **Politeness:** one request at a time, 5 s apart, with retries and backoff. KAIS's firewall
  rate-limits: about 25 requests at 2/s got a 403. **On a 403 the run stops.** It keeps what it has,
  and the next run resumes, skipping files already downloaded or known to be missing.
- **Zips:** member names are cp866. The `.dbf` attribute tables are UTF-8, as the `.cpg` says. They
  are read straight from the zip; Burgas's 730 MB units table converts in seconds.

### Monthly refresh

`.github/workflows/refresh.yml` runs on the 25th of each month (KAIS re-exports around the 20th).
It fetches the oblasts in `vars.OBLASTS` (default `BGS`), converts them, publishes a release
`data-<export date>` and redeploys Pages. Raw zips are cached per month, so a run cut short by a 403
resumes when re-run. Use *Run workflow → skip_fetch* to redeploy Pages from the latest release
without touching KAIS.

## Rest of the country

| | Burgas oblast (measured) | Whole country (estimate) |
|---|---|---|
| Settlements tried | 249 | 5,256 |
| Requests per refresh (2 files each) | ~500 | ~10,500 |
| Time at 5 s/request | ~45 min | ~15 h |
| Zips downloaded (buildings + units) | see release manifest | ~0.6–1 GB |
| Parquet published | ~6 MB | ~120–200 MB |

Plan:

1. **Request time is the limit, not storage.** ~15 h is more than a single Actions job allows
   (6 h). Run the oblasts as a matrix with `max-parallel: 1`: one job per oblast, sequential, each
   well under 6 h. That keeps one request at a time against KAIS. A final job collects the
   artifacts and publishes one release.
2. **Skip what can't exist.** Many villages have no cadastral map yet, and each of them costs two
   404s every month. Keep the previous month's manifest and re-check missing settlements only
   quarterly.
3. **Sofia city** (`SOF`, Столична община) may use different folder names. Check the first run's
   404s against the KAIS page by hand and add overrides in `ekatte.py` if needed.
4. **Hosting:** the Parquet stays far below GitHub Pages' 1 GB site limit and the 2 GB per release
   asset limit. If the search files grow, split `search` per municipality.
5. **Optional extras:** `--geometry` (bbox centres in the native CRS, BGS2005 / cadastral). Full
   polygons as GeoParquet and the поземлени имоти file can follow if anyone needs them. The
   ownership files stay out.
6. **If KAIS blocks GitHub's runners:** a run fails at the first 403 and nothing is published that
   month. Ask АГКК (oss_kais@cadastre.bg) for an allowance, or run the fetch from a home machine
   and upload with `gh release upload`.
