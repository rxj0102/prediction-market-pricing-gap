"""Collector logic that decides what gets saved. The option chain cannot be
backfilled, so a bug here loses data for good."""
import csv
import datetime as dt
import gzip

import pytest

import collector as c

UTC = dt.timezone.utc


# ------------------------------------------------------------------ parsing

@pytest.mark.parametrize("symbol, expected", [
    ("SPXW261120C07500000", ("SPXW", dt.date(2026, 11, 20), "C", 7500.0)),
    ("SPX261218P05000000", ("SPX", dt.date(2026, 12, 18), "P", 5000.0)),
    ("SPXW270115C07512500", ("SPXW", dt.date(2027, 1, 15), "C", 7512.5)),
])
def test_parse_occ(symbol, expected):
    assert c.parse_occ(symbol) == expected


def test_keep_expiry_window_edges():
    today = dt.date(2026, 11, 19)
    ye = {dt.date(2026, 12, 31)}
    assert c.keep_expiry(today, today, ye)
    assert c.keep_expiry(today + dt.timedelta(days=c.NEAR_DAYS), today, ye)
    assert not c.keep_expiry(today + dt.timedelta(days=c.NEAR_DAYS + 1), today, ye)
    assert not c.keep_expiry(today - dt.timedelta(days=1), today, ye)
    assert c.keep_expiry(dt.date(2026, 12, 31), today, ye)


# ------------------------------------------------------------------ classify

def test_classify_intraday_when_tick_is_fresh():
    # 15:00 Eastern on a winter day is 20:00 UTC; tick 10 minutes old.
    now = dt.datetime(2026, 11, 19, 20, 0, tzinfo=UTC)
    assert c.classify(now, "2026-11-19T14:50:00") == (dt.date(2026, 11, 19), "intraday")


def test_classify_close_when_tick_is_stale_and_keeps_session_date():
    # Run after midnight UTC still belongs to the previous Eastern session.
    now = dt.datetime(2026, 11, 20, 3, 0, tzinfo=UTC)
    assert c.classify(now, "2026-11-19T16:14:59") == (dt.date(2026, 11, 19), "close")


def test_classify_handles_summer_time():
    # 15:50 EDT is 19:50 UTC; with a fixed UTC-5 offset this would look stale.
    now = dt.datetime(2026, 7, 15, 19, 55, tzinfo=UTC)
    assert c.classify(now, "2026-07-15T15:50:00")[1] == "intraday"


def test_classify_boundary_is_thirty_minutes():
    tick = "2026-11-19T15:00:00"   # 20:00 UTC
    just_fresh = dt.datetime(2026, 11, 19, 20, 29, 59, tzinfo=UTC)
    just_stale = dt.datetime(2026, 11, 19, 20, 30, 0, tzinfo=UTC)
    assert c.classify(just_fresh, tick)[1] == "intraday"
    assert c.classify(just_stale, tick)[1] == "close"


# ------------------------------------------------------------------ files

def test_write_gz_round_trip_ignores_extra_fields(tmp_path):
    p = tmp_path / "x.csv.gz"
    c.write_gz(p, ["a", "b"], [{"a": 1, "b": 2, "junk": 3}, {"a": 4}])
    with gzip.open(p, "rt", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows == [{"a": "1", "b": "2"}, {"a": "4", "b": ""}]


def test_read_manifest_missing_file(tmp_path):
    assert c.read_manifest(tmp_path / "manifest.csv") == []


def test_read_manifest_upgrades_rows_without_kind(tmp_path):
    p = tmp_path / "manifest.csv"
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date_et", "fetch_start_utc", "spx_last_tick_et"])
        w.writeheader()
        w.writerow({"date_et": "2026-11-18", "fetch_start_utc": "2026-11-18T22:14:14+00:00",
                    "spx_last_tick_et": "2026-11-18T16:14:59"})
        w.writerow({"date_et": "2026-11-18", "fetch_start_utc": "2026-11-18T17:10:48+00:00",
                    "spx_last_tick_et": "2026-11-18T12:10:30"})
    assert [r["kind"] for r in c.read_manifest(p)] == ["close", "intraday"]


# ------------------------------------------------------------------ network

class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_get_json_retries_on_429_and_5xx(monkeypatch):
    replies = iter([FakeResponse(429), FakeResponse(503), FakeResponse(200, {"ok": 1})])
    monkeypatch.setattr(c.requests, "get", lambda *a, **k: next(replies))
    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    assert c.get_json("u") == {"ok": 1}


def test_get_json_gives_up(monkeypatch):
    monkeypatch.setattr(c.requests, "get", lambda *a, **k: FakeResponse(503))
    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="gave up"):
        c.get_json("u", tries=3)


