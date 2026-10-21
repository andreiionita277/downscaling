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
import multiprocessing as mp_ctx
import sys
import time
from pathlib import Path

import pandas as pd
import rasterio
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
    p.add_argument('--product-workers', type=int, default=8,
                   help='GeoTIFF export+check tasks run at once (one process each, ~1-3 GB each at 46 M px)')
    p.add_argument('--products-only', action='store_true',
                   help='only daily statistics + GeoTIFFs from an existing hourly store (no Topoclass, so the '
                        'main process, which the GeoTIFF workers copy, stays small)')
    p.add_argument('--horizon-batch', type=int, default=8,
                   help='azimuths computed at once (one process each, ~1 GB each at 46 M px)')
    a = p.parse_args()

    cfg, out, dem = wf.load_config(a.config)
    t_all = time.time()
    timer = wf.Timer()
    print(f'Run started {pd.Timestamp.now():%Y-%m-%d %H:%M:%S}; config {a.config}', flush=True)

    year0, year1 = pd.Timestamp(cfg['project']['start']), pd.Timestamp(cfg['project']['end'])
    if a.products_only:
        return products(a, cfg, out, dem, year0, year1, timer, t_all, None)

    mp = tc.Topoclass(a.config)
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
        return finish(out, int(len(mp.toposub.df_centroids)), timer, t_all, {}, a)

    # ---- ERA5 (EDH -> ERA5.zarr; fetch_era5_edh resumes from its finished blocks)
    timer('get_era5', mp.get_era5)
    if a.stop_after == 'era5':
        return finish(out, int(len(mp.toposub.df_centroids)), timer, t_all, {}, a)

    # ---- hourly downscaling, whole year, one store (resumable per cluster)
    timer('downscale_climate', mp.downscale_climate)
    if a.stop_after == 'downscale':
        return finish(out, int(len(mp.toposub.df_centroids)), timer, t_all, {}, a)

    # The Topoclass object holds GBs of terrain data the products do not need; drop it before the
    # GeoTIFF workers are forked from this process.
    n_points = int(len(mp.toposub.df_centroids))
    del mp
    gc.collect()
    return products(a, cfg, out, dem, year0, year1, timer, t_all, n_points)


def products(a, cfg, out, dem, year0, year1, timer, t_all, n_points):
    # ---- products. Phase A, per month in order: QA, daily statistics -> daily.zarr (seconds per month).
    #      Phase B, in parallel: one task per (month, statistic) = GeoTIFF export + its checks
    #      (~4.5 min each when serial; 36 tasks for a year), on a fork pool sharing the pixel maps.
    store = out / 'downscaled' / cfg['outputs']['file']['zarr_store']
    daily_path = out / 'downscaled' / 'daily.zarr'
    geotiff_dir = out / 'geotiff'
    months_dir = out / 'months'
    ds_param_path = out / cfg['outputs']['file']['ds_param']
    mask_file = cfg['sampling']['toposub']['clustering_mask']
    results, recs, dailies = {}, {}, {}
    for m0, m1 in wf.month_periods(year0, year1):
        if a.months and m0.month not in a.months:
            continue
        ym = f'{m0:%Y-%m}'
        marker = months_dir / f'{ym}.json'
        if marker.exists() and json.loads(marker.read_text()).get('status') == 'ok' and not a.redo_products:
            print(f'\n#################### {ym}: complete (see {marker.name}), skipped', flush=True)
            results[ym] = 'ok (earlier)'
            continue
        print(f'\n#################### {ym}: {m0.date()} .. {m1.date()} (daily statistics)', flush=True)
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
            del hourly
            rec['qa_ok'] = qa_ok
            dailies[ym] = daily
        except Exception:
            import traceback
            rec['status'] = 'error'
            rec['error'] = traceback.format_exc()
            print(rec['error'], flush=True)
        rec['timings_s'] = mt.t
        recs[ym] = rec
        if rec.get('status') == 'error':
            break

    if dailies:
        pts = next(iter(dailies.values())).point_name.values
        idx, profile, pn = timer('pixel_index', wf.pixel_index, ds_param_path, dem, pts)
        prep = timer('check_prep', wf.check_prep, pn, mask_file, dem, pts)
        _G.update(dailies=dailies, idx=idx, profile=profile, prep=prep, geotiff_dir=geotiff_dir, var=a.var,
                  cfg=cfg, overwrite=a.redo_products)
        del pn
        gc.collect()
        tasks = [(ym, s) for ym in dailies for s in a.stats]
        n = max(1, min(a.product_workers, len(tasks)))
        print(f'\n========== GeoTIFFs: {len(tasks)} tasks (month x statistic) on {n} processes; '
              f'main process RSS now {wf.rss_gb():.1f} GB', flush=True)
        t0 = time.time()
        with mp_ctx.get_context('fork').Pool(n, maxtasksperchild=4) as pool:
            for k, (ym, stat, res) in enumerate(pool.imap_unordered(_product_task, tasks), start=1):
                rec = recs[ym]
                rec.setdefault('geotiff_checks', {})
                rec['timings_s'].update({f'{key}_{stat}': v for key, v in res['timings_s'].items()})
                if 'error' in res:
                    rec.setdefault('errors', []).append(res['error'])
                    print(f'[{k}/{len(tasks)}] {ym} {stat}: ERROR\n{res["error"]}', flush=True)
                else:
                    rec['geotiff_checks'].update(res['report'])
                    rec.setdefault('stat_ok', {})[stat] = res['ok']
                    print(f'[{k}/{len(tasks)}] {ym} {stat}: {"OK" if res["ok"] else "FAILED CHECKS"} '
                          f'(export {res["timings_s"]["export_geotiff"]:.0f} s, check '
                          f'{res["timings_s"]["check_geotiff"]:.0f} s; {time.time() - t0:.0f} s elapsed)',
                          flush=True)
                if len(rec.get('stat_ok', {})) + len(rec.get('errors', [])) == len(a.stats):
                    close_month(ym, rec, a, months_dir, results)     # all statistics of this month are back
        timer.t['geotiffs_parallel'] = round(time.time() - t0, 1)

    for ym, rec in recs.items():
        if ym not in results:
            close_month(ym, rec, a, months_dir, results)
    return finish(out, n_points, timer, t_all, results, a)


