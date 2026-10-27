"""
Building blocks of the Rila-Pirin Zarr workflow (used by run_year.py and the viewer notebook).

All computations are TopoPyScale's own (Topoclass steps, topo_scale_zarr, topo_daily); this module
only organises them per month and adds the products and checks:

    month_periods        calendar months of the run period
    qa_hourly            completeness (no NaN) and loose physical bounds of one month of hourly output
    write_daily_month    daily statistics (topo_daily.daily_stats, UTC days) of one month, written into
                         its slice of a yearly daily.zarr (created on the first month)
    pixel_index          cluster map (ds_param point_name) -> position of each DEM pixel's cluster
    export_month         one Cloud-Optimised GeoTIFF per statistic for the month, one band per day
    check_month          independent checks of those GeoTIFFs (grid, nodata, values, authors' mapping)

The GeoTIFF mapping and its checks are the ones validated on 2026-10-06
(bulgaria_rila_pirin/scripts/export_geotiff.py and check_geotiff.py), adapted to Zarr caches and
to one month at a time.
"""
import json
import shutil
import tempfile
import time
from pathlib import Path

import dask.array as dska
import numpy as np
import pandas as pd
import rasterio
import rasterio.shutil
import xarray as xr
import yaml

from TopoPyScale import topo_daily
from TopoPyScale import topo_utils as tu

NODATA = -9999.0
COG_OPTIONS = dict(driver='COG', compress='DEFLATE', predictor='YES', blocksize=512, bigtiff='IF_SAFER',
                   overviews='AUTO', resampling='AVERAGE', num_threads='ALL_CPUS')
UNITS = {'t': 'degC', 'tp': 'mm/day'}
OFFSET = {'t': 273.15}          # K -> degC for the maps

RANGES = {  # loose physical bounds for the Rila-Pirin domain, hourly values (as qa_run.py)
    't': (220, 320), 'q': (0, 0.04), 'p': (50000, 105000), 'u': (-60, 60), 'v': (-60, 60), 'ws': (0, 60),
    'wd': (0, 2 * np.pi + 1e-5),   # outputs are rounded to 5 decimals (authors): 2*pi -> 6.28319
    # (fixed 2026-10-27: the SW bounds below were inside the comment above, so SW was never range-checked)
    'SW': (0, 1400), 'SW_direct': (0, 1400), 'SW_diffuse': (0, 1400),
    'LW': (50, 550), 'tp': (0, 200), 'w': (0, 0.04),
}
SW_VARS = ('SW', 'SW_direct', 'SW_diffuse')


def load_config(path):
    cfg = yaml.safe_load(Path(path).read_text())
    out = Path(cfg['project']['directory']) / cfg['outputs'].get('directory', 'outputs')
    dem = Path(cfg['dem']['path']) / cfg['dem']['file']
    return cfg, out, dem


