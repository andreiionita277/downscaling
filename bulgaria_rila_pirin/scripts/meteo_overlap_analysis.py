import pandas as pd
import numpy as np
from pathlib import Path

DATA_DIR = Path("/app/bulgaria_rila_pirin/data/meterac")

combined = pd.read_csv(DATA_DIR / "all_stations_hourly_full_history.csv", parse_dates=["time_utc"])

combined["day"] = combined["time_utc"].dt.date
day_counts = combined.groupby(["node", "day"])["coverage"].value_counts().unstack(fill_value=0)
for col in ["complete", "partial", "gap"]:
    if col not in day_counts.columns:
        day_counts[col] = 0
day_counts["n_hours"] = day_counts[["complete", "partial", "gap"]].sum(axis=1)


def day_status(row):
    if row["n_hours"] < 24:
        return "no_data"  # station didn't exist for the full day (start/end edge)
    if row["gap"] == 0 and row["partial"] == 0:
        return "full"
    if row["gap"] == 0:
        return "usable"  # no full-gap hours, but some partial hours
    return "gappy"


day_counts["day_status"] = day_counts.apply(day_status, axis=1)
day_counts = day_counts.reset_index()
day_counts.to_csv(DATA_DIR / "station_daily_status.csv", index=False)

pivot = day_counts.pivot(index="day", columns="node", values="day_status")
all_days = pd.date_range(pivot.index.min(), pivot.index.max(), freq="D").date
pivot = pivot.reindex(all_days).fillna("no_data")

n_full = (pivot == "full").sum(axis=1)
n_usable_or_better = pivot.isin(["full", "usable"]).sum(axis=1)
n_any_data = (pivot != "no_data").sum(axis=1)

network_daily = pd.DataFrame({
    "n_stations_full": n_full,
    "n_stations_usable_or_better": n_usable_or_better,
    "n_stations_any_data": n_any_data,
}, index=pivot.index)
network_daily.index.name = "day"
network_daily.to_csv(DATA_DIR / "network_daily_summary.csv")

print("=== Network-wide daily station availability ===")
print(f"Date range covered: {network_daily.index.min()} to {network_daily.index.max()} ({len(network_daily)} days)")
print()
print("Distribution of n_stations_full across all days:")
print(n_full.describe())
print()

best_full = network_daily["n_stations_full"].max()
best_days_full = network_daily[network_daily["n_stations_full"] == best_full]
print(f"Max n_stations_full on any single day: {best_full} (out of 28)")
print(f"Number of days achieving this max: {len(best_days_full)}")
print(f"First 10 such days: {list(best_days_full.index[:10])}")
print()

# find longest run of days with n_stations_full >= threshold
for thresh in [28, 25, 22, 20, 18, 15, 12, 10]:
    mask = network_daily["n_stations_full"] >= thresh
    if not mask.any():
        continue
    # find longest consecutive run
    groups = (~mask).cumsum()
    run_lengths = mask.groupby(groups).sum()
    best_run_id = run_lengths.idxmax()
    best_run_len = run_lengths.max()
    run_days = network_daily.index[(groups == best_run_id) & mask]
    print(f"threshold >={thresh} stations 'full': longest consecutive run = {best_run_len} days "
          f"({run_days.min()} to {run_days.max()})" if len(run_days) else f"threshold >={thresh}: none")

print()
# check our chosen test date specifically
target = pd.Timestamp("2023-03-15").date()
if target in network_daily.index:
    row = network_daily.loc[target]
    print(f"2023-03-15 specifically: {int(row.n_stations_full)} full / {int(row.n_stations_usable_or_better)} usable-or-better / {int(row.n_stations_any_data)} any-data (out of 28)")
