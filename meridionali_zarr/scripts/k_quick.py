"""
Quick choice of k (number of TopoSUB clusters): terrain error of the clustering, per elevation band.

    python meridionali_zarr/scripts/k_quick.py meridionali_zarr/config/config_meridionali_2025.yml
           [--ks 500 1000 2000 3000 4000] [--n-sample 10000000]

Each pixel receives the climate downscaled at its cluster centre, so the clustering error is, to first
order, the difference between the pixel's terrain and its centre's terrain. For temperature, the
elevation difference x lapse rate dominates (~6.5 K/km: 100 m ~ 0.65 K). This is the metric used for the
Rila-Pirin choice (PROGRESS.md: elevation RMSE vs k, diminishing returns).

Clustering exactly as Topoclass.extract_topo_cluster_param: pixels inside the mask, StandardScaler, features
multiplied by their weights, MiniBatchKMeans (batch 256 x CPU_cores, seed from the config). Centre
values = mean of the member pixels (= the inverse-scaled centres TopoPyScale uses).
It runs on a random sample of the masked pixels instead of all 46 M. The density of clusters per pixel
is kept: k is scaled by n_sample / n_masked, and the result is reported for the real k.

Output: <outputs>/k_quick/{results.csv, summary.json, k_quick.png}
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from sklearn import cluster
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow as wf  # noqa: E402

BANDS = [0, 500, 1000, 1500, 2000, 3000]
LAPSE = 6.5e-3   # K/m, only to translate elevation error into an approximate temperature error


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--ks', type=int, nargs='+', default=[500, 1000, 2000, 3000, 4000])
    p.add_argument('--n-sample', type=int, default=10_000_000)
    p.add_argument('--ds-param', help='terrain file to read (default: the config\'s ds_param). Use a copy\n'
                   'while the main run is still writing ds_param.nc (it rewrites it after clustering)')
    a = p.parse_args()
    cfg, out, dem = wf.load_config(a.config)
    ts = cfg['sampling']['toposub']
    features = ts['clustering_features']
    seed = ts['random_seed']
    cores = cfg['project']['parallelization']['setting']['multicore']['CPU_cores']
    res_dir = out / 'k_quick'
    res_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    ds = xr.open_dataset(a.ds_param or out / cfg['outputs']['file']['ds_param'])
    with rasterio.open(ts['clustering_mask']) as r:
        inside = (r.read(1) == 1).ravel()
    elev = ds.elevation.transpose('y', 'x').values.ravel()
    valid = np.flatnonzero(inside & np.isfinite(elev))
    rng = np.random.default_rng(42)
    pick = np.sort(rng.choice(valid, min(a.n_sample, len(valid)), replace=False))
    ny, nx = ds.sizes['y'], ds.sizes['x']
    iy, ix = np.divmod(pick, nx)
    cols = {}
    for f in features:
        if f in ('x', 'y'):
            cols[f] = ds[f].values[ix if f == 'x' else iy]
        else:
            cols[f] = ds[f].transpose('y', 'x').values.ravel()[pick]
    df = pd.DataFrame(cols)
    frac = len(pick) / len(valid)
    print(f'{len(valid):,} masked pixels, sample {len(pick):,} ({frac:.3%}); loaded in {time.time() - t0:.0f} s',
          flush=True)

    X = StandardScaler().fit_transform(df[list(features)].values)
    X *= np.array([features[f] for f in features])
    band = pd.cut(df.elevation, BANDS, right=False)

    rows, summary = [], {}
    for k in a.ks:
        k_s = max(2, int(round(k * frac)))   # same pixels per cluster as the real run
        t1 = time.time()
        km = cluster.MiniBatchKMeans(n_clusters=k_s, batch_size=256 * cores, random_state=seed).fit(X)
        lab = km.labels_
        centre = df.groupby(lab).mean()
        d = df[['elevation', 'slope', 'svf']].values - centre.loc[lab, ['elevation', 'slope', 'svf']].values
        de = d[:, 0]
        rec = dict(k=k, k_in_sample=k_s, fit_s=round(time.time() - t1, 1),
                   elev_rmse_m=float(np.sqrt(np.mean(de ** 2))),
                   elev_p95_abs_m=float(np.percentile(np.abs(de), 95)),
                   slope_rmse_deg=float(np.degrees(np.sqrt(np.mean(d[:, 1] ** 2)))),
                   svf_rmse=float(np.sqrt(np.mean(d[:, 2] ** 2))),
                   n_used_clusters=int(len(centre)))
        rec['t_equiv_rmse_K'] = rec['elev_rmse_m'] * LAPSE
        for b, g in pd.Series(de).groupby(band.values, observed=True):
            rec[f'elev_rmse_m_{int(b.left)}-{int(b.right)}'] = float(np.sqrt(np.mean(g.values ** 2)))
        # how many clusters each band gets (by centre elevation), real-k equivalent
        cb = pd.cut(centre.elevation, BANDS, right=False).value_counts().sort_index()
        for b, n in cb.items():
            rec[f'clusters_{int(b.left)}-{int(b.right)}'] = int(round(n * k / k_s))
        rows.append(rec)
        print(f'k={k:5d} (sample k={k_s}): elevation RMSE {rec["elev_rmse_m"]:.1f} m '
              f'(~{rec["t_equiv_rmse_K"]:.2f} K), p95 {rec["elev_p95_abs_m"]:.0f} m, '
              f'slope RMSE {rec["slope_rmse_deg"]:.1f} deg, fit {rec["fit_s"]} s', flush=True)

    res = pd.DataFrame(rows)
    res.to_csv(res_dir / 'results.csv', index=False)
    pct = (band.value_counts(normalize=True).sort_index() * 100).round(1)
    summary = dict(n_masked=int(len(valid)), n_sample=int(len(pick)), features=features, seed=seed,
                   pixel_share_by_band_pct={str(k): float(v) for k, v in pct.items()},
                   results=rows, runtime_s=round(time.time() - t0, 1))
    wf.write_json(res_dir / 'summary.json', summary)
    print('pixel share by elevation band (%):', summary['pixel_share_by_band_pct'], flush=True)

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    ax[0].plot(res.k, res.elev_rmse_m, 'o-', color='k', label='all pixels')
    for c in [c for c in res.columns if c.startswith('elev_rmse_m_')]:
        ax[0].plot(res.k, res[c], 'o-', lw=1, label=c.replace('elev_rmse_m_', '') + ' m')
    ax[0].set(xlabel='k (clusters)', ylabel='elevation RMSE pixel vs centre (m)', title='Clustering error')
    ax[0].legend(fontsize=7)
    sec = ax[0].secondary_yaxis('right', functions=(lambda m: m * LAPSE, lambda t: t / LAPSE))
    sec.set_ylabel('~ temperature error (K, 6.5 K/km)')
    gains = -np.diff(res.elev_rmse_m.values)
    ax[1].bar([f'{a}→{b}' for a, b in zip(res.k[:-1], res.k[1:])], gains, color='grey')
    ax[1].set(ylabel='RMSE gain (m)', title='Gain per step')
    fig.tight_layout()
    fig.savefig(res_dir / 'k_quick.png', dpi=130)
    print(f'---> {res_dir}', flush=True)


if __name__ == '__main__':
    main()
