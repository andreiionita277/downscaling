"""
Independent checks of the GeoTIFFs written by export_geotiff.py.

    python bulgaria_rila_pirin/scripts/check_geotiff.py <config.yml> [--var t] [--n-pixels 1000]

For every <var>_*_daily_*.tif in outputs/geotiff:
    - grid: CRS, transform and shape identical to the DEM
    - nodata: exactly the pixels outside the clustering mask (mask.tif), in every band
    - values: n random pixels inside the mask equal the daily value of their cluster in daily.zarr
For one band per file, the whole field is also compared against TopoPyScale's own mapping
expression, ds[var].sel(point_name=ds_param.point_name) (topo_plot.map_variable).
Exits non-zero if any check fails.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
import yaml

NODATA = -9999.0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--var', default='t')
    p.add_argument('--n-pixels', type=int, default=1000)
    a = p.parse_args()
    cfg = yaml.safe_load(Path(a.config).read_text())
    out = Path(cfg['project']['directory']) / cfg['outputs'].get('directory', 'outputs')
    dem = Path(cfg['dem']['path']) / cfg['dem']['file']
    mask_file = cfg['sampling']['toposub']['clustering_mask']

    daily = xr.open_zarr(out / 'downscaled' / 'daily.zarr')
    pn = xr.open_dataset(out / 'ds_param.nc').point_name.transpose('y', 'x')
    with rasterio.open(mask_file) as r:
        inside = r.read(1) == 1
    with rasterio.open(dem) as r:
        dem_crs, dem_transform, dem_shape = r.crs, r.transform, (r.height, r.width)
    if not ((pn.values.astype(str) != '-9999') == inside).all():
        sys.exit('FAIL: cluster map in ds_param.nc and mask.tif disagree')

    rng = np.random.default_rng(0)
    iy, ix = np.nonzero(inside)
    pick = rng.choice(len(iy), a.n_pixels, replace=False)
    py, px = iy[pick], ix[pick]
    p_names = pn.values.astype(str)[py, px]
    offset = 273.15 if a.var == 't' else 0.0
    fill = pn.where(pn != '-9999', daily.point_name.values[0])     # any valid name; masked out below

    files = sorted((out / 'geotiff').glob(f'{a.var}_*_daily_*.tif'))
    if not files:
        sys.exit('FAIL: no GeoTIFFs found')
    ok = True
    for f in files:
        stat = f.stem.split('_')[1]
        with rasterio.open(f) as r:
            errs = []
            if r.crs != dem_crs or r.transform != dem_transform or (r.height, r.width) != dem_shape:
                errs.append('grid differs from DEM')
            dates = pd.to_datetime(list(r.descriptions))
            ref = daily[f'{a.var}_{stat}'].sel(time=dates).load() - offset
            max_diff = 0.0
            for b in range(1, r.count + 1):
                band = r.read(b)
                if not ((band == NODATA) == ~inside).all():
                    errs.append(f'band {b}: nodata pixels != outside mask')
                    break
                expect = ref.isel(time=b - 1).sel(point_name=p_names).values
                max_diff = max(max_diff, float(np.nanmax(np.abs(band[py, px] - expect))))
            # whole field of the first band vs TopoPyScale's own mapping expression
            authors = ref.isel(time=0).sel(point_name=fill).values
            authors = np.where(inside, authors, NODATA).astype('float32')
            field_diff = float(np.abs(r.read(1) - authors).max())
            if max_diff > 1e-4 or field_diff > 1e-4:
                errs.append(f'values differ (sample max {max_diff:.2e}, band-1 field max {field_diff:.2e})')
            inside_vals = r.read(1)[inside]
        status = 'OK  ' if not errs else 'FAIL'
        ok &= not errs
        print(f'{status} {f.name}: {r.count} bands, {r.crs}, sample max |diff| {max_diff:.1e}, '
              f'band-1 field max |diff| {field_diff:.1e}, band-1 range {inside_vals.min():.2f}..{inside_vals.max():.2f}'
              + (f'  <- {"; ".join(errs)}' if errs else ''), flush=True)
    print('ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
