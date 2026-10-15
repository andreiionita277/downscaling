"""
Validate downscaled air temperature against the METER.AC stations, for several runs at once.

    python bulgaria_rila_pirin/scripts/validate_stations.py --runs LABEL=config.yml [LABEL=config.yml ...]
                                                            [--year 2025] [--out /data/runs/validation_2025]

What is compared:
    - clustered runs (sampling: toposub): the value of the TopoSUB cluster the station's 25 m pixel
      belongs to, i.e. exactly what the exported maps show at the station
    - point runs (sampling: points): the series downscaled at the station's own terrain (no
      clustering), the reference for how good TopoSCALE itself is at each station
Observations: stations_hourly_full_history.csv, hours with coverage 'complete' only (>= 10 of 12
5-min readings). Station hours are left-labelled means (10:00 = 10:00-10:55), so the model value
for an hour is the mean of its instantaneous values at HH:00 and HH+1:00.
Daily statistics: UTC days with >= 20 complete hours; model and station use the same hours.
Stations need >= MIN_HOURS complete hours in the year.

Metrics (per station and run): n, bias (model - obs), MAE, RMSE, Pearson r for hourly t and
daily mean/min/max. Summaries: mean over stations, by elevation group, bias-vs-elevation slope,
and paired run-vs-run differences of per-station RMSE with a bootstrap 95 % CI over stations.

Outputs in --out: station_metrics.csv, summary.json, fig_station_validation.png/.pdf, run_info.txt
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
import yaml
from pyproj import Transformer

DATA = Path('/app/bulgaria_rila_pirin/data')
MIN_HOURS = 500            # ~3 weeks of complete hours; keeps Tevno ezero (2525 m, 674 h, Jun-Nov only)
MIN_HOURS_PER_DAY = 20
N_BOOT = 10000
ELEV_GROUPS = [(0, 1000, '< 1000 m'), (1000, 1500, '1000-1500 m'), (1500, 3000, '> 1500 m')]


def load_obs(year):
    h = pd.read_csv(DATA / 'stations_hourly_full_history.csv')
    h['time'] = pd.to_datetime(h.time_utc, utc=True).dt.tz_localize(None)
    h = h[(h.time.dt.year == year) & (h.coverage == 'complete')]
    meta = pd.read_csv(DATA / 'selected_stations_metadata.csv').rename(columns={'NodeID': 'node'})
    n = h.groupby('node').size()
    keep = n[n >= MIN_HOURS].index
    meta = meta[meta.node.isin(keep)].reset_index(drop=True)
    obs = h[h.node.isin(keep)].pivot(index='time', columns='node', values='temp_c_mean')
    return obs, meta


def model_series(cfg_path, meta):
    """Hourly model t (degC) at each station, as hour means aligned with the station hours."""
    cfg = yaml.safe_load(Path(cfg_path).read_text())
    out = Path(cfg['project']['directory']) / cfg['outputs'].get('directory', 'outputs')
    ds = xr.open_zarr(out / 'downscaled' / cfg['outputs']['file']['zarr_store'])
    names = ds.point_name.values.astype(str)
    if cfg['sampling']['method'] == 'points':
        col = {n: n for n in meta.node}
        kind = 'point'
    else:
        ds_param = xr.open_dataset(out / 'ds_param.nc')
        pn = ds_param.point_name.transpose('y', 'x').values.astype(str)
        with rasterio.open(Path(cfg['dem']['path']) / cfg['dem']['file']) as r:
            tr = Transformer.from_crs(4326, r.crs, always_xy=True)
            x, y = tr.transform(meta.Longitude.values, meta.Latitude.values)
            rows, cols = rasterio.transform.rowcol(r.transform, x, y)
        col = {n: pn[a, b] for n, a, b in zip(meta.node, rows, cols)}
        kind = 'cluster'
    idx = {n: int(np.flatnonzero(names == c)[0]) for n, c in col.items() if c in names}
    t = ds.t.isel(point_ind=list(idx.values())).transpose('time', 'point_ind').values - 273.15
    df = pd.DataFrame(t, index=pd.DatetimeIndex(ds.time.values), columns=list(idx))
    df = (df + df.shift(-1)) / 2            # mean of HH:00 and HH+1:00 = the hour HH:00-HH:59
    centroid_elev = None
    if kind == 'cluster':
        c = pd.read_pickle(out / cfg['outputs']['file']['df_centroids']).set_index('point_name')
        centroid_elev = {n: float(c.elevation.get(col[n], np.nan)) for n in idx}
    return df, kind, centroid_elev, cfg


def scores(m, o):
    ok = m.notna() & o.notna()
    m, o = m[ok], o[ok]
    if len(m) < 3:
        return dict(n=int(len(m)), bias=np.nan, mae=np.nan, rmse=np.nan, r=np.nan)
    d = m - o
    return dict(n=int(len(m)), bias=float(d.mean()), mae=float(d.abs().mean()),
                rmse=float(np.sqrt((d ** 2).mean())), r=float(np.corrcoef(m, o)[0, 1]))


def daily(m, o):
    ok = m.notna() & o.notna()
    m, o = m.where(ok), o.where(ok)
    day = m.index.floor('D')
    cnt = ok.groupby(day).sum()
    good = cnt[cnt >= MIN_HOURS_PER_DAY].index
    res = {}
    for stat in ('mean', 'min', 'max'):
        res[stat] = (getattr(m.groupby(day), stat)().loc[good], getattr(o.groupby(day), stat)().loc[good])
    return res


def boot_ci(x, rng):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    b = rng.choice(x, (N_BOOT, len(x)), replace=True).mean(1)
    return float(x.mean()), float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--runs', nargs='+', required=True, help='LABEL=config.yml')
    p.add_argument('--year', type=int, default=2025)
    p.add_argument('--out', default='/data/runs/validation_2025')
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    obs, meta = load_obs(a.year)
    print(f'{len(meta)} stations with >= {MIN_HOURS} complete hours in {a.year}', flush=True)

    rows, kinds = [], {}
    for spec in a.runs:
        label, cfg_path = spec.split('=', 1)
        mod, kind, celev, _ = model_series(cfg_path, meta)
        kinds[label] = kind
        for _, st in meta.iterrows():
            n = st.node
            if n not in mod:
                continue
            m = mod[n].reindex(obs.index)
            o = obs[n]
            r = dict(run=label, kind=kind, node=n, station=st.Location, altitude=st.Altitude,
                     centroid_elev=(celev or {}).get(n, np.nan))
            r.update({f'hourly_{k}': v for k, v in scores(m, o).items()})
            for stat, (md, od) in daily(m, o).items():
                r.update({f'daily_{stat}_{k}': v for k, v in scores(md, od).items()})
            rows.append(r)
        print(f'  {label}: {kind} run, {sum(1 for r in rows if r["run"] == label)} stations', flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(out / 'station_metrics.csv', index=False)

    rng = np.random.default_rng(0)
    labels = list(dict.fromkeys(df.run))
    metrics = ['hourly_bias', 'hourly_rmse', 'hourly_r', 'daily_mean_bias', 'daily_mean_rmse',
               'daily_min_bias', 'daily_min_rmse', 'daily_max_bias', 'daily_max_rmse']
    summary = {'stations': meta[['node', 'Location', 'Altitude']].to_dict('records'), 'runs': {}, 'paired': {}}
    for lab in labels:
        g = df[df.run == lab]
        s = {m: round(float(g[m].mean()), 3) for m in metrics}
        s['by_elevation'] = {name: {m: round(float(g[(g.altitude >= lo) & (g.altitude < hi)][m].mean()), 3)
                                    for m in ('hourly_bias', 'hourly_rmse', 'daily_mean_bias')}
                             | {'n_stations': int(((g.altitude >= lo) & (g.altitude < hi)).sum())}
                             for lo, hi, name in ELEV_GROUPS}
        slope = np.polyfit(g.altitude / 1000, g.hourly_bias, 1)[0]
        s['bias_vs_elevation_K_per_km'] = round(float(slope), 3)
        summary['runs'][lab] = s
    for i, l1 in enumerate(labels):
        for l2 in labels[i + 1:]:
            w = df.pivot(index='node', columns='run', values='hourly_rmse')[[l1, l2]].dropna()
            d = (w[l2] - w[l1]).values
            mean, lo, hi = boot_ci(d, rng)
            summary['paired'][f'{l2} - {l1}'] = dict(
                metric='hourly RMSE (K), per station', n_stations=int(len(d)), mean_diff=round(mean, 3),
                ci95=[round(lo, 3), round(hi, 3)], stations_better=int((d < 0).sum()), stations_worse=int((d > 0).sum()))
    (out / 'summary.json').write_text(json.dumps(summary, indent=2))
    (out / 'run_info.txt').write_text(
        f'year {a.year}; runs {a.runs}\nobs: complete hours (>=10/12 readings), stations with >= {MIN_HOURS} h; '
        f'model hour = mean(HH:00, HH+1:00); daily: days with >= {MIN_HOURS_PER_DAY} common hours; '
        f'bootstrap {N_BOOT} resamples over stations, seed 0\n')

    print('\nmean over stations (K):')
    print(pd.DataFrame({l: {m: summary['runs'][l][m] for m in metrics + ['bias_vs_elevation_K_per_km']}
                        for l in labels}).to_string())
    print('\nby elevation group (hourly bias / RMSE, K):')
    for l in labels:
        print(f'  {l:10s} ' + '  '.join(f"{k}: {v['hourly_bias']:+.2f} / {v['hourly_rmse']:.2f} (n={v['n_stations']})"
                                        for k, v in summary['runs'][l]['by_elevation'].items()))
    print('\npaired differences of per-station hourly RMSE (negative = second run better):')
    for k, v in summary['paired'].items():
        print(f"  {k:22s} {v['mean_diff']:+.3f} K  95% CI [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}]  "
              f"better at {v['stations_better']}, worse at {v['stations_worse']} of {v['n_stations']}")
    plot(df, labels, kinds, out)
    points = [l for l in labels if kinds[l] == 'point']
    if points:
        clustering_error(a.runs, points[0], out, rng)


def clustering_error(run_specs, point_label, out, rng):
    """Error added by clustering alone: clustered run minus the point run at the same station
    terrain, over the whole year, at every station of the point run (observations not needed)."""
    st = pd.read_csv('/app/bulgaria_rila_pirin/inputs/dem/station_list.csv').rename(
        columns={'stn_number': 'node', 'latitude': 'Latitude', 'longitude': 'Longitude', 'Name': 'Location',
                 'elevation_m': 'Altitude'})
    specs = dict(s.split('=', 1) for s in run_specs)
    ref, _, _, _ = model_series(specs[point_label], st)
    rows = []
    for lab, cfg in specs.items():
        if lab == point_label:
            continue
        mod, kind, celev, _ = model_series(cfg, st)
        for _, s in st.iterrows():
            if s.node not in mod or s.node not in ref:
                continue
            d = (mod[s.node] - ref[s.node]).dropna()
            rows.append(dict(run=lab, node=s.node, station=s.Location, altitude=s.Altitude,
                             centroid_elev=celev.get(s.node, np.nan), bias=float(d.mean()),
                             rmse=float(np.sqrt((d ** 2).mean()))))
    ce = pd.DataFrame(rows)
    ce.to_csv(out / 'clustering_error.csv', index=False)
    labs = list(dict.fromkeys(ce.run))
    res = {'definition': f'hourly t of the clustered run minus run "{point_label}" (station terrain), full year',
           'runs': {}, 'paired': {}}
    for lab in labs:
        g = ce[ce.run == lab]
        res['runs'][lab] = dict(n_stations=int(len(g)), mean_abs_bias=round(float(g.bias.abs().mean()), 3),
                                mean_rmse=round(float(g.rmse.mean()), 3),
                                by_elevation={name: dict(n=int(((g.altitude >= lo) & (g.altitude < hi)).sum()),
                                                         bias=round(float(g[(g.altitude >= lo) & (g.altitude < hi)].bias.mean()), 3),
                                                         rmse=round(float(g[(g.altitude >= lo) & (g.altitude < hi)].rmse.mean()), 3))
                                              for lo, hi, name in ELEV_GROUPS})
    w = ce.pivot(index='node', columns='run', values='rmse')
    for i, l1 in enumerate(labs):
        for l2 in labs[i + 1:]:
            d = (w[l2] - w[l1]).dropna().values
            mean, lo, hi = boot_ci(d, rng)
            res['paired'][f'{l2} - {l1}'] = dict(mean_diff=round(mean, 3), ci95=[round(lo, 3), round(hi, 3)],
                                                 stations_better=int((d < 0).sum()), stations_worse=int((d > 0).sum()),
                                                 n_stations=int(len(d)))
    (out / 'clustering_error.json').write_text(json.dumps(res, indent=2))
    print(f'\nclustering error = clustered run - "{point_label}" at the station terrain, all of 2025, '
          f'{ce.node.nunique()} stations (no observations needed):')
    for lab, v in res['runs'].items():
        print(f"  {lab:4s} mean RMSE {v['mean_rmse']:.2f} K, mean |bias| {v['mean_abs_bias']:.2f} K   " +
              '  '.join(f"{k}: bias {b['bias']:+.2f} / RMSE {b['rmse']:.2f} (n={b['n']})" for k, b in v['by_elevation'].items()))
    for k, v in res['paired'].items():
        print(f"  {k:8s} RMSE diff {v['mean_diff']:+.3f} K  95% CI [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}]  "
              f"better at {v['stations_better']}, worse at {v['stations_worse']} of {v['n_stations']}")
    hi_st = ce[ce.altitude >= 1900].pivot(index=['station', 'altitude'], columns='run', values=['bias', 'centroid_elev'])
    print('\nhigh stations (>= 1900 m): clustering bias (K) and centroid elevation (m)')
    print(hi_st.round(2).to_string())
    plot_clustering_error(ce, labs, res, point_label, out)


def plot_clustering_error(ce, labs, res, point_label, out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    INK, INK2, GRID = '#1f1f1e', '#5f5e58', '#e4e3dc'
    slots = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4']
    markers = ['o', 's', 'D', '^', 'v']
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.titlesize': 10, 'axes.titleweight': 'bold',
                         'axes.titlelocation': 'left', 'savefig.dpi': 300})
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.6), constrained_layout=True,
                             gridspec_kw=dict(width_ratios=[1.6, 1]))
    ax = axes[0]
    for i, lab in enumerate(labs):
        g = ce[ce.run == lab]
        ax.plot(g.altitude, g.bias, ls='none', marker=markers[i], ms=5, color=slots[i],
                markeredgecolor='white', markeredgewidth=0.8, label=lab)
    ax.axhline(0, color=INK2, lw=0.8)
    ax.set_xlabel('Station elevation (m)')
    ax.set_ylabel(f'Clustered − {point_label} (K)')
    ax.set_title('a  Bias added by clustering, per station')
    ax.legend(frameon=False, fontsize=8, loc='upper left')
    ax = axes[1]
    vals = [res['runs'][l]['mean_rmse'] for l in labs]
    ax.bar(range(len(labs)), vals, width=0.6, color=[slots[i] for i in range(len(labs))])
    for i, v in enumerate(vals):
        ax.text(i, v + 0.02, f'{v:.2f} K', ha='center', va='bottom', fontsize=8, color=INK)
    ax.set_xticks(range(len(labs)), labs)
    ax.set_ylabel('Mean RMSE over stations (K)')
    ax.set_title('b  Error added by clustering')
    ax.set_ylim(0, max(vals) * 1.2)
    for a in axes:
        a.spines[['top', 'right']].set_visible(False)
        a.grid(axis='y', color=GRID, lw=0.8)
        a.set_axisbelow(True)
    fig.text(0, -0.03, f'Hourly air temperature, all of 2025, {ce.node.nunique()} station locations: value of the TopoSUB '
             f'cluster containing the station pixel minus the same model run at the exact station terrain '
             f'("{point_label}").', fontsize=7.5, color=INK2)
    for ext in ('png', 'pdf'):
        fig.savefig(out / f'fig_clustering_error.{ext}', bbox_inches='tight', facecolor='white')
    plt.close(fig)


def plot(df, labels, kinds, out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    INK, INK2, GRID = '#1f1f1e', '#5f5e58', '#e4e3dc'
    slots = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4']
    markers = ['o', 's', 'D', '^', 'v']
    style, k = {}, 0
    for l in labels:
        if kinds[l] == 'point':
            style[l] = dict(color=INK2, marker='x', ls=':')
        else:
            style[l] = dict(color=slots[k], marker=markers[k], ls='-')
            k += 1
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.titlesize': 10, 'axes.titleweight': 'bold',
                         'axes.titlelocation': 'left', 'savefig.dpi': 300})
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.6), constrained_layout=True)
    for ax, col, ylab, title in [(axes[0], 'hourly_bias', 'Bias, model - station (K)', 'a  Hourly bias vs station elevation'),
                                 (axes[1], 'hourly_rmse', 'RMSE (K)', 'b  Hourly RMSE vs station elevation')]:
        for l in labels:
            g = df[df.run == l].sort_values('altitude')
            st = style[l]
            ax.plot(g.altitude, g[col], color=st['color'], ls='none', marker=st['marker'], ms=5,
                    markeredgecolor='white' if st['marker'] != 'x' else st['color'], markeredgewidth=0.8, label=l)
            if col == 'hourly_bias':
                c = np.polyfit(g.altitude, g[col], 1)
                xs = np.array([g.altitude.min(), g.altitude.max()])
                ax.plot(xs, np.polyval(c, xs), color=st['color'], ls=st['ls'], lw=1.5)
        if col == 'hourly_bias':
            ax.axhline(0, color=INK2, lw=0.8)
        ax.set_xlabel('Station elevation (m)')
        ax.set_ylabel(ylab)
        ax.set_title(title)
        ax.spines[['top', 'right']].set_visible(False)
        ax.grid(axis='y', color=GRID, lw=0.8)
        ax.set_axisbelow(True)
    axes[0].legend(frameon=False, fontsize=8)
    fig.text(0, -0.03, 'METER.AC stations, 2025, complete hours only. Clustered runs: value of the cluster containing the '
             'station pixel (as in the maps); points: downscaled at the station terrain.', fontsize=7.5, color=INK2)
    for ext in ('png', 'pdf'):
        fig.savefig(out / f'fig_station_validation.{ext}', bbox_inches='tight', facecolor='white')
    plt.close(fig)


if __name__ == '__main__':
    main()
