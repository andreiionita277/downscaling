"""
NOAA NCEI Global Hourly (ISD) station-year files -> clean hourly and daily tables with every variable the Romanian
stations report (format: NCEI "Federal Climate Complex Data Documentation for Integrated Surface Data"). Readings with
a suspect/erroneous quality flag (2, 3, 6, 7) and the 9999-type missing codes are dropped. One row per full UTC hour
(several reports of the same hour are combined: first valid value).

Hourly columns: t, td (degC), rh (%), q (g/kg, from td and station pressure), p_st (station pressure, hPa),
slp (sea-level pressure, hPa), wd (deg), ws (m/s), gust (m/s), cloud_okta (0-8, 9 = sky obscured), ceiling (m),
vis (m), pr_6h / pr_12h / pr_24h (mm, totals ending at that hour), snow_cm (snow depth), tmax_12h / tmin_12h (degC,
SYNOP extremes), ww (present weather code).

Daily (UTC day, needs >= 20 hours for the hourly means): t_mean, t_max_h, t_min_h, td_mean, rh_mean, q_mean, p_st_mean,
slp_mean, ws_mean, ws_max_h, gust_max, wd_vec (vector mean direction), cloud_mean, vis_min, n_hours;
pr_06_06 (mm, 06 UTC of the day before -> 06 UTC of this day: the 24-h report at 06, else 12-h at 18 + 06, else the four
6-h reports), snow_cm_06 (snow depth at 06 UTC), tmax_synop (12-h max reported at 18 UTC), tmin_synop (12-h min reported
at 06 UTC).

    python meridionali_zarr/scripts/isd_parse.py meridionali_zarr/data/stations/noaa_isd_hourly [--years 2025]
Output: <in>/parsed/hourly/<station>_<year>.csv.gz, <in>/parsed/daily/<station>.csv (all years of the station)
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

BAD = {'2', '3', '6', '7'}


def num(s, scale=1.0, missing=None):
    v = pd.to_numeric(s, errors='coerce')
    if missing is not None:
        v = v.where(v != missing)
    return v / scale


def parse_hourly(f):
    d = pd.read_csv(f, dtype=str, low_memory=False)
    d['time'] = pd.to_datetime(d.DATE)
    d = d[d.time.dt.minute == 0]
    o = pd.DataFrame({'time': d.time.values})

    def part(col, n):
        if col not in d:
            return pd.DataFrame(index=d.index, columns=range(n), dtype=str)
        return d[col].str.split(',', expand=True).reindex(columns=range(n))

    def ok(q):
        return ~q.isin(BAD).values

    t = part('TMP', 2)
    o['t'] = np.where(ok(t[1]), num(t[0], 10, 9999).values, np.nan)
    td = part('DEW', 2)
    o['td'] = np.where(ok(td[1]), num(td[0], 10, 9999).values, np.nan)
    sl = part('SLP', 2)
    o['slp'] = np.where(ok(sl[1]), num(sl[0], 10, 99999).values, np.nan)
    ma = part('MA1', 4)
    o['p_st'] = np.where(ok(ma[3]), num(ma[2], 10, 99999).values, np.nan)
    w = part('WND', 5)
    o['wd'] = np.where(ok(w[1]) & (w[2] != 'C').values, num(w[0], 1, 999).values, np.nan)
    o.loc[(w[2] == 'C').values, 'wd'] = np.nan                     # calm
    o['ws'] = np.where(ok(w[4]), num(w[3], 10, 9999).values, np.nan)
    g = part('OC1', 2)
    o['gust'] = np.where(ok(g[1]), num(g[0], 10, 9999).values, np.nan)
    gf = part('GF1', 13)
    o['cloud_okta'] = np.where(ok(gf[2]), num(gf[0], 1, 99).values, np.nan)
    c = part('CIG', 4)
    o['ceiling'] = np.where(ok(c[1]), num(c[0], 1, 99999).values, np.nan)
    v = part('VIS', 4)
    o['vis'] = np.where(ok(v[1]), num(v[0], 1, 999999).values, np.nan)
    for k in ('pr_6h', 'pr_12h', 'pr_24h'):
        o[k] = np.nan
    for col in ('AA1', 'AA2', 'AA3', 'AA4'):
        a = part(col, 4)
        per = num(a[0]).values
        # condition code 9 = missing (some such reports still carry a bogus depth, e.g. 3800); 2 = trace -> 0 mm
        mm = np.where(ok(a[3]) & (a[2] != '9').values, num(a[1], 10, 9999).values, np.nan)
        mm = np.where((a[2] == '2').values, 0.0, mm)
        for h in (6, 12, 24):
            sel = (per == h) & np.isfinite(mm)
            o.loc[sel, f'pr_{h}h'] = mm[sel]
    s = part('AJ1', 6)
    o['snow_cm'] = np.where(ok(s[2]), num(s[0], 1, 9999).values, np.nan)
    o['tmax_12h'] = np.nan
    o['tmin_12h'] = np.nan
    for col in ('KA1', 'KA2', 'KA3', 'KA4'):
        k = part(col, 4)
        per = num(k[0], 10).values
        val = np.where(ok(k[3]), num(k[2], 10, 9999).values, np.nan)
        for code, dst in (('M', 'tmax_12h'), ('N', 'tmin_12h')):
            sel = (k[1] == code).values & (per == 12) & np.isfinite(val)
            o.loc[sel, dst] = val[sel]
    mw = part('MW1', 2)
    o['ww'] = np.where(ok(mw[1]), num(mw[0]).values, np.nan)
    # humidity from t, td (Magnus over water) and station pressure
    es = lambda x: 6.112 * np.exp(17.62 * x / (243.12 + x))     # noqa: E731
    o['rh'] = (100 * es(o.td) / es(o.t)).clip(upper=100)
    e = es(o.td)
    o['q'] = 1000 * 0.622 * e / (o.p_st - 0.378 * e)
    o = o.groupby('time').first().sort_index()                  # several reports of one hour: first valid value
    return o


def daily(h):
    g = h.groupby(h.index.floor('D'))
    n = g.t.count()
    rad = np.deg2rad(h.wd)
    u, v = (-h.ws * np.sin(rad)).groupby(h.index.floor('D')).mean(), (-h.ws * np.cos(rad)).groupby(h.index.floor('D')).mean()
    d = pd.DataFrame({
        't_mean': g.t.mean(), 't_max_h': g.t.max(), 't_min_h': g.t.min(), 'td_mean': g.td.mean(),
        'rh_mean': g.rh.mean(), 'q_mean': g.q.mean(), 'p_st_mean': g.p_st.mean(), 'slp_mean': g.slp.mean(),
        'ws_mean': g.ws.mean(), 'ws_max_h': g.ws.max(), 'gust_max': g.gust.max(),
        'wd_vec': (np.rad2deg(np.arctan2(-u, -v)) % 360), 'cloud_mean': g.cloud_okta.mean(), 'vis_min': g.vis.min(),
        'n_hours': n})
    hourly_cols = ['t_mean', 't_max_h', 't_min_h', 'td_mean', 'rh_mean', 'q_mean', 'p_st_mean', 'slp_mean', 'ws_mean',
                   'ws_max_h', 'wd_vec', 'cloud_mean', 'vis_min']
    # basis of the daily means: >= 20 hourly readings ('hourly'); else all 8 synoptic hours 00, 03, ..., 21 UTC ('synop8',
    # how older years were reported: 3-hourly until the 1990s/2010s); else none (3-4 readings/day before ~1960)
    s8 = h[h.index.hour % 3 == 0]
    g8 = s8.groupby(s8.index.floor('D'))
    n8 = g8.t.count().reindex(d.index).fillna(0)
    rad8 = np.deg2rad(s8.wd)
    u8 = (-s8.ws * np.sin(rad8)).groupby(s8.index.floor('D')).mean()
    v8 = (-s8.ws * np.cos(rad8)).groupby(s8.index.floor('D')).mean()
    syn = pd.DataFrame({
        't_mean': g8.t.mean(), 't_max_h': g8.t.max(), 't_min_h': g8.t.min(), 'td_mean': g8.td.mean(),
        'rh_mean': g8.rh.mean(), 'q_mean': g8.q.mean(), 'p_st_mean': g8.p_st.mean(), 'slp_mean': g8.slp.mean(),
        'ws_mean': g8.ws.mean(), 'ws_max_h': g8.ws.max(), 'wd_vec': (np.rad2deg(np.arctan2(-u8, -v8)) % 360),
        'cloud_mean': g8.cloud_okta.mean(), 'vis_min': g8.vis.min()}).reindex(d.index)
    use8 = (n < 20) & (n8 == 8)
    d.loc[use8, hourly_cols] = syn.loc[use8, hourly_cols]
    d.loc[(n < 20) & ~use8, hourly_cols] = np.nan
    d['basis'] = np.where(n >= 20, 'hourly', np.where(use8, 'synop8', ''))
    # precipitation 06 -> 06 UTC, credited to the day it ends
    at = lambda hh, col: h[h.index.hour == hh][col].set_axis(h[h.index.hour == hh].index.floor('D'))  # noqa: E731
    p24 = at(6, 'pr_24h')
    p12 = at(6, 'pr_12h') + at(18, 'pr_12h').shift(1, freq='D').reindex(at(6, 'pr_12h').index)
    p6 = sum(at(hh, 'pr_6h').shift(1 if hh > 6 else 0, freq='D').reindex(d.index) for hh in (6, 12, 18, 0)) \
        if 'pr_6h' in h else None
    pr = p24.reindex(d.index)
    pr = pr.fillna(p12.reindex(d.index))
    if p6 is not None:
        pr = pr.fillna(p6)
    d['pr_06_06'] = pr
    d['snow_cm_06'] = at(6, 'snow_cm').reindex(d.index)
    d['tmax_synop'] = at(18, 'tmax_12h').reindex(d.index)
    d['tmin_synop'] = at(6, 'tmin_12h').reindex(d.index)
    d.index.name = 'date'
    return d


def main():
    p = argparse.ArgumentParser()
    p.add_argument('src')
    p.add_argument('--years', nargs='+', type=int)
    a = p.parse_args()
    src = Path(a.src)
    hdir, ddir = src / 'parsed' / 'hourly', src / 'parsed' / 'daily'
    hdir.mkdir(parents=True, exist_ok=True)
    ddir.mkdir(parents=True, exist_ok=True)
    files = sorted(f for f in src.glob('[0-9][0-9][0-9][0-9]/*.csv')
                   if not a.years or int(f.parent.name) in a.years)
    by_station = {}
    for f in files:
        sid, year = f.stem, f.parent.name
        try:
            h = parse_hourly(f)
        except Exception as e:  # noqa: BLE001
            print(f'{sid} {year}: parse failed ({e})', flush=True)
            continue
        h.to_csv(hdir / f'{sid}_{year}.csv.gz', float_format='%.2f')
        by_station.setdefault(sid, []).append(daily(h))
    for sid, parts in by_station.items():
        d = pd.concat(parts).sort_index()
        d = d[~d.index.duplicated()]
        f = ddir / f'{sid}.csv'
        if f.exists() and a.years:                                 # merge with years parsed earlier
            old = pd.read_csv(f, parse_dates=['date']).set_index('date')
            d = pd.concat([old[~old.index.isin(d.index)], d]).sort_index()
        d.to_csv(f, float_format='%.2f')
        cov = d.notna().mean().round(2)
        print(f'{sid}: {len(d)} days {d.index.min().date()}..{d.index.max().date()}; filled: t {cov.t_mean:.0%}, '
              f'q {cov.q_mean:.0%}, p {cov.p_st_mean:.0%}, ws {cov.ws_mean:.0%}, pr {cov.pr_06_06:.0%}, '
              f'snow {cov.snow_cm_06:.0%}, cloud {cov.cloud_mean:.0%}', flush=True)


if __name__ == '__main__':
    main()
