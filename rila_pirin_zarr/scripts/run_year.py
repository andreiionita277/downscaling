"""
Run one year of the Rila-Pirin FABDEM run (rila_pirin_zarr), month by month, into Zarr.
Copy of zarr_workflow/scripts/run_year.py; changes: shared terrain caches, aspect-fix check, GeoTIFFs.

    python rila_pirin_zarr/scripts/run_year.py rila_pirin_zarr/config/years/config_2020.yml [--months 1 2 ...]
           [--terrain DIR] [--daily-geotiff] [--var t] [--stats mean min max]

Terrain (prepare_terrain.py, once for all years): ds_param.zarr and da_horizon.zarr are linked from --terrain
(default data/terrain/outputs), df_centroids.pck is copied (compute_horizon rewrites it). The terrain is never
recomputed or rewritten here: compute_dem_param is skipped (the fork would rewrite the cached store).
Once for the year:
    2 extract_topo_param      -> loads outputs/df_centroids.pck (must carry the centroid aspect fix)
    3 compute_solar_geometry  -> outputs/ds_solar.zarr    (whole year)
    4 compute_horizon         -> samples the linked da_horizon.zarr at the centroids
    5 get_era5                -> climate path/ERA5.zarr   (Earth Data Hub, resumable; or, with
                                 data_repository: local, only a completeness check of the store
                                 prepared by zarr_workflow/era5_download)
Then for every month (a month is skipped when outputs/months/<YYYY-MM>.json says it is complete):
    6 downscale_climate       -> its time slice of outputs/downscaled/down.zarr (hourly, whole year store)
    7 QA of the month's hourly values (no NaN, physical bounds)
    8 daily statistics        -> its days in outputs/downscaled/daily.zarr
    9 only with --daily-geotiff: daily GeoTIFFs + checks (outputs/geotiff/<var>_<stat>_daily_<YYYY-MM>.tif)
   10 outputs/months/<YYYY-MM>.json (status, timings, QA)
When every month is ok: outputs/geotiff/<var>_mean_monthly_<YYYY>.tif (12 bands, monthly mean of the daily
mean, for viewing; checked against daily.zarr). Year summary -> outputs/year_summary.json. Exit code 0 only if
every month passed.
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow as wf  # noqa: E402

from TopoPyScale import topo_daily  # noqa: E402
from TopoPyScale import topo_scale_zarr as tz  # noqa: E402
from TopoPyScale import topoclass as tc  # noqa: E402
from TopoPyScale import topo_utils as tu  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--months', type=int, nargs='+', help='only these months (1-12); default all')
    p.add_argument('--redo-products', action='store_true', help='rewrite daily/GeoTIFFs of done months')
    p.add_argument('--var', default='t')
    p.add_argument('--stats', nargs='+', default=['mean', 'min', 'max'])
    p.add_argument('--terrain', default=str(Path(__file__).resolve().parents[1] / 'data' / 'terrain' / 'outputs'))
    p.add_argument('--daily-geotiff', action='store_true', help='also daily GeoTIFFs per month (off: Zarr only)')
    a = p.parse_args()

    cfg, out, dem = wf.load_config(a.config)
    t_all = time.time()
    timer = wf.Timer()
    print(f'Run started {pd.Timestamp.now():%Y-%m-%d %H:%M:%S}; config {a.config}', flush=True)

    link_terrain(Path(cfg['project']['directory']) / cfg['outputs']['directory'], Path(a.terrain), cfg)
    mp = tc.Topoclass(a.config)
    year0, year1 = pd.Timestamp(mp.config.project.start), pd.Timestamp(mp.config.project.end)
    if not mp.config.outputs.file.zarr_store or not mp.config.outputs.file.zarr_store_start:
        raise ValueError('config: outputs.file.zarr_store and zarr_store_start/end are required')

    # ---- once for the year
    mp.toposub.ds_param = tu.open_dataset(mp.config.outputs.path / mp.config.outputs.file.ds_param)
    timer('extract_topo_param', mp.extract_topo_param)
    if 'aspect_arith' not in mp.toposub.df_centroids:
        raise ValueError('df_centroids.pck without the centroid aspect fix (prepare_terrain.py)')
    timer('compute_solar_geometry', mp.compute_solar_geometry)
    timer('compute_horizon', mp.compute_horizon)
    era = cfg['climate']['era5']
    if era.get('data_repository') == 'local':
        # the store was prepared by zarr_workflow/era5_download (CDS/NCAR, 600-1000 hPa): only check it
        sel = [(m0, m1) for m0, m1 in wf.month_periods(year0, year1) if not a.months or m0.month in a.months]
        timer('get_era5', wf.check_era5_store, mp.config.climate.path / era['zarr_store'],
              sel[0][0], sel[-1][1], era.get('plevels'))     # the months this run will downscale
    else:
        timer('get_era5', mp.get_era5)
    year_timings = dict(timer.t)
    # Free memory: after a fresh computation Topoclass holds the full horizon (float64, ~7 GB) and
    # terrain arrays in memory (~20 GB RSS). The downscaler only needs the horizon angles already
    # sampled into df_centroids (hori_azi_*), so reopen both lazily from their Zarr caches.
    mp.da_horizon = tu.open_dataarray(mp.config.outputs.path / mp.config.outputs.file.da_horizon)
    mp.toposub.ds_param = tu.open_dataset(mp.config.outputs.path / mp.config.outputs.file.ds_param)
    import gc
    gc.collect()

    store = mp.config.outputs.downscaled / mp.config.outputs.file.zarr_store
    daily_path = mp.config.outputs.downscaled / 'daily.zarr'
    geotiff_dir = mp.config.outputs.path / 'geotiff'
    months_dir = mp.config.outputs.path / 'months'
    ds_param_path = mp.config.outputs.path / mp.config.outputs.file.ds_param
    mask_file = cfg['sampling']['toposub']['clustering_mask']
    idx = profile = pn = None

    results = {}
    for m0, m1 in wf.month_periods(year0, year1):
        if a.months and m0.month not in a.months:
            continue
        ym = f'{m0:%Y-%m}'
        marker = months_dir / f'{ym}.json'
        prev = None
        if marker.exists():
            import json
            prev = json.loads(marker.read_text())
            if prev.get('status') == 'ok' and not a.redo_products:
                print(f'\n#################### {ym}: complete (see {marker.name}), skipped', flush=True)
                results[ym] = 'ok (earlier)'
                continue
        print(f'\n#################### {ym}: {m0.date()} .. {m1.date()}', flush=True)
        mt = wf.Timer()
        rec = dict(month=ym, start=str(m0.date()), end=str(m1.date()), started=str(pd.Timestamp.now()))
        try:
            # 6 downscale this month into its slice of the yearly store (resumable per point)
            mp.config.project.start, mp.config.project.end = m0.date(), m1.date()
            mt('downscale_climate', mp.downscale_climate)
            # 7 QA of the hourly values
            hourly = tz.open_downscaled_store(store).sel(time=slice(m0, m1 + pd.Timedelta('23h'))).load()
            n_expected = int((m1 - m0) / pd.Timedelta('1h')) + 24
            qa_ok, qa = mt('qa_hourly', wf.qa_hourly, hourly)
            if hourly.sizes['time'] != n_expected:
                qa_ok = False
                qa['time_steps'] = f'{hourly.sizes["time"]} != {n_expected}'
            rec['qa_hourly'] = qa
            print(f'     QA hourly: {"OK" if qa_ok else "FAIL"}; t {qa["t"]["min"] - 273.15:.1f}..'
                  f'{qa["t"]["max"] - 273.15:.1f} degC, SW values above bound: {qa["SW"]["n_outside_bounds"]}',
                  flush=True)
            # 8 daily statistics (UTC days) into the yearly daily store
            daily = mt('daily_stats', topo_daily.daily_stats, hourly, utc_offset_hours=0)
            daily = daily.assign_coords(point_name=daily.point_name.values.astype(str))
            mt('write_daily', wf.write_daily_month, daily_path, daily, year0, year1)
            if daily[f'{a.var}_mean'].isnull().any():
                qa_ok = False
                rec['daily_nan'] = True
            if not a.daily_geotiff:
                rec['status'] = 'ok' if qa_ok else 'failed checks'
                raise _MonthDone
            # 9 GeoTIFFs, 10 checks
            if idx is None:
                idx, profile, pn = mt('pixel_index', wf.pixel_index, ds_param_path, dem,
                                      daily.point_name.values)
            files = mt('export_geotiff', wf.export_month, daily, idx, profile, geotiff_dir, a.var, a.stats, cfg,
                       overwrite=a.redo_products)
            chk_ok, chk = mt('check_geotiff', wf.check_month, files, daily, pn, mask_file, dem, a.var)
            rec['geotiff_checks'] = chk
            rec['status'] = 'ok' if (qa_ok and chk_ok) else 'failed checks'
        except _MonthDone:
            pass
        except Exception as e:
            import traceback
            rec['status'] = 'error'
            rec['error'] = traceback.format_exc()
            print(rec['error'], flush=True)
        rec['timings_s'] = mt.t
        rec['finished'] = str(pd.Timestamp.now())
        wf.write_json(marker, rec)
        results[ym] = rec['status']
        print(f'#################### {ym}: {rec["status"]} ({sum(mt.t.values()):.0f} s)', flush=True)
        if rec['status'] == 'error':
            break      # stop at the first error; a rerun resumes from this month

    mp.config.project.start, mp.config.project.end = year0.date(), year1.date()
    all_ok = all(str(v).startswith('ok') for v in results.values()) and len(results) > 0
    monthly_tif = None
    if all_ok and not a.months:
        dst = geotiff_dir / f'{a.var}_mean_monthly_{year0.year}.tif'
        try:
            names = xr.open_zarr(daily_path).point_name.values.astype(str)
            idx, profile, pn = timer('pixel_index', wf.pixel_index, ds_param_path, dem, names)
            ok, monthly_tif = timer('export_year_monthly', wf.export_year_monthly, daily_path, idx, profile, dst,
                                    a.var, 'mean', cfg)
            print(f'---> {dst.name}: {"OK" if ok else "CHECK FAILED"} {monthly_tif}', flush=True)
            all_ok = all_ok and ok
        except Exception:
            import traceback
            monthly_tif = dict(error=traceback.format_exc())
            print(monthly_tif['error'], flush=True)
            all_ok = False
    summary = dict(config=str(a.config), finished=str(pd.Timestamp.now()), months=results,
                   year_steps_s=year_timings, total_s=round(time.time() - t_all, 1),
                   n_points=int(len(mp.toposub.df_centroids)), monthly_geotiff=monthly_tif)
    wf.write_json(mp.config.outputs.path / 'year_summary.json', summary)
    print('\n' + '\n'.join(f'{k}: {v}' for k, v in results.items()), flush=True)
    print('YEAR COMPLETE, ALL CHECKS PASSED' if all_ok else 'NOT COMPLETE / SOME CHECKS FAILED', flush=True)
    sys.exit(0 if all_ok else 1)


class _MonthDone(Exception):
    """Ends a month's try block early when no daily GeoTIFFs are made (status already set)."""


def link_terrain(out, terrain, cfg):
    """ds_param / da_horizon linked (relative symlinks, read only here), df_centroids copied."""
    out.mkdir(parents=True, exist_ok=True)
    files = cfg['outputs']['file']
    for name in (files['ds_param'], files['da_horizon']):
        src, dst = terrain / name, out / name
        if not src.exists():
            raise FileNotFoundError(f'{src}: run prepare_terrain.py first')
        if not dst.is_symlink() and not dst.exists():
            dst.symlink_to(os.path.relpath(src, out))
    if not (out / files['df_centroids']).exists():
        shutil.copy2(terrain / files['df_centroids'], out / files['df_centroids'])


# The guard is required: multiprocessing workers (forkserver/spawn) may import this file.
if __name__ == '__main__':
    main()
