"""Generates view_results.ipynb (kept as a script so the notebook's code is reviewable in git).

    ../../../venv-viewer/bin/python make_viewer_notebook.py
"""
from pathlib import Path

import nbformat as nbf

md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell
cells = [
    md("""# Rila–Pirin 2025: results viewer

Reads the workflow's Zarr stores directly (nothing is copied):

| store | content | dims |
|---|---|---|
| `data/outputs/downscaled/down.zarr` | hourly downscaled climate (t, q, p, u, v, ws, wd, tp, SW, LW, …) | cluster × hour |
| `data/outputs/downscaled/daily.zarr` | daily statistics (UTC days): `t_mean/min/max`, `tp_sum`, `SW_mean`, … | cluster × day |
| `data/outputs/ds_param.zarr` | terrain + **cluster map** (which cluster each 25 m pixel belongs to) | y × x |
| `data/outputs/geotiff/*.tif` | daily maps at 25 m, one file per statistic and month, **one band per day** | (band = day) × y × x |

A *map* is made by giving each pixel the value of its cluster (exactly what the GeoTIFFs and TopoPyScale's
`topo_plot.map_variable` do). Maps here are drawn at **100 m** (every 4th pixel) to stay fast; the
GeoTIFFs are the full 25 m product.

**Start:** from the `zarr_workflow` folder on the host:
`../../../venv-viewer/bin/jupyter lab notebooks/view_results.ipynb`  (a browser tab opens). Run all cells.
The viewer works while the run is still going: months not yet computed are simply absent."""),
    code("""%matplotlib inline
import json
from pathlib import Path

import ipywidgets as W
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr

DATA = Path('../data')
OUT = DATA / 'outputs'
STEP = 4                                     # 100 m preview grid (every 4th 25 m pixel)

hourly = xr.open_zarr(OUT / 'downscaled' / 'down.zarr').swap_dims(point_ind='point_name')
daily = xr.open_zarr(OUT / 'downscaled' / 'daily.zarr')
cent = pd.read_pickle(OUT / 'df_centroids.pck').set_index('point_name')
param = xr.open_zarr(OUT / 'ds_param.zarr')
stations = pd.read_csv(DATA / 'dem' / 'station_list.csv')

pn = param.point_name.transpose('y', 'x')[::STEP, ::STEP].values.astype(str)
elev = param.elevation.transpose('y', 'x')[::STEP, ::STEP].values
X, Y = param.x.values[::STEP], param.y.values[::STEP]
names = daily.point_name.values.astype(str)
pix = pd.Index(names).get_indexer(pn.ravel()).reshape(pn.shape)
inside = pix >= 0
extent = [X.min() / 1e3, X.max() / 1e3, Y.min() / 1e3, Y.max() / 1e3]   # km, EPSG:32634

t_ok = daily.t_mean.notnull().any('point_name').values
days = pd.DatetimeIndex(daily.time.values)[t_ok]
print(f'{len(names)} clusters, {len(days)} days with results '
      f'({days.min().date() if len(days) else "-"} .. {days.max().date() if len(days) else "-"}), '
      f'{hourly.sizes["time"]} hours in the hourly store')


def to_map(values):
    \"\"\"Per-cluster values (ordered like `names`) -> 100 m field (NaN outside the mask).\"\"\"
    return np.where(inside, np.asarray(values)[np.where(inside, pix, 0)], np.nan)"""),
    md("## 1. Run status (one row per month, from `outputs/months/*.json`)"),
    code("""rows = []
for f in sorted((OUT / 'months').glob('*.json')):
    r = json.loads(f.read_text())
    qa = r.get('qa_hourly', {})
    chk = r.get('geotiff_checks', {})
    rows.append({'month': r['month'], 'status': r['status'],
                 'minutes': round(sum(r.get('timings_s', {}).values()) / 60, 1),
                 'hourly t min (°C)': round(qa['t']['min'] - 273.15, 1) if 't' in qa else None,
                 'hourly t max (°C)': round(qa['t']['max'] - 273.15, 1) if 't' in qa else None,
                 'SW spikes > bound': qa.get('SW', {}).get('n_outside_bounds'),
                 'GeoTIFFs checked': f"{sum(v['ok'] for v in chk.values())}/{len(chk)}"})
pd.DataFrame(rows).set_index('month')"""),
    md("""## 2. Daily map explorer
Pick a variable and a day. *Same colour scale all year* keeps colours comparable between days."""),
    code("""VARS = {'Daily mean temperature (°C)': ('t_mean', -273.15, 'RdYlBu_r'),
        'Daily minimum temperature (°C)': ('t_min', -273.15, 'RdYlBu_r'),
        'Daily maximum temperature (°C)': ('t_max', -273.15, 'RdYlBu_r'),
        'Precipitation (mm/day)': ('tp_sum', 0, 'Blues'),
        'Shortwave radiation, daily mean (W/m²)': ('SW_mean', 0, 'magma'),
        'Longwave radiation, daily mean (W/m²)': ('LW_mean', 0, 'inferno'),
        'Wind speed, daily mean (m/s)': ('ws_mean', 0, 'viridis'),
        'Specific humidity, daily mean (g/kg)': ('q_mean', 0, 'GnBu')}
VARS = {k: v for k, v in VARS.items() if v[0] in daily}
yr_range = {v[0]: (float(daily[v[0]].min()), float(daily[v[0]].max())) for v in VARS.values()}


def show_day(var, day, fixed_scale):
    name, off, cmap = VARS[var]
    vals = daily[name].sel(time=day).values + off
    if name == 'q_mean':
        vals = vals * 1e3
    field = to_map(vals)
    if fixed_scale:
        lo, hi = (np.array(yr_range[name]) + off) * (1e3 if name == 'q_mean' else 1)
    else:
        lo, hi = np.nanpercentile(field, [1, 99])
    fig, ax = plt.subplots(figsize=(9, 7.5))
    im = ax.imshow(field, extent=extent, cmap=cmap, vmin=lo, vmax=hi, interpolation='nearest')
    ax.contour(X / 1e3, Y / 1e3, np.where(np.isnan(elev), 0, elev), levels=[1500, 2500], colors='k',
               linewidths=0.4, alpha=0.5)
    ax.scatter(stations.x / 1e3, stations.y / 1e3, s=12, c='k', marker='^', label='stations')
    plt.colorbar(im, ax=ax, shrink=0.8, label=var)
    ax.set_title(f'{var}, {pd.Timestamp(day).date()}  (contours: 1500 and 2500 m)')
    ax.set_xlabel('x (km, UTM 34N)'); ax.set_ylabel('y (km)')
    print(f'inside mask: min {np.nanmin(field):.2f}, mean {np.nanmean(field):.2f}, max {np.nanmax(field):.2f}')
    plt.show()


if len(days):
    W.interact(show_day, var=W.Dropdown(options=list(VARS), description='variable', layout=W.Layout(width='420px')),
               day=W.SelectionSlider(options=[(d.strftime('%Y-%m-%d'), d) for d in days], description='day',
                                     continuous_update=False, layout=W.Layout(width='700px')),
               fixed_scale=W.Checkbox(value=True, description='same colour scale all year'))"""),
    md("""## 3. Time series at a station (or any cluster)
The station's pixel belongs to one cluster; the series shown is that cluster's. The table shows how far the
cluster centre's elevation is from the station's (the main source of clustering error)."""),
    code("""def cluster_at(x, y):
    return str(param.point_name.sel(x=x, y=y, method='nearest').values)


stations['cluster'] = [cluster_at(x, y) for x, y in zip(stations.x, stations.y)]
stations['cluster_elev_m'] = cent.loc[stations.cluster, 'elevation'].values.round(0)
stations['elev_diff_m'] = (stations.cluster_elev_m - stations.elevation_m).round(0)
stations[['Name', 'elevation_m', 'cluster', 'cluster_elev_m', 'elev_diff_m']]"""),
    code("""months = sorted({d.strftime('%Y-%m') for d in days})


def show_series(station, month):
    pt = stations.set_index('Name').loc[station, 'cluster']
    d = daily.sel(point_name=pt).sel(time=days)
    m0 = pd.Timestamp(month + '-01')
    h = hourly.t.sel(point_name=pt, time=slice(m0, m0 + pd.offsets.MonthEnd(0) + pd.Timedelta('23h'))) - 273.15
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 7))
    a1.fill_between(d.time.values, d.t_min - 273.15, d.t_max - 273.15, color='tab:red', alpha=0.25, label='daily min-max')
    a1.plot(d.time.values, d.t_mean - 273.15, color='tab:red', lw=1, label='daily mean')
    a1.axvspan(m0, m0 + pd.offsets.MonthEnd(0), color='grey', alpha=0.15)
    a1.set_ylabel('t (°C)'); a1.legend(loc='upper left'); a1.grid(alpha=0.3)
    a1.set_title(f'{station}: cluster {pt}, centre at {cent.loc[pt, "elevation"]:.0f} m')
    a2.plot(h.time.values, h.values, lw=0.8, color='tab:blue', label='hourly t')
    if 'tp' in hourly:
        a3 = a2.twinx()
        a3.bar(h.time.values, hourly.tp.sel(point_name=pt, time=h.time).values, width=1 / 24, color='tab:green',
               alpha=0.5, label='tp (mm/h)')
        a3.set_ylabel('tp (mm/h)', color='tab:green')
    a2.set_ylabel('t (°C)'); a2.set_title(f'hourly, {month}'); a2.grid(alpha=0.3)
    plt.tight_layout(); plt.show()


if len(days):
    W.interact(show_series, station=W.Dropdown(options=list(stations.Name), description='station'),
               month=W.Dropdown(options=months, value=months[0], description='month'))"""),
    md("## 4. Monthly mean temperature (mean of the daily means)"),
    code("""tm = daily.t_mean.sel(time=days).groupby('time.month').mean().load() - 273.15
n = tm.sizes['month']
if n:
    lo, hi = float(tm.min()), float(tm.max())
    cols = min(4, n)
    fig, axs = plt.subplots(int(np.ceil(n / cols)), cols, figsize=(4 * cols, 3.6 * np.ceil(n / cols)), squeeze=False)
    for ax, m in zip(axs.ravel(), tm.month.values):
        im = ax.imshow(to_map(tm.sel(month=m).values), extent=extent, cmap='RdYlBu_r', vmin=lo, vmax=hi)
        ax.set_title(pd.Timestamp(2025, int(m), 1).strftime('%B')); ax.set_xticks([]); ax.set_yticks([])
    for ax in axs.ravel()[n:]:
        ax.axis('off')
    fig.colorbar(im, ax=axs, shrink=0.6, label='°C'); plt.show()"""),
    md("""## 5. Temperature vs elevation (one dot per cluster)
The slope is the effective lapse rate of the downscaled climate. Coloured by aspect, to show the terrain effect."""),
    code("""tmean = (daily.t_mean.sel(time=days).mean('time').load() - 273.15).to_series()
c = cent.loc[tmean.index]
fit = np.polyfit(c.elevation, tmean.values, 1)
fig, ax = plt.subplots(figsize=(8, 5))
sc = ax.scatter(c.elevation, tmean, c=np.degrees(c.aspect) if 'aspect' in c else None, cmap='twilight', s=10)
xx = np.array([c.elevation.min(), c.elevation.max()])
ax.plot(xx, np.polyval(fit, xx), 'k--', label=f'fit: {fit[0] * 1000:.2f} K/km')
ax.set_xlabel('cluster centre elevation (m)'); ax.set_ylabel(f'mean t over {len(days)} days (°C)')
if 'aspect' in c:
    plt.colorbar(sc, label='aspect (°)')
ax.legend(); ax.grid(alpha=0.3); plt.show()"""),
    md("""## 6. Cross-check: the GeoTIFF of a day equals the map from the Zarr store
Reads one band of a GeoTIFF (25 m) and compares it with the cluster values (difference should be ~1e-6, float32)."""),
    code("""import rasterio

tifs = sorted((OUT / 'geotiff').glob('t_mean_daily_*.tif'))
if tifs:
    f = tifs[0]
    with rasterio.open(f) as r:
        band1 = r.read(1)
        day = pd.Timestamp(r.descriptions[0])
        print(f'{f.name}: {r.count} bands (days), {r.width} x {r.height} px, {r.crs}, band 1 = {day.date()}')
    full_pn = param.point_name.transpose('y', 'x').values.astype(str)
    ii = pd.Index(names).get_indexer(full_pn.ravel()).reshape(full_pn.shape)
    vals = daily.t_mean.sel(time=day).values - 273.15
    ref = np.where(ii >= 0, vals[np.where(ii >= 0, ii, 0)], -9999).astype('float32')
    print('max |GeoTIFF - Zarr| =', float(np.abs(band1 - ref).max()))"""),
]
nb = nbf.v4.new_notebook(cells=cells, metadata={'kernelspec': {'name': 'python3', 'display_name': 'Python 3'}})
nbf.write(nb, Path(__file__).with_name('view_results.ipynb'))
print('written view_results.ipynb')
