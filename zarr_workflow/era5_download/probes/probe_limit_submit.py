"""Find the CDS size limit with REAL submissions, largest first. Accepted jobs are deleted at once
(nothing is downloaded); rejected ones show the server's error. Results -> limit_search.json."""
import itertools, json, time
from probe_costs import c, L37
P = 'reanalysis-era5-pressure-levels'
V = ['geopotential','temperature','u_component_of_wind','v_component_of_wind','specific_humidity','relative_humidity']
def build(nv, nl, months, nd, nh):
    return {'product_type': ['reanalysis'], 'variable': V[:nv], 'year': ['2025'],
            'month': [f'{m:02d}' for m in range(1, months+1)], 'day': [f'{d:02d}' for d in range(1, nd+1)],
            'time': [f'{h:02d}:00' for h in range(nh)], 'pressure_level': L37[:nl],
            'data_format': 'grib', 'download_format': 'unarchived', 'area': [43.0, 22.25, 40.75, 24.75]}
# smallest achievable item count just above 60000 (months 1..2 so all days <= 28 are valid)
best = min((nv*nl*m*d*h, (nv, nl, m, d, h)) for nv, nl, m, d, h in itertools.product(
    range(1, 7), range(1, 38), (1, 2), range(1, 29), range(1, 25)) if nv*nl*m*d*h > 60000)
print('smallest count above 60000:', best)
cases = [('6 vars x 37 lev x Jan (=1 month all PLEV)', (6, 37, 1, 31, 24)),
         ('3 vars x 37 lev x Jan', (3, 37, 1, 31, 24)),
         ('smallest above 60000', best[1]),
         ('exactly 60000', (2, 25, 2, 25, 24)),
         ('2 vars x 37 lev x Jan (planned request)', (2, 37, 1, 31, 24))]
out = []
for name, a in cases:
    r = build(*a)
    est = c.estimate_costs(P, r)
    t = time.time()
    try:
        remote = c.submit(P, r)
        status = remote.status
        res = {'accepted': True, 'status_after_submit': status, 'request_id': remote.request_id}
        remote.delete()
        res['deleted'] = True
    except Exception as e:
        res = {'accepted': False, 'error': f'{type(e).__name__}: {e}'[:400]}
    res.update(case=name, args=a, cost=est['cost'], limit=est['limit'], seconds=round(time.time()-t, 1))
    print(json.dumps(res)); out.append(res)
json.dump(out, open('/app/zarr_workflow/data/era5_37/probe/limit_search.json', 'w'), indent=1)
