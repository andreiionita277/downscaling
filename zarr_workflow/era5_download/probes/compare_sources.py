"""Compare January 2025 between the stores: CDS store vs NCAR test store (all 37 levels), both vs the EDH store
(5 shared levels), and both vs the independent single CDS request (T 700 hPa, 2025-01-15 12:00).
Writes data/era5_37/probe/compare_sources_2025-01.json."""
import json
import numpy as np
import pandas as pd
import xarray as xr

D = '/app/zarr_workflow/data'
T = pd.date_range('2025-01-01', '2025-01-31T23', freq='1h')
stores = {'cds': f'{D}/era5_37/ERA5.zarr', 'ncar': f'{D}/era5_37/ncar_test/ERA5.zarr', 'edh': f'{D}/era5/ERA5.zarr'}
ds = {k: xr.open_zarr(v).sel(time=T) for k, v in stores.items()}
out = {}


def diff(a, b):
    d = np.abs(a.values.astype('float64') - b.values.astype('float64'))
    return {'max_abs': float(np.nanmax(d)), 'mean_abs': float(np.nanmean(d)), 'frac_identical': float((d == 0).mean()),
            'nan_a': int(np.isnan(a.values).sum()), 'nan_b': int(np.isnan(b.values).sum())}


for v in ['z', 't', 'u', 'v', 'q', 'r']:
    out[f'cds_vs_ncar_{v}'] = diff(ds['cds'][v], ds['ncar'][v])
    lev = [600.0, 700.0, 850.0, 925.0, 1000.0]
    for k in ('cds', 'ncar'):
        out[f'{k}_vs_edh_{v}'] = diff(ds[k][v].sel(level=lev), ds['edh'][v].sel(level=lev))
for v in ['z_surf', 'd2m', 'sp', 'strd', 'ssrd', 'tp', 't2m']:
    out[f'cds_vs_edh_{v}'] = diff(ds['cds'][v], ds['edh'][v])
ref = xr.open_dataset(f'{D}/era5_37/probe/tiny_t700_20250115T12.grib', engine='cfgrib',
                      backend_kwargs={'indexpath': ''}).t.sortby('latitude', ascending=False)
for k in ('cds', 'ncar', 'edh'):
    out[f'single_request_vs_{k}'] = diff(ds[k].t.sel(time='2025-01-15T12', level=700.0), ref)
for k, r in out.items():
    print(f'{k:24s} {r}')
json.dump(out, open(f'{D}/era5_37/probe/compare_sources_2025-01.json', 'w'), indent=1)
