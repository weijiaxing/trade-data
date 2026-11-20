#!/usr/bin/env python3
"""QQQ historical backfill + incremental OHLCV updater using Longbridge OpenAPI.
Requires LONGPORT_APP_KEY, LONGPORT_APP_SECRET, LONGPORT_ACCESS_TOKEN.
Writes one CSV per trading day for intraday bars, and annual CSV for daily bars.
"""
import argparse
import csv
import os
import pathlib
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from longport.openapi import Config, QuoteContext, Period, AdjustType, TradeSessions

ROOT = pathlib.Path(__file__).resolve().parents[1]
START = date(2023, 11, 1)
NY = ZoneInfo("America/New_York")
FIELDS = ["timestamp", "open", "high", "low", "close", "volume"]

def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(FIELDS)
        writer.writerows(rows)
    temp.replace(path)

def convert(candles):
    rows = []
    for c in candles:
        ts = c.timestamp
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts, NY)
        elif isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = ts.replace(tzinfo=NY)
        if isinstance(ts, datetime):
            ts = ts.astimezone(NY)
            # Exclude pre/post-market, and never include the 16:00 bar.
            if not (9 * 60 + 30 <= ts.hour * 60 + ts.minute < 16 * 60):
                continue
            timestamp = ts.isoformat()
        else:
            timestamp = str(ts)
        rows.append([timestamp, str(c.open), str(c.high), str(c.low),
                     str(c.close), str(c.volume)])
    return sorted({row[0]: row for row in rows}.values(), key=lambda x: x[0])

def commit(paths):
    if not paths:
        return
    subprocess.run(["git", "add", "--", *map(str, paths)], cwd=ROOT, check=True)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if changed.returncode == 0:
        return
    subprocess.run(["git", "commit", "-m", "data: update QQQ historical candles"], cwd=ROOT, check=True)
    subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT, check=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default=None, help="YYYY-MM-DD; defaults to last NY calendar day")
    parser.add_argument("--max-days", type=int, default=40)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    end = date.fromisoformat(args.end) if args.end else datetime.now(NY).date() - timedelta(days=1)
    cfg = Config.from_env()
    ctx = QuoteContext(cfg)
    processed = 0
    changed = []
    for i in range((end - START).days + 1):
        day = START + timedelta(days=i)
        if day.weekday() >= 5:
            continue
        targets = [(name, period) for name, period in
                   [("1m", Period.Min_1), ("5m", Period.Min_5)]
                   if not (ROOT / "QQQ" / name / (day.isoformat() + ".csv")).exists()]
        if not targets:
            continue
        for name, period in targets:
            try:
                bars = ctx.history_candlesticks_by_date(
                    "QQQ.US", period, AdjustType.NoAdjust,
                    day, day, TradeSessions.Intraday)
                rows = convert(bars)
                if not rows:
                    continue  # market holiday or unavailable; retry next run
                path = ROOT / "QQQ" / name / (day.isoformat() + ".csv")
                write_csv(path, rows)
                changed.append(path.relative_to(ROOT))
                print(f"{day} {name}: {len(rows)} bars", flush=True)
                time.sleep(0.2)
            except Exception as exc:
                print(f"ERROR {day} {name}: {exc}", file=sys.stderr, flush=True)
        processed += 1
        if args.push and len(changed) >= 40:
            commit(changed)
            changed.clear()
        if processed >= args.max_days:
            break
    if args.push:
        commit(changed)
    print(f"Processed {processed} trading dates; remaining dates continue on next run.")

if __name__ == "__main__":
    main()
