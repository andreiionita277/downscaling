"""
Run one year of the Meridional Carpathians workflow with the PUBLIC TopoPyScale fork (commit 7d54b9a),
natively (no Docker). Variant of run_year.py for a fork without Nandhni's 2026-10-13 changes.

    python meridionali_zarr/scripts/run_year_public.py meridionali_zarr/config/config_meridionali_2025.yml
           [--stop-after terrain|era5|downscale] [--months 1 2 ...] [--redo-products] [--var t]
           [--stats mean min max]

Differences from run_year.py (and why):
  - terrain caches (ds_param, ds_solar, da_horizon) are NetCDF: the public fork writes NetCDF only.
  - horizon: horizon_batched.compute_horizon_batched (a few azimuths at a time, float32, resumable)
    instead of topo_param.compute_horizon, which needs ~40 GB at 46 M px. Same values (checked on the
    Bucegi DEM: max |diff| 6e-8 rad = float32 rounding).
  - the hourly store is filled for the WHOLE YEAR in one downscale_climate call (topo_scale_zarr,
    resumable per cluster), because the public fork has no zarr_store_start/end (month-by-month filling).
    Daily statistics, GeoTIFFs and their checks are then done month by month from that store, with the
    same functions as run_year.py (workflow.py).
Every step is skipped when its output exists, so after a crash the same command resumes.
"""
import argparse
import gc
import json
import sys
import time
from pathlib import Path

import pandas as pd
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import horizon_batched as hb  # noqa: E402
import workflow as wf  # noqa: E402

from TopoPyScale import topo_daily  # noqa: E402
from TopoPyScale import topo_scale_zarr as tz  # noqa: E402
from TopoPyScale import topoclass as tc  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--stop-after', choices=['terrain', 'era5', 'downscale'])
    p.add_argument('--months', type=int, nargs='+', help='only these months (1-12); default all')
    p.add_argument('--redo-products', action='store_true', help='rewrite daily/GeoTIFFs of done months')
    p.add_argument('--var', default='t')
    p.add_argument('--stats', nargs='+', default=['mean', 'min', 'max'])
    p.add_argument('--horizon-batch', type=int, default=8,
                   help='azimuths computed at once (one process each, ~1 GB each at 46 M px)')
    a = p.parse_args()

    cfg, out, dem = wf.load_config(a.config)
    t_all = time.time()
    timer = wf.Timer()
    print(f'Run started {pd.Timestamp.now():%Y-%m-%d %H:%M:%S}; config {a.config}', flush=True)

    mp = tc.Topoclass(a.config)
    year0, year1 = pd.Timestamp(mp.config.project.start), pd.Timestamp(mp.config.project.end)
    if not mp.config.outputs.file.zarr_store or not mp.config.climate.era5.zarr_store:
        raise ValueError('config: climate.era5.zarr_store and outputs.file.zarr_store are required')

    # ---- terrain, once (Topoclass skips each step whose cache exists)
    timer('compute_dem_param', mp.compute_dem_param)
    timer('extract_topo_param', mp.extract_topo_param)
    timer('compute_solar_geometry', mp.compute_solar_geometry)
    f_hor = mp.config.outputs.path / mp.config.outputs.file.da_horizon
    if not f_hor.exists():
        timer('compute_horizon_batched', hb.compute_horizon_batched, mp.config.dem.filepath,
              mp.config.dem.horizon_increments, f_hor, batch=a.horizon_batch, n_procs=a.horizon_batch)
    timer('compute_horizon (load + sample at centroids)', mp.compute_horizon)
    # The downscaler only needs the horizon sampled into df_centroids (hori_azi_*); keep the rest lazy.
    mp.toposub.ds_param = xr.open_dataset(mp.config.outputs.path / mp.config.outputs.file.ds_param)
    gc.collect()
    if a.stop_after == 'terrain':
        return finish(mp, timer, t_all, {}, a)

    # ---- ERA5 (EDH -> ERA5.zarr; fetch_era5_edh resumes from its finished blocks)
    timer('get_era5', mp.get_era5)
    if a.stop_after == 'era5':
        return finish(mp, timer, t_all, {}, a)

    # ---- hourly downscaling, whole year, one store (resumable per cluster)
    timer('downscale_climate', mp.downscale_climate)
    if a.stop_after == 'downscale':
        return finish(mp, timer, t_all, {}, a)

    # ---- per month: QA, daily statistics, GeoTIFFs, checks
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
        if marker.exists() and json.loads(marker.read_text()).get('status') == 'ok' and not a.redo_products:
            print(f'\n#################### {ym}: complete (see {marker.name}), skipped', flush=True)
            results[ym] = 'ok (earlier)'
            continue
        print(f'\n#################### {ym}: {m0.date()} .. {m1.date()}', flush=True)
        mt = wf.Timer()
        rec = dict(month=ym, start=str(m0.date()), end=str(m1.date()), started=str(pd.Timestamp.now()))
        try:
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
            daily = mt('daily_stats', topo_daily.daily_stats, hourly, utc_offset_hours=0)
            daily = daily.assign_coords(point_name=daily.point_name.values.astype(str))
            mt('write_daily', wf.write_daily_month, daily_path, daily, year0, year1)
            if daily[f'{a.var}_mean'].isnull().any():
                qa_ok = False
                rec['daily_nan'] = True
            if idx is None:
                idx, profile, pn = mt('pixel_index', wf.pixel_index, ds_param_path, dem, daily.point_name.values)
            files = mt('export_geotiff', wf.export_month, daily, idx, profile, geotiff_dir, a.var, a.stats, cfg,
                       overwrite=a.redo_products)
            chk_ok, chk = mt('check_geotiff', wf.check_month, files, daily, pn, mask_file, dem, a.var)
            rec['geotiff_checks'] = chk
            rec['status'] = 'ok' if (qa_ok and chk_ok) else 'failed checks'
        except Exception:
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
            break
    return finish(mp, timer, t_all, results, a)


def finish(mp, timer, t_all, results, a):
    summary = dict(config=str(a.config), finished=str(pd.Timestamp.now()), stop_after=a.stop_after,
                   months=results, steps_s=timer.t, total_s=round(time.time() - t_all, 1),
                   n_points=int(len(mp.toposub.df_centroids)) if mp.toposub.df_centroids is not None else None)
    wf.write_json(mp.config.outputs.path / 'year_summary.json', summary)
    print('\n' + '\n'.join(f'{k}: {v}' for k, v in results.items()), flush=True)
    if a.stop_after:
        print(f'STOPPED AFTER {a.stop_after.upper()} (as requested)', flush=True)
        sys.exit(0)
    all_ok = all(str(v).startswith('ok') for v in results.values()) and len(results) > 0
    print('YEAR COMPLETE, ALL CHECKS PASSED' if all_ok else 'NOT COMPLETE / SOME CHECKS FAILED', flush=True)
    sys.exit(0 if all_ok else 1)


# The guard is required: multiprocessing workers (forkserver/spawn) may import this file.
if __name__ == '__main__':
    main()
