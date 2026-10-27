"""Tiny timed CDS request: 1 h, 1 level, 1 variable, our box, GRIB. Prints queue/run/download timings."""
import json, time
from probe_costs import c
P = 'reanalysis-era5-pressure-levels'
r = {'product_type': ['reanalysis'], 'variable': ['temperature'], 'year': ['2025'], 'month': ['01'], 'day': ['15'],
     'time': ['12:00'], 'pressure_level': ['700'], 'data_format': 'grib', 'download_format': 'unarchived',
     'area': [43.0, 22.25, 40.75, 24.75]}
t0 = time.time(); remote = c.submit(P, r); seen = {}
while True:
    s = remote.status
    if s not in seen: seen[s] = round(time.time()-t0, 1); print(s, seen[s], flush=True)
    if s in ('successful', 'failed', 'rejected', 'dismissed'): break
    time.sleep(5)
out = '/app/zarr_workflow/data/era5_37/probe/tiny_t700_20250115T12.grib'
t1 = time.time(); remote.download(out); seen['download_s'] = round(time.time()-t1, 1)
seen['total_s'] = round(time.time()-t0, 1); print(json.dumps(seen))
import xarray as xr
ds = xr.open_dataset(out, engine='cfgrib', backend_kwargs={'indexpath': ''})
print(ds); print(float(ds.t.min()), float(ds.t.max()))
