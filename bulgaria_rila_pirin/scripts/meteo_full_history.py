import pandas as pd
import numpy as np
from pathlib import Path

DATA_DIR = Path("/app/bulgaria_rila_pirin/data/meterac")
if not DATA_DIR.exists():
    DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "meterac"

# Per-station output files live alongside the raw data; combined/summary
# files and the metadata CSV were moved up a level, into data/ directly.
OUT_DIR = DATA_DIR
COMBINED_DIR = DATA_DIR.parent
EXPECTED_PER_HOUR = 12  # nominal 5-min reporting for all METER.AC nodes (confirmed via metadata)
COMPLETE_MIN_OBS = 10   # >=10/12 -> complete (allow 1-2 dropped readings)
COMPLETE_MIN_HOURS = 20  # >=20/24 hours with data in a day -> complete (allow a few dropped hours)

STATIONS = [
    "N27", "N32", "N48", "N058", "N067", "N072", "N081", "N094", "N096",
    "N097", "N098", "N108", "N110", "N122", "N141", "N142", "N155", "N164",
    "N175", "N211", "N221", "N235", "N303", "N306", "N313", "N321", "N323", "N330", "N349",
]

metadata = pd.read_csv(COMBINED_DIR / "selected_stations_metadata.csv").set_index("NodeID")


def classify(n, min_obs):
    if n == 0:
        return "gap"
    elif n < min_obs:
        return "partial"
    else:
        return "complete"


all_hourly = []
all_daily = []
station_summaries = []

for node in STATIONS:
    name = metadata.loc[node, "Location"] if node in metadata.index else node
    raw_path = DATA_DIR / f"{node}_history_raw.txt"
    raw = pd.read_csv(raw_path, usecols=["T [deg C]", "Unix time"])
    raw = raw.rename(columns={"T [deg C]": "temp_c", "Unix time": "unix_time"})
    raw["temp_c"] = pd.to_numeric(raw["temp_c"], errors="coerce")
    raw["dt"] = pd.to_datetime(raw["unix_time"], unit="s", utc=True)
    raw = raw.dropna(subset=["dt"]).sort_values("dt")

    if raw.empty:
        station_summaries.append({
            "node": node, "station_name": name, "start": pd.NaT, "end": pd.NaT,
            "n_hours_total": 0, "pct_complete": 0.0, "pct_partial": 0.0, "pct_gap": 0.0,
            "overall_status": "no_data",
        })
        print(f"{node} ({name}): no data ever recorded")
        continue

    # --- hourly aggregation (mean/min/max of the 5-min readings within each hour) ---
    hourly = raw.set_index("dt")["temp_c"].resample("1h").agg(["min", "max", "mean", "count"])
    hourly = hourly.rename(columns={"min": "temp_c_min", "max": "temp_c_max", "mean": "temp_c_mean", "count": "n_obs"})

    full_range = pd.date_range(hourly.index.min(), hourly.index.max(), freq="1h", tz="UTC")
    hourly = hourly.reindex(full_range)
    hourly["n_obs"] = hourly["n_obs"].fillna(0).astype(int)
    hourly.index.name = "time_utc"
    hourly["coverage"] = hourly["n_obs"].apply(lambda n: classify(n, COMPLETE_MIN_OBS))
    hourly.insert(0, "station_name", name)
    hourly.insert(0, "node", node)

    out_path = OUT_DIR / f"{node}_hourly_full_history.csv"
    hourly.to_csv(out_path)
    all_hourly.append(hourly)

    # --- daily aggregation (mean/min/max across the 24 hourly values of a day) ---
    daily = hourly.resample("1D").agg(
        temp_c_min=("temp_c_min", "min"),
        temp_c_max=("temp_c_max", "max"),
        temp_c_mean=("temp_c_mean", "mean"),
        n_hours_with_data=("n_obs", lambda s: int((s > 0).sum())),
    )
    daily.index.name = "date"
    daily["coverage"] = daily["n_hours_with_data"].apply(lambda n: classify(n, COMPLETE_MIN_HOURS))
    daily.insert(0, "station_name", name)
    daily.insert(0, "node", node)

    daily_out_path = OUT_DIR / f"{node}_daily_full_history.csv"
    daily.to_csv(daily_out_path)
    all_daily.append(daily)

    n_total = len(hourly)
    n_complete = int((hourly.coverage == "complete").sum())
    n_partial = int((hourly.coverage == "partial").sum())
    n_gap = int((hourly.coverage == "gap").sum())
    station_summaries.append({
        "node": node,
        "station_name": name,
        "start": hourly.index.min(),
        "end": hourly.index.max(),
        "n_hours_total": n_total,
        "pct_complete": round(100 * n_complete / n_total, 1),
        "pct_partial": round(100 * n_partial / n_total, 1),
        "pct_gap": round(100 * n_gap / n_total, 1),
        "overall_status": "good" if n_complete / n_total >= 0.8 else ("partial" if n_complete / n_total >= 0.3 else "mostly_gap"),
    })
    print(f"{node} ({name}): {n_total} hours [{hourly.index.min().date()} -> {hourly.index.max().date()}], "
          f"{n_complete} complete / {n_partial} partial / {n_gap} gap")

combined_hourly = pd.concat(all_hourly)
combined_hourly.to_csv(COMBINED_DIR / "all_stations_hourly_full_history.csv")
print(f"\nWrote combined hourly file: {COMBINED_DIR / 'all_stations_hourly_full_history.csv'} ({len(combined_hourly)} rows)")

combined_daily = pd.concat(all_daily)
combined_daily.to_csv(COMBINED_DIR / "all_stations_daily_full_history.csv")
print(f"Wrote combined daily file: {COMBINED_DIR / 'all_stations_daily_full_history.csv'} ({len(combined_daily)} rows)")

summary_df = pd.DataFrame(station_summaries).sort_values("start")
summary_df.to_csv(COMBINED_DIR / "station_coverage_summary.csv", index=False)
pd.set_option("display.width", 160)
print("\nPer-station summary (sorted by start), all 29 in-AOI stations:")
print(summary_df.to_string(index=False))
