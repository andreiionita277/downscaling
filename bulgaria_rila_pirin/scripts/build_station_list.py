import pandas as pd
import geopandas as gpd
from pathlib import Path

DATA_DIR = Path("/app/bulgaria_rila_pirin/data")
if not DATA_DIR.exists():
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEM_CRS = "EPSG:32634"

# All 28 in-AOI stations with any real data (excludes N211/Makedonia_Hut,
# which never reported anything and so can never be validated against).
STATIONS = [
    "N27", "N32", "N48", "N058", "N067", "N072", "N081", "N094", "N096",
    "N097", "N098", "N108", "N110", "N122", "N141", "N142", "N155", "N164",
    "N175", "N221", "N235", "N303", "N306", "N313", "N321", "N323", "N330", "N349",
]

meta = pd.read_csv(DATA_DIR / "selected_stations_metadata.csv").set_index("NodeID")

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
if not station_list_path.parent.exists():
    station_list_path = Path(__file__).resolve().parent.parent / "inputs" / "dem" / "station_list.csv"

station_list.to_csv(station_list_path, index=False)
print(f"Wrote {station_list_path} ({len(station_list)} stations)")
print(station_list.to_string(index=False))
