# SUPERSEDED by meteomanz_csv.py (Excel reported a problem with these openpyxl 3.1.2 files)
"""Meteomanz daily files from the user's zip (D:\\...\\Meteomanz\\Meteomanz_Daily_20140701_20251231.zip) -> one tidy Excel
per station inside the model domain, named after the station."""
import sys, csv, datetime as dt, openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter
src, dst, meta_csv = sys.argv[1:4]
ST = {'BaleaLac': 'Balea Lac', 'Cuntu': 'Cuntu', 'Fundata': 'Fundata', 'ObarsiaLotrului': 'Obarsia Lotrului',
      'Paltinis': 'Paltinis', 'Parang': 'Parang', 'Petrosani': 'Petrosani', 'Predeal': 'Predeal', 'Sinaia1500': 'Sinaia 1500',
      'Tarcu': 'Tarcu', 'VfOmu': 'Vf. Omu', 'Voineasa': 'Voineasa'}
meta = {r['name']: r for r in csv.DictReader(open(meta_csv))}
HEAD = Font(bold=True, color='FFFFFF'); FILL = PatternFill('solid', fgColor='2A78D6')
for key, name in ST.items():
    wb_in = openpyxl.load_workbook(f'{src}/{key}_20140701_20251231.xlsx', read_only=True, data_only=True)
    ws_in = wb_in.worksheets[0]; wmo = ws_in.title.split('_')[0]
    rows = list(ws_in.iter_rows(values_only=True))
    hdr = list(rows[0])
    if hdr[-1] is None: hdr[-1] = 'snow_depth_cm'                 # Voineasa: unnamed last column = snow depth
    data = [r for r in rows[1:] if r[0] is not None]
    i = {c: j for j, c in enumerate(hdr)}
    g = sorted(float(r[i['slp_hpa']]) for r in data if isinstance(r[i['slp_hpa']], (int, float)))
    med = g[len(g) // 2]
    level = '925hPa' if med < 1100 else ('850hPa' if med < 2500 else '700hPa')
    cols = [('date', 'date', None), ('tmax_c', 'tmax_c', '0.0'), ('tmin_c', 'tmin_c', '0.0'), ('precip_mm', 'precip_mm', '0.0'),
            (f'geopot_{level}_m', 'slp_hpa', '0'), ('wind_dir_deg', 'wind_dir_deg', '0'), ('wind_sp_kmh', 'wind_sp_kmh', '0'),
            ('cloud_oktas', 'cloud_oktas', '@'), ('insolation_h', 'insolation_h', '0.0'), ('snow_depth_cm', 'snow_depth_cm', '0')]
    cols = [c for c in cols if c[1] in i]
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'daily'
    ws.append([c[0] for c in cols])
    for r in data:
        out = []
        for _, src_col, fmt in cols:
            v = r[i[src_col]]
            if src_col == 'date': v = v.date() if isinstance(v, dt.datetime) else v
            elif src_col == 'cloud_oktas': v = None if v in (None, '', 'nan') else str(v)
            elif v in ('', 'nan'): v = None
            out.append(v)
        ws.append(out)
    for j, (_, _, fmt) in enumerate(cols, 1):
        for cell in ws.iter_cols(min_col=j, max_col=j, min_row=2):
            for c in cell:
                c.number_format = 'yyyy-mm-dd' if j == 1 else fmt
        ws.column_dimensions[get_column_letter(j)].width = 14 if j > 1 else 12
    for c in ws[1]: c.font = HEAD; c.fill = FILL; c.alignment = Alignment(horizontal='center')
    ws.freeze_panes = 'A2'; ws.auto_filter.ref = ws.dimensions
    m = meta.get(name, {})
    flags = []
    if 'snow_depth_cm' in i:
        bad = [(str(r[0])[:10], r[i['snow_depth_cm']]) for r in data if isinstance(r[i['snow_depth_cm']], (int, float)) and r[i['snow_depth_cm']] > 400]
        flags += [f'snow_depth_cm {v} on {d}: implausible, probably a reporting error (kept as reported)' for d, v in bad]
    info = wb.create_sheet('info')
    lines = [('Station', name), ('WMO', int(wmo)), ('Latitude', float(m.get('lat') or 'nan')), ('Longitude', float(m.get('lon') or 'nan')),
             ('Elevation (m)', float(m.get('elev') or 'nan')), ('DEM elevation at the station pixel (m)', float(m.get('dem_z') or 'nan')),
             ('Period', f'{data[0][0]:%Y-%m-%d} to {data[-1][0]:%Y-%m-%d}'), ('Days', len(data)),
             ('Source', 'meteomanz.com daily SYNOP table (sy2), from the user\'s zip Meteomanz_Daily_20140701_20251231.zip, '
                        f'file {key}_20140701_20251231.xlsx'), ('Coordinates', 'user\'s Excel Statii_AdancimeZilnicaZapada_IonitaAndrei.xlsx'),
             ('', ''), ('Column', 'Meaning')]
    desc = {'date': 'day (as given by Meteomanz)', 'tmax_c': 'maximum temperature (degC)', 'tmin_c': 'minimum temperature (degC)',
            'precip_mm': 'precipitation (mm), 06 UTC of this day -> 06 UTC of the next day (Meteomanz credits the 06->06 total to the day it starts)',
            f'geopot_{level}_m': f'geopotential height of the {level[:3]} hPa level (m), reported by mountain stations instead of sea-level '
                                 f'pressure (column "slp_hpa" in the zip)',
            'wind_dir_deg': 'wind direction (deg)', 'wind_sp_kmh': 'wind speed (km/h)',
            'cloud_oktas': 'cloud cover in eighths of the sky, as on Meteomanz (e.g. 6/8)', 'insolation_h': 'sunshine duration (hours)',
            'snow_depth_cm': 'snow depth (cm)' + (' (column without a name in the zip)' if name == 'Voineasa' else '')}
    lines += [(c[0], desc[c[1] if c[1] != 'slp_hpa' else c[0]]) for c in cols]
    lines += [('', ''), ('Notes', 'No daily mean temperature on Meteomanz daily; (Tmax+Tmin)/2 is not the same (up to 1.2 degC warmer in valleys).'),
              ('', 'Column wind_dir_card of the zip is empty for every station and is left out.')]
    lines += [('Flag', f) for f in flags]
    for row in lines: info.append([v if v != '' else None for v in row])   # '' -> invalid empty inlineStr in openpyxl 3.1.2
    for c in info['A']: c.font = Font(bold=True)
    info.column_dimensions['A'].width = 38; info.column_dimensions['B'].width = 120
    wb.security = None          # openpyxl 3.1.2 writes an empty <workbookProtection/> that Excel reports as a problem
    f = f'{dst}/{name}.xlsx'; wb.save(f)
    print(f'{name:17s} WMO {wmo}: {len(data)} days, {len(cols)} columns ({level} geopotential){"; " + "; ".join(flags) if flags else ""}')
