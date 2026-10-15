"""
Daily statistics and 25 m GeoTIFF maps from a topo_scale_zarr run.

    python bulgaria_rila_pirin/scripts/export_geotiff.py <config.yml> [--var t] [--stats mean min max]
                                                         [--epsg 7801] [--overwrite]

Steps:
    1. Daily statistics of the hourly output store (TopoPyScale.topo_daily.daily_stats, UTC days)
       -> outputs/downscaled/daily.zarr  (skipped when it exists)
    2. Map each daily field back to the DEM grid the way TopoPyScale does it
       (topo_plot.map_variable: ds[var].sel(point_name=ds_param.point_name)): every pixel takes the
       value of its own TopoSUB cluster, read from the cluster map in outputs/ds_param.nc.
       Pixels outside the clustering mask (point_name '-9999') are nodata. No other processing
       (no per-pixel lapse-rate correction); temperature is only converted from K to degC.
    3. Write one Cloud-Optimised GeoTIFF per statistic and month, one band per day (band
       description = date), on the DEM grid and CRS (EPSG:32634 for Rila-Pirin)
       -> outputs/geotiff/<var>_<stat>_daily_<YYYY-MM>.tif
       --epsg N additionally writes a reprojected copy (bilinear, same 25 m resolution)
       -> outputs/geotiff_epsg<N>/...

Each file is written band by band to a temporary tiled GeoTIFF and then converted to COG, so
memory stays at about one 25 m field per band. Existing files are skipped (rerun = resume).
"""
import argparse
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import rasterio.shutil
import xarray as xr
import yaml
from rasterio.warp import Resampling, calculate_default_transform, reproject

from TopoPyScale import topo_daily
from TopoPyScale import topo_scale_zarr as tz

NODATA = -9999.0
COG_OPTIONS = dict(driver='COG', compress='DEFLATE', predictor='YES', blocksize=512, bigtiff='IF_SAFER',
                   overviews='AUTO', resampling='AVERAGE', num_threads='ALL_CPUS')
UNITS = {'t': 'degC'}


def load_config(path):
    cfg = yaml.safe_load(Path(path).read_text())
    out = Path(cfg['project']['directory']) / cfg['outputs'].get('directory', 'outputs')
    dem = Path(cfg['dem']['path']) / cfg['dem']['file']
    return cfg, out, dem


def daily_store(cfg, out):
    """Daily statistics of the hourly store, computed once and cached as daily.zarr."""
    path = out / 'downscaled' / 'daily.zarr'
    if path.exists():
        print(f'---> {path} exists, reused', flush=True)
        return xr.open_zarr(path)
    hourly = tz.open_downscaled_store(out / 'downscaled' / cfg['outputs']['file']['zarr_store'])
    hourly = hourly.sel(time=slice(str(cfg['project']['start']), f"{cfg['project']['end']}T23:59")).load()
    daily = topo_daily.daily_stats(hourly, utc_offset_hours=0)
    daily.attrs.update(source_config=str(cfg['project']['name']))
    for v in daily.variables:            # string coords: keep a plain fixed-width dtype for Zarr
        if daily[v].dtype.kind in 'OUT':
            daily[v] = daily[v].astype(str)
    daily.to_zarr(path, mode='w')
    print(f'---> daily statistics written to {path} ({daily.sizes["time"]} days)', flush=True)
    return xr.open_zarr(path)


def pixel_index(out, dem, point_names):
    """Index into the point_name axis for every DEM pixel (-1 outside the clustering mask).

    Same lookup as ds[var].sel(point_name=ds_param.point_name) in topo_plot.map_variable,
    done once as integer positions so each band is a single numpy take.
    """
    ds_param = xr.open_dataset(out / 'ds_param.nc')
    pn = ds_param.point_name.transpose('y', 'x').values.astype(str)
    with rasterio.open(dem) as r:
        profile = r.profile
        if (r.height, r.width) != pn.shape:
            raise ValueError(f'ds_param grid {pn.shape} != DEM {(r.height, r.width)}')
        x0, y0 = r.xy(0, 0)
        if not (np.isclose(ds_param.x.values[0], x0) and np.isclose(ds_param.y.values[0], y0)):
            raise ValueError('ds_param coordinates are not the DEM pixel centres')
    idx = pd.Index(point_names).get_indexer(pn.ravel()).reshape(pn.shape)
    outside = pn == '-9999'
    if (idx[~outside] < 0).any():
        raise ValueError(f'{int((idx[~outside] < 0).sum())} clustered pixels have no downscaled point')
    print(f'---> pixel map: {int((~outside).sum()):,} pixels in {len(np.unique(pn[~outside]))} clusters, '
          f'{int(outside.sum()):,} nodata', flush=True)
    return idx, profile


