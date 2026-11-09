"""Append the 2026 days (2026-01-01 .. 2026-07-31) of the user's daily Meteomanz tables
(zip Meteomanz_Daily_2020-01-01_2026-07-31.zip: Balea_/Tarcu_Meteomanz_Daily_2020-01-01_2026-07-31.xlsx) to
data/stations/Meteomanz/Balea Lac.csv and Tarcu.csv, with the same columns (slp_hpa -> geopot_<level>_m, cloud text + number).
Days already in the CSV are not touched; days missing in the CSV (also before 2026) are added; rows kept in date order. No tmean_c for 2026 (no hourly data).
    /usr/bin/python3 meridionali_zarr/scripts/meteomanz_add_2026.py <extracted zip folder> meridionali_zarr/data/stations/Meteomanz
"""
import csv
import sys

import openpyxl

src, dst = sys.argv[1:3]
for key, name in (('Balea', 'Balea Lac'), ('Tarcu', 'Tarcu')):
    rows = list(openpyxl.load_workbook(f'{src}/{key}_Meteomanz_Daily_2020-01-01_2026-07-31.xlsx', read_only=True,
                                       data_only=True).worksheets[0].iter_rows(values_only=True))
    i = {c: j for j, c in enumerate(rows[0])}
    p = f'{dst}/{name}.csv'
    old = list(csv.reader(open(p, encoding='utf-8')))
    hdr, have = old[0], {r[0] for r in old[1:]}
    geo = [c for c in hdr if c.startswith('geopot_')][0]
    src_of = {'tmax_c': 'tmax_c', 'tmin_c': 'tmin_c', 'precip_mm': 'precip_mm', geo: 'slp_hpa', 'wind_dir_deg': 'wind_dir_deg',
              'wind_sp_kmh': 'wind_sp_kmh', 'cloud_oktas': 'cloud_oktas', 'insolation_h': 'insolation_h', 'snow_depth_cm': 'snow_depth_cm'}
    new = []
    for r in rows[1:]:
        if not r[0] or f'{r[0]:%Y-%m-%d}' in have:
            continue
        out = []
        for c in hdr:
            if c == 'date':
                v = f'{r[0]:%Y-%m-%d}'
            elif c == 'cloud_okta_num':
                v = r[i['cloud_oktas']]
                v = str(v).split('/')[0] if v not in (None, '', 'nan') else ''
            elif c in src_of:
                v = r[i[src_of[c]]]
                v = '' if v in (None, 'nan') else v
            else:                                               # tmean_c, tmean_n_hours
                v = ''
            out.append(v)
        new.append(out)
    allrows = sorted(old[1:] + new, key=lambda r: r[0])             # also fills days missing in the first zip -> keep date order
    with open(p, 'w', newline='', encoding='utf-8') as fo:
        w = csv.writer(fo)
        w.writerow(hdr)
        w.writerows(allrows)
    print(f'{name}.csv: +{len(new)} days, {sum(1 for r in new if r[0] < "2026")} of them before 2026 (missing in the first zip)')
