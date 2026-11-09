"""
NOAA NCEI Global Hourly (ISD) files -> daily air temperature per station: the mean of the hourly readings of the
UTC day (same day as the model's daily.zarr), plus the hourly max/min. Only readings that passed NCEI's quality
control are used (TMP quality flag not 2, 3, 6 or 7), one value per full hour (several reports of the same hour
are averaged), and a day needs >= MIN_HOURS hours.

Data: public bucket https://noaa-global-hourly-pds.s3.amazonaws.com/<year>/<WMO>099999.csv (open data, bulk use
intended). For Romania the 2025 files end on 24 Aug 2025.

    python meridionali_zarr/scripts/isd_daily.py meridionali_zarr/data/stations/noaa_isd_hourly/2025
Output: <folder>/daily/<WMO>.csv (date, tmean24, n_hours, tmax_h, tmin_h)
"""
import sys
from pathlib import Path

import pandas as pd

MIN_HOURS = 20
BAD_QC = set('2367')


def daily(path):
    h = pd.read_csv(path, usecols=['DATE', 'TMP'], dtype=str)
    t = h.TMP.str.split(',', expand=True)
    h['t'] = pd.to_numeric(t[0], errors='coerce') / 10
    h = h[(t[0] != '+9999') & ~t[1].isin(BAD_QC) & h.t.notna()]
    h['time'] = pd.to_datetime(h.DATE)
    h = h[h.time.dt.minute == 0].groupby('time').t.mean()        # full hours only, duplicates averaged
    g = h.groupby(h.index.floor('D'))
    d = pd.DataFrame({'tmean24': g.mean(), 'n_hours': g.size(), 'tmax_h': g.max(), 'tmin_h': g.min()})
    d = d[d.n_hours >= MIN_HOURS]
    d.index.name = 'date'
    return d, len(h)


def main():
    src = Path(sys.argv[1])
    out = src / 'daily'
    out.mkdir(exist_ok=True)
    for f in sorted(src.glob('*099999.csv')):
        d, nh = daily(f)
        wmo = f.name[:5]
        d.to_csv(out / f'{wmo}.csv', float_format='%.2f')
        print(f'{wmo}: {nh} hourly readings, {len(d)} days with >= {MIN_HOURS} h '
              f'({d.index.min().date()} .. {d.index.max().date()})', flush=True)


if __name__ == '__main__':
    main()
