"""
Daily temperature (and annual precipitation) of one downscaled year against the Meteomanz SYNOP stations inside
the domain. The stores are only read. For every station the model is taken three ways:
    cluster   the value of the cluster the station pixel belongs to (TopoSCALE output, no adjustment)
    pixel     cluster value + Gamma * (DEM pixel elevation - cluster centre elevation)   (= the lapse GeoTIFFs)
    station   cluster value + Gamma * (station elevation - cluster centre elevation)
with Gamma the day's local lapse rate of the cluster (as lapse_map_test.py / lapse_geotiff_year.py, k=50, clip
1.5 K/100 m), computed separately for t_min, t_max and t_mean.
Observed daily mean = (Tmax + Tmin) / 2, so it is compared with the model's (t_max + t_min) / 2. SYNOP Tmax/Tmin
are 12-h extremes (usually 06-18 / 18-06 UTC) while the model day is 00-24 UTC; the lag test shows whether
observed Tmin fits the model's same day or the next/previous one better.

    python meridionali_zarr/scripts/station_compare.py meridionali_zarr/config/config_meridionali_E_2025.yml
           [--stations meridionali_zarr/data/stations/stations_in_domain.csv]
           [--obs meridionali_zarr/data/stations/meteomanz_daily_csv] [--geotiff <folder of the lapse GeoTIFFs>]

Output: <outputs>/validation/: station_stats.csv, monthly_bias.csv, lag_test.csv, precip_annual.csv, daily_<year>.csv,
bias_vs_elevation.png, monthly_bias_tmid.png, scatter_tmid.png
"""
import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
import xarray as xr  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow as wf  # noqa: E402
from lapse_map_test import local_lapse  # noqa: E402

FILES = {'Balea Lac': 'BaleaLac', 'Cuntu': 'Cuntu', 'Fundata': 'Fundata', 'Obarsia Lotrului': 'ObarsiaLotrului',
         'Paltinis': 'Paltinis', 'Parang': 'Parang', 'Petrosani': 'Petrosani', 'Predeal': 'Predeal',
         'Sinaia 1500': 'Sinaia1500', 'Tarcu': 'Tarcu', 'Vf. Omu': 'VfOmu', 'Voineasa': 'Voineasa'}
WMO = {'Balea Lac': 15279, 'Cuntu': 15316, 'Fundata': 15301, 'Obarsia Lotrului': 15297, 'Paltinis': 15254,
       'Parang': 15320, 'Petrosani': 15296, 'Predeal': 15302, 'Sinaia 1500': 15325, 'Tarcu': 15317, 'Vf. Omu': 15280,
       'Voineasa': 15319}
VERSIONS = ['cluster', 'pixel', 'station']
INK, INK2, GRID, SURF = '#0b0b0b', '#52514e', '#e4e3df', '#fcfcfb'
SERIES = {'cluster': '#2a78d6', 'pixel': '#1baf7a', 'station': '#eb6834'}     # categorical slots 1, 3, 2


def stats(m, o, min_n=30):
    ok = np.isfinite(m) & np.isfinite(o)
    m, o = m[ok], o[ok]
    if len(m) < min_n:
        return dict(n=len(m), bias=np.nan, mae=np.nan, rmse=np.nan, r=np.nan)
    d = m - o
    return dict(n=len(m), bias=d.mean(), mae=np.abs(d).mean(), rmse=np.sqrt((d ** 2).mean()), r=np.corrcoef(m, o)[0, 1])


