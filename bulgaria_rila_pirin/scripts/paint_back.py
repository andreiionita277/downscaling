import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from sklearn.preprocessing import StandardScaler
from scipy.spatial import cKDTree

param_path = "/app/bulgaria_rila_pirin/outputs/ds_param.nc"
centroids_path = "/app/bulgaria_rila_pirin/outputs/df_centroids.pck.bak"
downscaled_path = "/app/bulgaria_rila_pirin/outputs/output.nc"
dem_path = "/app/bulgaria_rila_pirin/inputs/dem/rila_pirin_dem_25m_buffered_32634_bilinear_resampling.tif"
mask_path = "/app/bulgaria_rila_pirin/inputs/dem/mask.tif"
out_path = "/app/bulgaria_rila_pirin/outputs/t_2023-03-15_hourly_25m.tif"
variable = "t"
nodata = -9999.0

features = ["x", "y", "elevation", "slope", "aspect_cos", "aspect_sin", "svf"]

ds_param = xr.open_dataset(param_path)
centroids = pd.read_pickle(centroids_path)
ds_down = xr.open_dataset(downscaled_path)

with rasterio.open(mask_path) as r:
    mask = r.read(1) == 1  # (y, x), True inside AOI

ny, nx = mask.shape
yy, xx = np.meshgrid(ds_param.y.values, ds_param.x.values, indexing="ij")

pixel_feats = {
    "x": xx[mask],
    "y": yy[mask],
    "elevation": ds_param.elevation.values[mask],
    "slope": ds_param.slope.values[mask],
    "aspect_cos": ds_param.aspect_cos.values[mask],
    "aspect_sin": ds_param.aspect_sin.values[mask],
    "svf": ds_param.svf.values[mask],
}
pixel_matrix = np.column_stack([pixel_feats[f] for f in features])

scaler = StandardScaler()
pixel_scaled = scaler.fit_transform(pixel_matrix)
centroid_matrix = centroids[features].values
centroid_scaled = scaler.transform(centroid_matrix)

tree = cKDTree(centroid_scaled)
_, nearest_idx = tree.query(pixel_scaled, k=1)

point_names = centroids["point_name"].values
pixel_point_name = point_names[nearest_idx]

point_name_grid = np.full((ny, nx), "-9999", dtype="<U5")
point_name_grid[mask] = pixel_point_name

unique_names, inverse = np.unique(point_name_grid, return_inverse=True)
inverse = inverse.reshape(point_name_grid.shape)

var = ds_down[variable]
n_time = var.sizes["time"]
lookup = np.full((len(unique_names), n_time), nodata, dtype="float32")
down_names = var.point_name.values.astype(str)
name_to_row = {name: i for i, name in enumerate(down_names)}
for i, name in enumerate(unique_names):
    row = name_to_row.get(name)
    if row is not None:
        lookup[i, :] = var.values[row, :]

painted = lookup[inverse]
painted = np.moveaxis(painted, -1, 0)  # (time, y, x)

with rasterio.open(dem_path) as dem:
    transform = dem.transform
    crs = dem.crs

profile = {
    "driver": "GTiff",
    "height": painted.shape[1],
    "width": painted.shape[2],
    "count": n_time,
    "dtype": "float32",
    "crs": crs,
    "transform": transform,
    "nodata": nodata,
    "compress": "lzw",
}

with rasterio.open(out_path, "w", **profile) as dst:
    for i in range(n_time):
        dst.write(painted[i], i + 1)
        dst.set_band_description(i + 1, str(ds_down.time.values[i]))

valid = painted != nodata
print("Wrote", out_path)
print("bands:", n_time, "shape:", painted.shape[1:])
print("valid pixel count:", int(valid[0].sum()), "of", mask.sum())
print("value range (K):", float(painted[valid].min()), float(painted[valid].max()))
