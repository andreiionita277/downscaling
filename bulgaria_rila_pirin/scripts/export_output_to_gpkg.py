import pandas as pd
import geopandas as gpd
import xarray as xr
from pathlib import Path

BASE = Path("/app/bulgaria_rila_pirin")
if not BASE.exists():
    BASE = Path(__file__).resolve().parent.parent

DEM_CRS = "EPSG:32634"

ds = xr.open_dataset(BASE / "outputs_points" / "output.nc")
stations = pd.read_csv(BASE / "inputs" / "dem" / "station_list.csv").rename(columns={"stn_number": "node"})

# Long format: one row per (station, hour) so QGIS's Temporal Controller can
# animate through time using the "time" field, rather than needing 48
# separate wide columns.
df = ds.to_dataframe().reset_index().rename(columns={"point_name": "node"})
df["t_celsius"] = df["t"] - 273.15

df = df.merge(stations[["node", "Name", "x", "y", "elevation_m"]], on="node", how="left")

keep_vars = ["t", "t_celsius", "ws", "wd", "tp", "q", "p", "LW", "SW"]
cols = ["node", "Name", "x", "y", "elevation_m", "time"] + [v for v in keep_vars if v in df.columns]
df = df[cols]

gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.x, df.y), crs=DEM_CRS)

out_path = BASE / "outputs_points" / "output_points_for_qgis.gpkg"
gdf.to_file(out_path, driver="GPKG", layer="downscaled_points")

print(f"Wrote {out_path}")
print(f"{gdf.node.nunique()} stations x {gdf.time.nunique()} hourly timesteps = {len(gdf)} rows")
print(f"CRS: {gdf.crs}")
print(f"Columns: {list(gdf.columns)}")
