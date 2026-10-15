"""
Run the TopoPyScale pipeline with ERA5 from Earth Data Hub and the Zarr downscaler.

    python bulgaria_rila_pirin/scripts/run_pipeline_zarr.py <config.yml> [--skip-fetch]

Steps (each one is skipped when its output file already exists, so a rerun resumes):
    1. compute_dem_param     terrain parameters          -> outputs/ds_param.nc
    2. extract_topo_param    TopoSUB clustering          -> outputs/df_centroids.pck
    3. compute_solar_geometry                            -> outputs/ds_solar.nc
    4. compute_horizon       horizon angles              -> outputs/da_horizon.nc
    5. get_era5              EDH fetch (resumable)       -> climate.path/ERA5.zarr
    6. downscale_climate     topo_scale_zarr (resumable) -> outputs/downscaled/<zarr_store>
Timings of every step are appended to outputs/timings.json.
"""
import argparse
import json
import time
from pathlib import Path

from TopoPyScale import topoclass as tc


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--skip-fetch', action='store_true', help='ERA5 already on disk (e.g. converted CDS data)')
    a = p.parse_args()

    t_all = time.time()
    timings = {}


    def step(name, fun):
        t = time.time()
        print(f'\n========== {name} ==========', flush=True)
        fun()
        timings[name] = round(time.time() - t, 1)
        print(f'========== {name} done in {timings[name]} s', flush=True)


    mp = tc.Topoclass(a.config)
    step('compute_dem_param', mp.compute_dem_param)
    step('extract_topo_param', mp.extract_topo_param)
    step('compute_solar_geometry', mp.compute_solar_geometry)
    step('compute_horizon', mp.compute_horizon)
    if not a.skip_fetch:
        step('get_era5', mp.get_era5)
    step('downscale_climate', mp.downscale_climate)
    timings['total'] = round(time.time() - t_all, 1)

    f = Path(mp.config.outputs.path) / 'timings.json'
    hist = json.loads(f.read_text()) if f.exists() else []
    hist.append({'config': str(a.config), 'n_points': int(len(mp.toposub.df_centroids)),
                 'start': str(mp.config.project.start), 'end': str(mp.config.project.end), 'timings_s': timings})
    f.write_text(json.dumps(hist, indent=2))
    print(json.dumps(timings, indent=2))


# The guard is required: multiprocessing workers (forkserver/spawn) may import this file.
if __name__ == '__main__':
    main()
