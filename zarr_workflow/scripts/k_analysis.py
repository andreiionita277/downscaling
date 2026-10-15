"""
How much error does TopoSUB clustering add to the downscaled climate, as a function of k?

    python zarr_workflow/scripts/k_analysis.py zarr_workflow/config/config_2025.yml
           [--ks 100 300 500 800 1000 2000] [--w1-ks 500] [--n-per-band 250]

Idea: a pixel's "true" TopoSCALE climate is the one downscaled at its own terrain (sampling: points).
In a clustered run the pixel instead gets its cluster's climate. The difference between the two is the
error caused by clustering alone (same ERA5, same physics, same year), i.e. what k and the feature
weights control. Observations are not needed (the model-vs-station error, ~4 K hourly, is a separate
question; see the station validation).

Steps (outputs in <project>/k_analysis/, every step resumable):
  1 sample pixels inside the mask, stratified by elevation band (n per band, fixed seed) -> samples.csv
  2 point run at the samples' exact terrain                        -> points/
  3 clustered runs for each k (elevation weight 4, others 1) and,
    for comparison, elevation weight 1 at --w1-ks                   -> w4_k<k>/, w1_k<k>/
  4 daily t mean/min/max and SW/tp per sample: clustered minus point -> results.csv, summary.json, figures
All runs use the main workflow's terrain (ds_param.zarr, without its cluster map), horizon and ERA5.

Note: these runs cluster on the cached (int16-packed) terrain, as every run from a cached ds_param does,
while the main workflow run clustered on freshly computed values (PROGRESS.md 2026-10-13). The main run
is therefore also evaluated ('main_w4_k500') to show the size of that difference.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow as wf  # noqa: E402

from TopoPyScale import topo_daily  # noqa: E402
from TopoPyScale import topo_export as te  # noqa: E402
from TopoPyScale import topo_scale_zarr as tz  # noqa: E402
from TopoPyScale import topo_utils as tu  # noqa: E402
from TopoPyScale import topoclass as tc  # noqa: E402

BANDS = [0, 500, 1000, 1500, 2000, 2500, 3000]
TERRAIN = ['elevation', 'slope', 'aspect', 'aspect_cos', 'aspect_sin', 'svf']
STATS = {'t_mean': 'K', 't_min': 'K', 't_max': 'K', 'SW_mean': 'W/m2', 'LW_mean': 'W/m2', 'tp_sum': 'mm/day'}


def sample_pixels(ds_param_path, root, n_per_band, seed=42):
    f = root / 'samples.csv'
    if f.exists():
        return pd.read_csv(f, dtype={'name': str})
    p = tu.open_dataset(ds_param_path)[['elevation', 'point_name']].transpose('y', 'x').load()
    inside = p.point_name.values.astype(str) != '-9999'
    el = p.elevation.values
    iy, ix = np.nonzero(inside)
    band = np.digitize(el[iy, ix], BANDS) - 1
    rng = np.random.default_rng(seed)
    rows = []
    for b in range(len(BANDS) - 1):
        cand = np.nonzero(band == b)[0]
        take = rng.choice(cand, min(n_per_band, len(cand)), replace=False)
        for k in take:
            rows.append(dict(x=float(p.x.values[ix[k]]), y=float(p.y.values[iy[k]]),
                             elevation=float(el[iy[k], ix[k]]), band=f'{BANDS[b]}-{BANDS[b + 1]}',
                             band_area_px=int(len(cand))))
    df = pd.DataFrame(rows)
    df['name'] = [f'S{i:04d}' for i in range(len(df))]
    df.to_csv(f, index=False)
    print(f'---> {len(df)} sample pixels written to {f}', flush=True)
    return df


def terrain_only(base_param, root):
    """The main run's terrain parameters without its cluster map, as the starting cache for every run."""
    dst = root / 'terrain_only.zarr'
    if not tu.dataset_exists(dst):
        ds = tu.open_dataset(base_param)
        te.save_dataset(ds[[v for v in TERRAIN if v in ds]].load(), dst)
    return dst


def run_config(base_cfg, run_dir, sampling, cores=10):
    cfg = json.loads(json.dumps(base_cfg, default=str))          # deep copy, dates as strings
    cfg['project']['directory'] = str(run_dir)
    cfg['project']['name'] = f'k analysis {run_dir.name}'
    cfg['sampling'] = sampling
    f = cfg['outputs']['file']
    f.pop('zarr_store_start', None)
    f.pop('zarr_store_end', None)                                # one run over the whole period
    cfg['clean_up']['rm_tmp_dirs'] = False                       # tmp dirs would include the shared ERA5 tmp
    # fewer workers than the main run: each holds ~2 GB (solar geometry of all points); 64 GB machine
    cfg['project']['parallelization']['setting']['multicore']['CPU_cores'] = cores
    path = run_dir / 'config.yml'
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return path


