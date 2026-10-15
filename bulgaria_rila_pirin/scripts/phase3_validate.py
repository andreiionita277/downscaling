"""
Phase 3 validation: Earth Data Hub (5 pressure levels) vs CDS (14 pressure levels), January 2025.

    python bulgaria_rila_pirin/scripts/phase3_validate.py convert   # CDS files -> ERA5.zarr (TopoPyScale layout)
    python bulgaria_rila_pirin/scripts/phase3_validate.py prepare   # run B directory, reusing run A's clusters
    python bulgaria_rila_pirin/scripts/phase3_validate.py report    # all comparisons -> /data/runs/phase3/

Questions answered (numbers in /data/runs/phase3/phase3_report.json and .md):
  1. Are EDH and CDS the same ERA5 data? Raw fields on common grid cells, levels and hours.
  2. Does dropping 9 pressure levels change the downscaled results? Run A (EDH, 600/700/850/925/1000)
     vs run B (CDS, 600-1000 every 25-50 hPa): bias, RMSE, max |diff| of t, q, ws, SW, LW, tp.
  3. Are the differences concentrated in winter valley inversions? Same stats split by elevation
     band and by inversion hours (from the 14-level CDS profile).
  4. How many clusters sit below the 1000 hPa geopotential height, and how often? (Those use
     the surface/lowest-level fallback instead of vertical interpolation.)
  5. Download and downscaling wall times for both sources.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from pyproj import Transformer

CDS_DIR = Path('/data/era5/cds_jan2025')
EDH_ZARR = Path('/data/era5/edh_2025/ERA5.zarr')
RUN_A = Path('/data/runs/jan2025_edh')
RUN_B = Path('/data/runs/jan2025_cds')
OUT = Path('/data/runs/phase3')
START, END = '2025-01-01T00', '2025-01-31T23'
G = 9.81
DOWN_VARS = ['t', 'q', 'ws', 'SW', 'LW', 'tp']
UNITS = {'t': 'K', 'q': 'kg/kg', 'ws': 'm/s', 'SW': 'W/m2', 'LW': 'W/m2', 'tp': 'mm/h'}


# ---------------------------------------------------------------- 1. CDS -> Zarr
def cds_to_topopyscale(ds, kind):
    """CDS (new API) netcdf -> TopoPyScale names, as fetch_era5.remap_CDSbeta does."""
    ren = {'valid_time': 'time'}
    if kind == 'plev':
        ren['pressure_level'] = 'level'
    ds = ds.rename({k: v for k, v in ren.items() if k in ds.variables})
    ds = ds.drop_vars([c for c in ('number', 'expver') if c in ds.variables])
    if kind == 'plev':
        ds = ds.assign_coords(level=ds.level.astype('float64')).sortby('level')
    return ds.sortby('latitude', ascending=False).sortby('longitude')


def convert():
    plev = cds_to_topopyscale(xr.open_mfdataset(sorted((CDS_DIR / 'monthly').glob('mPLEV_*.nc')),
                                                combine='by_coords'), 'plev')
    surf = cds_to_topopyscale(xr.open_dataset(CDS_DIR / 'monthly' / 'mSURF_202501.nc'), 'surf')
    ds = xr.merge([plev, surf.rename({'z': 'z_surf'})], compat='override').sel(time=slice(START, END)).load()
    assert ds.sizes['time'] == 744, ds.sizes
    ds = ds.chunk({'time': 744, 'level': -1, 'latitude': -1, 'longitude': -1})
    for v in ds.variables:
        ds[v].encoding = {}
    ds.to_zarr(CDS_DIR / 'ERA5.zarr', mode='w', zarr_format=3, consolidated=True)
    print('CDS Zarr:', dict(ds.sizes), 'levels', ds.level.values.astype(int).tolist(),
          'lat', ds.latitude.values[[0, -1]], 'lon', ds.longitude.values[[0, -1]])


# ---------------------------------------------------------------- run B setup
def prepare():
    """Run B = run A's config with the CDS data, and the SAME clusters/solar/horizon files."""
    import shutil
    (RUN_B / 'outputs').mkdir(parents=True, exist_ok=True)
    for f in ('ds_param.nc', 'df_centroids.pck', 'ds_solar.nc'):
        shutil.copy2(RUN_A / 'outputs' / f, RUN_B / 'outputs' / f)
    link = RUN_B / 'outputs' / 'da_horizon.nc'
    if not link.exists():
        link.symlink_to((RUN_A / 'outputs' / 'da_horizon.nc').resolve())
    src = Path('/app/bulgaria_rila_pirin/runs/config_jan2025_edh.yml').read_text()
    cfg = (src.replace('/data/runs/jan2025_edh', str(RUN_B))
              .replace('path: /data/era5/edh_2025        # holds ERA5.zarr for all of 2025; the run selects its period',
                       f'path: {CDS_DIR}   # CDS, 14 levels, converted by phase3_validate.py convert')
              .replace('data_repository: edh', 'data_repository: cds')
              .replace('Phase 3 validation run A (EDH levels 600/700/850/925/1000)',
                       'Phase 3 validation run B (CDS levels 600-1000, 14 levels)')
              .replace('name: Rila-Pirin Jan 2025 EDH', 'name: Rila-Pirin Jan 2025 CDS'))
    assert str(CDS_DIR) in cfg and 'data_repository: cds' in cfg
    Path('/app/bulgaria_rila_pirin/runs/config_jan2025_cds.yml').write_text(cfg)
    print('run B prepared:', RUN_B)


