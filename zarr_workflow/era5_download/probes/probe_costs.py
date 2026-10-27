"""Ask the CDS how much a request costs and what the limit is, without submitting it (estimate_costs)."""
import json, sys, time
from ecmwf.datastores import Client

def cds_client():
    cfg = dict(l.split(':', 1) for l in open('/home/icam/.cdsapirc').read().splitlines() if ':' in l)
    return Client(url=cfg['url'].strip(), key=cfg['key'].strip())
c = cds_client()
c.check_authentication()
L37 = ['1','2','3','5','7','10','20','30','50','70','100','125','150','175','200','225','250','300','350','400',
       '450','500','550','600','650','700','750','775','800','825','850','875','900','925','950','975','1000']
def req(nvar, days, levels=L37, hours=24, fmt='grib', ds='reanalysis-era5-pressure-levels'):
    v = ['geopotential','temperature','u_component_of_wind','v_component_of_wind','specific_humidity','relative_humidity'][:nvar]
    return {'product_type': ['reanalysis'], 'variable': v, 'year': ['2025'], 'month': ['01'],
            'day': [f'{d:02d}' for d in range(1, days+1)], 'time': [f'{h:02d}:00' for h in range(hours)],
            'pressure_level': levels, 'data_format': fmt, 'download_format': 'unarchived',
            'area': [43.0, 22.25, 40.75, 24.75]}
for args in [(1,1,L37[:1],1), (1,31), (6,31)]:
    r = req(*args)
    t = time.time()
    print(args[:2], json.dumps(c.estimate_costs('reanalysis-era5-pressure-levels', r)), f'{time.time()-t:.1f}s')