def style(ax):
    ax.set_facecolor(SURF)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    for s in ('left', 'bottom'):
        ax.spines[s].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--stations', default='meridionali_zarr/data/stations/stations_in_domain.csv')
    p.add_argument('--obs', default='meridionali_zarr/data/stations/meteomanz_daily_csv')
    p.add_argument('--isd', default='meridionali_zarr/data/stations/noaa_isd_hourly/{year}/daily',
                   help='daily 24-h means from isd_daily.py ({year} is filled in); skipped if missing')
    p.add_argument('--geotiff', default='/mnt/d/Downscaling/meridionali_zarr/meridionali_E_2025/geotiff_lapse')
    p.add_argument('--k', type=int, default=50)
    p.add_argument('--clip', type=float, default=0.015)
    a = p.parse_args()
    cfg, out, _ = wf.load_config(a.config)
    year = int(pd.Timestamp(cfg['project']['start']).year)
    vdir = out / 'validation'
    vdir.mkdir(exist_ok=True)

    c = pd.read_pickle(out / cfg['outputs']['file']['df_centroids'])
    pos = pd.Series(np.arange(len(c)), index=c.point_ind.values)
    daily = xr.open_zarr(out / 'downscaled' / 'daily.zarr')
    days = pd.DatetimeIndex(daily.time.values)
    st = pd.read_csv(a.stations)
    st = st[st.name.isin(FILES)].reset_index(drop=True)
    st['k'] = pos.reindex(st.point_ind).values
    print(f'{year}: {len(st)} stations with data inside the mask', flush=True)

    # model per station, version and variable (degC)
    model = {}
    for var in ('t_min', 't_max', 't_mean'):
        v = daily[var].load().sel(point_name=c.point_name.values).transpose('time', 'point_name').values - wf.OFFSET['t']
        lapse = np.stack([local_lapse(c.x.values, c.y.values, c.elevation.values, v[i], a.k, a.clip)
                          for i in range(len(days))])
        for _, s in st.iterrows():
            k, cz = int(s.k), c.elevation.values[int(s.k)]
            model[(s['name'], var, 'cluster')] = v[:, k]
            model[(s['name'], var, 'pixel')] = v[:, k] + lapse[:, k] * (s.dem_z - cz)
            model[(s['name'], var, 'station')] = v[:, k] + lapse[:, k] * (s.elev - cz)
        print(f'  {var}: lapse rates for {len(days)} days', flush=True)
    tp = daily['tp_sum'].load().sel(point_name=c.point_name.values).transpose('time', 'point_name').values

    # the 'pixel' version must equal the lapse GeoTIFFs (t_mean, 3 days)
    geo_check = []
    gdir = Path(a.geotiff)
    if gdir.exists():
        for d in (days[0], days[len(days) // 2], days[-1]):
            f = gdir / f't_mean_daily_lapse_{d:%Y-%m}.tif'
            with rasterio.open(f) as r:
                b = d.day
                for _, s in st.iterrows():
                    g = float(r.read(b, window=((s.row, s.row + 1), (s.col, s.col + 1)))[0, 0])
                    geo_check.append(abs(g - model[(s['name'], 't_mean', 'pixel')][days.get_loc(d)]))
        print(f'  GeoTIFF check: {len(geo_check)} station-days, max |GeoTIFF - pixel version| '
              f'{max(geo_check):.2e} K', flush=True)

    rows, mrows, lrows, prows, drows = [], [], [], [], []
    for _, s in st.iterrows():
        n = s['name']
        o = pd.read_csv(Path(a.obs) / f'{FILES[n]}.csv', parse_dates=['date']).set_index('date')
        o = o[~o.index.duplicated()].reindex(days)
        obs = {'tmax': o.tmax_c.values.astype(float), 'tmin': o.tmin_c.values.astype(float)}
        obs['tmid'] = (obs['tmax'] + obs['tmin']) / 2
        fi = Path(a.isd.format(year=year)) / f'{WMO[n]}.csv'
        if fi.exists():                     # true UTC-day mean of the hourly readings (NOAA ISD)
            h = pd.read_csv(fi, parse_dates=['date']).set_index('date').reindex(days)
            obs['tmean24'] = h.tmean24.values.astype(float)
            obs['tmid_isd'] = np.where(np.isfinite(obs['tmean24']), obs['tmid'], np.nan)   # same days as tmean24
        for ver in VERSIONS:
            mod = {'tmax': model[(n, 't_max', ver)], 'tmin': model[(n, 't_min', ver)]}
            mod['tmid'] = (mod['tmax'] + mod['tmin']) / 2
            if 'tmean24' in obs:
                mod['tmean24'] = model[(n, 't_mean', ver)]
                mod['tmid_isd'] = mod['tmid']
            for var in [k for k in ('tmid', 'tmin', 'tmax', 'tmean24', 'tmid_isd') if k in obs]:
                rows.append(dict(station=n, elev=s.elev, dem_z=s.dem_z, cluster_z=s.cluster_z, var=var, version=ver,
                                 **stats(mod[var], obs[var])))
            if ver == 'station':
                for mth in range(1, 13):
                    sel = days.month == mth
                    for var in [k for k in ('tmid', 'tmin', 'tmax', 'tmean24') if k in obs]:
                        mrows.append(dict(station=n, elev=s.elev, month=mth, var=var,
                                          **stats(mod[var][sel], obs[var][sel], min_n=20)))
                for var in ('tmin', 'tmax'):
                    for lag in (-1, 0, 1):                    # model shifted: model day d+lag vs observed day d
                        m = np.roll(mod[var], -lag)
                        if lag:
                            m[(-1 if lag > 0 else 0)] = np.nan
                        lrows.append(dict(station=n, var=var, lag=lag, **stats(m, obs[var])))
                drows.append(pd.DataFrame({'date': days, 'station': n, **{f'obs_{k}': v for k, v in obs.items()},
                                           **{f'mod_{k}': v for k, v in mod.items()}}))
        po = o.precip_mm.astype(float)
        prows.append(dict(station=n, elev=s.elev, obs_mm=po.sum(), obs_days=int(po.notna().sum()),
                          model_mm=tp[:, int(s.k)][po.notna().values].sum()))

    S = pd.DataFrame(rows); M = pd.DataFrame(mrows); L = pd.DataFrame(lrows); P = pd.DataFrame(prows)
    S.to_csv(vdir / 'station_stats.csv', index=False, float_format='%.3f')
    M.to_csv(vdir / 'monthly_bias.csv', index=False, float_format='%.3f')
    L.to_csv(vdir / 'lag_test.csv', index=False, float_format='%.3f')
    P.to_csv(vdir / 'precip_annual.csv', index=False, float_format='%.1f')
    pd.concat(drows).to_csv(vdir / f'daily_{year}.csv', index=False, float_format='%.2f')

    # figure 1: bias vs elevation, one panel per variable, the three model versions
    fig, axs = plt.subplots(1, 3, figsize=(13, 4.4), sharey=True, facecolor=SURF)
    for ax, var, title in zip(axs, ('tmid', 'tmin', 'tmax'), ('(Tmax+Tmin)/2', 'Tmin', 'Tmax')):
        style(ax)
        ax.axhline(0, color=INK2, lw=1)
        for ver in VERSIONS:
            d = S[(S['var'] == var) & (S.version == ver)].sort_values('elev')
            ax.plot(d.elev, d.bias, 'o', ms=7, color=SERIES[ver], mec=SURF, mew=1.5, label=ver, zorder=3)
        d = S[(S['var'] == var) & (S.version == 'station')]
        for _, r in d.iterrows():
            ax.annotate(r.station, (r.elev, r.bias), xytext=(4, 3), textcoords='offset points', fontsize=6.5,
                        color=INK2)
        ax.set_title(title, color=INK, fontsize=10, loc='left')
        ax.set_xlabel('station elevation (m)', color=INK2, fontsize=8)
    axs[0].set_ylabel('model - station, annual mean (°C)', color=INK2, fontsize=8)
    axs[0].legend(frameon=False, fontsize=8, labelcolor=INK2, title='model taken at', title_fontsize=8)
    fig.suptitle(f'{year}: daily temperature bias at {len(st)} stations', color=INK, fontsize=11, x=0.01, ha='left')
    fig.tight_layout()
    fig.savefig(vdir / 'bias_vs_elevation.png', dpi=150, facecolor=SURF)

    # figure 1b: 24-h mean (NOAA hourly) vs (Tmax+Tmin)/2 (Meteomanz) on the same days, station version
    if (S['var'] == 'tmean24').any():
        fig, ax = plt.subplots(figsize=(8, 4.6), facecolor=SURF)
        style(ax)
        ax.axhline(0, color=INK2, lw=1)
        q = S[S.version == 'station'].pivot(index='station', columns='var', values='bias').join(st.set_index('name').elev)
        q = q.dropna(subset=['tmean24']).sort_values('elev')
        n_days = int(S[(S['var'] == 'tmean24') & (S.version == 'station')].n.median())
        for var, col, lab in (('tmid_isd', '#2a78d6', '(Tmax+Tmin)/2, Meteomanz'),
                              ('tmean24', '#eb6834', '24-h mean of hourly readings, NOAA')):
            ax.plot(q.elev, q[var], 'o', ms=8, color=col, mec=SURF, mew=1.5, label=lab, zorder=3)
        for nm, r in q.iterrows():
            ax.annotate(nm, (r.elev, r.tmean24), xytext=(5, -3), textcoords='offset points', fontsize=6.5, color=INK2)
        ax.set_xlabel('station elevation (m)', color=INK2, fontsize=8)
        ax.set_ylabel('model - station, mean over the days (°C)', color=INK2, fontsize=8)
        ax.legend(frameon=False, fontsize=8, labelcolor=INK2, title='station daily mean taken as', title_fontsize=8)
        ax.set_title(f'{year}: daily mean temperature bias, same ~{n_days} days (1 Jan - 23 Aug), model at station '
                     'elevation', color=INK, fontsize=9.5, loc='left')
        fig.tight_layout()
        fig.savefig(vdir / 'bias_tmean24_vs_tmid.png', dpi=150, facecolor=SURF)

    # figure 2: monthly bias of (Tmax+Tmin)/2, station version, stations sorted by elevation (diverging blue-red)
    d = M[M['var'] == 'tmid'].pivot(index='station', columns='month', values='bias')
    d = d.loc[st.sort_values('elev', ascending=False).name]
    lim = max(1.0, np.nanmax(np.abs(d.values)))
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list('div', ['#2a78d6', '#f0efec', '#e34948'])
    fig, ax = plt.subplots(figsize=(9, 5.2), facecolor=SURF)
    im = ax.imshow(d.values, cmap=cmap, vmin=-lim, vmax=lim, aspect='auto')
    for i in range(d.shape[0]):
        for j in range(d.shape[1]):
            if np.isfinite(d.values[i, j]):
                ax.text(j, i, f'{d.values[i, j]:+.1f}', ha='center', va='center', fontsize=7, color=INK)
    elev = st.set_index('name').elev
    ax.set_yticks(range(len(d)), [f'{n} ({elev[n]:.0f} m)' for n in d.index], fontsize=8, color=INK2)
    ax.set_xticks(range(12), ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'], fontsize=8, color=INK2)
    for sp in ax.spines.values():
        sp.set_visible(False)
    cb = fig.colorbar(im, ax=ax, shrink=0.8)
    cb.set_label('model - station (°C); blue = model colder', color=INK2, fontsize=8)
    cb.ax.tick_params(labelsize=7, colors=INK2)
    ax.set_title(f'{year}: monthly bias of (Tmax+Tmin)/2, model at station elevation', color=INK, fontsize=10,
                 loc='left')
    fig.tight_layout()
    fig.savefig(vdir / 'monthly_bias_tmid.png', dpi=150, facecolor=SURF)

    # figure 3: daily scatter per station (small multiples), station version
    D = pd.concat(drows)
    order = list(st.sort_values('elev', ascending=False).name)
    fig, axs = plt.subplots(3, 4, figsize=(13, 9.5), facecolor=SURF)
    lo, hi = np.nanmin(D[['obs_tmid', 'mod_tmid']].values), np.nanmax(D[['obs_tmid', 'mod_tmid']].values)
    for ax, n in zip(axs.flat, order):
        style(ax)
        q = D[D.station == n]
        ax.plot([lo, hi], [lo, hi], color=INK2, lw=1)
        ax.plot(q.obs_tmid, q.mod_tmid, 'o', ms=3, color=SERIES['station'], alpha=0.6, mec='none')
        r = S[(S.station == n) & (S['var'] == 'tmid') & (S.version == 'station')].iloc[0]
        ax.set_title(f'{n} ({r.elev:.0f} m)', color=INK, fontsize=9, loc='left')
        ax.text(0.03, 0.95, f'bias {r.bias:+.2f} °C\nRMSE {r.rmse:.2f} °C\nr {r.r:.3f}', transform=ax.transAxes,
                va='top', fontsize=7.5, color=INK2)
        ax.set_xlim(lo, hi); ax.set_ylim(lo, hi)
    for ax in axs[-1]:
        ax.set_xlabel('station (Tmax+Tmin)/2 (°C)', color=INK2, fontsize=8)
    for ax in axs[:, 0]:
        ax.set_ylabel('model (°C)', color=INK2, fontsize=8)
    fig.suptitle(f'{year}: daily (Tmax+Tmin)/2, model at station elevation vs station', color=INK, fontsize=11,
                 x=0.01, ha='left')
    fig.tight_layout()
    fig.savefig(vdir / 'scatter_tmid.png', dpi=130, facecolor=SURF)

    pd.set_option('display.width', 200)
    t = S.pivot_table(index=['station', 'elev'], columns=['var', 'version'], values='bias').sort_index(level=1)
    print('\nannual mean bias (model - station, K):')
    print(t.round(2).to_string())
    print('\nRMSE / r of (Tmax+Tmin)/2, station version:')
    print(S[(S['var'] == 'tmid') & (S.version == 'station')][['station', 'elev', 'n', 'bias', 'rmse', 'r']]
          .sort_values('elev').round(3).to_string(index=False))
    if (S['var'] == 'tmean24').any():
        print('\n24-h mean (NOAA hourly) vs (Tmax+Tmin)/2 on the same days, station version:')
        q = S[(S.version == 'station') & S['var'].isin(['tmean24', 'tmid_isd'])]
        print(q.pivot_table(index=['station', 'elev'], columns='var', values=['bias', 'rmse', 'r', 'n'])
              .sort_index(level=1).round(3).to_string())
    print('\nlag test (mean r over stations; lag +1 = model next day):')
    print(L.groupby(['var', 'lag']).r.mean().unstack().round(3).to_string())
    print('\nprecipitation, sum over days with an observation (mm):')
    print(P.assign(ratio=P.model_mm / P.obs_mm).sort_values('elev').round(2).to_string(index=False))
    print(f'\n---> {vdir}')


if __name__ == '__main__':
    main()
