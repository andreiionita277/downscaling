"""
Quality checks of a finished topo_scale_zarr run.

    python bulgaria_rila_pirin/scripts/qa_run.py <config.yml> [--compare <other_config.yml>]

- completeness: every point has a .done marker, the time axis is the full project period at the
  configured timestep without gaps, no NaN in any variable
- physical ranges of every variable (min / max / mean), summary per elevation band for t
- --compare: the other run's store must hold the same points; on the common times every variable
  must agree (max |diff| reported). Used to check that a short run with the same clusters
  reproduces a slice of the long run (period independence).
Exits non-zero if a check fails.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
import yaml

RANGES = {  # loose physical bounds for the Rila-Pirin domain, hourly values
    't': (220, 320), 'q': (0, 0.04), 'p': (50000, 105000), 'u': (-60, 60), 'v': (-60, 60), 'ws': (0, 60),
    'wd': (0, 2 * np.pi + 1e-6), 'SW': (0, 1400), 'SW_direct': (0, 1400), 'SW_diffuse': (0, 1400),
    'LW': (50, 550), 'tp': (0, 200), 'w': (0, 0.04),
}


def store_of(cfg_path):
    cfg = yaml.safe_load(Path(cfg_path).read_text())
    out = Path(cfg['project']['directory']) / cfg['outputs'].get('directory', 'outputs')
    return cfg, out, out / 'downscaled' / cfg['outputs']['file']['zarr_store']


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--compare')
    a = p.parse_args()
    cfg, out, store = store_of(a.config)
    ok = True
    ds = xr.open_zarr(store)
    n_pts = ds.sizes['point_ind']
    n_done = len(list(Path(str(store) + '.progress').glob('*.done')))
    step = cfg['climate']['era5']['timestep']
    expect = pd.date_range(str(cfg['project']['start']), f"{cfg['project']['end']} 23:00", freq=step.replace('H', 'h'))
    t = pd.DatetimeIndex(ds.time.values)
    print(f'points {n_pts}, done markers {n_done}; time {t[0]} -> {t[-1]} ({len(t)} steps, expected {len(expect)})')
    if n_done != n_pts or not t.equals(expect):
        print('FAIL: incomplete run or time axis differs from the project period')
        ok = False
    print(f'{"var":11s} {"NaN":>6s} {"min":>11s} {"max":>11s} {"mean":>11s}')
    for v in ds.data_vars:
        x = ds[v].values
        nan = int(np.isnan(x).sum())
        lo, hi = RANGES.get(v, (-np.inf, np.inf))
        bad = nan > 0 or np.nanmin(x) < lo or np.nanmax(x) > hi
        ok &= not bad
        print(f'{v:11s} {nan:6d} {np.nanmin(x):11.4g} {np.nanmax(x):11.4g} {np.nanmean(x):11.4g}' + ('  <- FAIL' if bad else ''))

    c = pd.read_pickle(out / cfg['outputs']['file']['df_centroids'])
    tm = ds.t.mean('time').values - 273.15
    bands = pd.cut(c.elevation.values, [0, 500, 1000, 1500, 2000, 2500, 3000])
    print('\nannual mean t by centroid elevation band (degC):')
    print(pd.Series(tm).groupby(bands, observed=True).agg(['count', 'mean', 'min', 'max']).round(2).to_string())

    if a.compare:
        _, out2, store2 = store_of(a.compare)
        ds2 = xr.open_zarr(store2)
        c2 = pd.read_pickle(out2 / cfg['outputs']['file']['df_centroids'])
        same_pts = np.allclose(c[['x', 'y', 'elevation']].values, c2[['x', 'y', 'elevation']].values)
        common = np.intersect1d(ds.time.values, ds2.time.values)
        print(f'\ncompare with {store2}: same centroids {same_pts}, {len(common)} common hours')
        ok &= same_pts and len(common) > 0
        for v in ds.data_vars:
            d = float(np.nanmax(np.abs(ds[v].sel(time=common).values - ds2[v].sel(time=common).values)))
            flag = d > 1e-4
            ok &= not flag
            print(f'  {v:11s} max |diff| {d:.2e}' + ('  <- FAIL' if flag else ''))
    print('\nALL CHECKS PASSED' if ok else '\nSOME CHECKS FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
