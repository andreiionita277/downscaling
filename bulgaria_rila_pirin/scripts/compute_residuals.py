import pandas as pd
import numpy as np
import xarray as xr
from pathlib import Path

BASE = Path("/app/bulgaria_rila_pirin")
if not BASE.exists():
    BASE = Path(__file__).resolve().parent.parent

# The 19 stations confirmed `complete` on BOTH 2022-11-27 and 2022-11-28
# (see PROGRESS.md "Validation date decided") -- the only ones with real
# observations to validate against for this window. All 28 were downscaled,
# but the other 9 have no usable ground truth here.
VALIDATED_STATIONS = [
    "N306", "N094", "N098", "N27", "N164", "N067", "N155", "N122", "N221",
    "N142", "N48", "N081", "N072", "N097", "N32", "N303", "N108", "N110", "N235",
]

ds = xr.open_dataset(BASE / "outputs_points" / "output.nc")
t_topo = (ds["t"] - 273.15).to_dataframe()["t"].rename("t_topo").reset_index()
t_topo = t_topo.rename(columns={"point_name": "node"})
t_topo["time"] = pd.to_datetime(t_topo["time"], utc=True)

obs = pd.read_csv(BASE / "data" / "stations_hourly_full_history.csv", parse_dates=["time_utc"])
obs = obs[obs["node"].isin(VALIDATED_STATIONS)]
obs = obs[(obs["time_utc"] >= "2022-11-27") & (obs["time_utc"] < "2022-11-29")]
obs = obs.rename(columns={"time_utc": "time", "temp_c_mean": "t_obs"})[["node", "time", "t_obs", "n_obs", "coverage"]]

centroids = pd.read_pickle(BASE / "outputs_points" / "df_centroids.pck")
centroids = centroids.rename(columns={"stn_number": "node", "elevation": "elevation_dem", "elevation_m": "elevation_station"})
terrain_cols = ["node", "elevation_dem", "elevation_station", "slope", "aspect_cos", "aspect_sin", "svf"]
centroids = centroids[terrain_cols]
centroids["elevation_diff"] = centroids["elevation_station"] - centroids["elevation_dem"]

df = t_topo.merge(obs, on=["node", "time"], how="inner").merge(centroids, on="node", how="left")
df["residual"] = df["t_obs"] - df["t_topo"]
df["hour"] = df["time"].dt.hour
df["month"] = df["time"].dt.month

out_path = BASE / "data" / "residuals_2022-11-27_28.csv"
df.to_csv(out_path, index=False)

print(f"Wrote {out_path} ({len(df)} rows, {df.node.nunique()} stations)")
print()
print(f"Overall residual (T_obs - T_topo): mean={df.residual.mean():.3f}C, "
      f"std={df.residual.std():.3f}C, RMSE={np.sqrt((df.residual**2).mean()):.3f}C, "
      f"MAE={df.residual.abs().mean():.3f}C")
print()
print("Per-station mean residual (sorted by elevation_diff):")
per_station = df.groupby("node").agg(
    elevation_station=("elevation_station", "first"),
    elevation_dem=("elevation_dem", "first"),
    elevation_diff=("elevation_diff", "first"),
    mean_residual=("residual", "mean"),
    rmse=("residual", lambda s: np.sqrt((s**2).mean())),
    n=("residual", "count"),
).sort_values("elevation_diff")
pd.set_option("display.width", 160)
print(per_station.to_string())
print()
print("Correlation of residual with elevation_diff:", df["residual"].corr(df["elevation_diff"]))
print("Correlation of residual with hour:", df["residual"].corr(df["hour"]))
print("Correlation of residual with svf:", df["residual"].corr(df["svf"]))
