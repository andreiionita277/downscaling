"""
NOAA station data, organised like data/stations/Meteomanz: one daily CSV per station (named after the station), the
hourly readings in hourly/<station>_<year>_hourly.csv, stations_info.csv and README.md. Every station inside the model
domain (38). Built from the parsed NOAA ISD files (scripts/isd_parse.py: data/stations/noaa_isd_hourly/parsed/), which
come from the raw station-year files in data/stations/noaa_isd_hourly/<year>/ (kept unchanged).
Station names: the Meteomanz name for the 12 stations that also have Meteomanz data, else NOAA's name.

    python meridionali_zarr/scripts/noaa_organize.py
"""
from pathlib import Path

import pandas as pd

ST = Path(__file__).resolve().parents[1] / 'data' / 'stations'
OUT = ST / 'NOAA'
PARSED = ST / 'noaa_isd_hourly' / 'parsed'

DAILY = {  # parsed daily column -> name in the CSV (order = column order)
    't_mean': 'tmean_c', 'tmax_synop': 'tmax_synop_c', 'tmin_synop': 'tmin_synop_c', 't_max_h': 'tmax_readings_c',
    't_min_h': 'tmin_readings_c', 'td_mean': 'tdew_mean_c', 'rh_mean': 'rh_mean_pct', 'q_mean': 'q_mean_gkg',
    'p_st_mean': 'p_station_hpa', 'slp_mean': 'p_sealevel_hpa', 'ws_mean': 'wind_sp_mean_ms', 'ws_max_h': 'wind_sp_max_ms',
    'gust_max': 'gust_max_ms', 'wd_vec': 'wind_dir_mean_deg', 'cloud_mean': 'cloud_mean_oktas', 'vis_min': 'vis_min_m',
    'pr_06_06': 'precip_24h_mm', 'snow_cm_06': 'snow_depth_cm', 'n_hours': 'n_readings', 'basis': 'mean_basis'}
HOURLY = {
    't': 'temp_c', 'td': 'tdew_c', 'rh': 'rh_pct', 'q': 'q_gkg', 'p_st': 'p_station_hpa', 'slp': 'p_sealevel_hpa',
    'wd': 'wind_dir_deg', 'ws': 'wind_sp_ms', 'gust': 'gust_ms', 'cloud_okta': 'cloud_oktas', 'ceiling': 'cloud_base_m',
    'vis': 'vis_m', 'pr_6h': 'precip_6h_mm', 'pr_12h': 'precip_12h_mm', 'pr_24h': 'precip_24h_mm', 'snow_cm': 'snow_depth_cm',
    'tmax_12h': 'tmax_12h_c', 'tmin_12h': 'tmin_12h_c', 'ww': 'present_weather_code'}


def main():
    (OUT / 'hourly').mkdir(parents=True, exist_ok=True)
    st = pd.read_csv(ST / 'merged' / 'stations.csv', dtype={'wmo': str, 'noaa_id': str})
    mm = pd.read_csv(ST / 'Meteomanz' / 'stations_info.csv', dtype={'wmo': str}).set_index('wmo').station
    info = []
    for _, s in st.sort_values('elev').iterrows():
        name = mm.get(s.wmo, s['name'])
        fd = PARSED / 'daily' / f'{s.noaa_id}.csv'
        if not fd.exists():
            continue
        d = pd.read_csv(fd, parse_dates=['date'], low_memory=False)
        d = d[['date'] + list(DAILY)].rename(columns=DAILY)
        d['mean_basis'] = d.mean_basis.fillna('')
        vals = [c for c in d if c not in ('date', 'n_readings', 'mean_basis')]
        d = d[d[vals].notna().any(axis=1)]                                     # days with at least one value
        d['date'] = d.date.dt.strftime('%Y-%m-%d')
        d.to_csv(OUT / f'{name}.csv', index=False, float_format='%.2f')
        years = []
        for fh in sorted((PARSED / 'hourly').glob(f'{s.noaa_id}_*.csv.gz')):
            year = fh.name.split('_')[1][:4]
            h = pd.read_csv(fh, parse_dates=['time']).rename(columns=HOURLY)
            h = h[['time'] + list(HOURLY.values())]
            h = h[h.drop(columns='time').notna().any(axis=1)]
            if h.empty:
                continue
            h['time'] = h.time.dt.strftime('%Y-%m-%d %H:%M')
            h.rename(columns={'time': 'datetime_utc'}).to_csv(OUT / 'hourly' / f'{name}_{year}_hourly.csv', index=False,
                                                              float_format='%.2f')
            years.append(int(year))
        tm = pd.Series(d.tmean_c.values, index=pd.to_datetime(d.date))
        per_year = tm.groupby(tm.index.year).count()
        info.append(dict(file=f'{name}.csv', station=name, noaa_name=s['name'], wmo=s.wmo, noaa_id=s.noaa_id, lat=s.lat,
                         lon=s.lon, elev_m=s.elev, coords_from=s.coords_from, dem_elev_m=s.dem_z, first=d.date.iloc[0],
                         last=d.date.iloc[-1], days_with_data=len(d), days_with_tmean=int(tm.notna().sum()),
                         years_tmean_300d=int((per_year >= 300).sum()),
                         hourly_years=f'{min(years)}-{max(years)} ({len(years)} files)' if years else '',
                         also_in_meteomanz='yes' if s.wmo in mm.index else ''))
        print(f'{name:20s} {len(d):6d} days {d.date.iloc[0]}..{d.date.iloc[-1]}, tmean {int(tm.notna().sum())} days, '
              f'{len(years)} hourly files', flush=True)
    pd.DataFrame(info).to_csv(OUT / 'stations_info.csv', index=False)


if __name__ == '__main__':
    main()