# ---------------------------------------------------------------- helpers
def stats(a, b):
    """a - b statistics over all elements (nan-safe)."""
    d = (np.asarray(a, 'float64') - np.asarray(b, 'float64')).ravel()
    d = d[np.isfinite(d)]
    return {'bias': float(d.mean()), 'rmse': float(np.sqrt((d ** 2).mean())), 'max_abs': float(np.abs(d).max()),
            'p99_abs': float(np.percentile(np.abs(d), 99)), 'n': int(d.size)}


def idw_weights(ERA, row, transformer):
    """Same 3x3 inverse-distance weights as the downscalers."""
    ilat = np.abs(ERA.latitude.values - row.lat).argmin()
    ilon = np.abs(ERA.longitude.values - row.lon).argmin()
    lat, lon = ERA.latitude.values[ilat - 1:ilat + 2], ERA.longitude.values[ilon - 1:ilon + 2]
    lons, lats = np.meshgrid(lon, lat)
    X, Y = transformer.transform(lons.ravel(), lats.ravel())
    d = np.sqrt((row.x - X) ** 2 + (row.y - Y) ** 2)
    w = 1 / d ** 2
    return slice(ilat - 1, ilat + 2), slice(ilon - 1, ilon + 2), (w / w.sum()).reshape(3, 3)


