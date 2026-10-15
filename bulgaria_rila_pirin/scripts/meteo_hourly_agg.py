import pandas as pd
import geopandas as gpd
from pathlib import Path

DATA_DIR = Path("/app/bulgaria_rila_pirin/data/meterac")
OUT_DIR = DATA_DIR
DEM_CRS = "EPSG:32634"
TARGET_DAY = pd.Timestamp("2023-03-15", tz="UTC")
TARGET_END = TARGET_DAY + pd.Timedelta(days=1)

STATIONS = [
    "N48", "N303", "N32", "N221", "N110", "N155", "N081", "N164", "N108",
    "N097", "N122", "N067", "N142", "N235", "N098", "N072", "N349",
]

meta = pd.read_csv(DATA_DIR / "selected_stations_metadata.csv")
meta = meta.set_index("NodeID")

hourly_frames = []
for node in STATIONS:
    raw = pd.read_csv(DATA_DIR / f"{node}_history_raw.txt", usecols=["T [deg C]", "Unix time"])
    raw = raw.rename(columns={"T [deg C]": "temp_c", "Unix time": "unix_time"})
    raw["temp_c"] = pd.to_numeric(raw["temp_c"], errors="coerce")
    raw["dt"] = pd.to_datetime(raw["unix_time"], unit="s", utc=True)
    day = raw[(raw.dt >= TARGET_DAY) & (raw.dt < TARGET_END)].dropna(subset=["temp_c"])

    hourly = day.set_index("dt")["temp_c"].resample("1h").agg(["min", "max", "mean", "count"])
    hourly = hourly.rename(columns={"min": "temp_c_min", "max": "temp_c_max", "mean": "temp_c_mean", "count": "n_obs"})
    hourly = hourly.reindex(pd.date_range(TARGET_DAY, TARGET_END - pd.Timedelta(hours=1), freq="1h", tz="UTC"))
    hourly.index.name = "time_utc"
    hourly.insert(0, "node", node)

    out_path = OUT_DIR / f"{node}_hourly_2023-03-15.csv"
    hourly.to_csv(out_path)
    hourly_frames.append(hourly)
    print(f"{node}: {hourly['n_obs'].sum():.0f} valid 5-min readings -> {int(hourly['temp_c_mean'].notna().sum())}/24 hours filled -> {out_path.name}")

combined = pd.concat(hourly_frames)
combined.to_csv(OUT_DIR / "all_stations_hourly_2023-03-15.csv")
print(f"\nWrote combined file: {OUT_DIR / 'all_stations_hourly_2023-03-15.csv'}")

# Build station_list.csv for TopoPyScale points sampling
gdf = gpd.GeoDataFrame(
    meta.loc[STATIONS].reset_index(),
    geometry=gpd.points_from_xy(meta.loc[STATIONS, "Longitude"], meta.loc[STATIONS, "Latitude"]),
    crs="EPSG:4326",
).to_crs(DEM_CRS)

station_list = pd.DataFrame({
    "Name": gdf["Location"],
    "stn_number": gdf["NodeID"],
    "latitude": meta.loc[STATIONS, "Latitude"].values,
    "longitude": meta.loc[STATIONS, "Longitude"].values,
    "x": gdf.geometry.x,
    "y": gdf.geometry.y,
    "elevation_m": meta.loc[STATIONS, "Altitude"].values,
})
station_list_path = Path("/app/bulgaria_rila_pirin/inputs/dem/station_list.csv")
station_list.to_csv(station_list_path, index=False)
print(f"Wrote {station_list_path}")
print(station_list.to_string(index=False))
