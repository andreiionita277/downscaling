"""
Checks one re-downscaled year (setup E, fixed TopoPyScale bbd0296) - 11 Nov 2026, aspect-fix rerun.

    ../venv/bin/python meridionali_zarr/scripts/check_year_fixed.py 2024
Checks:
  1. 12/12 months 'ok' in outputs/months/*.json (run_year_public's own hourly QA + daily statistics)
  2. time axis: every hour of the year, no duplicates, no gaps
  3. no NaN in any variable
  4. DJF (Jan, Feb, Dec) mean SW per elevation class x aspect sector (terrain aspect, circular mean, slope >= 5 deg):
     S > E, S > W, E > N, W > N in every class
  5. SW_direct max (reported; old code had spikes up to ~2900 W m-2)
Writes data/logs/rerun_fix/check_<year>.txt and sw_djf_<year>.csv; prints one summary line; exit 0 = all pass.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

y = int(sys.argv[1])
MZ = Path(__file__).resolve().parents[1]
out = MZ / 'data' / f'meridionali_E_{y}' / 'outputs'
LOG = MZ / 'data' / 'logs' / 'rerun_fix'
LOG.mkdir(parents=True, exist_ok=True)
lines, ok = [], True


def rep(name, passed, txt):
    global ok
    ok &= bool(passed)
    lines.append(f'{"PASS" if passed else "FAIL"}  {name}: {txt}')


st = [json.loads(f.read_text()).get('status', '') for f in sorted((out / 'months').glob('*.json'))]
n_ok = sum(1 for s in st if s.startswith('ok'))
rep('months ok', n_ok == 12, f'{n_ok}/12')

ds = xr.open_zarr(out / 'downscaled' / 'down.zarr')
t = pd.DatetimeIndex(ds.time.values)
exp = pd.date_range(f'{y}-01-01 00:00', f'{y}-12-31 23:00', freq='h')
rep('time axis', len(t) == len(exp) and (t == exp).all(),
    f'{len(t)} h (expected {len(exp)}), duplicates {t.duplicated().sum()}')

nan = {v: int(ds[v].isnull().sum().compute()) for v in ds.data_vars}
bad = {k: v for k, v in nan.items() if v}
rep('no NaN', not bad, 'all 13 variables' if not bad else str(bad))

c = pd.read_pickle(out / 'df_centroids.pck')
assert {'aspect_R', 'flat'} <= set(c.columns), 'df_centroids.pck is not the circular-mean (fixed) table'
assert (c.point_ind.values == ds.point_ind.values).all()
bearing = (180.0 - np.degrees(c.aspect.values)) % 360.0           # TopoPyScale aspect: 0 = south, + east
sector = np.where((bearing >= 315) | (bearing < 45), 'N', np.where(bearing < 135, 'E', np.where(bearing < 225, 'S', 'W')))
sector = np.where(c.flat.values, 'flat', sector)
elev = pd.cut(c.elevation.values, [-np.inf, 1000, 1500, 2000, np.inf], labels=['<1000', '1000-1500', '1500-2000', '>2000'])
djf = ds.SW.sel(time=ds.time.dt.month.isin([1, 2, 12])).mean('time').compute().values
tab = (pd.DataFrame({'elev': elev, 'sector': sector, 'SW': djf}).query('sector != "flat"')
       .groupby(['elev', 'sector'], observed=True).SW.median().unstack()[['N', 'E', 'S', 'W']])
tab.round(1).to_csv(LOG / f'sw_djf_{y}.csv')
order = ((tab.S > tab.E) & (tab.S > tab.W) & (tab.E > tab.N) & (tab.W > tab.N)).all()
rep('DJF SW order S > E,W > N', order,
    ' | '.join(f'{i}: N {r.N:.0f} E {r.E:.0f} S {r.S:.0f} W {r.W:.0f}' for i, r in tab.iterrows()))

swmax = float(ds.SW_direct.max().compute())
rep('SW_direct max', swmax < 1400, f'{swmax:.0f} W m-2')

(LOG / f'check_{y}.txt').write_text('\n'.join(lines) + '\n')
print('\n'.join(lines))
print(f'SUMMARY {y} {"ALL PASS" if ok else "FAILED"}; DJF SW S {tab.S.min():.0f}-{tab.S.max():.0f}, '
      f'N {tab.N.min():.0f}-{tab.N.max():.0f} W m-2; SW_direct max {swmax:.0f}')
sys.exit(0 if ok else 1)
