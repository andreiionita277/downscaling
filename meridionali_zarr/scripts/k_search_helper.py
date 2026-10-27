"""
Number of TopoSUB clusters with TopoPyScale's own helper, topo_sub.search_number_of_clusters
(elbow of the WCSS, Davies-Bouldin, Calinski-Harabasz, elevation RMSE). Nothing is downscaled.

    python meridionali_zarr/scripts/k_search_helper.py meridionali_zarr/config/config_meridionali_2025.yml
           [--ks 250 500 ... 5000] [--n-sample 10000000]

Input pixels as in k_quick.py / Topoclass.extract_topo_cluster_param: inside the mask, features from the
config. Runs on a random sample of the masked pixels; k is scaled by n_sample / n_masked so pixels per cluster
are as in the real run, and results are reported for the real k.
Why not Topoclass.search_optimum_number_of_clusters: it ignores the mask and the seed, and passes all
ds_param variables to the scores.

Output: <outputs>/k_search/{results.csv, summary.json, k_search.png}
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from TopoPyScale import topo_sub

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow as wf  # noqa: E402


def knee(x, y):
    """Elbow of a decreasing curve: point farthest below the chord from first to last point (both axes 0-1)."""
    xn = (x - x.min()) / (x.max() - x.min())
    yn = (y - y.min()) / (y.max() - y.min())
    return int(np.argmax((1 - xn) - yn))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--ks', type=int, nargs='+', default=[250, 500, 750, 1000, 1500, 2000, 2500, 3000, 4000, 5000])
    p.add_argument('--n-sample', type=int, default=10_000_000)
    a = p.parse_args()
    cfg, out, dem = wf.load_config(a.config)
    ts = cfg['sampling']['toposub']
    features = ts['clustering_features']
    res_dir = out / 'k_search'
    res_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    ds = xr.open_dataset(out / cfg['outputs']['file']['ds_param'])
    with rasterio.open(ts['clustering_mask']) as r:
        inside = (r.read(1) == 1).ravel()
    elev = ds.elevation.transpose('y', 'x').values.ravel()
    valid = np.flatnonzero(inside & np.isfinite(elev))
    rng = np.random.default_rng(42)
    pick = np.sort(rng.choice(valid, min(a.n_sample, len(valid)), replace=False))
    iy, ix = np.divmod(pick, ds.sizes['x'])
    cols = {}
    for f in features:
        if f in ('x', 'y'):
            cols[f] = ds[f].values[ix if f == 'x' else iy]
        else:
            cols[f] = ds[f].transpose('y', 'x').values.ravel()[pick]
    ds.close()
    df = pd.DataFrame(cols)
    frac = len(pick) / len(valid)
    k_sample = {max(2, int(round(k * frac))): k for k in a.ks}
    print(f'{len(valid):,} masked pixels, sample {len(pick):,} ({frac:.3%}); loaded in {time.time() - t0:.0f} s',
          flush=True)
    print(f'real k -> sample k: {dict((v, k) for k, v in k_sample.items())}', flush=True)

    res = topo_sub.search_number_of_clusters(df, method=ts['clustering_method'],
                                             cluster_range=np.array(list(k_sample)),
                                             features=features, seed=ts['random_seed'], plot=False)
    res.insert(0, 'k', res.n_clusters.map(k_sample))
    res = res.rename(columns={'n_clusters': 'k_in_sample'})
    res.to_csv(res_dir / 'results.csv', index=False)

    best = dict(wcss_elbow=int(res.k[knee(res.k.values, res.wcss_score.values)]),
                rmse_elbow=int(res.k[knee(res.k.values, res.rmse_elevation.values)]),
                davies_bouldin_min=int(res.k[res.db_score.idxmin()]),
                calinski_harabasz_max=int(res.k[res.ch_score.idxmax()]))
    wf.write_json(res_dir / 'summary.json', dict(n_masked=int(len(valid)), n_sample=int(len(pick)),
                                                 features=features, seed=ts['random_seed'], best_k=best,
                                                 results=res.to_dict('records'),
                                                 runtime_s=round(time.time() - t0, 1)))
    with pd.option_context('display.width', 200, 'display.max_columns', 20):
        print(res, flush=True)
    print('best k:', best, flush=True)

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(4, 1, sharex=True, figsize=(7, 9))
    for i, (c, lab) in enumerate([('wcss_score', 'WCSS (elbow)'), ('db_score', 'Davies-Bouldin (lower better)'),
                                  ('ch_score', 'Calinski-Harabasz (higher better)'),
                                  ('rmse_elevation', 'Elevation RMSE (m)')]):
        ax[i].plot(res.k, res[c], 'o-', color='k')
        ax[i].set_ylabel(lab, fontsize=8)
    ax[0].axvline(best['wcss_elbow'], color='r', ls='--')
    ax[3].axvline(best['rmse_elbow'], color='r', ls='--')
    ax[3].set_xlabel('k (clusters, full DEM equivalent)')
    fig.tight_layout()
    fig.savefig(res_dir / 'k_search.png', dpi=130)
    print(f'---> {res_dir}  ({time.time() - t0:.0f} s)', flush=True)


if __name__ == '__main__':
    main()
