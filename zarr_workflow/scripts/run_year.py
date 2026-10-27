"""
Run one year of the Rila-Pirin Zarr workflow, month by month.

    python zarr_workflow/scripts/run_year.py zarr_workflow/config/config_2025.yml [--months 1 2 ...]
                                             [--redo-products] [--var t] [--stats mean min max]

Once for the year (each step is skipped when its cache exists, TopoPyScale's own behaviour):
    1 compute_dem_param       -> outputs/ds_param.zarr    (terrain parameters, then the cluster map)
    2 extract_topo_param      -> outputs/df_centroids.pck (TopoSUB, k-means on the masked pixels)
    3 compute_solar_geometry  -> outputs/ds_solar.zarr    (whole year)
    4 compute_horizon         -> outputs/da_horizon.zarr
    5 get_era5                -> climate path/ERA5.zarr   (Earth Data Hub, resumable; or, with
                                 data_repository: local, only a completeness check of the store
                                 prepared by zarr_workflow/era5_download)
Then for every month (a month is skipped when outputs/months/<YYYY-MM>.json says it is complete):
    6 downscale_climate       -> its time slice of outputs/downscaled/down.zarr (hourly, whole year store)
    7 QA of the month's hourly values (no NaN, physical bounds)
    8 daily statistics        -> its days in outputs/downscaled/daily.zarr
    9 daily GeoTIFFs          -> outputs/geotiff/<var>_<stat>_daily_<YYYY-MM>.tif (one band per day)
   10 checks of the GeoTIFFs  -> outputs/months/<YYYY-MM>.json (status, timings, QA, checks)
Finally a year summary -> outputs/year_summary.json. Exit code 0 only if every month passed.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

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
    a = p.parse_args()

    cfg, out, dem = wf.load_config(a.config)
    t_all = time.time()
    timer = wf.Timer()
    print(f'Run started {pd.Timestamp.now():%Y-%m-%d %H:%M:%S}; config {a.config}', flush=True)

    mp = tc.Topoclass(a.config)
    year0, year1 = pd.Timestamp(mp.config.project.start), pd.Timestamp(mp.config.project.end)
    if not mp.config.outputs.file.zarr_store or not mp.config.outputs.file.zarr_store_start:
        raise ValueError('config: outputs.file.zarr_store and zarr_store_start/end are required')

    # ---- once for the year
    timer('compute_dem_param', mp.compute_dem_param)
    timer('extract_topo_param', mp.extract_topo_param)
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
            # 9 GeoTIFFs, 10 checks
            if idx is None:
                idx, profile, pn = mt('pixel_index', wf.pixel_index, ds_param_path, dem,
                                      daily.point_name.values)
            files = mt('export_geotiff', wf.export_month, daily, idx, profile, geotiff_dir, a.var, a.stats, cfg,
                       overwrite=a.redo_products)
            chk_ok, chk = mt('check_geotiff', wf.check_month, files, daily, pn, mask_file, dem, a.var)
            rec['geotiff_checks'] = chk
            rec['status'] = 'ok' if (qa_ok and chk_ok) else 'failed checks'
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
    summary = dict(config=str(a.config), finished=str(pd.Timestamp.now()), months=results,
                   year_steps_s=year_timings, total_s=round(time.time() - t_all, 1),
                   n_points=int(len(mp.toposub.df_centroids)))
    wf.write_json(mp.config.outputs.path / 'year_summary.json', summary)
    print('\n' + '\n'.join(f'{k}: {v}' for k, v in results.items()), flush=True)
    all_ok = all(str(v).startswith('ok') for v in results.values()) and len(results) > 0
    print('YEAR COMPLETE, ALL CHECKS PASSED' if all_ok else 'NOT COMPLETE / SOME CHECKS FAILED', flush=True)
    sys.exit(0 if all_ok else 1)


# The guard is required: multiprocessing workers (forkserver/spawn) may import this file.
if __name__ == '__main__':
    main()
