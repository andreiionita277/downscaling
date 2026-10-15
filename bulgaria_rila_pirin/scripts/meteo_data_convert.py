"""
Clean up raw METER.AC station dumps (data.raw.php) into readable CSVs.

Raw format has no real header names, zero-padded numeric strings, and bare
Unix timestamps. This parses that into a proper pandas DataFrame with a
real UTC datetime column and saves a clean CSV alongside a short coverage
summary (first/last record, count, and average sampling interval).

Usage:
    python meteo_data_convert.py data/meterac/N306_history_raw.txt
    python meteo_data_convert.py data/meterac/*_history_raw.txt   # summary table across all
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

COLUMN_MAP = {
    "Signal [a.u.|%]": "signal_pct",
    "PM2.5 [ug/m3]": "pm2_5_ugm3",
    "PM10 [ug/m3]": "pm10_ugm3",
    "P [hPa]": "pressure_hpa",
    "T [deg C]": "temp_c",
    "RH [%]": "rh_pct",
    "Gamma radiation [CPM 5 min]": "gamma_cpm",
    "Unix time": "unix_time",
    "Synchronization [s]": "sync_s",
}
NUMERIC_COLS = ["signal_pct", "pm2_5_ugm3", "pm10_ugm3", "pressure_hpa", "temp_c", "rh_pct", "gamma_cpm"]


def load_clean(path):
    df = pd.read_csv(path)
    df = df.rename(columns=COLUMN_MAP)
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["datetime_utc"] = pd.to_datetime(df["unix_time"], unit="s", utc=True)
    df["datetime_sofia"] = df["datetime_utc"].dt.tz_convert("Europe/Sofia")
    df = df.sort_values("datetime_utc").reset_index(drop=True)
    cols = ["datetime_utc", "datetime_sofia"] + [c for c in NUMERIC_COLS if c in df.columns]
    return df[cols]


def summarize(node_id, df):
    n = len(df)
    start, end = df["datetime_utc"].iloc[0], df["datetime_utc"].iloc[-1]
    span_days = (end - start).total_seconds() / 86400
    avg_interval_min = (span_days * 1440 / (n - 1)) if n > 1 else float("nan")
    return {
        "node": node_id,
        "start": start,
        "end": end,
        "n_records": n,
        "avg_interval_min": round(avg_interval_min, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_files", nargs="+", help="One or more *_history_raw.txt files")
    ap.add_argument("--no-csv", action="store_true", help="Only print the summary, skip writing cleaned CSVs")
    args = ap.parse_args()

    summaries = []
    for raw_path in args.raw_files:
        raw_path = Path(raw_path)
        node_id = raw_path.stem.replace("_history_raw", "")
        df = load_clean(raw_path)
        summaries.append(summarize(node_id, df))

        if not args.no_csv:
            out_path = raw_path.with_name(f"{node_id}_clean.csv")
            df.to_csv(out_path, index=False)
            print(f"{node_id}: wrote {out_path} ({len(df)} rows)")

    summary_df = pd.DataFrame(summaries).sort_values("start")
    pd.set_option("display.width", 160)
    print("\nCoverage summary (sorted by start date):")
    print(summary_df.to_string(index=False))


if __name__ == "__main__":
    main()
