import numpy as np
import rasterio

down_path = "/app/bulgaria_rila_pirin/outputs/t_2023-03-15_hourly_25m.tif"
era5_path = "/app/bulgaria_rila_pirin/outputs/era5_t2m_2023-03-15_resampled_25m.tif"
out_path = "/app/bulgaria_rila_pirin/outputs/diff_downscaled_minus_era5_2023-03-15_hourly.tif"
nodata = -9999.0

with rasterio.open(down_path) as d, rasterio.open(era5_path) as e:
    profile = d.profile.copy()
    down = d.read().astype("float32")
    era5 = e.read().astype("float32")
    band_descriptions = d.descriptions

valid = down != nodata
diff = np.where(valid, down - era5, nodata).astype("float32")

with rasterio.open(out_path, "w", **profile) as dst:
    dst.write(diff)
    for i, desc in enumerate(band_descriptions):
        if desc:
            dst.set_band_description(i + 1, desc)

print("wrote", out_path)
v = diff[valid]
print("diff min/max/mean (degC-equivalent, since both inputs are Kelvin):", v.min(), v.max(), v.mean())