def run(base_cfg, base_out, root, terrain, name, sampling, cores=10):
    run_dir = root / name
    out = run_dir / 'outputs'
    store = out / 'downscaled' / base_cfg['outputs']['file']['zarr_store']
    if (out / 'done.json').exists():
        return out
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    if not tu.dataset_exists(out / base_cfg['outputs']['file']['ds_param']):
        shutil.copytree(terrain, out / base_cfg['outputs']['file']['ds_param'])
    hz = out / base_cfg['outputs']['file']['da_horizon']
    if not hz.exists():
        hz.symlink_to(base_out / base_cfg['outputs']['file']['da_horizon'])
    cfgp = run_config(base_cfg, run_dir, sampling, cores)
    print(f'\n#################### {name}', flush=True)
    mp = tc.Topoclass(str(cfgp))
    timer = wf.Timer()
    timer('compute_dem_param', mp.compute_dem_param)
    timer('extract_topo_param', mp.extract_topo_param)
    timer('compute_solar_geometry', mp.compute_solar_geometry)
    timer('compute_horizon', mp.compute_horizon)
    timer('downscale_climate', mp.downscale_climate)
    wf.write_json(out / 'done.json', dict(timings_s=timer.t, total_s=round(time.time() - t0, 1),
                                          n_points=int(len(mp.toposub.df_centroids))))
    if not store.exists():
        raise RuntimeError(f'{store} missing after the run')
    return out


def daily_of(out, store_name, start, end):
    f = out / 'downscaled' / 'daily.zarr'
    if tu.dataset_exists(f):
        return xr.open_zarr(f)
    h = tz.open_downscaled_store(out / 'downscaled' / store_name).sel(
        time=slice(pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta('23h'))).load()
    d = topo_daily.daily_stats(h)
    d = d.assign_coords(point_name=d.point_name.values.astype(str))
    for v in d.variables:
        d[v].encoding = {}
        if d[v].dtype.kind in 'OU':
            d[v] = d[v].astype(str)
    d.to_zarr(f, mode='w', zarr_format=3)
    return xr.open_zarr(f)


def labels_at(ds_param_path, samples):
    p = tu.open_dataset(ds_param_path).point_name
    return np.array([str(p.sel(x=x, y=y).values) for x, y in zip(samples.x, samples.y)])


def compare(samples, ref, clu, labels):
    rows = []
    for s, (name, lab) in enumerate(zip(samples.name, labels)):
        r = {'name': name}
        for st in STATS:
            a = clu[st].sel(point_name=lab).values
            b = ref[st].sel(point_name=name).values
            d = a - b
            r[f'{st}_bias'] = float(np.nanmean(d))
            r[f'{st}_rmse'] = float(np.sqrt(np.nanmean(d ** 2)))
        rows.append(r)
    return pd.DataFrame(rows)


def area_weighted(df, samples, col):
    """Mean over samples, each band weighted by its share of the masked area (samples are stratified)."""
    j = df.merge(samples[['name', 'band', 'band_area_px']], on='name')
    g = j.groupby('band').agg(v=(col, 'mean'), a=('band_area_px', 'first'))
    return float((g.v * g.a).sum() / g.a.sum())


def bootstrap_ci(df, samples, col, n=2000, seed=0):
    j = df.merge(samples[['name', 'band', 'band_area_px']], on='name')
    rng = np.random.default_rng(seed)
    groups = {b: g[col].values for b, g in j.groupby('band')}
    area = j.groupby('band').band_area_px.first()
    vals = []
    for _ in range(n):
        m = {b: rng.choice(v, len(v)).mean() for b, v in groups.items()}
        vals.append(sum(m[b] * area[b] for b in m) / area.sum())
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]


