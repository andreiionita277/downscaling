"""
Download the ERA5 of a config (Topoclass.get_era5 -> EDH -> climate.path/ERA5.zarr) without touching the
terrain steps, so it can run while the terrain stage of run_year_public.py is still computing.
Resumable: fetch_era5_edh reuses its finished blocks.

    python meridionali_zarr/scripts/fetch_era5_only.py meridionali_zarr/config/config_meridionali_2025.yml
"""
import sys
import time

from TopoPyScale import topoclass as tc

if __name__ == '__main__':
    t0 = time.time()
    mp = tc.Topoclass(sys.argv[1])
    mp.get_era5()
    print(f'get_era5 done in {time.time() - t0:.0f} s', flush=True)
