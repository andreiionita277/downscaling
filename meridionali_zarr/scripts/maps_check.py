"""
Lapse-rate effect and sanity checks of the temperature maps of one downscaled year, computed from daily.zarr (the
adjustment is linear, so a period mean of the lapse-adjusted map = mean cluster value + mean Gamma * (z_p - z_k);
no need to read the daily GeoTIFFs). Same Gamma as lapse_geotiff_year.py (k=50 nearest clusters, clip 1.5 K/100 m).

    python meridionali_zarr/scripts/maps_check.py meridionali_zarr/config/config_meridionali_E_2025.yml
           [--dest /mnt/d/Downscaling/meridionali_zarr/meridionali_E_2025/geotiff_lapse/summary]

Checks:
  1. lapse effect on the annual mean of t_mean: adjusted - cluster value per pixel (distribution, map), and the steps
     at the clustering-group boundaries (800/1500/2000 m): mean T in the 25 m slices just below/above, both versions
  2. annual / DJF / JJA means, inversion days per cluster (Gamma > 0), range checks, coldest/warmest days
Output: <outputs>/validation/maps_*.png, maps_check.json; GeoTIFFs (annual/DJF/JJA mean t_mean adjusted, lapse
effect, inversion days) to --dest when given (tiled, DEFLATE, AVERAGE overviews).
"""
import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
import xarray as xr  # noqa: E402
from rasterio.enums import Resampling  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow as wf  # noqa: E402
from lapse_map_test import local_lapse  # noqa: E402

NODATA = -9999.0
INK, INK2, GRID, SURF = '#0b0b0b', '#52514e', '#e4e3df', '#fcfcfb'
SEQ = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b']      # blue ramp 100..700
DIV = ['#2a78d6', '#f0efec', '#e34948']                                                  # blue - gray - red
BLOCK = 8                                                                                 # 30 m -> 240 m for PNGs


