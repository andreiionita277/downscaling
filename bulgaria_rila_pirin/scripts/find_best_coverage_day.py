import pandas as pd
from pathlib import Path

DATA_DIR = Path("/app/bulgaria_rila_pirin/data")
if not DATA_DIR.exists():
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"

daily = pd.read_csv(DATA_DIR / "stations_daily_full_history.csv", parse_dates=["date"])

# One boolean column per station: was that day "complete" (>=20/24 hours reporting)?
complete = daily.pivot(index="date", columns="station_name", values="coverage").eq("complete")

n_complete = complete.sum(axis=1)
n_total_stations = complete.shape[1]

TOP_N = 10
top_days = n_complete.sort_values(ascending=False).head(TOP_N)

print(f"{n_total_stations} stations with any data considered.\n")
print(f"Top {TOP_N} days by number of stations fully complete:")
for date, count in top_days.items():
    print(f"  {date.date()}  {count}/{n_total_stations} complete")

best_day = n_complete.idxmax()
best_count = n_complete.loc[best_day]
complete_stations = complete.columns[complete.loc[best_day]].tolist()
missing_stations = complete.columns[~complete.loc[best_day]].tolist()

print(f"\nBest day: {best_day.date()} — {best_count}/{n_total_stations} stations complete")
print(f"Complete ({len(complete_stations)}): {complete_stations}")
print(f"Not complete ({len(missing_stations)}): {missing_stations}")
