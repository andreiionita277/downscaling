"""Daily mean temperature from Meteomanz hourly files -> added to data/stations/Meteomanz/<station>.csv.
Mean of the hourly readings of the UTC day (00:00-23:00), only days with >= 20 hours. The hourly files are also saved as
CSV in data/stations/Meteomanz/hourly/. Source: user's zip G:\\My Drive\\A_MeteoManz\\Meteomanz_Daily_2020-01-01_2026-07-31.zip
(copied to D:\\...\\meridionali_E_2025\\Meteomanz\\), folders Balea/ and Tarcu/ with meteomanz_<stn>_<year>_hourly_COMPLETE.xlsx.
    /usr/bin/python3 meridionali_zarr/scripts/meteomanz_add_tmean.py <extracted zip folder> meridionali_zarr/data/stations/Meteomanz
"""
import collections
import csv
import glob
import os
import sys

import openpyxl

src, dst = sys.argv[1:3]
NAMES = {'Balea': 'Balea Lac', 'Tarcu': 'Tarcu'}
MIN_HOURS = 20
os.makedirs(f'{dst}/hourly', exist_ok=True)
means = collections.defaultdict(dict)
for f in sorted(glob.glob(f'{src}/*/meteomanz_*_hourly_COMPLETE.xlsx')):
    key = f.split('/')[-2]
    name = NAMES[key]
    year = os.path.basename(f).split('_')[2]
    rows = list(openpyxl.load_workbook(f, read_only=True, data_only=True).worksheets[0].iter_rows(values_only=True))
    hdr, i = rows[0], {c: j for j, c in enumerate(rows[0])}
    with open(f'{dst}/hourly/{name}_{year}_hourly.csv', 'w', newline='', encoding='utf-8') as fo:
        w = csv.writer(fo)
        w.writerow(hdr)
        for r in rows[1:]:
            w.writerow([v.isoformat(sep=' ') if hasattr(v, 'isoformat') else ('' if v is None else v) for v in r])
    by = collections.defaultdict(dict)
    for r in rows[1:]:
        t, v = r[i['datetime_utc']], r[i['temp_c']]
        if t is not None and isinstance(v, (int, float)) and t.minute == 0:
            by[t.date().isoformat()].setdefault(t.hour, float(v))
    for d, hh in by.items():
        means[name][d] = (round(sum(hh.values()) / len(hh), 2) if len(hh) >= MIN_HOURS else '', len(hh))
    print(f'{name} {year}: {len(by)} days, {sum(1 for hh in by.values() if len(hh) >= MIN_HOURS)} with >= {MIN_HOURS} h')
for name, m in means.items():
    p = f'{dst}/{name}.csv'
    rows = list(csv.reader(open(p, encoding='utf-8')))
    hdr = [c for c in rows[0] if c not in ('tmean_c', 'tmean_n_hours')]
    keep = [j for j, c in enumerate(rows[0]) if c in hdr]
    new_hdr = [hdr[0], 'tmean_c'] + hdr[1:] + ['tmean_n_hours']
    n = 0
    with open(p, 'w', newline='', encoding='utf-8') as fo:
        w = csv.writer(fo)
        w.writerow(new_hdr)
        for r in rows[1:]:
            r = [r[j] for j in keep]
            tm, nh = m.get(r[0], ('', ''))
            n += tm != ''
            w.writerow([r[0], tm] + r[1:] + [nh])
    print(f'{name}.csv: tmean_c on {n} days')