def month_periods(start, end):
    """[(first day, last day), ...] of the calendar months between start and end (inclusive)."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    out = []
    for m0 in pd.date_range(start.replace(day=1), end, freq='MS'):
        a = max(m0, start)
        b = min(m0 + pd.offsets.MonthEnd(0), end)
        out.append((a, b))
    return out


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.part')
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    tmp.replace(path)


# ------------------------------------------------------------------------------------------- QA
def qa_hourly(hourly):
    """NaN count and range of every variable. SW is reported separately: TopoSCALE's known
    sunrise/sunset spikes (PROGRESS.md, reported only, not changed) can exceed the bound."""
    res, ok = {}, True
    for v in hourly.data_vars:
        x = hourly[v].values
        nan = int(np.isnan(x).sum())
        lo, hi = RANGES.get(v, (-np.inf, np.inf))
        n_out = int(((x < lo) | (x > hi)).sum())
        res[v] = dict(nan=nan, min=float(np.nanmin(x)), max=float(np.nanmax(x)), mean=float(np.nanmean(x)),
                      n_outside_bounds=n_out)
        if nan or (n_out and v not in SW_VARS):
            ok = False
    return ok, res


# ---------------------------------------------------------------------------------- daily store
def _plain(ds):
    for v in ds.variables:
        ds[v].encoding = {}
        if ds[v].dtype.kind in 'OU':
            ds[v] = ds[v].astype(str)
    return ds


def write_daily_month(daily_path, daily, year_start, year_end):
    """Write one month of daily statistics into the yearly daily store (created if absent).

    The store has one chunk per day (all points), so each month writes whole chunks only.
    """
    daily = _plain(daily.transpose('point_name', 'time'))
    days = pd.date_range(year_start, year_end, freq='D')
    if not tu.dataset_exists(daily_path):
        n_pts = daily.sizes['point_name']
        tmpl = xr.Dataset(
            {v: (('point_name', 'time'), dska.full((n_pts, len(days)), np.nan, chunks=(n_pts, 1), dtype='float64'),
                 daily[v].attrs) for v in daily.data_vars},
            coords={'time': days, 'point_name': daily.point_name.values.astype(str)})
        for c in daily.coords:
            if c not in ('time', 'point_name') and daily[c].dims == ('point_name',):
                tmpl = tmpl.assign_coords({c: ('point_name', daily[c].values)})
        tmpl.attrs = dict(daily.attrs)
        tmpl.to_zarr(daily_path, mode='w', compute=False, zarr_format=3, consolidated=True)
    store = xr.open_zarr(daily_path)
    if not np.array_equal(store.point_name.values.astype(str), daily.point_name.values.astype(str)):
        raise ValueError(f'{daily_path}: points differ from this month')
    pos = pd.DatetimeIndex(store.time.values).get_indexer(pd.DatetimeIndex(daily.time.values))
    if (pos < 0).any() or not np.array_equal(pos, np.arange(pos[0], pos[0] + len(pos))):
        raise ValueError(f'{daily_path}: days of this month not in the store')
    region = daily.drop_vars(list(daily.coords))
    region.to_zarr(daily_path, region={'point_name': slice(None), 'time': slice(int(pos[0]), int(pos[-1]) + 1)},
                   mode='r+', zarr_format=3, consolidated=False)


# ------------------------------------------------------------------------------------- GeoTIFFs
def pixel_index(ds_param_path, dem, point_names):
    """Index into `point_names` for every DEM pixel (-1 outside the clustering mask).

    Same lookup as ds[var].sel(point_name=ds_param.point_name) in topo_plot.map_variable,
    done once as integer positions so each band is a single numpy take.
    """
    ds_param = tu.open_dataset(ds_param_path)
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
    return idx, profile, pn


def _to_cog(tmp, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix('.tif.part')
    rasterio.shutil.copy(tmp, part, **COG_OPTIONS)
    part.rename(dst)


def export_month(daily, idx, profile, geotiff_dir, var, stats, cfg, overwrite=False):
    """One COG per statistic for this month, one band per day (band description = date)."""
    inside = idx >= 0
    safe = np.where(inside, idx, 0)
    profile = profile | dict(driver='GTiff', dtype='float32', nodata=NODATA, count=1, tiled=True,
                             blockxsize=512, blockysize=512, compress='DEFLATE', BIGTIFF='IF_SAFER')
    sampling = cfg['sampling']['toposub']
    geotiff_dir = Path(geotiff_dir)
    tmpdir = Path(tempfile.mkdtemp(dir=geotiff_dir.parent))
    files = []
    try:
        for stat in stats:
            da = daily[f'{var}_{stat}'].transpose('time', 'point_name')
            ym = pd.Timestamp(da.time.values[0]).strftime('%Y-%m')
            fname = f'{var}_{stat}_daily_{ym}.tif'
            dst = geotiff_dir / fname
            files.append(dst)
            if dst.exists() and not overwrite:
                print(f'     {fname} exists, skipped', flush=True)
                continue
            vals = da.values - OFFSET.get(var, 0.0)
            tmp = tmpdir / fname
            with rasterio.open(tmp, 'w', **(profile | dict(count=len(da.time)))) as f:
                for b, row in enumerate(vals, start=1):
                    band = np.where(inside, row[safe], NODATA).astype('float32')
                    band[inside & np.isnan(band)] = NODATA
                    f.write(band, b)
                    f.set_band_description(b, str(pd.Timestamp(da.time.values[b - 1]).date()))
                f.update_tags(variable=var, statistic=f'daily {stat}', units=UNITS.get(var, da.attrs.get('units', '')),
                              day_definition='UTC calendar day (00-23 UTC), hourly values',
                              method='TopoPyScale TopoSUB+TopoSCALE; each pixel = value of its cluster '
                                     '(ds_param.point_name), as topo_plot.map_variable',
                              n_clusters=str(sampling['n_clusters']),
                              clustering_features=str(sampling['clustering_features']),
                              era5_source=str(cfg['climate']['era5'].get('data_repository', 'cds')),
                              run=str(cfg['project']['name']), nodata_meaning='outside clustering mask')
            _to_cog(tmp, dst)
            tmp.unlink()
            print(f'---> {fname} ({len(da.time)} bands)', flush=True)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return files


def check_month(files, daily, pn, mask_file, dem, var, n_pixels=1000, seed=0):
    """Independent checks of this month's GeoTIFFs (as check_geotiff.py). Returns (ok, report)."""
    with rasterio.open(mask_file) as r:
        inside = r.read(1) == 1
    with rasterio.open(dem) as r:
        dem_crs, dem_transform, dem_shape = r.crs, r.transform, (r.height, r.width)
    report, ok = {}, True
    if not ((pn != '-9999') == inside).all():
        return False, {'error': 'cluster map in ds_param and mask.tif disagree'}
    rng = np.random.default_rng(seed)
    iy, ix = np.nonzero(inside)
    pick = rng.choice(len(iy), n_pixels, replace=False)
    py, px = iy[pick], ix[pick]
    p_names = pn[py, px]
    offset = OFFSET.get(var, 0.0)
    fill = xr.DataArray(np.where(pn != '-9999', pn, daily.point_name.values[0]), dims=('y', 'x'))
    for f in files:
        stat = f.stem.split('_')[1]
        errs = []
        with rasterio.open(f) as r:
            if r.crs != dem_crs or r.transform != dem_transform or (r.height, r.width) != dem_shape:
                errs.append('grid differs from DEM')
            dates = pd.to_datetime(list(r.descriptions))
            ref = daily[f'{var}_{stat}'].sel(time=dates) - offset
            max_diff = 0.0
            for b in range(1, r.count + 1):
                band = r.read(b)
                if not ((band == NODATA) == ~inside).all():
                    errs.append(f'band {b}: nodata pixels != outside mask')
                    break
                expect = ref.isel(time=b - 1).sel(point_name=p_names).values
                max_diff = max(max_diff, float(np.nanmax(np.abs(band[py, px] - expect))))
            authors = ref.isel(time=0).sel(point_name=fill).values      # topo_plot.map_variable expression
            authors = np.where(inside, authors, NODATA).astype('float32')
            field_diff = float(np.abs(r.read(1) - authors).max())
            b1 = r.read(1)[inside]
            n_bands = r.count
        if len(dates) != daily.sizes['time']:
            errs.append(f'{len(dates)} bands for {daily.sizes["time"]} days')
        if max_diff > 1e-4 or field_diff > 1e-4:
            errs.append(f'values differ (sample max {max_diff:.2e}, band-1 field max {field_diff:.2e})')
        ok &= not errs
        report[f.name] = dict(ok=not errs, bands=n_bands, sample_max_abs_diff=max_diff,
                              band1_field_max_abs_diff=field_diff, band1_min=float(b1.min()),
                              band1_max=float(b1.max()), errors=errs)
        print(f'     {"OK  " if not errs else "FAIL"} {f.name}: {n_bands} bands, sample max |diff| {max_diff:.1e}, '
              f'band-1 field max |diff| {field_diff:.1e}, band-1 range {b1.min():.2f}..{b1.max():.2f}'
              + (f'  <- {"; ".join(errs)}' if errs else ''), flush=True)
    return ok, report