def to_cog(tmp, dst, epsg=None):
    """Convert a temporary GeoTIFF to COG, optionally reprojected to EPSG:epsg at the same resolution."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix('.tif.part')
    if epsg is None:
        rasterio.shutil.copy(tmp, part, **COG_OPTIONS)
    else:
        with rasterio.open(tmp) as src:
            crs = rasterio.crs.CRS.from_epsg(epsg)
            transform, w, h = calculate_default_transform(src.crs, crs, src.width, src.height, *src.bounds,
                                                          resolution=src.res)
            prof = src.profile | dict(crs=crs, transform=transform, width=w, height=h)
            warped = Path(str(tmp) + f'.{epsg}.tif')
            with rasterio.open(warped, 'w', **prof) as wdst:
                for b in range(1, src.count + 1):
                    reproject(rasterio.band(src, b), rasterio.band(wdst, b), resampling=Resampling.bilinear,
                              src_nodata=NODATA, dst_nodata=NODATA)
                    wdst.set_band_description(b, src.descriptions[b - 1])
                wdst.update_tags(**src.tags())
                for b in range(1, src.count + 1):
                    wdst.update_tags(b, **src.tags(b))
        rasterio.shutil.copy(warped, part, **COG_OPTIONS)
        warped.unlink()
    part.rename(dst)


def export(cfg, out, dem, var, stats, epsg, overwrite):
    daily = daily_store(cfg, out)
    names = [f'{var}_{s}' for s in stats]
    missing = [n for n in names if n not in daily]
    if missing:
        raise ValueError(f'{missing} not in daily store; available: {list(daily.data_vars)}')
    idx, profile = pixel_index(out, dem, daily.point_name.values.astype(str))
    inside = idx >= 0
    safe = np.where(inside, idx, 0)
    profile = profile | dict(driver='GTiff', dtype='float32', nodata=NODATA, count=1, tiled=True,
                             blockxsize=512, blockysize=512, compress='DEFLATE', BIGTIFF='IF_SAFER')
    sampling = cfg['sampling']['toposub']
    tmpdir = Path(tempfile.mkdtemp(dir=out))
    try:
        for name, stat in zip(names, stats):
            da = daily[name].load()
            for month, days in da.groupby('time.month'):
                ym = pd.Timestamp(days.time.values[0]).strftime('%Y-%m')
                fname = f'{var}_{stat}_daily_{ym}.tif'
                dst = out / 'geotiff' / fname
                dst_proj = out / f'geotiff_epsg{epsg}' / fname if epsg else None
                if not overwrite and dst.exists() and (dst_proj is None or dst_proj.exists()):
                    print(f'     {fname} exists, skipped', flush=True)
                    continue
                vals = days.transpose('time', 'point_name').values
                if var == 't':
                    vals = vals - 273.15
                tmp = tmpdir / fname
                with rasterio.open(tmp, 'w', **(profile | dict(count=len(days.time)))) as f:
                    for b, row in enumerate(vals, start=1):
                        band = np.where(inside, row[safe], NODATA).astype('float32')
                        band[inside & np.isnan(band)] = NODATA       # incomplete days are NaN in topo_daily
                        f.write(band, b)
                        f.set_band_description(b, str(pd.Timestamp(days.time.values[b - 1]).date()))
                    f.update_tags(variable=var, statistic=f'daily {stat}', units=UNITS.get(var, days.attrs.get('units', '')),
                                  day_definition='UTC calendar day (00-23 UTC), hourly values',
                                  method='TopoPyScale TopoSUB+TopoSCALE; each pixel = value of its cluster '
                                         '(ds_param.point_name), as topo_plot.map_variable',
                                  n_clusters=str(sampling['n_clusters']),
                                  clustering_features=str(sampling['clustering_features']),
                                  era5_source=str(cfg['climate']['era5'].get('data_repository', 'cds')),
                                  run=str(cfg['project']['name']), nodata_meaning='outside clustering mask')
                if overwrite or not dst.exists():
                    to_cog(tmp, dst)
                if dst_proj is not None and (overwrite or not dst_proj.exists()):
                    to_cog(tmp, dst_proj, epsg)
                tmp.unlink()
                print(f'---> {fname} ({len(days.time)} bands)', flush=True)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--var', default='t')
    p.add_argument('--stats', nargs='+', default=['mean', 'min', 'max'])
    p.add_argument('--epsg', type=int, default=None, help='also write a reprojected copy, e.g. 7801 (BGS2005 / CCS2005)')
    p.add_argument('--overwrite', action='store_true')
    a = p.parse_args()
    cfg, out, dem = load_config(a.config)
    export(cfg, out, dem, a.var, a.stats, a.epsg, a.overwrite)


if __name__ == '__main__':
    main()
