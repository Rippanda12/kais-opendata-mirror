"""Polite downloader for the per-settlement KAIS open-data zips.

Uses only the public GET link the Отворени данни page itself offers. Low concurrency, retries
with backoff on transient errors, 404 = the settlement has no such file (skipped). A 403 means
the site's firewall refused us (it rate-limits: ~25 requests at 2/s, and ~30 at one per 2.5 s, were refused; one per 5 s ran 200+): we stop the
whole run instead of trying to get around it, unless the reply carries Retry-After: then we
wait exactly that long. Every 403/429 reply is saved as blocked-<time>.txt for inspection. Files already on disk are kept, so a later run
resumes where this one stopped.
"""
from __future__ import annotations

import email.utils
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path
from threading import Event, Lock

from .ekatte import USER_AGENT, Settlement

BASE = "https://kais.cadastre.bg/bg/OpenData/Download?path="
FILES = {"buildings": "сгради.zip", "units": "самостоятелни обекти.zip"}


class Forbidden(RuntimeError):
    pass


def url_for(s: Settlement, filename: str) -> str:
    return BASE + urllib.parse.quote(f"{s.folder}/{filename}")


MAX_RETRY_AFTER = 1800  # seconds; a longer requested pause stops the run instead


def _retry_after(value: str | None) -> float | None:
    """Retry-After is either seconds or an HTTP date."""
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _report_block(dest: Path, e: urllib.error.HTTPError, wait: float | None) -> None:
    """Keep what the firewall said (status, headers, start of the page) next to the manifest."""
    try:
        body = e.read(4000).decode("utf-8", errors="replace")
    except Exception:
        body = ""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = dest.parent.parent / f"blocked-{stamp}.txt"
    headers = "".join(f"{k}: {v}\n" for k, v in e.headers.items())
    report.write_text(f"HTTP {e.code} for {e.url}\nRetry-After parsed: {wait}\n\n{headers}\n{body}")
    print(f"HTTP {e.code}; Retry-After={e.headers.get('Retry-After')!r}; details in {report}", flush=True)


def _get(url: str, dest: Path, retries: int, stop: Event) -> dict:
    for attempt in range(retries + 1):
        if stop.is_set():
            return {"status": "aborted"}
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                if "zip" not in r.headers.get("Content-Type", ""):
                    return {"status": "missing"}
                tmp = dest.with_suffix(".part")
                with open(tmp, "wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                tmp.rename(dest)
                return {
                    "status": "ok",
                    "bytes": dest.stat().st_size,
                    "last_modified": r.headers.get("Last-Modified"),
                }
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"status": "missing"}
            if e.code in (403, 429):
                wait = _retry_after(e.headers.get("Retry-After"))
                _report_block(dest, e, wait)
                if wait is not None and wait <= MAX_RETRY_AFTER and attempt < retries:
                    time.sleep(wait + 1)  # the server said when to come back
                    continue
                stop.set()
                raise Forbidden(url) from e
            err = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = str(e)
        if attempt < retries:
            time.sleep(min(60, 2 ** attempt * 5) + random.random())
    return {"status": "error", "error": err}


def fetch(
    settlements: list[Settlement],
    out: Path,
    kinds=("buildings", "units"),
    workers: int = 1,
    delay: float = 5.0,
    retries: int = 4,
    log=lambda m: print(m, flush=True),
) -> list[dict]:
    """Download into out/<oblast>/<ekatte>/<kind>.zip; returns a manifest (also written to out)."""
    stop = Event()
    jobs = [(s, k) for s in settlements for k in kinds]
    # Entries of settlements outside this run (e.g. villages during a --only towns run) are kept.
    this_run = {(s.ekatte, k) for s, k in jobs}
    previous = []
    for oblast in {s.oblast for s in settlements}:
        prev = out / oblast / "manifest.json"
        if prev.exists():
            previous += json.loads(prev.read_text())
    known_missing = {(m["ekatte"], m["kind"]) for m in previous if m["status"] == "missing"}
    kept = [m for m in previous if (m["ekatte"], m["kind"]) not in this_run]

    done = []
    lock = Lock()
    started = time.monotonic()

    def save():
        for oblast in {s.oblast for s in settlements}:
            rows = [m for m in kept + done if m["settlement"]["oblast"] == oblast]
            rows.sort(key=lambda m: (m["settlement"]["obshtina"], m["ekatte"], m["kind"]))
            (out / oblast).mkdir(parents=True, exist_ok=True)
            tmp = out / oblast / "manifest.json.tmp"
            tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
            tmp.replace(out / oblast / "manifest.json")  # atomic: a kill never leaves half a file

    def one(job):
        s, kind = job
        t0 = time.monotonic()
        dest = out / s.oblast / s.ekatte / f"{kind}.zip"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            res = {"status": "ok", "bytes": dest.stat().st_size, "cached": True}
        elif (s.ekatte, kind) in known_missing:
            res = {"status": "missing", "cached": True}
        else:
            try:
                res = _get(url_for(s, FILES[kind]), dest, retries, stop)
            except Forbidden:
                res = {"status": "forbidden"}
            if res["status"] != "aborted":
                time.sleep(delay)
        res |= {"ekatte": s.ekatte, "kind": kind, "settlement": asdict(s)}
        with lock:
            done.append(res)
            n = len(done)
            if not res.get("cached") or n % 50 == 0:
                save()
        size = f"{res['bytes'] / 1e6:8.2f} MB" if "bytes" in res else " " * 11
        note = " (cached)" if res.get("cached") else ""
        log(f"[{n}/{len(jobs)} {time.monotonic() - started:6.0f}s] {res['status']:9} {size} "
            f"{time.monotonic() - t0:5.1f}s  {s.folder}/{FILES[kind]}{note}")
        return res

    with ThreadPoolExecutor(workers) as pool:
        manifest = list(pool.map(one, jobs))
    done[:] = manifest
    save()
    if stop.is_set():
        raise Forbidden(next(f"{m['settlement']['oblast']} {m['ekatte']} {m['kind']}"
                             for m in manifest if m["status"] == "forbidden"))
    return manifest