def test_kalshi_open_markets_follows_cursor(monkeypatch):
    pages = {None: {"markets": [{"ticker": "A"}], "cursor": "p2"},
             "p2": {"markets": [{"ticker": "B"}], "cursor": "p3"},
             "p3": {"markets": [], "cursor": "p4"}}
    monkeypatch.setattr(c, "get_json", lambda url, params: pages[params.get("cursor")])
    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    assert [m["ticker"] for m in c.kalshi_open_markets("KXINXU")] == ["A", "B"]


# ------------------------------------------------------------------ main

def fake_sources(monkeypatch, last_tick, now):
    chain = {"timestamp": "t", "data": {
        "last_trade_time": last_tick, "current_price": 7300.0,
        "options": [{"option": "SPXW261120C07500000", "bid": 1, "ask": 2},
                    {"option": "SPXW270319C07500000", "bid": 1, "ask": 2},   # too far out
                    {"option": "SPX261218C07500000", "bid": 1, "ask": 2}]}}  # year end
    monkeypatch.setattr(c, "get_json", lambda url, params=None: chain)
    monkeypatch.setattr(c, "kalshi_open_markets", lambda s: [{"ticker": s + "-1"}])

    class FrozenDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(c.dt, "datetime", FrozenDatetime)


def run_main(monkeypatch, out, *flags):
    monkeypatch.setattr("sys.argv", ["collector.py", "--out", str(out), *flags])
    return c.main()


def test_main_saves_one_closing_snapshot_per_session(monkeypatch, tmp_path):
    fake_sources(monkeypatch, "2026-11-19T16:14:59", dt.datetime(2026, 11, 20, 1, 0, tzinfo=UTC))
    assert run_main(monkeypatch, tmp_path) == 0
    assert run_main(monkeypatch, tmp_path) == 0          # later overnight run: skipped
    rows = c.read_manifest(tmp_path / "manifest.csv")
    assert [(r["date_et"], r["kind"]) for r in rows] == [("2026-11-19", "close")]
    assert rows[0]["option_rows"] == "2" and rows[0]["kalshi_rows"] == "2"
    assert run_main(monkeypatch, tmp_path, "--force") == 0
    assert len(c.read_manifest(tmp_path / "manifest.csv")) == 2


def test_main_writes_both_files_named_by_stamp(monkeypatch, tmp_path):
    fake_sources(monkeypatch, "2026-11-19T14:50:00", dt.datetime(2026, 11, 19, 20, 0, tzinfo=UTC))
    run_main(monkeypatch, tmp_path)
    row = c.read_manifest(tmp_path / "manifest.csv")[0]
    day = tmp_path / row["date_et"]
    assert row["kind"] == "intraday"
    assert (day / f"{row['stamp_utc']}_spx_options.csv.gz").exists()
    assert (day / f"{row['stamp_utc']}_kalshi.csv.gz").exists()


def test_main_flags_an_empty_side(monkeypatch, tmp_path):
    fake_sources(monkeypatch, "2026-11-19T14:50:00", dt.datetime(2026, 11, 19, 20, 0, tzinfo=UTC))
    monkeypatch.setattr(c, "kalshi_open_markets", lambda s: [])
    assert run_main(monkeypatch, tmp_path) == 1
