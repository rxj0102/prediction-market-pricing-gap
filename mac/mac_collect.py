"""Decide whether this Mac should take a snapshot now, take it, and push it.

launchd starts this every 10 minutes. It does nothing unless one of these is
due, so most starts exit within a second:

  intraday  one snapshot per US trading hour from 10:00 to 15:59 Eastern,
            taken at the first start from minute 20 of the hour. The Cboe
            feed runs about 15 minutes behind, so the quotes are from about
            minute 5 of the hour or later.
  close     from 16:50 Eastern on a weekday, if the closing chain for that
            session is not saved yet. This backs up the GitHub workflow.

The decision uses US Eastern time, so it keeps working when the Mac's own
time zone changes. A Mac that is asleep misses its slots. Holidays are
handled by collector.py, which labels a stale tick as a closing snapshot and
saves it only once.

Usage:
  python3 mac/mac_collect.py            normal run
  python3 mac/mac_collect.py --dry-run  print the decision and do nothing
"""
import argparse
import csv
import datetime as dt
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parent.parent
FIRST, LAST, MINUTE = 10, 15, 20     # intraday slots at HH:20 Eastern
CLOSE_AFTER = dt.time(16, 50)   # the last tick (16:15) is stale by 16:45


def manifest_rows():
    path = REPO / "data" / "manifest.csv"
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def decide(now_et, rows):
    """Return the kind of snapshot due now, or None."""
    if now_et.weekday() >= 5:
        return None
    today = now_et.date().isoformat()
    if FIRST <= now_et.hour <= LAST and now_et.minute >= MINUTE:
        for r in rows:
            if r["date_et"] != today or r.get("kind") != "intraday":
                continue
            fetched = dt.datetime.fromisoformat(r["fetch_start_utc"]).astimezone(ET)
            if fetched.date() == now_et.date() and fetched.hour == now_et.hour:
                return None
        return "intraday"
    if now_et.time() >= CLOSE_AFTER:
        if any(r["date_et"] == today and r.get("kind") == "close" for r in rows):
            return None
        return "close"
    return None


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    now_et = dt.datetime.now(ET)

    # Bring in snapshots GitHub may have pushed, so the decision sees them.
    pull = git("pull", "--rebase", "--autostash", "--quiet", "origin", "main")
    if pull.returncode != 0:
        print(f"{now_et:%Y-%m-%d %H:%M} ET: git pull failed: {pull.stderr.strip()}")
        return 1

    due = decide(now_et, manifest_rows())
    if due is None:
        return 0
    print(f"{now_et:%Y-%m-%d %H:%M} ET: {due} snapshot due")
    if args.dry_run:
        return 0

    run = subprocess.run([sys.executable, "collector.py"], cwd=REPO, capture_output=True, text=True)
    print(run.stdout.strip() or run.stderr.strip())
    if run.returncode != 0:
        return run.returncode

    git("add", "data")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return 0
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    git("commit", "--quiet", "-m", f"Add snapshot {stamp} from Mac")
    for _ in range(3):
        if git("pull", "--rebase", "--quiet", "origin", "main").returncode == 0 \
                and git("push", "--quiet", "origin", "HEAD:main").returncode == 0:
            print("pushed")
            return 0
    print("push failed; the commit stays local and goes out with the next push")
    return 1


if __name__ == "__main__":
    sys.exit(main())