class Timer:
    def __init__(self):
        self.t = {}

    def __call__(self, name, fun, *a, **k):
        t0 = time.time()
        print(f'\n========== {name} ==========', flush=True)
        r = fun(*a, **k)
        self.t[name] = round(time.time() - t0, 1)
        print(f'========== {name} done in {self.t[name]} s', flush=True)
        return r


# ---------------------------------------------------------------------------------- prepared ERA5 store
def check_era5_store(path, start, end, plevels):
    """For climate.era5.data_repository 'local': the ERA5 Zarr store was prepared beforehand by
    zarr_workflow/era5_download (CDS/NCAR). Check that it is complete for [start, end] instead of fetching:
    every hour present and without NaN, levels ascending and equal to the config's plevels."""
    ds = xr.open_zarr(path)
    tvec = pd.date_range(pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta('23h'), freq='1h')
    missing = tvec.difference(pd.DatetimeIndex(ds.time.values))
    if len(missing):
        raise ValueError(f'{path}: {len(missing)} hours of the run period missing, first {missing[:3].tolist()}')
    lev = ds.level.values.astype(float)
    if not np.all(np.diff(lev) > 0):
        raise ValueError(f'{path}: levels must be ascending')
    if plevels is not None and sorted(float(p) for p in plevels) != lev.tolist():
        raise ValueError(f'{path}: levels {lev.tolist()} differ from config climate.era5.plevels {plevels}')
    sub = ds.sel(time=tvec)
    nan = {v: int(sub[v].isnull().sum()) for v in sub.data_vars}
    bad = {v: n for v, n in nan.items() if n}
    if bad:
        raise ValueError(f'{path}: NaN in the run period {bad}; run era5_download.py download/status first')
    print(f'---> ERA5 store {path}: complete for {tvec[0]:%Y-%m-%d} .. {tvec[-1]:%Y-%m-%d}, '
          f'{len(lev)} levels {int(lev[0])}-{int(lev[-1])} hPa, {len(sub.data_vars)} variables, no NaN', flush=True)
    return True