def block_mean(a, b=BLOCK):
    """Area mean over b x b blocks, NaN-aware (display only; never nearest for continuous data)."""
    h, w = a.shape[0] // b * b, a.shape[1] // b * b
    v = a[:h, :w].reshape(h // b, b, w // b, b)
    with np.errstate(invalid='ignore'):
        return np.nanmean(v, axis=(1, 3))


def write_tif(path, arr, prof, desc):
    p = prof | dict(driver='GTiff', dtype='float32', count=1, nodata=NODATA, compress='deflate', predictor=3,
                    tiled=True, blockxsize=512, blockysize=512, BIGTIFF='IF_SAFER')
    a = np.where(np.isfinite(arr), arr, NODATA).astype('float32')
    with rasterio.open(path, 'w', **p) as w:
        w.write(a, 1)
        w.set_band_description(1, desc)
        w.build_overviews([2, 4, 8, 16, 32], Resampling.average)


def show(ax, arr, cmap, vmin, vmax, title, label):
    im = ax.imshow(block_mean(arr), cmap=cmap, vmin=vmin, vmax=vmax, interpolation='nearest')
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title(title, color=INK, fontsize=9, loc='left')
    cb = plt.colorbar(im, ax=ax, shrink=0.75, pad=0.01)
    cb.set_label(label, color=INK2, fontsize=7)
    cb.ax.tick_params(labelsize=7, colors=INK2)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--dest')
    p.add_argument('--k', type=int, default=50)
    p.add_argument('--clip', type=float, default=0.015)
    a = p.parse_args()
    cfg, out, dem = wf.load_config(a.config)
    year = int(pd.Timestamp(cfg['project']['start']).year)
    vdir = out / 'validation'
    vdir.mkdir(exist_ok=True)

    c = pd.read_pickle(out / cfg['outputs']['file']['df_centroids'])
    ds = xr.open_dataset(out / cfg['outputs']['file']['ds_param'])
    z = ds.elevation.transpose('y', 'x').values.astype('float32')
    pi = ds.point_ind.transpose('y', 'x').values.ravel()
    ok = pi >= 0
    pos = pd.Series(np.arange(len(c)), index=c.point_ind.values).reindex(pi[ok]).values.astype('int32')
    zk = c.elevation.values
    dz = z.ravel()[ok] - zk[pos]
    npx = np.bincount(pos, minlength=len(c))

    daily = xr.open_zarr(out / 'downscaled' / 'daily.zarr')
    days = pd.DatetimeIndex(daily.time.values)
    v = daily['t_mean'].load().sel(point_name=c.point_name.values).transpose('time', 'point_name').values \
        - wf.OFFSET['t']
    G = np.stack([local_lapse(c.x.values, c.y.values, zk, v[i], a.k, a.clip) for i in range(len(days))])
    print(f'{year}: {len(days)} days, {len(c)} clusters, {ok.sum()} pixels', flush=True)

    def pixel_map(vals):
        m = np.full(z.size, np.nan, 'float32')
        m[ok] = vals
        return m.reshape(z.shape)

    seasons = {'annual': np.ones(len(days), bool), 'DJF': days.month.isin([12, 1, 2]),
               'JJA': days.month.isin([6, 7, 8])}
    maps, res = {}, {'year': year}
    for sname, sel in seasons.items():
        tk, gk = v[sel].mean(0), G[sel].mean(0)
        maps[f'{sname}_adj'] = pixel_map(tk[pos] + gk[pos] * dz)
        maps[f'{sname}_cluster'] = pixel_map(tk[pos])
    eff = maps['annual_adj'] - maps['annual_cluster']
    e = eff[np.isfinite(eff)]
    res['lapse_effect_annual_K'] = dict(mean=round(float(e.mean()), 4), abs_p50=round(float(np.median(np.abs(e))), 3),
                                        abs_p95=round(float(np.percentile(np.abs(e), 95)), 3),
                                        abs_max=round(float(np.abs(e).max()), 3),
                                        area_mean_adj=round(float(np.nanmean(maps['annual_adj'])), 4),
                                        area_mean_cluster=round(float(np.average(v.mean(0), weights=npx)), 4))

    # group-boundary steps (annual mean): mean T in the 25 m slices just below and just above each boundary
    zz = z.ravel()[ok]
    steps = {}
    for b in (800, 1500, 2000):
        lo, hi = (zz >= b - 25) & (zz < b), (zz >= b) & (zz < b + 25)
        row = {}
        for ver in ('cluster', 'adj'):
            m = maps[f'annual_{ver}'].ravel()[ok]
            row[ver] = round(float(m[lo].mean() - m[hi].mean()), 3)
        # gradient from the 25 m slice means over +-200 m (a fit over millions of pixels is ill-conditioned)
        edges = np.arange(b - 200, b + 201, 25)
        idx = np.digitize(zz, edges) - 1
        g = (idx >= 0) & (idx < len(edges) - 1)
        m = maps['annual_adj'].ravel()[ok].astype('float64')
        means = np.bincount(idx[g], m[g], len(edges) - 1) / np.bincount(idx[g], minlength=len(edges) - 1)
        slope = np.polyfit((edges[:-1] + edges[1:]) / 2, means, 1)[0]
        row['expected_from_gradient'] = round(float(-slope * 25), 3)
        steps[f'{b} m'] = row
    res['boundary_step_K (below minus above, 25 m slices)'] = steps

    # inversions, ranges, extreme days
    inv = (G > 0).sum(0)
    res['inversion_days_per_cluster'] = dict(min=int(inv.min()), median=int(np.median(inv)), max=int(inv.max()),
                                            area_weighted_mean=round(float(np.average(inv, weights=npx)), 1))
    lapse_med = np.median(G, axis=1) * 100
    res['daily_median_lapse_K_per_100m'] = dict(p5=round(float(np.percentile(lapse_med, 5)), 3),
                                               p50=round(float(np.median(lapse_med)), 3),
                                               p95=round(float(np.percentile(lapse_med, 95)), 3),
                                               days_with_median_inversion=int((lapse_med > 0).sum()))
    res['clipped_fraction'] = round(float((np.abs(G) >= a.clip).mean()), 5)
    am = np.average(v, axis=1, weights=npx)
    res['area_mean_t_mean_degC'] = dict(coldest=[str(days[am.argmin()].date()), round(float(am.min()), 2)],
                                        warmest=[str(days[am.argmax()].date()), round(float(am.max()), 2)])
    for k, m in maps.items():
        if k.endswith('_adj'):
            res[f'range_{k}_degC'] = [round(float(np.nanmin(m)), 2), round(float(np.nanmax(m)), 2)]
    res['nan_in_mask'] = int(sum(np.isnan(maps[k].ravel()[ok]).sum() for k in maps))
    (vdir / 'maps_check.json').write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1), flush=True)

    # figures
    seq = matplotlib.colors.LinearSegmentedColormap.from_list('seq', SEQ[::-1])     # dark = cold
    div = matplotlib.colors.LinearSegmentedColormap.from_list('div', DIV)
    fig, axs = plt.subplots(2, 2, figsize=(14, 8.2), facecolor=SURF)
    lo_, hi_ = np.nanpercentile(maps['annual_adj'], [0.5, 99.5])
    show(axs[0, 0], maps['annual_adj'], seq, lo_, hi_, f'{year} annual mean t_mean, lapse-adjusted', '°C')
    lim = max(0.2, float(np.nanpercentile(np.abs(eff), 99)))
    show(axs[0, 1], eff, div, -lim, lim, 'lapse effect on the annual mean (adjusted - cluster value)',
         '°C; blue = adjusted colder')
    d1, d2 = np.nanpercentile(maps['DJF_adj'], [0.5, 99.5]), np.nanpercentile(maps['JJA_adj'], [0.5, 99.5])
    show(axs[1, 0], maps['DJF_adj'], seq, d1[0], d1[1], 'DJF mean t_mean (Jan, Feb, Dec of the year)', '°C')
    show(axs[1, 1], maps['JJA_adj'], seq, d2[0], d2[1], 'JJA mean t_mean', '°C')
    fig.tight_layout()
    fig.savefig(vdir / 'maps_annual_seasonal.png', dpi=130, facecolor=SURF)

    fig, axs = plt.subplots(1, 2, figsize=(14, 4.4), facecolor=SURF, gridspec_kw=dict(width_ratios=[1.6, 1]))
    invmap = pixel_map(inv[pos].astype('float32'))
    show(axs[0], invmap, seq.reversed(), 0, float(np.nanpercentile(invmap, 99.5)),
         f'{year}: days with a local inversion (lapse rate > 0, t_mean)', 'days')
    ax = axs[1]
    ax.set_facecolor(SURF)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(True, color=GRID, lw=0.6)
    ax.axhline(0, color=INK2, lw=1)
    ax.plot(days, lapse_med, color=SEQ[3], lw=1.5)
    ax.fill_between(days, np.percentile(G, 10, axis=1) * 100, np.percentile(G, 90, axis=1) * 100, color=SEQ[1],
                    alpha=0.6, lw=0)
    ax.set_ylabel('lapse rate (K / 100 m)', color=INK2, fontsize=8)
    ax.set_title('daily local lapse rate: median over clusters (line), 10-90 % (band)', color=INK, fontsize=9,
                 loc='left')
    fig.tight_layout()
    fig.savefig(vdir / 'maps_inversions.png', dpi=130, facecolor=SURF)

    # steps figure: annual mean vs pixel elevation around each boundary, both versions
    fig, axs = plt.subplots(1, 3, figsize=(13, 3.8), facecolor=SURF)
    for ax, b in zip(axs, (800, 1500, 2000)):
        ax.set_facecolor(SURF)
        for s in ('top', 'right'):
            ax.spines[s].set_visible(False)
        ax.tick_params(colors=INK2, labelsize=8)
        ax.grid(True, color=GRID, lw=0.6)
        edges = np.arange(b - 200, b + 201, 25)
        mid = (edges[:-1] + edges[1:]) / 2
        idx = np.digitize(zz, edges) - 1
        g = (idx >= 0) & (idx < len(mid))
        for ver, col, lab in (('cluster', '#2a78d6', 'cluster value'), ('adj', '#eb6834', 'lapse-adjusted')):
            m = maps[f'annual_{ver}'].ravel()[ok]
            ax.plot(mid, np.bincount(idx[g], m[g], len(mid)) / np.bincount(idx[g], minlength=len(mid)), 'o-',
                    color=col, ms=4, lw=1.5, label=lab)
        ax.axvline(b, color=INK2, lw=1, ls='--')
        ax.set_title(f'group boundary {b} m', color=INK, fontsize=9, loc='left')
        ax.set_xlabel('pixel elevation, 25 m slices (m)', color=INK2, fontsize=8)
    axs[0].set_ylabel(f'{year} annual mean t_mean (°C)', color=INK2, fontsize=8)
    axs[0].legend(frameon=False, fontsize=8, labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(vdir / 'maps_boundary_steps.png', dpi=130, facecolor=SURF)

    if a.dest:
        dest = Path(a.dest)
        dest.mkdir(parents=True, exist_ok=True)
        with rasterio.open(dem) as r:
            prof = r.profile
        for k, desc in (('annual_adj', f'{year} annual mean t_mean, lapse-adjusted (degC)'),
                        ('DJF_adj', f'{year} DJF mean t_mean (Jan, Feb, Dec), lapse-adjusted (degC)'),
                        ('JJA_adj', f'{year} JJA mean t_mean, lapse-adjusted (degC)')):
            write_tif(dest / f't_mean_{k}_{year}.tif', maps[k], prof, desc)
        write_tif(dest / f't_mean_lapse_effect_annual_{year}.tif', eff, prof,
                  f'{year} annual mean t_mean: lapse-adjusted minus cluster value (K)')
        write_tif(dest / f'inversion_days_{year}.tif', invmap, prof, f'{year} days with local lapse rate > 0 (t_mean)')
        print(f'---> GeoTIFFs in {dest}', flush=True)
    print(f'---> {vdir}', flush=True)


if __name__ == '__main__':
    main()
