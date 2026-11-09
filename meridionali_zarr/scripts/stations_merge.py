"""
One daily table per station from both station sources, for every station inside the model domain (DEM rectangle,
clustering mask), plus one station list. Sources (prepared by isd_parse.py and the Meteomanz extraction, LOG_28Oct2026):
  NOAA      data/stations/noaa_isd_hourly/parsed/daily/<USAF><WBAN>.csv   (hourly/3-hourly reports, all variables)
  Meteomanz data/stations/meteomanz_daily_full/<WMO>.csv                 (daily SYNOP summary, 2014-07 .. 2025-12)
Station metadata: NOAA isd-history (data/stations/noaa_isd_stations_in_domain.csv) and the user's Excel
(Statii_AdancimeZilnicaZapada_IonitaAndrei.xlsx, sheet Ideal; data/stations/stations_in_domain.csv). Where both give
coordinates, the user's Excel is used (more precise).

Columns: noaa_* and mm_* as in the sources, plus combined columns:
  t_mean      NOAA daily mean of the hourly / 8 synoptic readings (true UTC-day mean; Meteomanz has none)
  t_max/t_min Meteomanz SYNOP daily extremes; else NOAA SYNOP 12-h extremes (18 / 06 UTC)
  pr_06_06    precipitation 06 UTC of the previous day -> 06 UTC of this day (credited to the day it ENDS): NOAA 24-h
              total, else Meteomanz shifted by one day (Meteomanz credits the 06->06 total to the day it STARTS;
              checked on 2025: r 0.98-1.00, same totals)
  snow_cm     snow depth: Meteomanz, else NOAA at 06 UTC
  sun_h       sunshine hours (Meteomanz only; NOAA has none)
and *_src columns naming the source used.

    python meridionali_zarr/scripts/stations_merge.py
Output: data/stations/merged/stations.csv, data/stations/merged/daily/<WMO>.csv, data/stations/merged/coverage.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from pyproj import Transformer

MZ = Path(__file__).resolve().parents[1]
ST = MZ / 'data' / 'stations'
OUT = ST / 'merged'
DEM = MZ / 'data' / 'dem' / 'meridionali_2025' / 'dem.tif'
MASK = MZ / 'data' / 'dem' / 'meridionali_2025' / 'mask.tif'
TERRAIN = MZ / 'data' / 'meridionali_E_2025' / 'outputs'
MM_COLS = {'tmax_c': 'tmax', 'tmin_c': 'tmin', 'precip_mm': 'precip', 'slp_hpa': 'slp', 'wind_dir_deg': 'wd',
           'wind_sp_kmh': 'ws_kmh', 'cloud_oktas': 'cloud', 'insolation_h': 'sun_h', 'snow_depth_cm': 'snow_cm'}


def locate(st):
    """Pixel, DEM elevation, mask and cluster of each station."""
    dem, mask = rasterio.open(DEM), rasterio.open(MASK)
    tr = Transformer.from_crs(4326, dem.crs, always_xy=True)
    pi_all = xr.open_dataset(TERRAIN / 'ds_param.nc').point_ind.transpose('y', 'x').values
    cz = pd.read_pickle(TERRAIN / 'df_centroids.pck').set_index('point_ind').elevation
    out = []
    for _, s in st.iterrows():
        x, y = tr.transform(s.lon, s.lat)
        r, c = dem.index(x, y)
        if not (0 <= r < dem.height and 0 <= c < dem.width):
            out.append(dict(row=np.nan, col=np.nan, dem_z=np.nan, in_mask=0, point_ind=np.nan, cluster_z=np.nan))
            continue
        w = ((r, r + 1), (c, c + 1))
        pi = int(pi_all[r, c])
        out.append(dict(row=r, col=c, dem_z=round(float(dem.read(1, window=w)[0, 0]), 1),
                        in_mask=int(mask.read(1, window=w)[0, 0]), point_ind=pi, cluster_z=round(float(cz.get(pi, np.nan)), 1)))
    return pd.concat([st.reset_index(drop=True), pd.DataFrame(out)], axis=1)


def main():
    (OUT / 'daily').mkdir(parents=True, exist_ok=True)
    # station list: NOAA (all in domain) + Meteomanz (with the user's coordinates)
    n = pd.read_csv(ST / 'noaa_isd_stations_in_domain.csv', dtype={'usaf': str, 'wban': str})
    n = pd.DataFrame({'wmo': n.usaf.str[:5], 'name': n.name.str.title(), 'lat': n.lat, 'lon': n.lon, 'elev': n.elev,
                      'noaa_id': n.usaf + n.wban})
    user = pd.read_csv(ST / 'stations_in_domain.csv')                          # user's Excel, inside the DEM
    mm_files = {p.stem: p for p in (ST / 'meteomanz_daily_full').glob('*.csv')}
    mm_names = {w: pd.read_csv(p, nrows=1).file.iloc[0] for w, p in mm_files.items()}
    st = n.set_index('wmo')
    for w, fname in mm_names.items():
        if w not in st.index:
            continue                                                            # Meteomanz station outside the domain
    # user's coordinates/elevation where the names match
    norm = lambda s: ''.join(ch for ch in s.lower() if ch.isalnum()).replace('sibiu', '').replace('varfu', 'vf')   # noqa: E731
    for _, u in user.iterrows():
        hit = [w for w in st.index if norm(st.loc[w, 'name']).startswith(norm(u['name'])[:6])]
        if hit:
            st.loc[hit[0], ['lat', 'lon', 'elev']] = [u.lat, u.lon, u.elev]
            st.loc[hit[0], 'coords_from'] = 'user Excel'
    st['coords_from'] = st.get('coords_from', pd.Series(index=st.index, dtype=str)).fillna('NOAA isd-history')
    st['meteomanz_file'] = pd.Series(mm_names)
    st = locate(st.reset_index())
    st = st[st.in_mask == 1]

    cov = []
    for _, s in st.iterrows():
        parts = []
        fn = ST / 'noaa_isd_hourly' / 'parsed' / 'daily' / f'{s.noaa_id}.csv'
        if fn.exists():
            d = pd.read_csv(fn, parse_dates=['date']).set_index('date')
            parts.append(d.add_prefix('noaa_'))
        if isinstance(s.meteomanz_file, str):
            m = pd.read_csv(mm_files[s.wmo], parse_dates=['date']).set_index('date')
            m = m[~m.index.duplicated()][[c for c in MM_COLS if c in m]].rename(columns=MM_COLS)
            for c in m:
                if c == 'cloud':                                                  # '6/8' -> 6 oktas
                    m[c] = m[c].map(lambda v: str(v).split('/')[0] if pd.notna(v) else np.nan)
                m[c] = pd.to_numeric(m[c], errors='coerce')
            m['precip_06_06_end'] = m.precip.shift(1, freq='D').reindex(m.index)   # credited to the day it ends
            parts.append(m.add_prefix('mm_'))
        if not parts:
            continue
        d = pd.concat(parts, axis=1).sort_index()
        d.index.name = 'date'

        def pick(a, b, la, lb):
            va = d[a] if a in d else pd.Series(np.nan, index=d.index)
            vb = d[b] if b in d else pd.Series(np.nan, index=d.index)
            return va.where(va.notna(), vb), np.where(va.notna(), la, np.where(vb.notna(), lb, ''))

        d['t_mean'] = d['noaa_t_mean'] if 'noaa_t_mean' in d else np.nan
        d['t_mean_src'] = np.where(d.t_mean.notna(), 'noaa_' + d.get('noaa_basis', pd.Series('', index=d.index)).fillna('').astype(str), '')
        d['t_max'], d['t_max_src'] = pick('mm_tmax', 'noaa_tmax_synop', 'meteomanz', 'noaa_synop')
        d['t_min'], d['t_min_src'] = pick('mm_tmin', 'noaa_tmin_synop', 'meteomanz', 'noaa_synop')
        d['pr_06_06'], d['pr_06_06_src'] = pick('noaa_pr_06_06', 'mm_precip_06_06_end', 'noaa_24h', 'meteomanz')
        d['snow_cm'], d['snow_cm_src'] = pick('mm_snow_cm', 'noaa_snow_cm_06', 'meteomanz', 'noaa_06utc')
        d['sun_h'] = d['mm_sun_h'] if 'mm_sun_h' in d else np.nan
        d.to_csv(OUT / 'daily' / f'{s.wmo}.csv', float_format='%.2f')
        r = dict(wmo=s.wmo, name=s['name'], elev=s.elev, first=d.index.min().date(), last=d.index.max().date())
        for c in ('t_mean', 't_max', 't_min', 'pr_06_06', 'snow_cm', 'sun_h', 'noaa_q_mean', 'noaa_p_st_mean',
                  'noaa_ws_mean', 'noaa_cloud_mean'):
            v = d[c] if c in d else pd.Series(dtype=float)
            yrs = v.groupby(v.index.year).count() if len(v) else pd.Series(dtype=int)
            r[f'{c}_days'] = int(v.notna().sum())
            r[f'{c}_years300'] = int((yrs >= 300).sum())
        cov.append(r)
    st.drop(columns=[c for c in ('index',) if c in st]).to_csv(OUT / 'stations.csv', index=False)
    C = pd.DataFrame(cov).sort_values('elev')
    C.to_csv(OUT / 'coverage.csv', index=False)
    pd.set_option('display.width', 220)
    print(f'{len(st)} stations inside the mask, {len(C)} with data; Meteomanz: {st.meteomanz_file.notna().sum()}')
    print(C[['wmo', 'name', 'elev', 'first', 'last', 't_mean_years300', 't_max_years300', 'pr_06_06_years300',
             'sun_h_years300', 'noaa_q_mean_years300', 'noaa_ws_mean_years300']].to_string(index=False))


if __name__ == '__main__':
    main()