# ---------------------------------------------------------------- report
def report():
    OUT.mkdir(parents=True, exist_ok=True)
    rep = {}
    edh = xr.open_zarr(EDH_ZARR).sel(time=slice(START, END))
    cds = xr.open_zarr(CDS_DIR / 'ERA5.zarr')

    # ---- Q1 raw fields
    lat = np.intersect1d(np.round(edh.latitude.values, 4), np.round(cds.latitude.values, 4))
    lon = np.intersect1d(np.round(edh.longitude.values, 4), np.round(cds.longitude.values, 4))
    lev = np.intersect1d(edh.level.values, cds.level.values)
    e = edh.sel(latitude=lat, longitude=lon).load()
    c = cds.sel(latitude=lat, longitude=lon).load()
    raw = {'grid': {'lat': [float(lat.max()), float(lat.min()), int(lat.size)],
                    'lon': [float(lon.min()), float(lon.max()), int(lon.size)], 'common_levels': lev.tolist(),
                    'cds_levels': cds.level.values.tolist(), 'edh_levels': edh.level.values.tolist()}}
    for v in ['z', 't', 'u', 'v', 'q', 'r']:
        raw[v] = stats(e[v].sel(level=lev), c[v].sel(level=lev))
    for v in ['z_surf', 'd2m', 'sp', 'strd', 'ssrd', 'tp', 't2m']:
        raw[v] = stats(e[v], c[v])
    rep['q1_raw_edh_minus_cds'] = raw

    # ---- Q2/Q3 downscaled
    df = pd.read_pickle(RUN_A / 'outputs' / 'df_centroids.pck')
    A = xr.open_zarr(RUN_A / 'outputs' / 'downscaled' / 'down.zarr').load()
    B = xr.open_zarr(RUN_B / 'outputs' / 'downscaled' / 'down.zarr').load()
    assert np.array_equal(A.point_ind.values, B.point_ind.values)
    elev = df.set_index('point_ind').loc[A.point_ind.values, 'elevation'].values
    down = {v: stats(A[v], B[v]) for v in DOWN_VARS}
    down['_units'] = UNITS
    rep['q2_downscaled_edh_minus_cds_all'] = down

    bands = [(0, 1000), (1000, 1500), (1500, 2000), (2000, 3000)]
    rep['q3_by_elevation_band'] = {}
    for lo, hi in bands:
        m = (elev >= lo) & (elev < hi)
        if m.sum():
            rep['q3_by_elevation_band'][f'{lo}-{hi} m'] = {'n_points': int(m.sum()),
                                                            **{v: stats(A[v][m], B[v][m]) for v in ('t', 'q', 'LW')}}

    # inversion hours from the CDS 14-level profile averaged over the AOI cells:
    # an hour is an "inversion hour" if T increases with height anywhere between 1000 and 700 hPa
    prof = c.t.sel(level=slice(700, 1000)).mean(['latitude', 'longitude'])      # (time, level asc)
    zprof = (c.z.sel(level=slice(700, 1000)) / G).mean(['latitude', 'longitude'])
    dT = prof.diff('level')                    # T(level_i) - T(level_{i-1}); level ascending = downward
    inv_strength = (-dT).max('level')          # >0 means warmer above than below
    inv = (inv_strength > 0).values
    rep['q3_inversions'] = {'definition': 'hour with T increasing with height between 1000 and 700 hPa in the '
                                          'AOI-mean CDS 14-level profile',
                            'n_hours': int(inv.sum()), 'fraction_of_hours': float(inv.mean()),
                            'max_strength_K_per_layer': float(inv_strength.max())}
    for lo, hi in bands[:2] + [(1500, 3000)]:
        m = (elev >= lo) & (elev < hi)
        if m.sum():
            rep['q3_inversions'][f't_{lo}-{hi}m'] = {
                'inversion_hours': stats(A.t[m][:, inv], B.t[m][:, inv]),
                'other_hours': stats(A.t[m][:, ~inv], B.t[m][:, ~inv])}
    # worst point
    rmse_pt = np.sqrt(((A.t - B.t) ** 2).mean('time')).values
    k = int(np.argmax(rmse_pt))
    rep['q3_worst_point_t'] = {'point_ind': int(A.point_ind.values[k]), 'elevation_m': float(elev[k]),
                               'rmse_K': float(rmse_pt[k]),
                               'max_abs_K': float(np.abs(A.t[k] - B.t[k]).max())}

    # ---- Q4 points below z(1000 hPa)
    tr = Transformer.from_crs('epsg:4326', 'epsg:32634', always_xy=True)
    q4 = {}
    for name, src in (('edh', edh), ('cds', cds)):
        z1000 = (src.z.sel(level=1000) / G).load()
        below_frac = []
        for row in df.itertuples():
            sl, so, w = idw_weights(src, row, tr)
            zp = (z1000.isel(latitude=sl, longitude=so) * w).sum(['latitude', 'longitude']).values
            below_frac.append(float((row.elevation < zp).mean()))
        below_frac = np.array(below_frac)
        q4[name] = {'n_points_ever_below': int((below_frac > 0).sum()),
                    'n_points_always_below': int((below_frac == 1).sum()),
                    'mean_fraction_of_hours_for_those': float(below_frac[below_frac > 0].mean()) if (below_frac > 0).any() else 0.0,
                    'lowest_point_m': float(df.elevation.min())}
    q4['note'] = ('The downscalers take the surface/lowest-level fallback for a point if its elevation is below '
                  'z(1000 hPa) at ANY hour of the run period (classic topo_scale behaviour).')
    # which points, and how different are they between A and B
    low_pts = [int(p) for p, f in zip(df.point_ind, below_frac) if f > 0]
    sel = xr.DataArray(np.isin(A.point_ind.values, low_pts), dims='point_ind')
    q4['points_ever_below_edh'] = low_pts
    q4['t_edh_minus_cds_at_these_points'] = stats(A.t.where(sel), B.t.where(sel))
    rep['q4_below_1000hPa'] = q4

    # sunset/sunrise SW spikes (numerical sensitivity of the SW scheme when mu0 -> 0)
    dsw = np.abs(A.SW - B.SW)
    rep['sw_spikes'] = {'point_hours_abs_diff_gt_50Wm2': int((dsw > 50).sum()),
                        'total_point_hours': int(dsw.size),
                        'max_abs_diff_Wm2': float(dsw.max())}

    # ---- Q5 timings
    q5 = {}
    # the full-year download itself (fetch.log of /data/era5/edh_2025); later runs reuse the blocks
    flog = (EDH_ZARR.parent / 'fetch.log').read_text()
    m = re.search(r'Done\. Download (\d+)s, assembly (\d+)s, ~(\d+) chunk requests', flog)
    q5['edh_fetch_2025_full_year_s'] = int(m.group(1)) + int(m.group(2))
    q5['edh_requests_2025'] = int(m.group(3))
    log = (CDS_DIR / 'fetch.log').read_text()
    ts = re.findall(r'^(\d\d:\d\d:\d\d) ', log, flags=re.M)
    if ts:
        t0, t1 = pd.Timestamp(f'2026-01-01 {ts[0]}'), pd.Timestamp(f'2026-01-01 {ts[-1]}')
        q5['cds_fetch_jan2025_only_s'] = float((t1 - t0).total_seconds() % 86400)
    for name, run in (('run_a_edh', RUN_A), ('run_b_cds', RUN_B)):
        f = run / 'outputs' / 'timings.json'
        if f.exists():
            q5[name] = json.loads(f.read_text())[-1]
    rep['q5_timings'] = q5

    (OUT / 'phase3_report.json').write_text(json.dumps(rep, indent=2))
    figure(A, B, elev, inv, low_pts)
    print(json.dumps(rep, indent=2))


