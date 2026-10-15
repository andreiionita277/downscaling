"""
Search for the number of TopoSUB clusters (k) for the Rila-Pirin domain.

Uses TopoPyScale's own scaling and mini-batch k-means functions (topo_sub.scale_df,
topo_sub.minibatch_kmeans_clustering, topo_sub.inverse_scale_df) with the same settings
as the real pipeline (StandardScaler, feature weights, batch_size = 256 * CPU_cores), so
the scores describe the clustering the pipeline will actually produce.

Every (k, weight, seed) fit runs either on a random subsample of the
pixels inside the AOI mask (SUBSAMPLE_N), or on the full masked population when SUBSAMPLE_N is None
(the default, identical to the pipeline).

Run inside the container:
    python bulgaria_rila_pirin/scripts/cluster_search.py            # search + figures
    python bulgaria_rila_pirin/scripts/cluster_search.py --plot-only # redraw figures from CSV

Outputs (in --out, default /data/cluster_search):
    cluster_search_results.csv   one row per (k, elevation weight, k-means seed)
    full_population_check.csv    subsample vs full-population score at one k
    fig_cluster_search.png/.pdf  main thesis figure
    fig_cluster_search_supp.png/.pdf  supplementary scores
    run_info.txt                 exact settings, for the methods section
"""
import argparse
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# one BLAS/OpenMP thread per worker process; parallelism comes from the process pool
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np
import pandas as pd
import xarray as xr

DS_PARAM = "/data/terrain/ds_param_terrain_only.nc"
MASK = "/data/terrain/mask.tif"

K_VALUES = [100, 200, 300, 400, 500, 600, 800, 1000, 1250, 1500, 2000]
ELEV_WEIGHTS = [1, 4]
KMEANS_SEEDS = [2, 3, 4]          # 2 = random_seed in the project config
SUBSAMPLE_N = None                # None = cluster the full masked population, like the pipeline
SUBSAMPLE_SEED = 42
CPU_CORES_CONFIG = 20             # config multicore.CPU_cores -> batch_size 256*20
FULL_CHECK = dict(k=500, weight=4, seed=2)
LAPSE_RATE_K_PER_KM = 6.5         # only to translate elevation RMSE into an indicative temperature error

BASE_FEATURES = {'x': 1, 'y': 1, 'elevation': 1, 'slope': 1, 'aspect_cos': 1, 'aspect_sin': 1, 'svf': 1}


def features_for(weight):
    f = dict(BASE_FEATURES)
    f['elevation'] = weight
    return f


def load_masked_pixels():
    ds = xr.open_dataset(DS_PARAM)[['elevation', 'slope', 'aspect', 'aspect_cos', 'aspect_sin', 'svf']].load()
    mask = xr.open_dataset(MASK, engine='rasterio').band_data.isel(band=0).values
    if mask.shape != ds.elevation.shape:
        raise ValueError(f"mask shape {mask.shape} != DEM shape {ds.elevation.shape}")
    df = ds.to_dataframe().reset_index()[['x', 'y', 'elevation', 'slope', 'aspect', 'aspect_cos', 'aspect_sin', 'svf']]
    keep = (mask.ravel() == 1) & ~df.elevation.isna().values
    return df[keep].reset_index(drop=True)


def evaluate(df, k, weight, seed, label):
    """Cluster df exactly as Topoclass.extract_topo_cluster_param does and score the result."""
    from TopoPyScale import topo_sub as ts
    from sklearn.metrics import davies_bouldin_score, calinski_harabasz_score

    feats = features_for(weight)
    t0 = time.time()
    df_scaled, scaler = ts.scale_df(df, features=feats)
    df_centroids, km, labels = ts.minibatch_kmeans_clustering(df_scaled, n_clusters=k, features=feats,
                                                              n_cores=CPU_CORES_CONFIG, seed=seed)
    labels = np.asarray(labels)
    cent = ts.inverse_scale_df(df_centroids[list(feats)], scaler, features=feats)
    fit_s = time.time() - t0

    # Each pixel receives its cluster centroid's terrain; errors below are what the
    # downscaling "sees" when it replaces a pixel by its centroid.
    c = cent.iloc[labels]
    err_elev = df.elevation.values - c.elevation.values
    err_slope = np.degrees(df.slope.values - c.slope.values)  # slope stored in radians
    err_svf = df.svf.values - c.svf.values
    cent_aspect = np.arctan2(c.aspect_sin.values, c.aspect_cos.values)
    d_aspect = np.angle(np.exp(1j * (np.arctan2(df.aspect_sin.values, df.aspect_cos.values) - cent_aspect)))
    counts = np.bincount(labels, minlength=k)
    counts = counts[counts > 0]

    X = df_scaled[list(feats)].to_numpy()
    return dict(
        sample=label, n_pixels=len(df), k=k, elevation_weight=weight, seed=seed,
        rmse_elevation_m=float(np.sqrt(np.mean(err_elev ** 2))),
        mae_elevation_m=float(np.mean(np.abs(err_elev))),
        p95_abs_elevation_m=float(np.percentile(np.abs(err_elev), 95)),
        rmse_slope_deg=float(np.sqrt(np.mean(err_slope ** 2))),
        rmse_svf=float(np.sqrt(np.mean(err_svf ** 2))),
        mean_abs_aspect_deg=float(np.degrees(np.mean(np.abs(d_aspect)))),
        wcss_per_pixel=float(km.inertia_ / len(df)),
        davies_bouldin=float(davies_bouldin_score(X, labels)),
        calinski_harabasz=float(calinski_harabasz_score(X, labels)),
        n_nonempty_clusters=int(len(counts)),
        pixels_per_cluster_median=float(np.median(counts)),
        pixels_per_cluster_min=int(counts.min()),
        fit_seconds=round(fit_s, 1),
    )


