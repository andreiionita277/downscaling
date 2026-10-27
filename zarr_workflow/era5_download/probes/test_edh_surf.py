"""Does taking the single levels from EDH (rounded storage) instead of the CDS change the downscaling?
January 2025, 500 clusters: store A = 600-1000 hPa PLEV + CDS SURF (the run), store B = same PLEV + EDH SURF.
Output: data/era5_37/probe/compare_edh_surf_jan.json"""
import json
import time

import numpy as np
import pandas as pd
import xarray as xr
from TopoPyScale import topo_scale_zarr as tz

D = '/app/zarr_workflow/data'
SURF = ['z_surf', 'd2m', 'sp', 'strd', 'ssrd', 'tp', 't2m']


def main():
    T = pd.date_range('2025-01-01', '2025-01-31T23', freq='1h')
    a = xr.open_zarr(f'{D}/era5_600-1000hPa/ERA5.zarr').sel(time=T).load()
    edh = xr.open_zarr(f'{D}/era5/ERA5.zarr')[SURF].sel(time=T).load()
    b = a.copy()
    for v in SURF:
        b[v] = edh[v].astype('float32')
    for v in a.data_vars:
        b[v].encoding = {}
    hy = f'{D}/era5_37/probe/hybrid_edh_surf_jan.zarr'
    b.to_zarr(hy, mode='w', zarr_format=3, consolidated=True)
    dfc = pd.read_pickle(f'{D}/outputs/df_centroids.pck')
    ds_solar = xr.open_zarr(f'{D}/outputs/ds_solar.zarr')
    out = f'{D}/era5_37/probe/hybrid_down'
    t0 = time.time()
    cda = tz.ClimateDownscaler(era5_zarr_path=hy, output_path=out, df_centroids=dfc, da_horizon=None,
                               ds_solar=ds_solar, target_EPSG=32634, start_date='2025-01-01', end_date='2025-01-31',
                               tstep='1h', varout=tz.varout_default, interp_method='idw', lw_terrain_flag=True,
                               precip_lapse_rate_flag=True, file_pattern=None, store_name='down.zarr', overwrite=True)
    cda.multicore_parallel_process_multiple_subsets(n_core=10)
    new = xr.open_zarr(f'{out}/down.zarr').load()
    ref = xr.open_zarr(f'{D}/runs/2025_era5_600-1000hPa/outputs/downscaled/down.zarr').sel(time=new.time).load()
    res = {'seconds': round(time.time() - t0)}
    for v in ['t', 'p', 'q', 'LW', 'SW', 'tp', 'ws']:
        d = new[v].values - ref[v].values
        scale = float(np.nanmean(np.abs(ref[v].values)))
        res[v] = {'mean_diff': float(np.nanmean(d)), 'mean_abs_diff': float(np.nanmean(np.abs(d))),
                  'max_abs_diff': float(np.nanmax(np.abs(d))), 'mean_abs_ref': scale}
        print(v, res[v])
    json.dump(res, open(f'{D}/era5_37/probe/compare_edh_surf_jan.json', 'w'), indent=1)


if __name__ == '__main__':
    main()
