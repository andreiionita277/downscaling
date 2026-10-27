"""Cost estimates (no submission) across formats, datasets and sizes, to map the CDS limits."""
import json
from probe_costs import c, req, L37
P = 'reanalysis-era5-pressure-levels'
def est(ds, r):
    try: return c.estimate_costs(ds, r)
    except Exception as e: return f'ERR {type(e).__name__}: {e}'[:300]
print('--- pressure levels, 1 month, GRIB vs NetCDF')
for n in (1, 2, 3, 6):
    print(n, 'vars grib  ', est(P, req(n, 31)))
    print(n, 'vars netcdf', est(P, req(n, 31, fmt='netcdf')))
r = req(1, 31); r.pop('area'); print('1 var, no area', est(P, r))
r = req(2, 31, levels=L37[:-1]); print('2 vars 36 lev', est(P, r))
r = req(2, 31); r['month'] = ['02']; r['day'] = [f'{d:02d}' for d in range(1, 32)]; print('Feb with day 1..31', est(P, r))
r = req(2, 31); r['month'] = ['01','02']; r['day'] = [f'{d:02d}' for d in range(1, 26)]; r['pressure_level'] = L37[:25]; print('2v 25lev 2mon 25d 24h=60000', est(P, r))
r['time'] = r['time'] + []; r['pressure_level'] = L37[:26]; print('...26 lev =62400', est(P, r))
print('--- single levels')
S = 'reanalysis-era5-single-levels'
sv = ['geopotential','2m_dewpoint_temperature','surface_pressure','surface_thermal_radiation_downwards',
      'surface_solar_radiation_downwards','total_precipitation','2m_temperature']
rs = {'product_type': ['reanalysis'], 'variable': sv, 'year': ['2025'], 'month': ['01'],
      'day': [f'{d:02d}' for d in range(1, 32)], 'time': [f'{h:02d}:00' for h in range(24)],
      'data_format': 'grib', 'download_format': 'unarchived', 'area': [43.0, 22.25, 40.75, 24.75]}
print('7 vars 1 month grib', est(S, rs))
rs2 = dict(rs, month=[f'{m:02d}' for m in range(1, 13)]); print('7 vars 12 months grib', est(S, rs2))
print('7 vars 1 month netcdf', est(S, dict(rs, data_format='netcdf')))
