import email.utils
import json
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from kaismirror.ekatte import Settlement
from kaismirror.fetch import Forbidden, _retry_after, fetch


def settlement(ekatte, name, kind="гр."):
    return Settlement(ekatte=ekatte, kind=kind, name=name, oblast="BGS",
                      oblast_name="Бургас", obshtina="BGS04", obshtina_name="Бургас")


def row(s, kind, status):
    return {"ekatte": s.ekatte, "kind": kind, "status": status, "settlement": asdict(s)}


def fake_downloads(calls):
    def fake_get(url, dest, retries, stop):
        calls.append((dest.parent.name, dest.name))
        dest.write_bytes(b"PK")
        return {"status": "ok", "bytes": 2, "last_modified": None}
    return fake_get


def saved(out):
    return {(m["ekatte"], m["kind"]): m["status"]
            for m in json.loads((out / "BGS" / "manifest.json").read_text(encoding="utf-8"))}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("kaismirror.fetch.time",
                        SimpleNamespace(sleep=lambda s: None, monotonic=time.monotonic))


@pytest.mark.parametrize("value, expected", [
    ("120", 120.0),
    (None, None),
    (email.utils.format_datetime(datetime.now(timezone.utc) - timedelta(hours=2), usegmt=True), 0.0),
])
def test_retry_after(value, expected):
    assert _retry_after(value) == expected


def test_fetch_skips_files_on_disk_and_known_missing(monkeypatch, tmp_path):
    aytos, sredets = settlement("12345", "Айтос"), settlement("67890", "Средец")
    out = tmp_path / "out"
    (out / "BGS" / "12345").mkdir(parents=True)
    (out / "BGS" / "12345" / "buildings.zip").write_bytes(b"PK")
    (out / "BGS" / "manifest.json").write_text(
        json.dumps([row(aytos, "units", "missing")]), encoding="utf-8")
    calls = []
    monkeypatch.setattr("kaismirror.fetch._get", fake_downloads(calls))
    manifest = fetch([aytos, sredets], out, log=lambda m: None)
    assert set(calls) == {("67890", "buildings.zip"), ("67890", "units.zip")}
    by_pair = {(m["ekatte"], m["kind"]): m for m in manifest}
    assert by_pair[("12345", "buildings")]["status"] == "ok"
    assert by_pair[("12345", "buildings")]["bytes"] == 2
    assert by_pair[("12345", "units")]["status"] == "missing"
    assert all(by_pair[("12345", k)]["cached"] for k in ("buildings", "units"))
    assert "cached" not in by_pair[("67890", "buildings")]
    assert set(saved(out)) == {("12345", "buildings"), ("12345", "units"),
                               ("67890", "buildings"), ("67890", "units")}


def test_fetch_keeps_other_settlements_entries(monkeypatch, tmp_path):
    village, town = settlement("11111", "Равда", kind="с."), settlement("22222", "Айтос")
    out = tmp_path / "out"
    (out / "BGS").mkdir(parents=True)
    (out / "BGS" / "manifest.json").write_text(json.dumps(
        [row(village, "buildings", "ok"), row(village, "units", "missing")]), encoding="utf-8")
    monkeypatch.setattr("kaismirror.fetch._get", fake_downloads([]))
    fetch([town], out, kinds=("buildings",), log=lambda m: None)
    assert saved(out) == {("11111", "buildings"): "ok",
                          ("11111", "units"): "missing",
                          ("22222", "buildings"): "ok"}


def test_fetch_forbidden_writes_manifest_then_raises(monkeypatch, tmp_path):
    aytos, sredets = settlement("12345", "Айтос"), settlement("67890", "Средец")

    def blocked(url, dest, retries, stop):
        stop.set()  # the real _get does this before raising Forbidden
        raise Forbidden(url)

    monkeypatch.setattr("kaismirror.fetch._get", blocked)
    with pytest.raises(Forbidden):
        fetch([aytos, sredets], tmp_path / "out", log=lambda m: None)
    assert saved(tmp_path / "out") == {
        ("12345", "buildings"): "forbidden", ("12345", "units"): "forbidden",
        ("67890", "buildings"): "forbidden", ("67890", "units"): "forbidden"}