def figure(A, B, elev, inv, low_pts):
    """Per-cluster temperature difference EDH - CDS vs elevation, all hours vs inversion hours."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    BLUE, ORANGE, INK, INK2, GRID = '#2a78d6', '#eb6834', '#1f1f1e', '#5f5e58', '#e4e3dc'
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.titlesize': 10,
                         'axes.titleweight': 'bold', 'axes.titlelocation': 'left', 'savefig.dpi': 300})
    d = (A.t - B.t)
    low = np.isin(A.point_ind.values, low_pts)
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.3), constrained_layout=True, sharey=True)
    for ax, (title, sub) in zip(axes, [('a  All hours', d), ('b  Inversion hours only', d[:, inv])]):
        bias = sub.mean('time').values
        ax.axhline(0, color=INK2, lw=0.8)
        ax.scatter(elev[~low], bias[~low], s=10, color=BLUE, alpha=0.7, lw=0, label='vertical interpolation')
        ax.scatter(elev[low], bias[low], s=18, color=ORANGE, marker='s', lw=0.6, edgecolor='white',
                   label='below 1000 hPa at some hours (fallback)')
        ax.set_title(title)
        ax.set_xlabel('Cluster elevation (m)')
        ax.spines[['top', 'right']].set_visible(False)
        ax.grid(axis='y', color=GRID, lw=0.8); ax.set_axisbelow(True)
    axes[0].set_ylabel('Mean T difference, EDH 5 levels - CDS 14 levels (K)')
    axes[0].legend(frameon=False, fontsize=7.5, loc='upper right')
    fig.text(0, -0.03, f'January 2025, {len(elev)} TopoSUB clusters (k=500, elevation weight 4). Inversion hours: '
             f'{int(inv.sum())} of {inv.size} (AOI-mean CDS profile warmer aloft between 1000 and 700 hPa).',
             fontsize=7.5, color=INK2)
    for ext in ('png', 'pdf'):
        fig.savefig(OUT / f'fig_phase3_t_diff_vs_elevation.{ext}', bbox_inches='tight')
    plt.close(fig)


if __name__ == '__main__':
    {'convert': convert, 'prepare': prepare, 'report': report}[sys.argv[1]]()
