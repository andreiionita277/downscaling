"""
Clustering design test for Rila-Pirin: number of clusters k, elevation groups with density coefficients, and
feature weights. Nothing is downscaled; only TopoSUB is reproduced and its terrain error measured.

    python rila_pirin_zarr/scripts/k_design_test.py <ds_param.zarr> <mask.tif> <out_dir>
           [--scenarios S0 S1 ...] [--n-sample 6000000] [--tag _x]

Clustering exactly as Topoclass.extract_topo_cluster_param (fork): per group its own StandardScaler,
features x weights, MiniBatchKMeans (batch 256 x cores, seed 2), n_clusters_g = ceil(k x weight_g).
Centroid features = the k-means centres (inverse-scaled), as the fork; centroid `aspect` (not a feature)
= arithmetic mean of the members' aspect, as the fork ("fork aspect"), and the circular mean
atan2(sin, cos) of the centre ("circular aspect"). Runs on a random sample of the mask; k scaled to it.

Group weights from density coefficients c_g: weight_g = area_g x c_g / sum(area x c), i.e. clusters per km2
in group g are proportional to c_g (c = 1 everywhere = clusters proportional to area, but each group still
clustered on its own).

Metrics per reporting band (pixel minus its cluster centre):
  elevation RMSE / bias (m), slope RMSE (deg), svf RMSE, aspect error (deg, circular; pixels with slope > 10 deg),
  direct-beam clear-sky radiation factor on the slope relative to flat ground (Dec 21 and Mar 20, no horizon),
  RMSE in % of flat, for the circular and for the fork aspect; distance pixel-centre (km); clusters, km2/cluster.
  Group boundary jump: mean centre elevation of pixels 0-25 m above minus 0-25 m below each edge (~12-25 m
  expected without a boundary).
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from sklearn import cluster
from sklearn.preprocessing import StandardScaler

BANDS = [0, 800, 1000, 1500, 2000, 2500, 3000]
LAT = np.radians(41.9)        # domain centre
SEED, CORES = 2, 32
FEAT = {'x': 1, 'y': 1, 'elevation': 4, 'slope': 1, 'aspect_cos': 1, 'aspect_sin': 1, 'svf': 1}

E4 = [0, 800, 1500, 2000]     # group edges (lower bounds)
E5 = [0, 800, 1500, 2000, 2500]
# name: (k, group lower edges or None, density coefficients per group, feature weights)
SCEN = {
    'S0_k500_nogroups':        (500, None, None, FEAT),
    'S1_k1000_nogroups':       (1000, None, None, FEAT),
    'G1_k1000_area':           (1000, E4, [1, 1, 1, 1], FEAT),
    'G2_k1000_ramp':           (1000, E4, [0.5, 1, 1.5, 2], FEAT),
    'G3_k1000_steep':          (1000, E4, [0.5, 1, 2, 4], FEAT),
    'G2_k500_ramp':            (500, E4, [0.5, 1, 1.5, 2], FEAT),
    'G2_k1500_ramp':           (1500, E4, [0.5, 1, 1.5, 2], FEAT),
    'G5_k1000_ramp5':          (1000, E5, [0.5, 1, 1.5, 2, 2.5], FEAT),
    # feature weights, on G2_k1000_ramp
    'W_e2':      (1000, E4, [0.5, 1, 1.5, 2], {**FEAT, 'elevation': 2}),
    'W_e8':      (1000, E4, [0.5, 1, 1.5, 2], {**FEAT, 'elevation': 8}),
    'W_xy05':    (1000, E4, [0.5, 1, 1.5, 2], {**FEAT, 'x': 0.5, 'y': 0.5}),
    'W_asp2':    (1000, E4, [0.5, 1, 1.5, 2], {**FEAT, 'aspect_cos': 2, 'aspect_sin': 2}),
    'W_e2_asp2': (1000, E4, [0.5, 1, 1.5, 2], {**FEAT, 'elevation': 2, 'aspect_cos': 2, 'aspect_sin': 2}),
    'W_svf2':    (1000, E4, [0.5, 1, 1.5, 2], {**FEAT, 'svf': 2}),
    # round 2 (after round 1): aspect 2 with elevation 4-6, more clusters, steeper coefficients
    'R_e6_asp2':             (1000, E4, [0.5, 1, 1.5, 2], {**FEAT, 'elevation': 6, 'aspect_cos': 2, 'aspect_sin': 2}),
    'R_e4_asp2_slope2':      (1000, E4, [0.5, 1, 1.5, 2], {**FEAT, 'slope': 2, 'aspect_cos': 2, 'aspect_sin': 2}),
    'R_e4_asp2_steep':       (1000, E4, [0.5, 1, 2, 4], {**FEAT, 'aspect_cos': 2, 'aspect_sin': 2}),
    'R_e4_asp2_k1500':       (1500, E4, [0.5, 1, 1.5, 2], {**FEAT, 'aspect_cos': 2, 'aspect_sin': 2}),
    'R_e6_asp2_k1500':       (1500, E4, [0.5, 1, 1.5, 2], {**FEAT, 'elevation': 6, 'aspect_cos': 2, 'aspect_sin': 2}),
    'R_e4_asp2_nogroups_k1000': (1000, None, None, {**FEAT, 'aspect_cos': 2, 'aspect_sin': 2}),
}


def group_weights(area, coef):
    """weight_g ~ area_g x c_g, rounded to multiples of 1/1024 (exact in binary, so the fork's sum == 1 holds)."""
    w = np.asarray(area, float) * np.asarray(coef, float)
    w = w / w.sum() * 1024
    n = np.floor(w).astype(int)
    n[np.argsort(n - w)[:1024 - n.sum()]] += 1       # largest remainders
    return n / 1024


def sun_positions(doy):
    """Solar zenith/azimuth (rad, azimuth clockwise from north) every 15 min of a day, sun above horizon."""
    dec = np.radians(23.44) * np.sin(2 * np.pi * (284 + doy) / 365)
    h = np.radians(np.arange(-180, 180, 3.75))                 # hour angle
    cz = np.sin(LAT) * np.sin(dec) + np.cos(LAT) * np.cos(dec) * np.cos(h)
    zen = np.arccos(np.clip(cz, -1, 1))
    az = np.arctan2(np.sin(h), np.cos(h) * np.sin(LAT) - np.tan(dec) * np.cos(LAT)) + np.pi
    up = cz > 0.02
    return zen[up], az[up]


def beam_factor(slope, aspect, sun):
    """Daily sum of cos(incidence)+ on the slope / daily sum of cos(zenith) on flat ground."""
    zen, az = sun
    num = np.zeros_like(slope)
    for z, a in zip(zen, az):
        num += np.maximum(np.cos(z) * np.cos(slope) + np.sin(z) * np.sin(slope) * np.cos(a - aspect), 0)
    return num / np.cos(zen).sum()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('ds_param')
    p.add_argument('mask')
    p.add_argument('out_dir')
    p.add_argument('--scenarios', nargs='+')
    p.add_argument('--n-sample', type=int, default=6_000_000)
    p.add_argument('--tag', default='')
    a = p.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    ds = xr.open_zarr(a.ds_param)
    nx = ds.sizes['x']
    with rasterio.open(a.mask) as r:
        m = r.read(1).ravel() == 1
    z = ds.elevation.transpose('y', 'x').values.ravel()
    valid = np.flatnonzero(m & np.isfinite(z))
    rng = np.random.default_rng(42)
    pick = np.sort(rng.choice(valid, min(a.n_sample, len(valid)), replace=False))
    frac = len(pick) / len(valid)
    iy, ix = np.divmod(pick, nx)
    df = pd.DataFrame({'x': ds.x.values[ix], 'y': ds.y.values[iy]})
    for f in ['elevation', 'slope', 'aspect', 'aspect_cos', 'aspect_sin', 'svf']:
        df[f] = ds[f].transpose('y', 'x').values.ravel()[pick]
    del z
    print(f'{len(valid):,} px in mask, sample {len(df):,} ({frac:.1%}), load {time.time() - t0:.0f} s', flush=True)

    suns = {'dec': sun_positions(355), 'mar': sun_positions(79)}
    f_pix = {s: beam_factor(df.slope.values, df.aspect.values, sun) for s, sun in suns.items()}
    band = pd.cut(df.elevation, BANDS, right=False)
    band_idx = {f'{int(b.left)}-{int(b.right)}': i.values
                for b, i in pd.Series(np.arange(len(df))).groupby(band.values, observed=True)}
    steep = np.degrees(df.slope.values) > 10

    names = a.scenarios or list(SCEN)
    rows = []
    for name in names:
        k, edges, coef, feat = SCEN[name]
        t1 = time.time()
        cols = list(feat)
        if edges is None:
            group = np.zeros(len(df), int)
            w = np.array([1.0])
        else:
            group = np.digitize(df.elevation.values, edges[1:])
            w = group_weights(np.bincount(group, minlength=len(edges)), coef)
        lab = np.empty(len(df), int)
        cen, off = [], 0
        for g in range(len(w)):
            sel = group == g
            kg = max(2, int(round(np.ceil(k * w[g]) * frac)))
            sc = StandardScaler()
            X = sc.fit_transform(df.loc[sel, cols].values) * np.array([feat[c] for c in cols])
            km = cluster.MiniBatchKMeans(n_clusters=kg, batch_size=256 * CORES, random_state=SEED).fit(X)
            lab[sel] = km.labels_ + off
            cen.append(sc.inverse_transform(km.cluster_centers_ / np.array([feat[c] for c in cols])))
            off += kg
        C = pd.DataFrame(np.vstack(cen), columns=cols)
        C['aspect_circ'] = np.mod(np.arctan2(C.aspect_sin, C.aspect_cos), 2 * np.pi)
        C['aspect_fork'] = df.groupby(lab).aspect.mean().reindex(C.index).values
        c = C.iloc[lab]
        dz = df.elevation.values - c.elevation.values
        dslope = np.degrees(df.slope.values - c.slope.values)
        dsvf = df.svf.values - c.svf.values
        dasp = np.degrees(np.abs(np.angle(np.exp(1j * (df.aspect.values - c.aspect_circ.values)))))
        dist = np.hypot(df.x.values - c.x.values, df.y.values - c.y.values) / 1000
        drad = {}
        for s, sun in suns.items():
            for kind in ('circ', 'fork'):
                fc = beam_factor(C.slope.values, C[f'aspect_{kind}'].values, sun)
                drad[f'{s}_{kind}'] = 100 * (f_pix[s] - fc[lab])

        rec = dict(scenario=name, k=k, k_real=int(sum(np.ceil(k * w))), groups=edges, coef=coef,
                   weights=[float(x) for x in w], features=feat)
        for tag, i in band_idx.items():
            rec[f'elev_rmse_{tag}'] = np.sqrt(np.mean(dz[i] ** 2))
            rec[f'elev_bias_{tag}'] = np.mean(dz[i])
            rec[f'slope_rmse_{tag}'] = np.sqrt(np.mean(dslope[i] ** 2))
            rec[f'svf_rmse_{tag}'] = np.sqrt(np.mean(dsvf[i] ** 2))
            rec[f'aspect_err_{tag}'] = np.mean(dasp[i][steep[i]])
            for kk, v in drad.items():
                rec[f'rad_{kk}_rmse_{tag}'] = np.sqrt(np.mean(v[i] ** 2))
            rec[f'dist_km_{tag}'] = np.mean(dist[i])
            lo, hi = map(float, tag.split('-'))
            ncl = int(round(((C.elevation >= lo) & (C.elevation < hi)).sum() / frac))
            rec[f'clusters_{tag}'] = ncl
            rec[f'km2_per_cluster_{tag}'] = len(i) / frac * 625e-6 / max(ncl, 1)
        rec['elev_rmse_all'] = np.sqrt(np.mean(dz ** 2))
        rec['slope_rmse_all'] = np.sqrt(np.mean(dslope ** 2))
        rec['rad_dec_circ_rmse_all'] = np.sqrt(np.mean(drad['dec_circ'] ** 2))
        rec['rad_dec_fork_rmse_all'] = np.sqrt(np.mean(drad['dec_fork'] ** 2))
        rec['dist_km_all'] = np.mean(dist)
        zc = c.elevation.values
        for e in [800, 1500, 2000, 2500]:
            below = (df.elevation.values >= e - 25) & (df.elevation.values < e)
            above = (df.elevation.values >= e) & (df.elevation.values < e + 25)
            rec[f'jump_{e}'] = zc[above].mean() - zc[below].mean()
        rec['fit_s'] = round(time.time() - t1, 1)
        rows.append(rec)
        print(f"{name}: k_real {rec['k_real']}, w {rec['weights']}, elev RMSE "
              + ' / '.join(f"{rec.get(f'elev_rmse_{t}', np.nan):.1f}" for t in band_idx)
              + f", Dec rad RMSE circ/fork {rec['rad_dec_circ_rmse_all']:.1f}/{rec['rad_dec_fork_rmse_all']:.1f} %"
              + f", {rec['fit_s']} s", flush=True)

    pd.DataFrame(rows).to_csv(out / f'results{a.tag}.csv', index=False)
    with open(out / f'summary{a.tag}.json', 'w') as fh:
        json.dump(dict(n_mask=int(len(valid)), sample_frac=frac, bands=BANDS, seed=SEED, results=rows), fh,
                  indent=1, default=float)
    print(f'---> {out}  ({time.time() - t0:.0f} s)', flush=True)


if __name__ == '__main__':
    main()