def figure(summary, samples, root, ks):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    bands = sorted(samples.band.unique(), key=lambda b: int(b.split('-')[0]))
    fig, axs = plt.subplots(1, 3, figsize=(15, 4.6))
    for ax, st, lab in zip(axs, ['t_mean', 't_min', 'SW_mean'],
                           ['daily mean t RMSE (K)', 'daily min t RMSE (K)', 'daily mean SW RMSE (W/m²)']):
        cmap = plt.get_cmap('viridis', len(bands))
        for i, b in enumerate(bands):
            ax.plot(ks, [summary[f'w4_k{k}']['by_band'][b][f'{st}_rmse'] for k in ks], '-o', ms=3, color=cmap(i),
                    label=f'{b} m')
        ax.plot(ks, [summary[f'w4_k{k}'][f'{st}_rmse_area_weighted'] for k in ks], 'k-o', lw=2.2, label='whole AOI')
        for k in [k for k in ks if f'w1_k{k}' in summary]:
            ax.plot([k], [summary[f'w1_k{k}'][f'{st}_rmse_area_weighted']], 'rs', ms=8, label=f'weight 1, k={k}')
        ax.set_xlabel('number of clusters k'); ax.set_ylabel(lab); ax.grid(alpha=0.3)
    axs[0].legend(fontsize=7)
    fig.suptitle('Error added by clustering (cluster climate minus exact-terrain climate), 2025, elevation weight 4')
    fig.tight_layout()
    for ext in ('png', 'pdf'):
        fig.savefig(root / f'fig_k_analysis.{ext}', dpi=300)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--ks', type=int, nargs='+', default=[100, 300, 500, 800, 1000, 2000])
    p.add_argument('--w1-ks', type=int, nargs='*', default=[500])
    p.add_argument('--n-per-band', type=int, default=250)
    p.add_argument('--cores', type=int, default=10, help='workers per downscaling run (memory: ~2 GB each)')
    a = p.parse_args()
    base_cfg, base_out, dem = wf.load_config(a.config)
    root = Path(base_cfg['project']['directory']) / 'k_analysis'
    root.mkdir(parents=True, exist_ok=True)
    start, end = str(base_cfg['project']['start']), str(base_cfg['project']['end'])
    fparam = base_out / base_cfg['outputs']['file']['ds_param']
    store_name = base_cfg['outputs']['file']['zarr_store']
    t_all = time.time()

    samples = sample_pixels(fparam, root, a.n_per_band)
    terrain = terrain_only(fparam, root)
    samples[['name', 'x', 'y']].to_csv(root / 'points.csv', index=False)
    pts = run(base_cfg, base_out, root, terrain, 'points',
              {'method': 'points', 'points': {'csv_file': str(root / 'points.csv'), 'name_column': 'name'}}, a.cores)
    ref = daily_of(pts, store_name, start, end).load()

    runs = {f'w4_k{k}': (4, k) for k in a.ks} | {f'w1_k{k}': (1, k) for k in a.w1_ks}
    summary, tables = {}, []
    base_tk = base_cfg['sampling']['toposub']
    for name, (w, k) in runs.items():
        feats = {'x': 1, 'y': 1, 'elevation': w, 'slope': 1, 'aspect_cos': 1, 'aspect_sin': 1, 'svf': 1}
        out = run(base_cfg, base_out, root, terrain, name,
                  {'method': 'toposub', 'toposub': dict(base_tk, n_clusters=k, clustering_features=feats)}, a.cores)
        clu = daily_of(out, store_name, start, end).load()
        lab = labels_at(out / base_cfg['outputs']['file']['ds_param'], samples)
        tables.append(compare(samples, ref, clu, lab).assign(run=name, k=k, weight=w))
    # the main workflow run itself (clustered on fresh, unpacked terrain); wait until it has finished
    while not (base_out / 'year_summary.json').exists():
        print('---> waiting for the main run to finish (year_summary.json)', flush=True)
        time.sleep(120)
    main_daily = xr.open_zarr(base_out / 'downscaled' / 'daily.zarr').load()
    tables.append(compare(samples, ref, main_daily, labels_at(fparam, samples)).assign(run='main_w4_k500', k=500, weight=4))

    res = pd.concat(tables, ignore_index=True).merge(samples[['name', 'band', 'elevation']], on='name')
    res.to_csv(root / 'results.csv', index=False)
    for name, g in res.groupby('run'):
        s = {'k': int(g.k.iloc[0]), 'weight': int(g.weight.iloc[0]), 'by_band': {}}
        for st in STATS:
            for m in ('rmse', 'bias'):
                s[f'{st}_{m}_area_weighted'] = area_weighted(g, samples, f'{st}_{m}')
            s[f'{st}_rmse_ci95'] = bootstrap_ci(g, samples, f'{st}_rmse')
        for b, gb in g.groupby('band'):
            s['by_band'][b] = {f'{st}_{m}': float(gb[f'{st}_{m}'].mean()) for st in STATS for m in ('rmse', 'bias')}
        summary[name] = s
    summary['_info'] = dict(n_samples=len(samples), n_per_band=a.n_per_band, period=f'{start}..{end}',
                            reference='TopoSCALE at each sample pixel\'s exact terrain (sampling: points)',
                            metric='per sample: RMSE/bias over the days of clustered minus reference; '
                                   'mean over samples, bands weighted by their share of the masked area',
                            total_s=round(time.time() - t_all, 1))
    wf.write_json(root / 'summary.json', summary)
    figure(summary, samples, root, a.ks)
    print('\nrun            daily t_mean RMSE (K) [95% CI]   t_min RMSE   t_max RMSE   SW_mean RMSE', flush=True)
    for name in list(runs) + ['main_w4_k500']:
        s = summary[name]
        print(f'{name:14s} {s["t_mean_rmse_area_weighted"]:.3f} [{s["t_mean_rmse_ci95"][0]:.3f}, '
              f'{s["t_mean_rmse_ci95"][1]:.3f}]   {s["t_min_rmse_area_weighted"]:.3f}   '
              f'{s["t_max_rmse_area_weighted"]:.3f}   {s["SW_mean_rmse_area_weighted"]:.2f}', flush=True)
    print(f'\nK ANALYSIS DONE in {time.time() - t_all:.0f} s; {root}', flush=True)


if __name__ == '__main__':
    main()