def close_month(ym, rec, a, months_dir, results):
    """Final status of one month -> months/YYYY-MM.json (written as soon as the month is complete)."""
    if rec.get('status') != 'error':
        stats_ok = rec.get('stat_ok', {})
        if rec.get('errors'):
            rec['status'] = 'error'
        elif rec['qa_ok'] and len(stats_ok) == len(a.stats) and all(stats_ok.values()):
            rec['status'] = 'ok'
        else:
            rec['status'] = 'failed checks'
    rec['finished'] = str(pd.Timestamp.now())
    wf.write_json(months_dir / f'{ym}.json', rec)
    results[ym] = rec['status']
    print(f'#################### {ym}: {rec["status"]}', flush=True)


# Shared with the fork workers of the GeoTIFF stage (set before the pool starts; read-only in workers).
_G = {}


def _product_task(task):
    """GeoTIFF export + checks of one (month, statistic); runs in a worker process."""
    ym, stat = task
    # 6 workers x GDAL's default cache (5 % of RAM each) and 24 compression threads each overfilled
    # memory on 2026-10-21; limit both per worker.
    wf.COG_OPTIONS['num_threads'] = '4'
    mt = wf.Timer()
    out = {}
    try:
        daily = _G['dailies'][ym]
        with rasterio.Env(GDAL_CACHEMAX=512):        # MB
            files = mt('export_geotiff', wf.export_month, daily, _G['idx'], _G['profile'], _G['geotiff_dir'],
                       _G['var'], [stat], _G['cfg'], overwrite=_G['overwrite'])
            out['ok'], out['report'] = mt('check_geotiff', wf.check_month, files, daily, None, None, None,
                                          _G['var'], prep=_G['prep'])
    except Exception:
        import traceback
        out['error'] = f'{ym} {stat}: ' + traceback.format_exc()
    out['timings_s'] = mt.t
    gc.collect()
    return ym, stat, out


def finish(out, n_points, timer, t_all, results, a):
    summary = dict(config=str(a.config), finished=str(pd.Timestamp.now()), stop_after=a.stop_after,
                   months=results, steps_s=timer.t, total_s=round(time.time() - t_all, 1), n_points=n_points)
    wf.write_json(out / 'year_summary.json', summary)
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
