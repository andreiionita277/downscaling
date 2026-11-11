"""
Run SURFEX-Crocus (snowtools s2m) on every point of a FORCING file, in parallel: the points are split into chunks, each
chunk is a separate s2m run in its own folder, N chunks run at the same time (the SURFEX build is NOMPI = one process).
Each chunk's forcing keeps the original point order; points.csv of each chunk lists the original point index and
point_ind (cluster). A chunk is OK when its log says "OFFLINE ENDS CORRECTLY" and its PRO file exists.

    python meridionali_zarr/scripts/crocus_parallel.py meridionali_zarr/data/surfex_E/FORCING_2024090100_2025083123.nc \
           --out meridionali_zarr/data/crocus_E/2024-2025 --chunks 12 --parallel 12
Needs: conda env surfex (python with snowtools' dependencies), PYTHONPATH with ~/git/snowtools, SURFEX in ~/git/surfex/exe
(short links PGD/PREP/OFFLINE/SODA; MY_RUN next to it). Same s2m options as the 12-station test of 9 Nov 2026
(default OPTIONS_V8.1_NEW_OUTPUTS_NC.nam, -g: initial ground temperature from the period's mean air temperature).
Output: <out>/chunk_NN/output/pro/PRO_*.nc, <out>/chunk_NN/s2m.log, <out>/chunk_NN/points.csv, <out>/run_summary.csv
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

PY = str(Path.home() / 'miniconda3' / 'envs' / 'surfex' / 'bin' / 'python')
SNOWTOOLS = str(Path.home() / 'git' / 'snowtools')
EXE = str(Path.home() / 'git' / 'surfex' / 'exe')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('forcing')
    p.add_argument('--out', required=True)
    p.add_argument('--chunks', type=int, default=12)
    p.add_argument('--parallel', type=int, default=12)
    a = p.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    f = xr.open_dataset(a.forcing)
    t = pd.DatetimeIndex(f.time.values)
    b, e = f'{t[0]:%Y%m%d%H}', f'{t[-1]:%Y%m%d%H}'
    n = f.sizes['Number_of_points']
    parts = np.array_split(np.arange(n), a.chunks)
    print(f'{n} points, {len(t)} h ({b} .. {e}) -> {a.chunks} chunks, {a.parallel} at a time -> {out}', flush=True)
    jobs = []
    for k, idx in enumerate(parts):
        d = out / f'chunk_{k:02d}'
        d.mkdir(exist_ok=True)
        ff = d / Path(a.forcing).name
        if not ff.exists():
            f.isel(Number_of_points=idx).to_netcdf(ff, format='NETCDF3_CLASSIC', unlimited_dims=['time'])
        pd.DataFrame({'point_in_chunk': range(len(idx)), 'point': idx,
                      'point_ind': f.point_ind.values[idx]}).to_csv(d / 'points.csv', index=False)
        jobs.append((k, d, ff))
    print(f'{time.strftime("%H:%M:%S")} chunk forcing files written', flush=True)

    env = dict(os.environ, PYTHONPATH=SNOWTOOLS)
    env.pop('LD_LIBRARY_PATH', None)
    running, done, t0 = {}, [], time.time()
    queue = [j for j in jobs if not ok(j[1])]
    for j in jobs:
        if ok(j[1]):
            print(f'chunk {j[0]:02d}: already done', flush=True)
    while queue or running:
        while queue and len(running) < a.parallel:
            k, d, ff = queue.pop(0)
            for sub in ('output', 'work'):
                subprocess.run(['rm', '-rf', str(d / sub)])
            cmd = [PY, '-m', 'snowtools.tasks.s2m_command', 'research', '-b', b, '-e', e, '-f', str(ff),
                   '-o', str(d / 'output'), '-w', str(d / 'work'), '-s', EXE, '-g']
            running[k] = (subprocess.Popen(cmd, cwd=d, env=env, stdout=open(d / 's2m.log', 'w'),
                                           stderr=subprocess.STDOUT), d, time.time())
        time.sleep(15)
        for k in list(running):
            pr, d, ts = running[k]
            if pr.poll() is not None:
                del running[k]
                good = pr.returncode == 0 and ok(d)
                done.append(dict(chunk=k, points=len(pd.read_csv(d / 'points.csv')), exit=pr.returncode, ok=good,
                                 minutes=round((time.time() - ts) / 60, 1)))
                print(f'{time.strftime("%H:%M:%S")} chunk {k:02d}: {"OK" if good else "FAILED"} (exit {pr.returncode}, '
                      f'{(time.time() - ts) / 60:.1f} min); {len(done)} of {len(queue) + len(running) + len(done)} done',
                      flush=True)
    s = pd.DataFrame(done).sort_values('chunk')
    s.to_csv(out / 'run_summary.csv', index=False)
    print(f'{time.strftime("%H:%M:%S")} finished in {(time.time() - t0) / 60:.1f} min: {int(s.ok.sum())} of {len(s)} '
          f'chunks OK', flush=True)
    sys.exit(0 if s.ok.all() else 1)


def ok(d):
    log = d / 's2m.log'
    return log.exists() and 'OFFLINE ENDS CORRECTLY' in log.read_text(errors='replace') and \
        any((d / 'output' / 'pro').glob('PRO_*.nc'))


if __name__ == '__main__':
    main()
