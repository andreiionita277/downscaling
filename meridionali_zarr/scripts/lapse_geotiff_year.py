"""
Daily temperature GeoTIFFs with the per-pixel lapse-rate adjustment (decision 27 Oct, LOG_27Oct.md 19:50), for
one downscaled year. One file per variable and month, one band per day, degC, float32. The Zarr stores are only
read, never changed. Method as in lapse_map_test.py, per day and cluster k:
    T(p) = T_k + Gamma_k * (z_p - z_k)
    Gamma_k = least-squares slope dT/dz of the day's cluster values against centre elevation over the k nearest
              clusters (x/y distance, itself included), clipped to +-clip K/m (inversions allowed)

    python meridionali_zarr/scripts/lapse_geotiff_year.py meridionali_zarr/config/config_meridionali_E_2025.yml
           [--vars t_mean t_min t_max] [--months 1 2 ...] [--workers 6] [--k 50] [--clip 0.015] [--overwrite]
           [--dest /mnt/d/Downscaling/meridionali_zarr/meridionali_E_2025/geotiff_lapse]

Output: <outputs>/geotiff_lapse/<var>_daily_lapse_<YYYY-MM>.tif (tiled 512, DEFLATE + float predictor, AVERAGE
overviews 2-32) and <outputs>/geotiff_lapse/checks/<var>_<YYYY-MM>.json. Each file is checked independently
after writing: band count and dates, nodata exactly outside the clustering mask, and 2000 random pixels on 3 days
recomputed with np.polyfit from the stores (tolerance 1e-3 K). A file that fails its check is renamed *.FAILED.tif.
With --dest, each file that passes is copied there (size compared) and deleted locally, so only the files being
written take space on the WSL disk (its .vhdx on C: grows but does not shrink by itself).
"""
import argparse
import calendar
import json
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from rasterio.enums import Resampling
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow as wf  # noqa: E402
from lapse_map_test import local_lapse  # noqa: E402

NODATA = -9999.0


def load_static(cfg_path):
    """Cluster table, store paths and the pixel -> cluster lookup (month-independent)."""
    cfg, out, dem = wf.load_config(cfg_path)
    c = pd.read_pickle(out / cfg['outputs']['file']['df_centroids'])
    ds = xr.open_dataset(out / cfg['outputs']['file']['ds_param'])
    z = ds.elevation.transpose('y', 'x').values.astype('float32')
    pi = ds.point_ind.transpose('y', 'x').values.ravel()
    ok = pi >= 0
    pos = pd.Series(np.arange(len(c)), index=c.point_ind.values).reindex(pi[ok]).values.astype('int32')
    return cfg, out, dem, c, z, ok, pos


def day_values(daily, var, day, c):
    return daily[var].sel(time=day).load().sel(point_name=c.point_name.values).values - wf.OFFSET.get('t', 0)


def export(cfg_path, var, year, month, k, clip, overwrite, dest=None):
    t0 = time.time()
    cfg, out, dem, c, z, ok, pos = load_static(cfg_path)
    odir = out / 'geotiff_lapse'
    f = odir / f'{var}_daily_lapse_{year}-{month:02d}.tif'
    if (f.exists() or (dest and (Path(dest) / f.name).exists())) and not overwrite:
        return var, month, 'exists', 0.0
    daily = xr.open_zarr(out / 'downscaled' / 'daily.zarr')
    days = pd.date_range(f'{year}-{month:02d}-01', periods=calendar.monthrange(year, month)[1], freq='D')
    with rasterio.open(dem) as r:
        prof = r.profile
    prof.update(driver='GTiff', dtype='float32', count=len(days), nodata=NODATA, compress='deflate', predictor=3,
                tiled=True, blockxsize=512, blockysize=512, BIGTIFF='IF_SAFER', interleave='band')
    tmp = f.with_suffix('.part.tif')
    zk = c.elevation.values
    dz = z.ravel()[ok] - zk[pos]
    band = np.full(z.size, NODATA, 'float32')
    lapse_stats = []
    with rasterio.open(tmp, 'w', **prof) as w:
        for b, day in enumerate(days, 1):
            v = day_values(daily, var, day, c)
            lapse = local_lapse(c.x.values, c.y.values, zk, v, k, clip)
            lapse_stats.append([np.percentile(lapse, 5) * 100, np.median(lapse) * 100,
                                np.percentile(lapse, 95) * 100, int((np.abs(lapse) >= clip).sum())])
            band[ok] = v[pos] + lapse[pos] * dz                 # lapse = dT/dz (signed); pixel - centre elevation
            w.write(band.reshape(z.shape), b)
            w.set_band_description(b, day.strftime('%Y-%m-%d'))
        w.update_tags(variable=var, units='degC', year=str(year), month=f'{month:02d}',
                      method='T(p) = T_k + Gamma_k * (z_p - z_k); Gamma_k = least-squares dT/dz over the '
                             f'{k} nearest clusters (x/y), clipped +-{clip} K/m; per day and variable',
                      source=f'{out}/downscaled/daily.zarr', nodata_meaning='outside clustering mask',
                      run=str(cfg['project']['name']))
    with rasterio.open(tmp, 'r+') as w:
        w.build_overviews([2, 4, 8, 16, 32], Resampling.average)   # never nearest for continuous data
        w.update_tags(ns='rio_overview', resampling='average')
    tmp.rename(f)
    ok_chk, chk = check(f, cfg_path, var, days, k, clip)
    chk['lapse_K_per_100m_p5_median_p95_nclipped_per_day'] = [[round(x, 3) for x in s[:3]] + [s[3]]
                                                               for s in lapse_stats]
    chk['seconds'] = round(time.time() - t0)
    chk['bytes'] = f.stat().st_size
    (odir / 'checks').mkdir(exist_ok=True)
    (odir / 'checks' / f'{var}_{year}-{month:02d}.json').write_text(json.dumps(chk, indent=1))
    if not ok_chk:
        f.rename(f.with_suffix('.FAILED.tif'))
    elif dest:
        move(f, Path(dest) / f.name)
        move(odir / 'checks' / f'{var}_{year}-{month:02d}.json', Path(dest) / 'checks' / f'{var}_{year}-{month:02d}.json')
    return var, month, 'ok' if ok_chk else f'FAILED: {chk["errors"]}', time.time() - t0