_DF = None


def _init(df):
    global _DF
    _DF = df


def _task(args):
    k, w, s = args
    return evaluate(_DF, k, w, s, 'subsample')


def run_search(out, workers):
    t0 = time.time()
    df_all = load_masked_pixels()
    print(f"masked pixels: {len(df_all):,}  (load {time.time() - t0:.0f}s)", flush=True)
    df_sub = df_all if SUBSAMPLE_N is None else df_all.sample(n=SUBSAMPLE_N, random_state=SUBSAMPLE_SEED).reset_index(drop=True)

    tasks = [(k, w, s) for k in sorted(K_VALUES, reverse=True) for w in ELEV_WEIGHTS for s in KMEANS_SEEDS]
    csv = out / 'cluster_search_results.csv'
    done = set()
    rows = []
    if csv.exists():  # resumable
        prev = pd.read_csv(csv)
        rows = prev.to_dict('records')
        done = {(r['k'], r['elevation_weight'], r['seed']) for r in rows}
    todo = [t for t in tasks if t not in done]
    print(f"{len(todo)} fits to run ({len(done)} already done), {workers} workers", flush=True)

    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(df_sub,)) as ex:
        futs = {ex.submit(_task, t): t for t in todo}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            rows.append(r)
            pd.DataFrame(rows).sort_values(['elevation_weight', 'k', 'seed']).to_csv(csv, index=False)
            print(f"[{i}/{len(todo)}] k={r['k']} w={r['elevation_weight']} seed={r['seed']} "
                  f"RMSE_elev={r['rmse_elevation_m']:.1f} m  ({r['fit_seconds']}s, "
                  f"elapsed {time.time() - t0:.0f}s)", flush=True)

    # representativeness check: same k/weight/seed on the full masked population
    chk_csv = out / 'full_population_check.csv'
    if SUBSAMPLE_N is not None and not chk_csv.exists():
        os.environ['OMP_NUM_THREADS'] = str(workers)
        full = evaluate(df_all, FULL_CHECK['k'], FULL_CHECK['weight'], FULL_CHECK['seed'], 'full_population')
        sub = [r for r in rows if r['k'] == FULL_CHECK['k'] and r['elevation_weight'] == FULL_CHECK['weight']
               and r['seed'] == FULL_CHECK['seed']][0]
        pd.DataFrame([sub, full]).to_csv(chk_csv, index=False)
        print(f"full-population check: subsample RMSE {sub['rmse_elevation_m']:.1f} m vs full "
              f"{full['rmse_elevation_m']:.1f} m", flush=True)

    (out / 'run_info.txt').write_text(
        f"ds_param: {DS_PARAM}\nmask: {MASK}\nmasked pixels: {len(df_all)}\n"
        f"subsample: {'none (full masked population)' if SUBSAMPLE_N is None else f'{SUBSAMPLE_N} pixels, random_state={SUBSAMPLE_SEED}'}\n"
        f"k values: {K_VALUES}\nelevation weights: {ELEV_WEIGHTS} (all other features weight 1)\n"
        f"features: {list(BASE_FEATURES)}\nscaler: sklearn StandardScaler, then feature * weight\n"
        f"clustering: sklearn MiniBatchKMeans via TopoPyScale.topo_sub.minibatch_kmeans_clustering, "
        f"batch_size={256 * CPU_CORES_CONFIG}\nk-means seeds: {KMEANS_SEEDS}\n"
        f"total wall time: {time.time() - t0:.0f}s\n")


# ---------------------------------------------------------------- figures
BLUE, ORANGE = '#2a78d6', '#eb6834'      # categorical slots 1 and 2 (validated default palette)
INK, INK2, GRID = '#1f1f1e', '#5f5e58', '#e4e3dc'
STYLE = {1: dict(color=BLUE, ls='-', marker='o', label='Elevation weight 1'),
         4: dict(color=ORANGE, ls='--', marker='s', label='Elevation weight 4')}


