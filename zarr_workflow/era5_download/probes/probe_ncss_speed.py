"""NCAR THREDDS NetcdfSubset (server-side crop): time 1, 4 and 8 parallel day-requests (T, Jan 2025)."""
import sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
BASE = 'https://thredds.rda.ucar.edu/thredds/ncss/grid/files/g/d633000/e5.oper.an.pl'
def url(code, var, day):
    d = f'2025-01-{day:02d}'; f = f'e5.oper.an.pl.{code}.ll025sc.2025{1:02d}{day:02d}00_2025{1:02d}{day:02d}23.nc'
    return (f'{BASE}/202501/{f}?var={var}&north=43&south=40.75&west=22.25&east=24.75&horizStride=1'
            f'&time_start={d}T00:00:00Z&time_end={d}T23:00:00Z&accept=netcdf')
def get(day):
    t = time.time(); n = len(urllib.request.urlopen(url('128_130_t', 'T', day), timeout=900).read()); return n, time.time() - t
day = 2
for n in (1, 4, 8):
    t = time.time()
    with ThreadPoolExecutor(n) as ex: r = list(ex.map(get, range(day, day + n)))
    dt = time.time() - t; day += n
    print(f'{n} parallel: {dt:.1f}s wall, per request {[round(x[1],1) for x in r]}, {sum(x[0] for x in r)/1e3:.0f} kB', flush=True)