def move(src, dst):
    """Copy to dst (e.g. D:), compare size, then delete the local file (keeps the WSL disk small)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + '.part')
    shutil.copyfile(src, part)
    if part.stat().st_size != src.stat().st_size:
        raise OSError(f'copy of {src} to {part} has the wrong size')
    part.rename(dst)
    src.unlink()


def check(f, cfg_path, var, days, k, clip, n=2000, seed=0):
    """Independent re-computation of sample pixels (np.polyfit instead of the vectorised slope)."""
    cfg, out, dem, c, z, ok, pos = load_static(cfg_path)
    daily = xr.open_zarr(out / 'downscaled' / 'daily.zarr')
    errs, res = [], {}
    rng = np.random.default_rng(seed)
    with rasterio.open(f) as r:
        if r.count != len(days):
            errs.append(f'{r.count} bands, expected {len(days)}')
        if list(r.descriptions) != [d.strftime('%Y-%m-%d') for d in days]:
            errs.append('band descriptions != dates')
        nd = r.read(1) == NODATA
        if not np.array_equal(nd.ravel(), ~ok):
            errs.append(f'nodata pixels != outside mask ({int((nd.ravel() != ~ok).sum())} differ)')
        if len(r.overviews(1)) != 5:
            errs.append(f'overviews {r.overviews(1)}')
        flat = np.flatnonzero(ok)
        sample = rng.choice(flat, n, replace=False)
        rows, cols = np.unravel_index(sample, z.shape)
        tree = cKDTree(np.c_[c.x.values, c.y.values])
        zk = c.elevation.values
        max_diff, n_bad_range = 0.0, 0
        for b in sorted({1, len(days) // 2 + 1, len(days)}):
            img = r.read(b)
            got = img[rows, cols]
            v = day_values(daily, var, days[b - 1], c)
            kk = pos[np.searchsorted(flat, sample)]
            exp = np.empty(n)
            for i, ci in enumerate(kk):
                _, nb = tree.query([c.x.values[ci], c.y.values[ci]], k=k)
                g = np.clip(np.polyfit(zk[nb], v[nb], 1)[0], -clip, clip)
                exp[i] = v[ci] + g * (z[rows[i], cols[i]] - zk[ci])
            max_diff = max(max_diff, float(np.abs(got - exp).max()))
            vals = img[img != NODATA]
            n_bad_range += int(((vals < -60) | (vals > 50) | ~np.isfinite(vals)).sum())
            res[f'band{b}'] = dict(mean=round(float(vals.mean()), 3), min=round(float(vals.min()), 2),
                                   max=round(float(vals.max()), 2),
                                   cluster_mean=round(float(np.average(v, weights=np.bincount(pos, minlength=len(c)))), 3))
        if max_diff > 1e-3:
            errs.append(f'sample pixels differ from formula by up to {max_diff:.4f} K')
        if n_bad_range:
            errs.append(f'{n_bad_range} values outside -60..50 degC or not finite')
    res.update(max_abs_diff_sample_K=max_diff, n_sample=n, errors=errs)
    return not errs, res


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--vars', nargs='+', default=['t_mean', 't_min', 't_max'])
    p.add_argument('--months', nargs='+', type=int, default=list(range(1, 13)))
    p.add_argument('--workers', type=int, default=6)
    p.add_argument('--k', type=int, default=50)
    p.add_argument('--clip', type=float, default=0.015, help='K/m')
    p.add_argument('--overwrite', action='store_true')
    p.add_argument('--dest', help='move each checked file (and its check JSON) here, e.g. a folder on /mnt/d')
    a = p.parse_args()
    cfg, out, _ = wf.load_config(a.config)
    year = int(pd.Timestamp(cfg['project']['start']).year)
    (out / 'geotiff_lapse').mkdir(exist_ok=True)
    jobs = [(v, m) for m in a.months for v in a.vars]
    print(f'{year}: {len(jobs)} files, {a.workers} workers -> {out / "geotiff_lapse"}', flush=True)
    t0, n_fail = time.time(), 0
    with ProcessPoolExecutor(a.workers) as ex:
        fut = [ex.submit(export, a.config, v, year, m, a.k, a.clip, a.overwrite, a.dest) for v, m in jobs]
        for fu in as_completed(fut):
            v, m, st, s = fu.result()
            n_fail += not (st in ('ok', 'exists'))
            print(f'{time.strftime("%H:%M:%S")} {v} {year}-{m:02d}: {st} ({s:.0f}s)', flush=True)
    print(f'done in {(time.time() - t0) / 60:.1f} min, {n_fail} failed', flush=True)
    sys.exit(1 if n_fail else 0)


if __name__ == '__main__':
    main()
