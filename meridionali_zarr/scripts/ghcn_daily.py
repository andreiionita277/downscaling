"""
NOAA GHCN-Daily station files (public archive https://www.ncei.noaa.gov/data/global-historical-climatology-network-daily/access/<id>.csv)
for the Romanian stations inside the model domain, merged into data/stations/NOAA/<station>.csv (the daily files made
from the hourly reports by noaa_organize.py) as ghcn_* columns; days only in GHCN-Daily are added, rows stay in date order
(user 9 Nov: "can't you merge the old csvs for those stations with this new data?"). Station list with periods and
sources: data/stations/NOAA/ghcn_stations_info.csv. Values with a quality flag (failed NOAA's checks) are dropped. Units
converted from tenths. Run noaa_organize.py first (it rewrites the daily files without the ghcn_* columns).

    python meridionali_zarr/scripts/ghcn_daily.py          (raw files in data/stations/noaa_ghcn_daily_raw/)
Columns added: ghcn_tmax_c, ghcn_tmin_c, ghcn_tavg_c, ghcn_precip_mm, ghcn_snow_depth_cm (+ any other element present),
and per variable ghcn_<var>_source: E = ECA&D (European Climate Assessment & Dataset, national daily data), S = NOAA Global Summary of the
Day (made from the hourly reports, like data/stations/NOAA), other letters as in the GHCN-Daily readme.
"""
from pathlib import Path

import pandas as pd

ST = Path(__file__).resolve().parents[1] / 'data' / 'stations'
RAW = ST / 'noaa_ghcn_daily_raw'
OUT = ST / 'NOAA'
STATIONS = {'ROM00015280': 'Vf. Omu', 'ROE00108901': 'Sibiu', 'ROE00108890': 'Caransebes', 'ROE00108894': 'Deva',
            'ROE00108899': 'Ramnicu Valcea', 'ROE00100904': 'Targu Jiu'}
ELEMENTS = {'TMAX': ('tmax_c', 10), 'TMIN': ('tmin_c', 10), 'TAVG': ('tavg_c', 10), 'PRCP': ('precip_mm', 10),
            'SNWD': ('snow_depth_cm', 10)}            # SNWD is in mm -> cm


def main():
    info = []
    for sid, name in STATIONS.items():
        r = pd.read_csv(RAW / f'{sid}.csv', dtype=str)
        d = pd.DataFrame({'date': r.DATE})
        elems = [c for c in r.columns if c + '_ATTRIBUTES' in r.columns]
        for el in elems:
            col, div = ELEMENTS.get(el, (el.lower(), 1))
            att = r[el + '_ATTRIBUTES'].fillna(',,,').str.split(',', expand=True).reindex(columns=range(4))
            v = pd.to_numeric(r[el].str.strip(), errors='coerce') / div
            ok = att[1].fillna('').str.strip() == ''                      # quality flag empty = passed
            d[col] = v.where(ok)
            d[col + '_source'] = att[2].where(v.notna() & ok, '')
            info.append(dict(station=name, ghcn_id=sid, variable=col, days=int(d[col].notna().sum()),
                             first=d.date[d[col].notna()].min(), last=d.date[d[col].notna()].max(),
                             dropped_qc=int((v.notna() & ~ok).sum()),
                             sources=' '.join(f'{k}:{n}' for k, n in d[col + '_source'][d[col].notna()].value_counts().items())))
        d = d[d[[ELEMENTS.get(e, (e.lower(), 1))[0] for e in elems]].notna().any(axis=1)]
        d = d[[c for c in d if c == 'date' or d[c].notna().any() and (d[c] != '').any()]]
        d = d.set_index('date').add_prefix('ghcn_')
        f = OUT / f'{name}.csv'
        old = pd.read_csv(f, dtype={'mean_basis': str}, low_memory=False).set_index('date')
        old = old[[c for c in old if not c.startswith('ghcn_')]]
        m = old.join(d, how='outer').sort_index()
        m.index.name = 'date'
        m.to_csv(f, float_format='%.2f')
        d = d.reset_index()
        print(f'{name:15s} merged: {len(m)} days {m.index[0]}..{m.index[-1]} ({len(m) - len(old)} new days); ' +
              ', '.join(f'{i["variable"]} {i["first"][:4]}-{i["last"][:4]} ({i["days"]} d)' for i in info if i['station'] == name))
    pd.DataFrame(info).to_csv(OUT / 'ghcn_stations_info.csv', index=False)


if __name__ == '__main__':
    main()
