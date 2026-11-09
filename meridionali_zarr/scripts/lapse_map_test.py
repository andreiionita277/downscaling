"""
TEST: daily temperature map with a per-pixel elevation (lapse-rate) adjustment, instead of each pixel taking its
cluster's value unchanged. Removes the steps at the clustering-group boundaries and the within-cluster elevation
error. The hourly/daily stores and the cluster values are not changed; only a separate GeoTIFF is written.

    python meridionali_zarr/scripts/lapse_map_test.py meridionali_zarr/config/config_meridionali_F_2025-01.yml
           [--date 2025-01-01] [--var t_mean] [--k 50] [--clip 0.015]

Per day: for every cluster, a local lapse rate = slope of a linear fit of the cluster values against centre
elevation over its k nearest clusters (x/y distance, itself included), clipped to +-clip K/m (inversions allowed).
    pixel value = cluster value + lapse(cluster) * (pixel elevation - centre elevation),  lapse = dT/dz (< 0 normally)

Output: <outputs>/geotiff_lapse_test/<var>_<date>_lapse.tif (degC; tiled, DEFLATE, AVERAGE overviews)
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from rasterio.enums import Resampling
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow as wf  # noqa: E402


def local_lapse(x, y, z, v, k, clip):
    """Slope dv/dz of a least-squares line over each point's k nearest neighbours (x, y), clipped."""
    _, nb = cKDTree(np.c_[x, y]).query(np.c_[x, y], k=k)
    zz, vv = z[nb], v[nb]
    zc = zz - zz.mean(axis=1, keepdims=True)
    slope = (zc * (vv - vv.mean(axis=1, keepdims=True))).sum(axis=1) / (zc ** 2).sum(axis=1)
    return np.clip(slope, -clip, clip)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--date', default='2025-01-01')
    p.add_argument('--var', default='t_mean')
    p.add_argument('--k', type=int, default=50)
    p.add_argument('--clip', type=float, default=0.015, help='K/m')
    a = p.parse_args()
    cfg, out, dem = wf.load_config(a.config)

    c = pd.read_pickle(out / cfg['outputs']['file']['df_centroids'])
    daily = xr.open_zarr(out / 'downscaled' / 'daily.zarr')
    val = daily[a.var].sel(time=a.date).load()
    v = val.sel(point_name=c.point_name.values).values - wf.OFFSET.get('t', 0)
    lapse = local_lapse(c.x.values, c.y.values, c.elevation.values, v, a.k, a.clip)
    print(f'{a.date} {a.var}: local lapse over {a.k} nearest clusters, K/100 m: '
          f'p5 {np.percentile(lapse, 5) * 100:+.2f}, median {np.median(lapse) * 100:+.2f}, '
          f'p95 {np.percentile(lapse, 95) * 100:+.2f}; clipped {(np.abs(lapse) >= a.clip).sum()} of {len(lapse)}',
          flush=True)

    ds = xr.open_dataset(out / cfg['outputs']['file']['ds_param'])
    z = ds.elevation.transpose('y', 'x').values
    pi = ds.point_ind.transpose('y', 'x').values.ravel()
    ok = pi >= 0
    pos = pd.Series(np.arange(len(c)), index=c.point_ind.values).reindex(pi[ok]).values
    corr = np.full(z.size, np.nan, 'float32')
    base = corr.copy()
    base[ok] = v[pos]
    corr[ok] = v[pos] + lapse[pos] * (z.ravel()[ok] - c.elevation.values[pos])     # lapse = dT/dz (signed)
    d = corr[ok] - base[ok]
    print(f'adjustment (adjusted - cluster value), K: mean {d.mean():+.3f}, |d| p95 {np.percentile(np.abs(d), 95):.3f}, '
          f'max |d| {np.abs(d).max():.3f}', flush=True)

    res_dir = out / 'geotiff_lapse_test'
    res_dir.mkdir(exist_ok=True)
    f = res_dir / f'{a.var}_{a.date}_lapse.tif'
    with rasterio.open(dem) as r:
        prof = r.profile
    nodata = -9999.0
    corr[~ok] = nodata
    prof.update(dtype='float32', count=1, nodata=nodata, compress='deflate', predictor=3, tiled=True,
                blockxsize=512, blockysize=512, BIGTIFF='IF_SAFER')
    with rasterio.open(f, 'w', **prof) as w:
        w.write(corr.reshape(z.shape), 1)
        w.set_band_description(1, a.date)
        w.build_overviews([2, 4, 8, 16, 32], Resampling.average)   # never nearest for continuous data
    print(f'---> {f}', flush=True)


if __name__ == '__main__':
    main()
