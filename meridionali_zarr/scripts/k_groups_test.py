"""
Mountain-only mask + elevation-band clustering groups: terrain error of the clustering per elevation band,
compared with the current setup (whole rectangle, k = 2000, no groups). Nothing is downscaled.

    python meridionali_zarr/scripts/k_groups_test.py meridionali_zarr/config/config_meridionali_2025.yml
           [--floor 800] [--min-patch-km2 5] [--n-sample 6000000]

Mountain mask: DEM >= floor, patches smaller than min-patch-km2 dropped, enclosed holes (intramontane basins)
filled. Written to <outputs>/k_groups/mask_mountains_<floor>.tif for viewing.

Clustering as Topoclass.extract_topo_cluster_param with clustering_groups: per group its own StandardScaler,
features x weights, MiniBatchKMeans (batch 256 x CPU_cores, seed from the config), n_clusters = k x group weight.
Runs on a random sample of each scenario's mask; k is scaled by n_sample / n_masked (same pixels per cluster
as the real run). Centre values = mean of the member pixels. Metric as k_quick.py: elevation RMSE pixel vs
centre per band, slope and svf RMSE.

Output: <outputs>/k_groups/{results.csv, summary.json, mask_mountains_<floor>.tif}
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from scipy import ndimage
from sklearn import cluster
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow as wf  # noqa: E402

BANDS = [0, 800, 1000, 1500, 2000, 3000]   # reporting bands (--bands to change); not the groups
LAPSE = 6.5e-3   # K/m


def mountain_mask(z, floor, min_px):
    lab, _ = ndimage.label(z >= floor)
    size = np.bincount(lab.ravel())
    size[0] = 0
    keep = size >= min_px
    return ndimage.binary_fill_holes(keep[lab])


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--floor', type=float, default=800)
    p.add_argument('--min-patch-km2', type=float, default=5)
    p.add_argument('--n-sample', type=int, default=6_000_000)
    p.add_argument('--scenarios', nargs='+', help='letters of the scenarios to run, e.g. A E F (default: all)')
    p.add_argument('--bands', type=float, nargs='+', help='reporting elevation bands (edges), default BANDS')
    p.add_argument('--tag', default='', help='suffix of the results files, so earlier results are kept')
    a = p.parse_args()
    global BANDS
    if a.bands:
        BANDS = a.bands
    cfg, out, dem = wf.load_config(a.config)
    ts = cfg['sampling']['toposub']
    features = ts['clustering_features']
    seed = ts['random_seed']
    cores = cfg['project']['parallelization']['setting']['multicore']['CPU_cores']
    res_dir = out / 'k_groups'
    res_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    ds = xr.open_dataset(out / cfg['outputs']['file']['ds_param'])
    ny, nx = ds.sizes['y'], ds.sizes['x']
    z2d = ds.elevation.transpose('y', 'x').values
    with rasterio.open(ts['clustering_mask']) as r:
        rect = r.read(1) == 1
        profile = r.profile
    mtn = mountain_mask(z2d, a.floor, a.min_patch_km2 * 1e6 / 900) & rect
    profile.update(dtype='uint8', nodata=None, compress='deflate')
    with rasterio.open(res_dir / f'mask_mountains_{int(a.floor)}.tif', 'w', **profile) as w:
        w.write(mtn.astype('uint8'), 1)
    print(f'mountain mask >= {a.floor:.0f} m: {mtn.sum():,} px ({mtn.sum() * 9e-4:,.0f} km2), '
          f'rectangle {rect.sum():,} px', flush=True)

    # (name, mask, total k, group edges or None, group weights)
    edges = [a.floor, 1500, 2000]
    edges_rect = [0, a.floor, 1500, 2000]          # whole rectangle, lowlands as their own group
    w_rect = [0.25, 0.375, 0.1875, 0.1875]         # sums to exactly 1 (the fork checks sum == 1)
    scenarios = [
        ('A_rectangle_k2000', rect, 2000, None, None),
        ('B_mountains_k2000', mtn, 2000, None, None),
        ('C_mountains_groups_k2000', mtn, 2000, edges, [0.5, 0.25, 0.25]),
        ('D_mountains_groups_k3000', mtn, 3000, edges, [0.5, 0.25, 0.25]),
        ('G_rectangle_groups_k2000', rect, 2000, edges_rect, w_rect),
        ('E_rectangle_groups_k3000', rect, 3000, edges_rect, w_rect),
        ('F_rectangle_groups_k4000', rect, 4000, edges_rect, w_rect),
        # treeline / 6.5 degC growing-season isotherm zone (~1700-2100 m) kept inside ONE group, no boundary in it
        ('H_rectangle_3groups_1600_k3000', rect, 3000, [0, a.floor, 1600], [0.25, 0.375, 0.375]),
    ]
    if a.scenarios:
        scenarios = [s for s in scenarios if s[0][0] in a.scenarios]
    rng = np.random.default_rng(42)
    rows = []
    for name, m2d, k, gedges, gw in scenarios:
        t1 = time.time()
        valid = np.flatnonzero(m2d.ravel() & np.isfinite(z2d.ravel()))
        pick = np.sort(rng.choice(valid, min(a.n_sample, len(valid)), replace=False))
        frac = len(pick) / len(valid)
        iy, ix = np.divmod(pick, nx)
        cols = {}
        for f in features:
            if f in ('x', 'y'):
                cols[f] = ds[f].values[ix if f == 'x' else iy]
            else:
                cols[f] = ds[f].transpose('y', 'x').values.ravel()[pick]
        df = pd.DataFrame(cols)
        if gedges is None:
            group = np.zeros(len(df), int)
            k_group = {0: k}
        else:
            group = np.digitize(df.elevation.values, gedges[1:])   # 0: floor-1500, 1: 1500-2000, 2: >= 2000
            k_group = {g: int(np.ceil(k * w)) for g, w in enumerate(gw)}
        lab = np.empty(len(df), int)
        offset = 0
        for g, kg in k_group.items():
            sel = group == g
            X = StandardScaler().fit_transform(df.loc[sel, list(features)].values)
            X *= np.array([features[f] for f in features])
            ks = max(2, int(round(kg * frac)))
            lab[sel] = cluster.MiniBatchKMeans(n_clusters=ks, batch_size=256 * cores,
                                               random_state=seed).fit(X).labels_ + offset
            offset += ks
        centre = df.groupby(lab).mean()
        d = df[['elevation', 'slope', 'svf']].values - centre.loc[lab, ['elevation', 'slope', 'svf']].values
        band = pd.cut(df.elevation, BANDS, right=False)
        rec = dict(scenario=name, k=k, n_masked=int(len(valid)), sample_frac=round(frac, 4),
                   fit_s=round(time.time() - t1, 1))
        for b, idx in pd.Series(np.arange(len(df))).groupby(band.values, observed=True):
            i = idx.values
            tag = f'{int(b.left)}-{int(b.right)}'
            rec[f'elev_rmse_m_{tag}'] = float(np.sqrt(np.mean(d[i, 0] ** 2)))
            rec[f'elev_bias_m_{tag}'] = float(np.mean(d[i, 0]))    # pixel - centre: > 0 = centres too low
            rec[f'slope_rmse_deg_{tag}'] = float(np.degrees(np.sqrt(np.mean(d[i, 1] ** 2))))
            rec[f'svf_rmse_{tag}'] = float(np.sqrt(np.mean(d[i, 2] ** 2)))
            # real-k equivalent number of clusters whose centre lies in the band, and pixels per cluster
            n_cl = int(round(((centre.elevation >= b.left) & (centre.elevation < b.right)).sum() / frac))
            rec[f'clusters_{tag}'] = n_cl
            rec[f'km2_per_cluster_{tag}'] = round(len(i) / frac * 9e-4 / max(n_cl, 1), 2)
        rows.append(rec)
        print(name, {c: round(v, 1) for c, v in rec.items() if c.startswith(('elev_rmse', 'clusters'))},
              f'{rec["fit_s"]} s', flush=True)

    res = pd.DataFrame(rows)
    res.to_csv(res_dir / f'results{a.tag}.csv', index=False)
    wf.write_json(res_dir / f'summary{a.tag}.json', dict(floor=a.floor, min_patch_km2=a.min_patch_km2,
                                                 n_mountain_px=int(mtn.sum()), features=features, seed=seed,
                                                 scenarios=[s[0] for s in scenarios],
                                                 results=rows, runtime_s=round(time.time() - t0, 1)))
    print(f'---> {res_dir}  ({time.time() - t0:.0f} s)', flush=True)


if __name__ == '__main__':
    main()
