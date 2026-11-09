"""
Export the hourly downscaled store of a finished run to SURFEX-Crocus forcing, without re-running anything.

Same as `mp.to_surfex(year_start=..., year_end=...)` after `mp.downscale_climate()`: it calls TopoPyScale's
own topo_export.to_surfex with the arguments Topoclass.to_surfex passes (one NetCDF per year, all points,
snow/rain partition 'continuous'), on the store that downscale_climate wrote (down.zarr) and the cluster
table (df_centroids.pck).

    python meridionali_zarr/scripts/export_surfex.py meridionali_zarr/config/config_meridionali_2025.yml \
           --start 2025-01-01 --end 2025-12-31

Local adaptations (TopoPyScale itself is not changed):
- to_surfex reads LAT/LON from df_centroids columns 'latitude'/'longitude'; this fork names them lat/lon.
- to_surfex drops variables of the NetCDF downscaler (vp, cse, cos_illumination, precip_lapse_rate) that
  the Zarr store does not have; they are added as zeros so it can drop them (they are not exported).
- topo_utils.time_vecs ends each yearly window at 00:00 of its last day, so to_surfex drops
that day's last 23 hours (2025: 8737 of 8760 h). Here each window ends at 23:00 of its last day instead.
--fork-year-end keeps the fork's behaviour.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
import yaml

from TopoPyScale import topo_export as te
from TopoPyScale import topo_scale_zarr as tz
from TopoPyScale import topo_utils as tu


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--start', required=True, help='start of the first year, e.g. 2025-01-01 or 2019-09-01')
    p.add_argument('--end', required=True, help='last day, e.g. 2025-12-31 or 2024-08-31')
    p.add_argument('--fname', default='FORCING*.nc', help='file name pattern (as Topoclass.to_surfex)')
    p.add_argument('--scale-precip', type=float, default=1)
    p.add_argument('--fork-year-end', action='store_true', help='keep the fork\'s window end (00:00 of the last day)')
    a = p.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text())
    out = Path(cfg['project']['directory']) / cfg['outputs'].get('directory', 'outputs')
    store = out / 'downscaled' / cfg['outputs']['file']['zarr_store']
    df = pd.read_pickle(out / cfg['outputs']['file']['df_centroids'])

    ds = tz.open_downscaled_store(store)
    # topo_export.to_surfex looks up ZS/slope/aspect/LAT/LON by position (0..n-1 on both sides).
    if not np.array_equal(ds.point_ind.values, df.point_ind.values) or \
            not np.array_equal(ds.point_name.values.astype(str), df.point_name.values.astype(str)):
        raise ValueError('points of the store and of df_centroids are not in the same order')

    # to_surfex reads LAT/LON from columns 'latitude'/'longitude'; this fork's df_centroids calls them lat/lon.
    if 'latitude' not in df:
        df = df.assign(latitude=df.lat, longitude=df.lon)

    # to_surfex drops these intermediate variables of the NetCDF downscaler right away (it never uses them);
    # the Zarr downscaler does not store them, so drop_vars would fail. Add them as zeros to be dropped.
    for v in ('vp', 'cse', 'cos_illumination', 'precip_lapse_rate'):
        if v not in ds:
            ds[v] = xr.zeros_like(ds.t)

    if not a.fork_year_end:
        fork_time_vecs = tu.time_vecs

        def time_vecs_full_days(start_date, end_date):
            s, e = fork_time_vecs(start_date, end_date)
            return s, e + pd.Timedelta('23h')
        tu.time_vecs = time_vecs_full_days

    t0 = time.time()
    print(f'Export {store} -> {out / a.fname}, {a.start} .. {a.end}', flush=True)
    te.to_surfex(ds, df, fname_format=out / a.fname, scale_precip=a.scale_precip,
                 climate_dataset_name=cfg['project']['climate'], project_author=cfg['project']['authors'],
                 snow_partition_method='continuous', year_start=a.start, year_end=a.end)
    print(f'to_surfex done in {time.time() - t0:.0f} s', flush=True)

    # checks of every file written
    starts, ends = tu.time_vecs(a.start, a.end)
    ok = True
    for s, e in zip(starts, ends):
        f = Path(f"{str(out / a.fname).split('*')[0]}_{s:%Y%m%d%H}_{e:%Y%m%d%H}.nc")
        ok &= check(f, ds.sel(time=slice(s, e)), df)
    print('ALL SURFEX FILES OK' if ok else 'SOME SURFEX CHECKS FAILED', flush=True)
    sys.exit(0 if ok else 1)


def check(f, ref, df):
    """The file has every hour, no NaN, and the values/point info of the store and df_centroids."""
    errs = []
    with xr.open_dataset(f) as s:
        n_exp = int((ref.time.values[-1] - ref.time.values[0]) / np.timedelta64(1, 'h')) + 1
        if s.sizes['time'] != n_exp or ref.sizes['time'] != n_exp:
            errs.append(f'{s.sizes["time"]} hours in file, {n_exp} expected')
        for v in ('Tair', 'Qair', 'PSurf', 'Wind', 'Wind_DIR', 'LWdown', 'DIR_SWdown', 'Rainf', 'Snowf', 'HUMREL'):
            if int(s[v].isnull().sum()):
                errs.append(f'{v} has NaN')
        tair = s.Tair.transpose('Number_of_points', 'time').values
        d_t = float(np.abs(tair - ref.t.values).max())
        if d_t > 1e-6:
            errs.append(f'Tair differs from the store (max {d_t:.2e} K)')
        precip = (s.Rainf + s.Snowf).transpose('Number_of_points', 'time').values * 3600
        d_p = float(np.abs(precip - ref.tp.values).max())
        if d_p > 1e-6:
            errs.append(f'Rainf + Snowf differs from tp (max {d_p:.2e} mm/h)')
        d_z = float(np.abs(s.ZS.values - df.elevation.values).max())
        if d_z > 1e-6:
            errs.append(f'ZS differs from the cluster elevations (max {d_z:.2e} m)')
        size_gb = f.stat().st_size / 1e9
        print(f'{"OK  " if not errs else "FAIL"} {f.name}: {s.sizes["Number_of_points"]} points x {s.sizes["time"]} h '
              f'({pd.Timestamp(s.time.values[0])} .. {pd.Timestamp(s.time.values[-1])}), {size_gb:.2f} GB; '
              f'Tair {float(s.Tair.min()) - 273.15:.1f}..{float(s.Tair.max()) - 273.15:.1f} degC, '
              f'snowfall share {float(s.Snowf.sum() / (s.Rainf.sum() + s.Snowf.sum())):.1%}, '
              f'ZS {float(s.ZS.min()):.0f}..{float(s.ZS.max()):.0f} m'
              + (f'  <- {"; ".join(errs)}' if errs else ''), flush=True)
    return not errs


if __name__ == '__main__':
    main()
