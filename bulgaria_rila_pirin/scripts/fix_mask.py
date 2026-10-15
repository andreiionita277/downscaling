import rasterio
import geopandas as gpd
from rasterio.features import rasterize

dem_path = "/app/bulgaria_rila_pirin/inputs/dem/rila_pirin_dem_25m_buffered_32634_bilinear_resampling.tif"
mask_geojson = "/app/bulgaria_rila_pirin/inputs/mask/rila_pirin_PERFECT_RECTANGLE_32634.geojson"
mask_out = "/app/bulgaria_rila_pirin/inputs/dem/mask.tif"

with rasterio.open(dem_path) as dem:
    transform = dem.transform
    shape = (dem.height, dem.width)
    crs = dem.crs
    profile = dem.profile.copy()

gdf = gpd.read_file(mask_geojson).to_crs(crs)

burned = rasterize(
    [(geom, 1) for geom in gdf.geometry],
    out_shape=shape,
    transform=transform,
    fill=0,
    dtype="uint8",
)

profile.update(dtype="uint8", count=1, nodata=0, compress="lzw")

with rasterio.open(mask_out, "w", **profile) as dst:
    dst.write(burned, 1)

with rasterio.open(mask_out) as check, rasterio.open(dem_path) as dem:
    print("mask bounds", check.bounds)
    print("dem bounds ", dem.bounds)
    print("bounds match exactly:", check.bounds == dem.bounds)
    print("resolution match:", check.res == dem.res)
    print("mask sum (pixels inside AOI):", int(burned.sum()))
