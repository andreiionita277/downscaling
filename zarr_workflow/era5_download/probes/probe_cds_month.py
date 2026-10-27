"""CDS timing test for one month: Jan 2025, 3 PLEV requests (2 vars x 37 levels each) + the whole-year SURF
request, submitted at the same time. Writes GRIBs to data/era5_37/raw/ with the downloader's names."""
import json, sys, time
from concurrent.futures import ThreadPoolExecutor
from probe_costs import c, L37
RAW = '/app/zarr_workflow/data/era5_37/raw'
import os; os.makedirs(RAW, exist_ok=True)
AREA = [43.0, 22.25, 40.75, 24.75]
PAIRS = [('geopotential', 'temperature', 'z-t'), ('u_component_of_wind', 'v_component_of_wind', 'u-v'),
         ('specific_humidity', 'relative_humidity', 'q-r')]
def plev(a, b):
    return {'product_type': ['reanalysis'], 'variable': [a, b], 'year': ['2025'], 'month': ['01'],
            'day': [f'{d:02d}' for d in range(1, 32)], 'time': [f'{h:02d}:00' for h in range(24)],
            'pressure_level': L37, 'data_format': 'grib', 'download_format': 'unarchived', 'area': AREA}
surf = {'product_type': ['reanalysis'], 'variable': ['geopotential', '2m_dewpoint_temperature', 'surface_pressure',
        'surface_thermal_radiation_downwards', 'surface_solar_radiation_downwards', 'total_precipitation', '2m_temperature'],
        'year': ['2025'], 'month': [f'{m:02d}' for m in range(1, 13)], 'day': [f'{d:02d}' for d in range(1, 32)],
        'time': [f'{h:02d}:00' for h in range(24)], 'data_format': 'grib', 'download_format': 'unarchived', 'area': AREA}
jobs = [(f'plev_2025-01_{tag}', 'reanalysis-era5-pressure-levels', plev(a, b)) for a, b, tag in PAIRS]
jobs.append(('surf_2025', 'reanalysis-era5-single-levels', surf))
def run(job):
    name, ds, r = job
    t0 = time.time(); remote = c.submit(ds, r); seen = {'request_id': remote.request_id}
    while True:
        s = remote.status
        if s not in seen: seen[s] = round(time.time() - t0); print(name, s, seen[s], flush=True)
        if s in ('successful', 'failed', 'rejected', 'dismissed', 'deleted'): break
        time.sleep(15)
    if s == 'successful':
        t1 = time.time(); remote.download(f'{RAW}/{name}.grib.part'); os.replace(f'{RAW}/{name}.grib.part', f'{RAW}/{name}.grib')
        seen['download_s'] = round(time.time() - t1, 1); seen['bytes'] = os.path.getsize(f'{RAW}/{name}.grib')
    seen['total_s'] = round(time.time() - t0); seen['name'] = name
    print(json.dumps(seen), flush=True); return seen
with ThreadPoolExecutor(4) as ex: res = list(ex.map(run, jobs))
json.dump(res, open('/app/zarr_workflow/data/era5_37/probe/cds_month_timing.json', 'w'), indent=1)
