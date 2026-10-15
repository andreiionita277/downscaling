"""
Horizon angles computed in batches of azimuths and written to da_horizon.nc as they finish.

Same result as TopoPyScale.topo_param.compute_horizon (same azimuths, same topocalc call, same
pi/2 - arccos conversion, same coordinates from topo_param.open_dem), but that function keeps every
azimuth in memory twice in float64 (~40 GB for 46 M px x 36 azimuths). Here at most `batch` azimuths are
in memory, values are stored as float32 (angles in radians; float32 resolution ~1e-7 rad), and a sidecar
file (da_horizon.nc.done.json) records which azimuths are finished, so an interrupted run resumes.

Parallel with PROCESSES, not threads: topocalc's horizon holds the GIL, so a thread pool (as in TopoPyScale)
runs one azimuth at a time (measured: 12 azimuths serial 3.2 s, 6 threads 3.4 s, 6 processes 0.7 s).
Workers are forked after the DEM is loaded, so they share it (copy-on-write) instead of receiving a copy.

Topoclass.compute_horizon() then finds the file, opens it (lazily) and samples it at the cluster centres
as usual.
"""
import json
import time
import multiprocessing as mproc
from pathlib import Path

import netCDF4
import numpy as np
from topocalc import horizon

from TopoPyScale import topo_param as tp

_DEM = {}   # set before forking the workers; read by _one


def _one(az):
    return horizon.horizon(az, _DEM['elev'], _DEM['dx'])


def compute_horizon_batched(dem_file, azimuth_inc, fname, batch=8, n_procs=8):
    fname = Path(fname)
    ds = tp.open_dem(dem_file)
    dx = ds.x.diff('x').median().values
    azimuth = np.arange(-180 + azimuth_inc / 2, 180, azimuth_inc)   # as topo_param.compute_horizon
    _DEM['elev'], _DEM['dx'] = ds.elevation.values, dx
    part = fname.with_suffix('.nc.part')
    done_file = fname.with_suffix('.nc.done.json')

    if not part.exists():
        done_file.unlink(missing_ok=True)
        part.parent.mkdir(parents=True, exist_ok=True)
        with netCDF4.Dataset(part, 'w') as nc:
            nc.createDimension('azimuth', len(azimuth))
            nc.createDimension('y', ds.sizes['y'])
            nc.createDimension('x', ds.sizes['x'])
            for name, vals in (('azimuth', azimuth), ('y', ds.y.values), ('x', ds.x.values)):
                v = nc.createVariable(name, 'f8', (name,))
                v[:] = vals
            nc['x'].units = nc['y'].units = 'm'
            h = nc.createVariable('horizon', 'f4', ('azimuth', 'y', 'x'), zlib=True, complevel=1,
                                  chunksizes=(1, min(512, ds.sizes['y']), min(512, ds.sizes['x'])))
            h.long_name = 'Horizon angles'
            h.units = 'degree'          # sic: same attribute as TopoPyScale (values are radians)

    with netCDF4.Dataset(part, 'a') as nc:
        done = set(json.loads(done_file.read_text())) if done_file.exists() else set()
        todo = [i for i in range(len(azimuth)) if i not in done]
        print(f'---> Horizon, {azimuth_inc} deg increments: {len(azimuth) - len(todo)}/{len(azimuth)} azimuths '
              f'already done, batches of {batch}', flush=True)
        for b0 in range(0, len(todo), batch):
            sel = todo[b0:b0 + batch]
            t0 = time.time()
            with mproc.get_context('fork').Pool(min(n_procs, len(sel))) as pool:
                res = pool.map(_one, [azimuth[i] for i in sel])
            for i, a in zip(sel, res):
                nc['horizon'][i, :, :] = (np.pi / 2 - np.arccos(a)).astype('float32')
            nc.sync()
            done.update(sel)
            done_file.write_text(json.dumps(sorted(done)))
            del res
            print(f'     azimuths {[float(azimuth[i]) for i in sel]} done in {time.time() - t0:.0f} s', flush=True)

    # complete: rename to the name Topoclass looks for
    part.rename(fname)
    done_file.unlink()
    return fname
