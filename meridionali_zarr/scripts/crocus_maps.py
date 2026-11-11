"""
Snow maps (GeoTIFF) from the Crocus PRO files of crocus_parallel.py: every pixel gets the value of its cluster (the
clusters are the Crocus points; no elevation adjustment for snow).

    python meridionali_zarr/scripts/crocus_maps.py meridionali_zarr/data/crocus_E/2024-2025 \
           --terrain meridionali_zarr/data/meridionali_F_2025/outputs \
           --dest /mnt/d/Downscaling/meridionali_zarr/crocus_E_2024-2025

Output (written locally in <run>/maps/, checked, then moved to --dest):
  snow_depth_daily_06utc_<YYYY-MM>.tif   snow depth at 06 UTC (cm, the time stations measure it), one band per day
  summary/snow_depth_max_cm.tif          maximum snow depth of the period (cm, from the 6-hourly output)
  summary/snow_days_1cm.tif              days with >= 1 cm of snow at 06 UTC
  summary/snow_first_day.tif, snow_last_day.tif   first / last day with >= 1 cm at 06 UTC (day of the period, 1 = first day)
  summary/snow_depth_mean_DJF_cm.tif     mean snow depth at 06 UTC, December-February (cm)
  summary/swe_max_mm.tif                 maximum snow water equivalent (kg/m2 = mm)
float32, nodata -9999 outside the clustering mask, tiled 512, DEFLATE, AVERAGE overviews. Check: for every file, 2000
random pixels equal the PRO value of their cluster.
"""
import argparse
import glob
import shutil
from pathlib import Path

import netCDF4
import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from rasterio.enums import Resampling

NODATA = -9999.0


def load(run):
    """Snow depth (m) and SWE (kg/m2) of all chunks, columns ordered by cluster (point_ind), and the times."""
    hs, swe, pind, t = [], [], [], None
    for d in sorted(glob.glob(f'{run}/chunk_*')):
        p = xr.open_dataset(glob.glob(f'{d}/output/pro/PRO_*.nc')[0], decode_times=False)
        for lst, v in ((hs, p.DSN_T_ISBA), (swe, p.WSN_T_ISBA)):
            if 'Number_of_Patches' in v.dims:
                v = v.isel(Number_of_Patches=0)
            lst.append(v.transpose('time', 'Number_of_points').values)
        pind.append(pd.read_csv(f'{d}/points.csv').point_ind.values)
        if t is None:
            t = pd.DatetimeIndex(netCDF4.num2date(p.time.values, p.time.units, only_use_cftime_datetimes=False,
                                                  only_use_python_datetimes=True))
    return np.concatenate(hs, 1), np.concatenate(swe, 1), np.concatenate(pind), t


def write(path, bands, descs, z_shape, ok, pos, prof):
    p = prof | dict(driver='GTiff', dtype='float32', count=len(bands), nodata=NODATA, compress='deflate', predictor=3,
                    tiled=True, blockxsize=512, blockysize=512, BIGTIFF='IF_SAFER', interleave='band')
    out = np.full(np.prod(z_shape), NODATA, 'float32')
    with rasterio.open(path, 'w', **p) as w:
        for b, (vals, desc) in enumerate(zip(bands, descs), 1):
            out[ok] = vals[pos]
            w.write(out.reshape(z_shape), b)
            w.set_band_description(b, desc)
    with rasterio.open(path, 'r+') as w:
        w.build_overviews([2, 4, 8, 16, 32], Resampling.average)       # never nearest for continuous data


def check(path, bands, rows, cols, kk):
    with rasterio.open(path) as r:
        worst = 0.0
        for b in sorted({1, len(bands)}):
            got = r.read(b)[rows, cols]
            worst = max(worst, float(np.abs(got - bands[b - 1][kk]).max()))
    return worst


def main():
    pa = argparse.ArgumentParser()
    pa.add_argument('run')
    pa.add_argument('--terrain', required=True, help='outputs folder with ds_param.nc and df_centroids.pck of the setup')
    pa.add_argument('--dem', default='meridionali_zarr/data/dem/meridionali_2025/dem.tif')
    pa.add_argument('--dest')
    a = pa.parse_args()
    run = Path(a.run)
    loc = run / 'maps'
    (loc / 'summary').mkdir(parents=True, exist_ok=True)

    hs, swe, pind, t = load(run)
    print(f'{hs.shape[1]} clusters, {len(t)} time steps {t[0]} .. {t[-1]}', flush=True)
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
    rng = np.random.default_rng(0)
    smp = rng.choice(np.flatnonzero(ok), 2000, replace=False)
    rows, cols = np.unravel_index(smp, z_shape)
    kk = pos[np.searchsorted(np.flatnonzero(ok), smp)]

    h06 = pd.DataFrame(hs[t.hour == 6] * 100, index=t[t.hour == 6].normalize())      # cm at 06 UTC
    files = []
    for month, g in h06.groupby(h06.index.to_period('M')):
        f = loc / f'snow_depth_daily_06utc_{month}.tif'
        bands = [np.round(r, 1).astype('float32') for r in g.values]
        write(f, bands, [f'{d:%Y-%m-%d}' for d in g.index], z_shape, ok, pos, prof)
        files.append((f, bands))
        print(f'{f.name}: {len(bands)} days', flush=True)

    days = h06.index
    snow = h06.values >= 1.0
    first = np.where(snow.any(0), snow.argmax(0) + 1, 0).astype('float32')
    last = np.where(snow.any(0), len(days) - snow[::-1].argmax(0), 0).astype('float32')
    djf = days.month.isin([12, 1, 2])
    summary = {'snow_depth_max_cm': (np.round(hs.max(0) * 100, 1), 'maximum snow depth (cm)'),
               'snow_days_1cm': (snow.sum(0).astype('float32'), 'days with >= 1 cm of snow at 06 UTC'),
               'snow_first_day': (first, f'first day with >= 1 cm at 06 UTC (1 = {days[0]:%Y-%m-%d}; 0 = no snow)'),
               'snow_last_day': (last, f'last day with >= 1 cm at 06 UTC (1 = {days[0]:%Y-%m-%d}; 0 = no snow)'),
               'snow_depth_mean_DJF_cm': (np.round(h06.values[djf].mean(0), 1), 'mean snow depth at 06 UTC, Dec-Feb (cm)'),
               'swe_max_mm': (np.round(swe.max(0), 1), 'maximum snow water equivalent (mm)')}
    for name, (vals, desc) in summary.items():
        f = loc / 'summary' / f'{name}.tif'
        bands = [vals.astype('float32')]
        write(f, bands, [f'{desc}, {days[0]:%Y-%m-%d} .. {days[-1]:%Y-%m-%d}'], z_shape, ok, pos, prof)
        files.append((f, bands))
        print(f'{f.name}: median over clusters {np.median(vals):.1f}', flush=True)

    worst = max(check(f, b, rows, cols, kk) for f, b in files)
    print(f'check: {len(files)} files, 2000 pixels each, max |map - cluster value| = {worst:.2e}', flush=True)
    if worst > 1e-3:
        raise SystemExit('CHECK FAILED - files kept locally')
    if a.dest:
        dest = Path(a.dest)
        for f, _ in files:
            sub = dest / f.parent.relative_to(loc)
            sub.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, sub / (f.name + '.part'))
            if (sub / (f.name + '.part')).stat().st_size != f.stat().st_size:
                raise OSError(f'copy of {f.name} has the wrong size')
            (sub / (f.name + '.part')).rename(sub / f.name)
            f.unlink()
        print(f'moved {len(files)} files to {dest}', flush=True)


if __name__ == '__main__':
    main()
