"""
Snow depth GeoTIFF for ONE date/time from a Crocus run (17 Nov 2026). Same mapping and check as crocus_maps.py (every
pixel gets the value of its cluster, no elevation adjustment); reuses its load / write / check functions.

    python meridionali_zarr/scripts/snow_map_date.py /mnt/d/Downscaling/meridionali_zarr/crocus_Efix/2005-2006_3hourly \
           2006-02-20 --hour 6 --terrain meridionali_zarr/data/meridionali_F_2025/outputs \
           --out /mnt/d/Downscaling/meridionali_zarr/crocus_Efix/maps/snow_depth_2006-02-20_06utc.tif

Output: float32 GeoTIFF, snow depth in cm (rounded to 0.1), nodata -9999 outside the clustering mask, DEM grid/CRS,
DEFLATE, AVERAGE overviews. Check: 2000 random pixels equal the PRO value of their cluster.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr

sys.path.insert(0, str(Path(__file__).parent))
from crocus_maps import check, load, write  # noqa: E402


def main():
    pa = argparse.ArgumentParser()
    pa.add_argument('run')
    pa.add_argument('date', help='YYYY-MM-DD')
    pa.add_argument('--hour', type=int, default=6, help='UTC hour (3-hourly output: 0, 3, ..., 21); 6 = station time')
    pa.add_argument('--terrain', required=True, help='outputs folder with ds_param.nc of the setup')
    pa.add_argument('--dem', default='meridionali_zarr/data/dem/meridionali_2025/dem.tif')
    pa.add_argument('--out', required=True)
    a = pa.parse_args()

    hs, _, pind, t = load(a.run)
    when = pd.Timestamp(a.date) + pd.Timedelta(hours=a.hour)
    k = np.flatnonzero(t == when)
    if len(k) != 1:
        raise SystemExit(f'{when} not in the output ({t[0]} .. {t[-1]})')
    vals = np.round(hs[k[0]] * 100, 1).astype('float32')                     # m -> cm

    ds = xr.open_dataset(Path(a.terrain) / 'ds_param.nc')
    z_shape = ds.point_ind.transpose('y', 'x').shape
    pi = ds.point_ind.transpose('y', 'x').values.ravel()
    ok = pi >= 0
    pos = pd.Series(np.arange(len(pind)), index=pind).reindex(pi[ok]).values
    if np.isnan(pos).any():
        raise ValueError('pixels of clusters that are not in the Crocus output')
    pos = pos.astype('int64')
    with rasterio.open(a.dem) as r:
        prof = r.profile
        if (r.height, r.width) != z_shape:
            raise ValueError(f'DEM {r.height}x{r.width} != cluster grid {z_shape}')

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write(out, [vals], [f'snow depth (cm), Crocus, {when:%Y-%m-%d %H:%M} UTC'], z_shape, ok, pos, prof)

    rng = np.random.default_rng(0)
    smp = rng.choice(np.flatnonzero(ok), 2000, replace=False)
    rows, cols = np.unravel_index(smp, z_shape)
    kk = pos[np.searchsorted(np.flatnonzero(ok), smp)]
    worst = check(out, [vals], rows, cols, kk)
    print(f'{out}: {len(pind)} clusters, {when} UTC; snow depth cm over clusters: median {np.median(vals):.1f}, '
          f'max {vals.max():.1f}, clusters with >= 1 cm {int((vals >= 1).sum())}; '
          f'check 2000 pixels max |map - cluster| = {worst:.1e}')
    if worst > 1e-3:
        raise SystemExit('CHECK FAILED')


if __name__ == '__main__':
    main()
