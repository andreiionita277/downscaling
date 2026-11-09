"""Meteomanz daily files from the user's zip (D:\\...\\Meteomanz\\Meteomanz_Daily_20140701_20251231.zip, extracted) -> one
CSV per station inside the model domain, named after the station, + stations_info.csv and README.md.
Replaces meteomanz_excel.py (Excel kept reporting a problem with the openpyxl 3.1.2 files; user: "make them csv").
    /usr/bin/python3 meridionali_zarr/scripts/meteomanz_csv.py <extracted zip folder> <out folder> <stations_in_domain.csv>
"""
import csv
import datetime as dt
import sys

import openpyxl

src, dst, meta_csv = sys.argv[1:4]
ST = {'BaleaLac': 'Balea Lac', 'Cuntu': 'Cuntu', 'Fundata': 'Fundata', 'ObarsiaLotrului': 'Obarsia Lotrului',
      'Paltinis': 'Paltinis', 'Parang': 'Parang', 'Petrosani': 'Petrosani', 'Predeal': 'Predeal', 'Sinaia1500': 'Sinaia 1500',
      'Tarcu': 'Tarcu', 'VfOmu': 'Vf. Omu', 'Voineasa': 'Voineasa'}
meta = {r['name']: r for r in csv.DictReader(open(meta_csv))}
info = []
for key, name in ST.items():
    ws = openpyxl.load_workbook(f'{src}/{key}_20140701_20251231.xlsx', read_only=True, data_only=True).worksheets[0]
    wmo = ws.title.split('_')[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr = list(rows[0])
    if hdr[-1] is None:
        hdr[-1] = 'snow_depth_cm'                                   # Voineasa: unnamed last column = snow depth
    data = [r for r in rows[1:] if r[0] is not None]
    i = {c: j for j, c in enumerate(hdr)}
    g = sorted(float(r[i['slp_hpa']]) for r in data if isinstance(r[i['slp_hpa']], (int, float)))
    level = '925hPa' if g[len(g) // 2] < 1100 else ('850hPa' if g[len(g) // 2] < 2500 else '700hPa')
    cols = [('date', 'date'), ('tmax_c', 'tmax_c'), ('tmin_c', 'tmin_c'), ('precip_mm', 'precip_mm'),
            (f'geopot_{level}_m', 'slp_hpa'), ('wind_dir_deg', 'wind_dir_deg'), ('wind_sp_kmh', 'wind_sp_kmh'),
            ('cloud_oktas', 'cloud_oktas'), ('cloud_okta_num', 'cloud_oktas'), ('insolation_h', 'insolation_h'),
            ('snow_depth_cm', 'snow_depth_cm')]
    cols = [c for c in cols if c[1] in i]
    flags = []
    with open(f'{dst}/{name}.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow([c[0] for c in cols])
        for r in data:
            out = []
            for new, old in cols:
                v = r[i[old]]
                if old == 'date':
                    v = (v.date() if isinstance(v, dt.datetime) else v).isoformat()
                elif old == 'cloud_oktas':
                    v = '' if v in (None, '', 'nan') else str(v)
                    if new == 'cloud_okta_num' and v:
                        v = v.split('/')[0]
                elif v in (None, 'nan'):
                    v = ''
                out.append(v)
            w.writerow(out)
            if 'snow_depth_cm' in i and isinstance(r[i['snow_depth_cm']], (int, float)) and r[i['snow_depth_cm']] > 400:
                flags.append(f'snow_depth_cm {r[i["snow_depth_cm"]]} on {r[0]:%Y-%m-%d} implausible (kept as reported)')
    m = meta.get(name, {})
    info.append(dict(file=f'{name}.csv', station=name, wmo=wmo, lat=m.get('lat'), lon=m.get('lon'), elev_m=m.get('elev'),
                     dem_elev_m=m.get('dem_z'), first=f'{data[0][0]:%Y-%m-%d}', last=f'{data[-1][0]:%Y-%m-%d}', days=len(data),
                     geopot_level=level, columns=' '.join(c[0] for c in cols), flags='; '.join(flags)))
    print(f'{name:17s} WMO {wmo}: {len(data)} days, {len(cols)} columns ({level}){" | " + "; ".join(flags) if flags else ""}')
with open(f'{dst}/stations_info.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(info[0]))
    w.writeheader()
    w.writerows(info)
