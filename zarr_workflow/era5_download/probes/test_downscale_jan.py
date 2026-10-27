"""End-to-end check: downscale January 2025 for all 500 clusters with topo_scale_zarr from the 37-level store,
same settings as zarr_workflow/config/config_2025.yml, and compare with the existing EDH (5-level) run.
Output: data/era5_37/downscale_test/down_jan.zarr + compare_downscaled_jan.json"""
import json
import time

import numpy as np
import pandas as pd
import xarray as xr
from TopoPyScale import topo_scale_zarr as tz


# multiprocessing uses forkserver (Python 3.13): everything must sit under the __main__ guard
def main():
    D = '/app/zarr_workflow/data'
    OUT = f'{D}/era5_37/downscale_test'
    dfc = pd.read_pickle(f'{D}/outputs/df_centroids.pck')
    ds_solar = xr.open_zarr(f'{D}/outputs/ds_solar.zarr')
    t0 = time.time()
    cda = tz.ClimateDownscaler(era5_zarr_path=f'{D}/era5_37/ERA5.zarr', output_path=OUT, df_centroids=dfc,
                               da_horizon=None, ds_solar=ds_solar, target_EPSG=32634,
                               start_date='2025-01-01', end_date='2025-01-31', tstep='1h', varout=tz.varout_default,
                               interp_method='idw', lw_terrain_flag=True, precip_lapse_rate_flag=True,
                               file_pattern=None, store_name='down_jan.zarr', overwrite=True)
    cda.multicore_parallel_process_multiple_subsets(n_core=10)
    dt = time.time() - t0
    print(f'downscaling 500 clusters x 744 h: {dt:.0f}s')

    new = xr.open_zarr(f'{OUT}/down_jan.zarr').load()
    old = xr.open_zarr(f'{D}/outputs/downscaled/down.zarr').sel(time=new.time).load()
    elev = dfc.set_index('point_ind').loc[new.point_ind.values, 'elevation'].values
    res = {'downscale_seconds': round(dt), 'n_points': int(new.sizes['point_ind'])}
    for v in ['t', 'p', 'q', 'LW', 'SW', 'tp', 'ws']:
        d = (new[v] - old[v]).values                      # 37-level minus 5-level (EDH)
        r = {'nan_new': int(np.isnan(new[v].values).sum()), 'mean_diff': float(np.nanmean(d)),
             'mean_abs_diff': float(np.nanmean(np.abs(d))), 'p99_abs_diff': float(np.nanpercentile(np.abs(d), 99)),
             'max_abs_diff': float(np.nanmax(np.abs(d)))}
        if v == 't':
            pm = np.nanmean(d, axis=1)                    # mean diff per point
            for lo, hi in [(0, 1000), (1000, 1500), (1500, 2000), (2000, 3000)]:
                m = (elev >= lo) & (elev < hi)
                r[f'mean_diff_{lo}-{hi}m'] = float(pm[m].mean()) if m.any() else None
            r['abs_diff_hourly_gt_1K_share'] = float((np.abs(d) > 1).mean())
        res[v] = r
        print(v, r)
    json.dump(res, open(f'{D}/era5_37/probe/compare_downscaled_jan.json', 'w'), indent=1)


if __name__ == '__main__':
    main()
