"""
Terrain of the FABDEM run, once for all years (each step is skipped when its cache exists):

    python rila_pirin_zarr/scripts/prepare_terrain.py rila_pirin_zarr/config/config_terrain.yml [--stop-after dem_param]

    1 compute_dem_param   -> data/terrain/outputs/ds_param.zarr  (slope, aspect, svf)
      (--stop-after dem_param: stop here, e.g. to re-run k_design_test.py on these terrain parameters)
    2 extract_topo_param  -> df_centroids.pck, cluster map + cluster_group in ds_param.zarr (groups, k-means)
    3 centroid aspect fix -> df_centroids.aspect = atan2(aspect_sin, aspect_cos) mod 2pi; the fork's value
                             (arithmetic mean of the members' aspect, wrong across north) kept as aspect_arith
    4 compute_horizon     -> da_horizon.zarr, horizon angles sampled into df_centroids.pck
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow as wf  # noqa: E402

from TopoPyScale import topoclass as tc  # noqa: E402
from TopoPyScale import topo_utils as tu  # noqa: E402


def fix_centroid_aspect(df):
    """Circular centroid aspect from the k-means centre's (aspect_cos, aspect_sin). Idempotent."""
    if 'aspect_arith' not in df:
        df['aspect_arith'] = df['aspect']
    df['aspect'] = np.mod(np.arctan2(df.aspect_sin, df.aspect_cos), 2 * np.pi)
    d = np.degrees(np.abs(np.angle(np.exp(1j * (df.aspect - df.aspect_arith)))))
    print(f'---> centroid aspect fixed: {int((d > 45).sum())} centroids moved > 45 deg, {int((d > 90).sum())} > 90 deg '
          f'(of {len(df)})', flush=True)
    return df


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--stop-after', choices=['dem_param'])
    a = p.parse_args()
    t_all = time.time()
    timer = wf.Timer()
    print(f'Terrain started {pd.Timestamp.now():%Y-%m-%d %H:%M:%S}; config {a.config}', flush=True)
    mp = tc.Topoclass(a.config)
    out = mp.config.outputs.path
    f_cent = out / mp.config.outputs.file.df_centroids

    if not f_cent.is_file():
        timer('compute_dem_param', mp.compute_dem_param)
        if a.stop_after == 'dem_param':
            print(f'stopped after compute_dem_param ({time.time() - t_all:.0f} s)', flush=True)
            return
        timer('extract_topo_param', mp.extract_topo_param)
    else:
        mp.toposub.ds_param = tu.open_dataset(out / mp.config.outputs.file.ds_param)
        mp.extract_topo_param()
    mp.toposub.df_centroids = fix_centroid_aspect(mp.toposub.df_centroids)
    mp.toposub.df_centroids.to_pickle(f_cent)
    timer('compute_horizon', mp.compute_horizon)

    df = pd.read_pickle(f_cent)
    hori = [c for c in df if c.startswith('hori_azi_')]
    summary = dict(finished=str(pd.Timestamp.now()), n_centroids=len(df), n_horizon_azimuths=len(hori),
                   aspect_fixed=bool('aspect_arith' in df), steps_s=timer.t, total_s=round(time.time() - t_all, 1))
    ds = tu.open_dataset(out / mp.config.outputs.file.ds_param)
    if 'cluster_group' in ds:
        cg = ds.cluster_group.values
        pn = ds.point_name.values
        g_of_cluster = pd.Series(cg[pn != '-9999']).groupby(pn[pn != '-9999']).agg(['min', 'max'])
        summary['clusters_per_group'] = {int(g): int((g_of_cluster['min'] == g).sum())
                                         for g in sorted(np.unique(g_of_cluster['min']))}
        summary['clusters_spanning_groups'] = int((g_of_cluster['min'] != g_of_cluster['max']).sum())
        summary['centre_elevation_per_group'] = {
            int(g): [float(df.elevation[df.point_name.isin(g_of_cluster.index[g_of_cluster['min'] == g])].min()),
                     float(df.elevation[df.point_name.isin(g_of_cluster.index[g_of_cluster['min'] == g])].max())]
            for g in sorted(np.unique(g_of_cluster['min']))}
    wf.write_json(out / 'terrain_summary.json', summary)
    print(summary, flush=True)
    if len(hori) == 0 or not summary['aspect_fixed']:
        sys.exit(1)


if __name__ == '__main__':
    main()