def _style_axes(ax):
    ax.spines[['top', 'right']].set_visible(False)
    for s in ('left', 'bottom'):
        ax.spines[s].set_color(INK2)
        ax.spines[s].set_linewidth(0.8)
    ax.tick_params(colors=INK2, labelcolor=INK, labelsize=9, width=0.8)
    ax.grid(axis='y', color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _series(ax, g, col, w, show_band=True):
    st = STYLE[w]
    agg = g.groupby('k')[col].agg(['mean', 'min', 'max'])
    if show_band:
        ax.fill_between(agg.index, agg['min'], agg['max'], color=st['color'], alpha=0.18, linewidth=0)
    ax.plot(agg.index, agg['mean'], color=st['color'], ls=st['ls'], lw=2, marker=st['marker'], ms=5,
            markeredgecolor='white', markeredgewidth=0.8, label=st['label'])
    return agg


def plot(out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.labelcolor': INK,
                         'axes.titlesize': 10, 'axes.titleweight': 'bold', 'axes.titlelocation': 'left',
                         'figure.facecolor': 'white', 'axes.facecolor': 'white', 'savefig.dpi': 300})
    df = pd.read_csv(out / 'cluster_search_results.csv')

    # ---- main figure: (a) elevation RMSE vs k, (b) marginal gain per +100 clusters
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.3), constrained_layout=True)
    ax = axes[0]
    for w in ELEV_WEIGHTS:
        agg = _series(ax, df[df.elevation_weight == w], 'rmse_elevation_m', w)
        last = agg.iloc[-1]
        ax.annotate(f"{last['mean']:.0f} m", (agg.index[-1], last['mean']), xytext=(4, 0),
                    textcoords='offset points', va='center', fontsize=8, color=INK)
    ax.set_xlabel('Number of clusters, k')
    ax.set_ylabel('Elevation RMSE, pixel vs. centroid (m)')
    ax.set_title('a  Terrain represented by cluster centroids')
    ax.set_ylim(bottom=0)
    ax.text(0.98, 0.97, f"100 m ≈ {LAPSE_RATE_K_PER_KM * 0.1:.2f} K at {LAPSE_RATE_K_PER_KM} K km$^{{-1}}$",
            transform=ax.transAxes, ha='right', va='top', fontsize=8, color=INK2)
    ax.legend(frameon=False, loc='lower left', fontsize=8)
    _style_axes(ax)

    ax = axes[1]
    for w in ELEV_WEIGHTS:
        g = df[df.elevation_weight == w].groupby('k').rmse_elevation_m.mean()
        gain = -np.diff(g.values) / np.diff(g.index.values) * 100     # m of RMSE removed per +100 clusters
        mid = (g.index.values[1:] + g.index.values[:-1]) / 2
        st = STYLE[w]
        ax.plot(mid, gain, color=st['color'], ls=st['ls'], lw=2, marker=st['marker'], ms=5,
                markeredgecolor='white', markeredgewidth=0.8, label=st['label'])
    ax.set_xlabel('Number of clusters, k (interval midpoint)')
    ax.set_ylabel('RMSE reduction per +100 clusters (m)')
    ax.set_title('b  Diminishing returns')
    ax.set_ylim(bottom=0)
    ax.legend(frameon=False, loc='upper right', fontsize=8)
    _style_axes(ax)

    n = df.n_pixels.iloc[0]
    fig.text(0.0, -0.02, f"Mini-batch k-means on all {n / 1e6:.2f} M 25 m pixels inside the Rila–Pirin mask; "
             f"line = mean of {df.seed.nunique()} k-means seeds, band = min–max.", fontsize=7.5, color=INK2)
    for ext in ('png', 'pdf'):
        fig.savefig(out / f'fig_cluster_search.{ext}', bbox_inches='tight')
    plt.close(fig)

    # ---- supplementary: other terrain features + internal scores
    panels = [('rmse_slope_deg', 'Slope RMSE (°)'), ('mean_abs_aspect_deg', 'Mean abs. aspect error (°)'),
              ('rmse_svf', 'Sky-view factor RMSE (–)'), ('wcss_per_pixel', 'WCSS per pixel (scaled units)'),
              ('davies_bouldin', 'Davies–Bouldin (lower = better)'), ('calinski_harabasz', 'Calinski–Harabasz (higher = better)')]
    fig, axes = plt.subplots(2, 3, figsize=(7.2, 4.6), constrained_layout=True, sharex=True)
    for (col, lab), ax, letter in zip(panels, axes.ravel(), 'abcdef'):
        for w in ELEV_WEIGHTS:
            _series(ax, df[df.elevation_weight == w], col, w)
        ax.set_title(f'{letter}  {lab}', fontsize=9)
        _style_axes(ax)
    for ax in axes[1]:
        ax.set_xlabel('Number of clusters, k')
    axes[0, 0].legend(frameon=False, fontsize=7.5)
    fig.text(0.0, -0.02, "WCSS, Davies–Bouldin and Calinski–Harabasz are computed in the weighted feature space, "
             "so they are only comparable within one elevation weight.", fontsize=7.5, color=INK2)
    for ext in ('png', 'pdf'):
        fig.savefig(out / f'fig_cluster_search_supp.{ext}', bbox_inches='tight')
    plt.close(fig)
    print(f"figures written to {out}")


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--out', default='/data/cluster_search')
    p.add_argument('--workers', type=int, default=8)
    p.add_argument('--plot-only', action='store_true')
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if not a.plot_only:
        run_search(out, a.workers)
    plot(out)
