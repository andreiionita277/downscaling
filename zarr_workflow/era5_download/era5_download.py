"""
ERA5 hourly on the configured pressure levels (any subset of the 37; 600-1000 hPa for Rila-Pirin) plus the
single-level variables TopoPyScale needs, into one Zarr store with the layout `topo_scale_zarr.ClimateDownscaler`
reads (same as the EDH store of zarr_workflow):

    dims    time (hourly), level (hPa, ascending), latitude (descending), longitude (-180..180, ascending)
    PLEV    z, t, u, v, q, r              (time, level, latitude, longitude) float32
    SURF    z_surf, d2m, sp, strd, ssrd, tp, t2m   (time, latitude, longitude) float32
    accumulations (tp, ssrd, strd) at a valid time = previous 1 h (CDS convention)

Usage (inside the container, from /app):
    python zarr_workflow/era5_download/era5_download.py CONFIG download [--months 1 2] [--only plev|surf]
    python zarr_workflow/era5_download/era5_download.py CONFIG status
    python zarr_workflow/era5_download/era5_download.py CONFIG verify [--months 1] [--spot-checks 2]
    python zarr_workflow/era5_download/era5_download.py CONFIG import --from OTHER_STORE [--months 1]
        (copy units that another store already holds, e.g. a 37-level store, instead of downloading them)

How a download works
    1. The store is pre-allocated once (metadata only, every value NaN).
    2. The period is cut into units: CDS PLEV = one month x a group of variables x all levels (the groups are
       made as few and as even as the CDS item limit allows: 37 levels -> 3 x 2 vars, 14 levels -> 2 x 3 vars),
       CDS SURF = as many months of all SURF variables as fit under the limit, NCAR PLEV = one variable x
       one day. Units listed as written in state/manifest.jsonl are skipped (resumable).
    3. Worker threads download units to raw/ (written as *.part, renamed when complete).
    4. The main thread alone converts each raw file, checks it, writes it with to_zarr(region=...),
       reads the slice back and compares it, records the unit in the manifest, then deletes the raw file.
       One writer means partially covered time chunks are never written by two processes at once.
    5. When every unit of a month is written, the month is validated (no NaN, all levels, all hours)
       and the result is saved in state/months/YYYY-MM.json.
    Failures (CDS errors, timeouts, bad files) are retried with exponential backoff, then logged as
    failed in the manifest; the run continues. Rerun the same command to retry them.
"""
import argparse
import itertools
import json
import logging
import os
import random
import shutil
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
import yaml

try:
    from zarr.codecs import BloscCodec
except ImportError:
    from zarr.codecs._blosc import BloscCodec

log = logging.getLogger("era5_download")

# TopoPyScale name -> CDS name, NCAR (code, file grid tag, variable name in file)
PLEV = {
    'z': ('geopotential', ('128_129_z', 'sc', 'Z')),
    't': ('temperature', ('128_130_t', 'sc', 'T')),
    'u': ('u_component_of_wind', ('128_131_u', 'uv', 'U')),
    'v': ('v_component_of_wind', ('128_132_v', 'uv', 'V')),
    'q': ('specific_humidity', ('128_133_q', 'sc', 'Q')),
    'r': ('relative_humidity', ('128_157_r', 'sc', 'R')),
}
SURF = {
    'z_surf': 'geopotential', 'd2m': '2m_dewpoint_temperature', 'sp': 'surface_pressure',
    'strd': 'surface_thermal_radiation_downwards', 'ssrd': 'surface_solar_radiation_downwards',
    'tp': 'total_precipitation', 't2m': '2m_temperature',
}
ATTRS = {
    'z': ('m**2 s**-2', 'Geopotential'), 't': ('K', 'Temperature'), 'u': ('m s**-1', 'U component of wind'),
    'v': ('m s**-1', 'V component of wind'), 'q': ('kg kg**-1', 'Specific humidity'), 'r': ('%', 'Relative humidity'),
    'z_surf': ('m**2 s**-2', 'Geopotential (surface)'), 'd2m': ('K', '2 metre dewpoint temperature'),
    'sp': ('Pa', 'Surface pressure'), 'strd': ('J m**-2', 'Surface thermal radiation downwards (1 h accumulation)'),
    'ssrd': ('J m**-2', 'Surface short-wave (solar) radiation downwards (1 h accumulation)'),
    'tp': ('m', 'Total precipitation (1 h accumulation)'), 't2m': ('K', '2 metre temperature'),
}
# NCAR (dataset d633000) single levels: our name -> list of (folder, code, variable in file); several = summed.
# Analysis fields: one file per month. Accumulations: forecast files (06/18 UTC starts, 1..12 h), two per month.
# tp is not stored: ERA5's tp = large-scale (lsp) + convective (cp) precipitation.
NCAR_SURF = {
    't2m': [('e5.oper.an.sfc', '128_167_2t', 'VAR_2T')],
    'd2m': [('e5.oper.an.sfc', '128_168_2d', 'VAR_2D')],
    'sp': [('e5.oper.an.sfc', '128_134_sp', 'SP')],
    'ssrd': [('e5.oper.fc.sfc.accumu', '128_169_ssrd', 'SSRD')],
    'strd': [('e5.oper.fc.sfc.accumu', '128_175_strd', 'STRD')],
    'tp': [('e5.oper.fc.sfc.accumu', '128_142_lsp', 'LSP'), ('e5.oper.fc.sfc.accumu', '128_143_cp', 'CP')],
    'z_surf': [('e5.oper.invariant', '128_129_z', 'Z')],
}
NCAR_DAP = 'https://thredds.rda.ucar.edu/thredds/dodsC/files/g/d633000'
_DAP_LOCK = threading.Lock()      # netCDF4/HDF5 is not thread-safe: OPeNDAP reads one at a time
DONE_STATES = ('written',)


# ---------------------------------------------------------------- config / setup

def load_config(path):
    cfg = yaml.safe_load(Path(path).read_text())
    cfg['_path'] = str(Path(path).resolve())
    for k in ('store', 'raw', 'state', 'logs'):
        cfg['paths'][k] = Path(cfg['paths'][k])
    cfg['levels'] = [float(x) for x in sorted(cfg['levels'])]
    return cfg


def setup_logging(cfg, command):
    cfg['paths']['logs'].mkdir(parents=True, exist_ok=True)
    f = cfg['paths']['logs'] / f'{command}_{time.strftime("%Y%m%d_%H%M%S")}.log'
    fmt = logging.Formatter('%(asctime)s %(levelname)-7s %(threadName)-12s %(message)s', '%Y-%m-%d %H:%M:%S')
    log.setLevel(logging.INFO)
    for h in (logging.FileHandler(f), logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.info(f'log file {f}')
    return f


def time_axis(cfg):
    return pd.date_range(pd.Timestamp(cfg['period']['start']),
                         pd.Timestamp(cfg['period']['end']) + pd.Timedelta('23h'), freq='1h')


def grid(cfg):
    a = cfg['area']
    lat = np.round(np.arange(a['north'], a['south'] - 1e-6, -0.25), 4)
    lon = np.round(np.arange(a['west'], a['east'] + 1e-6, 0.25), 4)
    return lat, lon


def cds_client():
    from ecmwf.datastores import Client
    rc = Path(os.environ.get('CDSAPI_RC', Path.home() / '.cdsapirc'))
    kv = dict(l.split(':', 1) for l in rc.read_text().splitlines() if ':' in l)
    return Client(url=kv['url'].strip(), key=kv['key'].strip())


# ---------------------------------------------------------------- units of work

def months_in(cfg):
    t = time_axis(cfg)
    return sorted({(d.year, d.month) for d in t})


def month_hours(y, m, cfg):
    t = time_axis(cfg)
    return t[(t.year == y) & (t.month == m)]


def make_units(cfg, months=None, only=None):
    """All units of the period, as dicts with a stable 'id'."""
    units = []
    sel = months_in(cfg) if months is None else [ym for ym in months_in(cfg) if ym[1] in months]
    nlev = len(cfg['levels'])
    if only in (None, 'plev'):
        if cfg['source']['plev'] == 'cds':
            groups = plev_groups(cfg)
            for (y, m), g in itertools.product(sel, groups):
                items = len(g) * nlev * len(month_hours(y, m, cfg))
                if items > cfg['cds']['max_items_plev']:
                    raise ValueError(f'{y}-{m:02d} {g}: {items} items > limit {cfg["cds"]["max_items_plev"]}; '
                                     f'set a lower cds.vars_per_request')
                units.append({'id': f'cds_plev_{y}-{m:02d}_{"-".join(g)}', 'kind': 'plev', 'src': 'cds',
                              'vars': g, 'months': [(y, m)], 'items': items})
        else:
            for (y, m), v in itertools.product(sel, cfg['plev_vars']):
                for day in sorted({d.day for d in month_hours(y, m, cfg)}):
                    units.append({'id': f'ncar_plev_{y}-{m:02d}-{day:02d}_{v}', 'kind': 'plev', 'src': 'ncar',
                                  'vars': [v], 'months': [(y, m)], 'day': day})
    if only in (None, 'surf') and cfg['source']['surf'] == 'ncar':
        for (y, m), v in itertools.product(sel, cfg['surf_vars']):
            units.append({'id': f'ncar_surf_{y}-{m:02d}_{v}', 'kind': 'surf', 'src': 'ncar',
                          'vars': [v], 'months': [(y, m)]})
    elif only in (None, 'surf'):
        # pack whole months of the WHOLE period while under the SURF limit (same unit ids whatever --months
        # says), then keep the blocks that touch the selected months
        nv = len(cfg['surf_vars'])
        block, items, surf = [], 0, []
        for y, m in months_in(cfg):
            n = nv * len(month_hours(y, m, cfg))
            if block and (items + n > cfg['cds']['max_items_surf'] or y != block[-1][0]):
                surf.append(_surf_unit(block, items, cfg))
                block, items = [], 0
            block.append((y, m))
            items += n
        if block:
            surf.append(_surf_unit(block, items, cfg))
        units += [u for u in surf if set(u['months']) & set(sel)]
    return units


def plev_groups(cfg):
    """Split plev_vars into as few, as even CDS requests as the item limit allows for the longest month
    (or into groups of cds.vars_per_request if that is set)."""
    v = list(cfg['plev_vars'])
    k = cfg['cds'].get('vars_per_request')
    if not k:
        kmax = cfg['cds']['max_items_plev'] // (len(cfg['levels']) * 744)
        if kmax < 1:
            raise ValueError('even one variable x one month exceeds the CDS limit; use fewer levels')
        n_req = -(-len(v) // kmax)                       # ceil
        k = -(-len(v) // n_req)                          # even group size
    return [v[i:i + k] for i in range(0, len(v), k)]


def _surf_unit(block, items, cfg):
    (y0, m0), (y1, m1) = block[0], block[-1]
    return {'id': f'cds_surf_{y0}-{m0:02d}_{y1}-{m1:02d}', 'kind': 'surf', 'src': 'cds',
            'vars': list(cfg['surf_vars']), 'months': block, 'items': items}


# ---------------------------------------------------------------- manifest

def manifest_path(cfg):
    return cfg['paths']['state'] / 'manifest.jsonl'


def read_manifest(cfg):
    """Last record per unit id."""
    p, last = manifest_path(cfg), {}
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                last[r['id']] = r
    return last


def append_manifest(cfg, record):
    record = dict(record, logged=time.strftime('%Y-%m-%dT%H:%M:%S'))
    with open(manifest_path(cfg), 'a') as fh:
        fh.write(json.dumps(record, default=str) + '\n')


# ---------------------------------------------------------------- store

def init_store(cfg):
    store = cfg['paths']['store']
    if store.exists():
        check_store_layout(cfg)
        return
    import dask.array as da
    t, (lat, lon), lev = time_axis(cfg), grid(cfg), np.array(cfg['levels'])
    tc = cfg['store']['time_chunk']
    coords = {'time': t, 'level': lev, 'latitude': lat, 'longitude': lon}
    data = {}
    for v in cfg['plev_vars']:
        data[v] = (('time', 'level', 'latitude', 'longitude'),
                   da.full((len(t), len(lev), len(lat), len(lon)), np.nan, dtype='float32',
                           chunks=(tc, len(lev), len(lat), len(lon))))
    for v in cfg['surf_vars']:
        data[v] = (('time', 'latitude', 'longitude'),
                   da.full((len(t), len(lat), len(lon)), np.nan, dtype='float32', chunks=(tc, len(lat), len(lon))))
    ds = xr.Dataset(data, coords=coords)
    for v in ds.data_vars:
        ds[v].attrs = {'units': ATTRS[v][0], 'long_name': ATTRS[v][1]}
    ds.latitude.attrs = {'units': 'degrees_north', 'standard_name': 'latitude', 'stored_direction': 'decreasing'}
    ds.longitude.attrs = {'units': 'degrees_east', 'standard_name': 'longitude'}
    ds.level.attrs = {'units': 'hPa', 'long_name': 'pressure level', 'positive': 'down'}
    ds.attrs = {'title': f'ERA5 hourly, {len(lev)} pressure levels {int(lev[0])}-{int(lev[-1])} hPa, '
                         f'cut and formatted for TopoPyScale',
                'source': f'ERA5; pressure levels from {cfg["source"]["plev"]}, single levels from {cfg["source"]["surf"]} '
                          f'(cds = Copernicus CDS GRIB; ncar = NCAR GDEX d633000 THREDDS subsets)',
                'Conventions': 'CF-1.7', 'fetched_with': 'zarr_workflow/era5_download/era5_download.py',
                'timestep_convention': '1h; accumulations = previous 1 h (CDS convention)',
                'config': cfg['_path']}
    comp = BloscCodec(cname='lz4', clevel=5, shuffle='bitshuffle', blocksize=0)
    enc = {v: {'compressors': comp, 'fill_value': np.float32('nan')} for v in ds.data_vars}
    ds.to_zarr(store, mode='w', zarr_format=3, encoding=enc, consolidated=True, compute=False)
    log.info(f'pre-allocated {store}: {dict(ds.sizes)}, time chunk {tc}')


def check_store_layout(cfg):
    ds = xr.open_zarr(cfg['paths']['store'])
    lat, lon = grid(cfg)
    ok = (np.array_equal(ds.time.values, time_axis(cfg).values) and np.allclose(ds.latitude, lat)
          and np.allclose(ds.longitude, lon) and np.allclose(ds.level, cfg['levels'])
          and set(cfg['plev_vars'] + cfg['surf_vars']) <= set(ds.data_vars))
    if not ok:
        raise ValueError(f'{cfg["paths"]["store"]} does not match the config (period/grid/levels/vars); '
                         f'use another store path or delete it')


def write_region(cfg, ds):
    """Write ds (time subset, full grid) into the store; read it back and compare. Returns (i0, i1)."""
    store = cfg['paths']['store']
    t_store = pd.DatetimeIndex(xr.open_zarr(store).time.values)
    i0 = t_store.get_loc(pd.Timestamp(ds.time.values[0]))
    i1 = i0 + ds.sizes['time']
    if not np.array_equal(t_store[i0:i1].values, ds.time.values):
        raise ValueError('unit times are not contiguous in the store time axis')
    out = ds.drop_vars(list(ds.coords))
    for v in out.data_vars:
        out[v].attrs, out[v].encoding = {}, {}
    out.to_zarr(store, region={'time': slice(i0, i1)}, consolidated=True)   # level/lat/lon: full extent
    back = xr.open_zarr(store)[list(ds.data_vars)].isel(time=slice(i0, i1)).load()
    for v in ds.data_vars:
        if not np.array_equal(back[v].values, ds[v].values.astype('float32'), equal_nan=True):
            raise ValueError(f'read-back mismatch for {v}')
    return i0, i1


# ---------------------------------------------------------------- CDS download

def cds_request(cfg, unit):
    a = cfg['area']
    area = [a['north'], a['west'], a['south'], a['east']]
    years = sorted({y for y, _ in unit['months']})
    if len(years) != 1:
        raise ValueError('a CDS unit must stay within one year')
    r = {'product_type': ['reanalysis'], 'year': [str(years[0])],
         'month': [f'{m:02d}' for _, m in unit['months']], 'day': [f'{d:02d}' for d in range(1, 32)],
         'time': [f'{h:02d}:00' for h in range(24)], 'data_format': 'grib', 'download_format': 'unarchived',
         'area': area}
    if unit['kind'] == 'plev':
        r['variable'] = [PLEV[v][0] for v in unit['vars']]
        r['pressure_level'] = [str(int(p)) for p in cfg['levels']]
        return 'reanalysis-era5-pressure-levels', r
    r['variable'] = [SURF[v] for v in unit['vars']]
    return 'reanalysis-era5-single-levels', r


def raw_file(cfg, unit):
    return cfg['paths']['raw'] / (unit['id'] + ('.grib' if unit['src'] == 'cds' else '.nc'))


def download_cds(cfg, unit, client):
    out = raw_file(cfg, unit)
    if out.exists():
        log.info(f'{unit["id"]}: raw file already there, reused')
        return out, {'reused_raw': True}
    ds_name, req = cds_request(cfg, unit)
    c = cfg['cds']
    stats = {'attempts': 0}
    for attempt in range(c['max_retries'] + 1):
        stats['attempts'] = attempt + 1
        t0 = time.time()
        remote = None
        try:
            remote = client.submit(ds_name, req)
            log.info(f'{unit["id"]}: submitted ({unit.get("items")} items), request {remote.request_id}')
            last = None
            while True:
                s = remote.status
                if s != last:
                    log.info(f'{unit["id"]}: {s} after {time.time() - t0:.0f}s')
                    stats[f't_{s}'] = round(time.time() - t0)
                    last = s
                if s == 'successful':
                    break
                if s in ('failed', 'rejected', 'dismissed', 'deleted'):
                    raise RuntimeError(f'CDS job {s}: {getattr(remote, "results_ready", "")}')
                if time.time() - t0 > c['request_timeout_hours'] * 3600:
                    raise TimeoutError(f'still {s} after {c["request_timeout_hours"]} h')
                time.sleep(c['poll_seconds'])
            td = time.time()
            part = out.with_name(out.name + '.part')
            remote.download(str(part))
            os.replace(part, out)
            stats.update(download_s=round(time.time() - td, 1), total_s=round(time.time() - t0), bytes=out.stat().st_size)
            log.info(f'{unit["id"]}: downloaded {out.stat().st_size / 1e6:.1f} MB in {stats["download_s"]}s '
                     f'(total {stats["total_s"]}s)')
            return out, stats
        except Exception as e:  # noqa: BLE001 - every CDS/network error is retried
            msg = f'{type(e).__name__}: {e}'.replace('\n', ' ')[:500]
            stats.setdefault('errors', []).append(msg)
            if 'cost limits exceeded' in msg or '403' in msg and 'too large' in msg:
                raise                                       # retrying cannot help
            if remote is not None and isinstance(e, TimeoutError):
                try:
                    remote.delete()
                except Exception:  # noqa: BLE001
                    pass
            if attempt == c['max_retries']:
                raise RuntimeError(f'gave up after {attempt + 1} attempts; last error {msg}')
            wait = c['backoff_seconds'] * 2 ** attempt
            log.warning(f'{unit["id"]}: attempt {attempt + 1} failed ({msg}); retry in {wait}s')
            time.sleep(wait)


# ---------------------------------------------------------------- NCAR download (THREDDS NetcdfSubset)

def ncar_url(cfg, unit):
    y, m = unit['months'][0]
    d = unit['day']
    code, tag, name = PLEV[unit['vars'][0]][1]
    day = f'{y}{m:02d}{d:02d}'
    f = f'e5.oper.an.pl.{code}.ll025{tag}.{day}00_{day}23.nc'
    a = cfg['area']
    iso = f'{y}-{m:02d}-{d:02d}'
    return (f'{cfg["ncar"]["base_url"]}/{y}{m:02d}/{f}?var={name}&north={a["north"]}&south={a["south"]}'
            f'&west={a["west"]}&east={a["east"]}&horizStride=1&time_start={iso}T00:00:00Z&time_end={iso}T23:00:00Z'
            f'&accept=netcdf')


def download_ncar(cfg, unit, client=None):
    out = raw_file(cfg, unit)
    if out.exists():
        return out, {'reused_raw': True}
    c = cfg['ncar']
    url = ncar_url(cfg, unit)
    stats = {}
    for attempt in range(c['max_retries'] + 1):
        t0 = time.time()
        try:
            part = out.with_name(out.name + '.part')
            with urllib.request.urlopen(url, timeout=c['timeout_seconds']) as resp, open(part, 'wb') as fh:
                shutil.copyfileobj(resp, fh)
            os.replace(part, out)
            stats.update(attempts=attempt + 1, total_s=round(time.time() - t0, 1), bytes=out.stat().st_size)
            return out, stats
        except Exception as e:  # noqa: BLE001
            msg = f'{type(e).__name__}: {e}'[:300]
            stats.setdefault('errors', []).append(msg)
            if attempt == c['max_retries']:
                raise RuntimeError(f'gave up after {attempt + 1} attempts; last error {msg}')
            wait = c['backoff_seconds'] * 2 ** attempt
            log.warning(f'{unit["id"]}: attempt {attempt + 1} failed ({msg}); retry in {wait}s')
            time.sleep(wait)


# ---------------------------------------------------------------- conversion to the store layout

def _dap_box(url, var, cfg):
    """Our box of one variable of one NCAR file over OPeNDAP (server-side subset), loaded."""
    a = cfg['area']
    w, e = a['west'] % 360, a['east'] % 360
    if w > e:
        raise NotImplementedError('NCAR single levels: boxes crossing 0 deg longitude are not supported')
    with _DAP_LOCK:
        with xr.open_dataset(url, engine='netcdf4') as ds:
            return ds[var].sel(latitude=slice(a['north'], a['south']), longitude=slice(w, e)).load()


def _ncar_accum_files(y, m):
    """Forecast files whose valid times cover month (y, m): last file of the previous month (starts at 18 UTC
    of its last day reach 00..06 UTC of day 1) and the two files of the month."""
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
    ny, nm = (y, m + 1) if m < 12 else (y + 1, 1)
    return [(f'{py}{pm:02d}', f'{py}{pm:02d}1606_{y}{m:02d}0106'),
            (f'{y}{m:02d}', f'{y}{m:02d}0106_{y}{m:02d}1606'),
            (f'{y}{m:02d}', f'{y}{m:02d}1606_{ny}{nm:02d}0106')]


def ncar_surf_month(cfg, var, y, m):
    """One single-level variable for one month from NCAR, as (time, latitude, longitude) on valid hours."""
    hours = month_hours(y, m, cfg)
    total = None
    for folder, code, name in NCAR_SURF[var]:
        if folder == 'e5.oper.an.sfc':
            last = hours[-1]
            f = f'{folder}.{code}.ll025sc.{y}{m:02d}0100_{y}{m:02d}{last.day:02d}23.nc'
            da = _dap_box(f'{NCAR_DAP}/{folder}/{y}{m:02d}/{f}', name, cfg).sel(time=hours)
        elif folder == 'e5.oper.invariant':
            f = f'{folder}.{code}.ll025sc.1979010100_1979010100.nc'
            z = _dap_box(f'{NCAR_DAP}/{folder}/197901/{f}', name, cfg).squeeze(drop=True)
            da = z.expand_dims(time=hours).transpose('time', ...)
        else:
            parts = []
            for ym, span in _ncar_accum_files(y, m):
                f = f'{folder}.{code}.ll025sc.{span}.nc'
                a = _dap_box(f'{NCAR_DAP}/{folder}/{ym}/{f}', name, cfg)
                a = a.stack(vt=('forecast_initial_time', 'forecast_hour'))
                vt = (pd.DatetimeIndex(a.forecast_initial_time.values)
                      + pd.to_timedelta(a.forecast_hour.values, unit='h'))
                a = a.drop_vars(['vt', 'forecast_initial_time', 'forecast_hour']).assign_coords(vt=vt)
                parts.append(a.rename(vt='time').transpose('time', ...))
            da = xr.concat(parts, dim='time')
            _, keep = np.unique(da.time.values, return_index=True)
            da = da.isel(time=np.sort(keep))
            missing = hours.difference(pd.DatetimeIndex(da.time.values))
            if len(missing):
                raise ValueError(f'{var} {code}: {len(missing)} hours missing, first {list(missing[:3])}')
            da = da.sel(time=hours)
        total = da if total is None else total + da
    drop = [c for c in total.coords if c not in total.dims]
    return total.drop_vars(drop).rename(var).astype('float32')


def download_ncar_surf(cfg, unit, client=None):
    out = raw_file(cfg, unit)
    if out.exists():
        return out, {'reused_raw': True}
    c = cfg['ncar']
    (y, m), v = unit['months'][0], unit['vars'][0]
    stats = {}
    for attempt in range(c['max_retries'] + 1):
        t0 = time.time()
        try:
            da = ncar_surf_month(cfg, v, y, m)
            part = out.with_name(out.name + '.part')
            da.to_dataset().to_netcdf(part, engine='scipy', format='NETCDF3_64BIT')   # scipy: no netCDF4 lock
            os.replace(part, out)
            stats.update(attempts=attempt + 1, total_s=round(time.time() - t0, 1), bytes=out.stat().st_size)
            return out, stats
        except Exception as e:  # noqa: BLE001
            msg = f'{type(e).__name__}: {e}'[:300]
            stats.setdefault('errors', []).append(msg)
            if attempt == c['max_retries'] or isinstance(e, (NotImplementedError, KeyError)):
                raise RuntimeError(f'gave up after {attempt + 1} attempts; last error {msg}')
            wait = c['backoff_seconds'] * 2 ** attempt
            log.warning(f'{unit["id"]}: attempt {attempt + 1} failed ({msg}); retry in {wait}s')
            time.sleep(wait)


def convert_ncar_surf(path, unit, cfg):
    # raw files are NetCDF-3 and read with scipy, so the main thread never waits for _DAP_LOCK (held by the SURF
    # thread during its OPeNDAP reads; with netCDF4 here every PLEV write waited 17-20 s, seen 2026-10-27).
    # Files are closed explicitly: a file left to the garbage collector can be closed from a thread inside
    # xarray's netCDF lock, which then deadlocks (seen 2026-10-27).
    with xr.open_dataset(path, engine='scipy') as f:
        ds = f.load()
    return _std_grid(ds, cfg)[unit['vars']].astype('float32')


def _std_grid(ds, cfg):
    """Rename/sort to (time, [level,] latitude desc, longitude asc -180..180) and check the grid."""
    if 'valid_time' in ds.coords and 'time' in ds.coords:
        # cfgrib analyses: `time` (dim, or scalar for a single hour) = valid time (step 0); drop the duplicate
        if not np.array_equal(ds.valid_time.values, ds.time.values):
            raise ValueError('valid_time differs from time (forecast fields need convert_cds_surf)')
        ds = ds.drop_vars('valid_time')
    ren = {k: v for k, v in {'isobaricInhPa': 'level', 'valid_time': 'time'}.items() if k in ds.dims or k in ds.coords}
    ds = ds.rename(ren)
    ds = ds.assign_coords(longitude=((ds.longitude + 180) % 360) - 180)
    ds = ds.sortby('latitude', ascending=False).sortby('longitude')
    lat, lon = grid(cfg)
    if not (np.allclose(ds.latitude.values, lat) and np.allclose(ds.longitude.values, lon)):
        raise ValueError(f'grid mismatch: lat {ds.latitude.values[[0, -1]]}, lon {ds.longitude.values[[0, -1]]}')
    ds = ds.assign_coords(latitude=lat, longitude=lon)
    if 'level' in ds.dims:
        ds = ds.assign_coords(level=ds.level.astype('float64')).sortby('level')
    drop = [c for c in ds.coords if c not in ds.dims]
    return ds.drop_vars(drop)


def convert_cds_plev(path, unit, cfg):
    ds = xr.open_dataset(path, engine='cfgrib', backend_kwargs={'indexpath': ''}, decode_timedelta=True)
    ds = _std_grid(ds, cfg)
    return ds[unit['vars']].astype('float32').load()


def convert_cds_surf(path, unit, cfg):
    """Instant fields are indexed by valid time; accumulations come as (forecast time, step): stack them
    to valid times. Every hour of the unit's months must come out exactly once."""
    import cfgrib
    parts = []
    for ds in cfgrib.open_datasets(str(path), backend_kwargs={'indexpath': ''}, decode_timedelta=True):
        ds = ds.rename({'z': 'z_surf'}) if 'z' in ds else ds
        if 'step' in ds.dims:
            ds = ds.stack(vt=[d for d in ('time', 'step') if d in ds.dims])
            vt = ds.valid_time.values
            ds = ds.drop_vars([c for c in ('time', 'step', 'valid_time', 'vt') if c in ds.coords])
            # stack() puts the new dim LAST: move time first before testing per-time NaN
            ds = ds.assign_coords(vt=vt).rename(vt='time').transpose('time', ...)
            first = ds[list(ds.data_vars)[0]].values
            ok = ~np.isnan(first).all(axis=tuple(range(1, first.ndim)))   # drop (time, step) pairs not delivered
            ds = ds.isel(time=np.where(ok)[0]).sortby('time')
        elif 'valid_time' in ds.coords and 'time' in ds.dims and 'step' not in ds.dims:
            ds = ds.drop_vars('valid_time') if 'valid_time' not in ds.dims else ds
        if 'time' not in ds.dims:
            ds = ds.expand_dims('time')
        parts.append(_std_grid(ds, cfg))
    ds = xr.merge(parts, compat='override', join='outer')
    want = pd.DatetimeIndex(np.concatenate([month_hours(y, m, cfg).values for y, m in unit['months']]))
    have = pd.DatetimeIndex(ds.time.values)
    if have.has_duplicates:
        raise ValueError('duplicate valid times after stacking accumulations')
    missing = want.difference(have)
    if len(missing):
        raise ValueError(f'{len(missing)} hours missing, first {list(missing[:3])}')
    return ds[unit['vars']].sel(time=want).astype('float32').load()


def convert_ncar(path, unit, cfg):
    name = PLEV[unit['vars'][0]][1][2]
    with xr.open_dataset(path, engine='scipy') as f:     # NCSS returns NetCDF-3: scipy, no lock (see convert_ncar_surf)
        ds = f[[name]].load().rename({name: unit['vars'][0]})
    ds = _std_grid(ds, cfg)
    ds = ds.sel(level=cfg['levels'])          # NCSS returns all 37 levels; keep the configured ones
    return ds.astype('float32').load()


def check_unit(ds, unit, cfg):
    """No NaN at all, all levels, all hours of the unit's months/day."""
    want = unit_hours(unit, cfg)
    if not np.array_equal(pd.DatetimeIndex(ds.time.values), want):
        raise ValueError(f'times: got {ds.sizes["time"]}, expected {len(want)}')
    if unit['kind'] == 'plev' and not np.allclose(ds.level.values, cfg['levels']):
        raise ValueError(f'levels: got {ds.level.values.tolist()}')
    stats = {}
    for v in unit['vars']:
        a = ds[v].values
        n = int(np.isnan(a).sum())
        if n:
            raise ValueError(f'{v}: {n} NaN values')
        stats[v] = [float(a.min()), float(a.max())]
    return stats


# ---------------------------------------------------------------- month validation

def validate_month(cfg, y, m, write=True):
    """Check the store for one month: every variable, every level, every hour present (no NaN)."""
    ds = xr.open_zarr(cfg['paths']['store']).sel(time=month_hours(y, m, cfg))
    n_expected = {31: 744, 30: 720, 29: 696, 28: 672}[pd.Period(f'{y}-{m:02d}').days_in_month]
    rep = {'month': f'{y}-{m:02d}', 'hours': int(ds.sizes['time']), 'hours_expected': n_expected,
           'levels': int(ds.sizes['level']), 'vars': {}, 'ok': True, 'checked': time.strftime('%Y-%m-%dT%H:%M:%S')}
    if ds.sizes['time'] != n_expected or ds.sizes['level'] != len(cfg['levels']):
        rep['ok'] = False
    for v in cfg['plev_vars'] + cfg['surf_vars']:
        a = ds[v].values
        nan = np.isnan(a)
        r = {'nan': int(nan.sum()), 'min': float(np.nanmin(a)) if (~nan).any() else None,
             'max': float(np.nanmax(a)) if (~nan).any() else None}
        # all-NaN slices: per hour (and per level for PLEV)
        r['nan_only_hours'] = int(nan.reshape(a.shape[0], -1).all(axis=1).sum())
        if a.ndim == 4:
            r['nan_only_levels'] = [float(cfg['levels'][i]) for i in np.where(nan.all(axis=(0, 2, 3)))[0]]
        if r['nan']:
            rep['ok'] = False
        rep['vars'][v] = r
    if write:
        d = cfg['paths']['state'] / 'months'
        d.mkdir(parents=True, exist_ok=True)
        (d / f'{y}-{m:02d}.json').write_text(json.dumps(rep, indent=1))
    return rep


# ---------------------------------------------------------------- commands

def cmd_download(cfg, args):
    for k in ('raw', 'state'):
        cfg['paths'][k].mkdir(parents=True, exist_ok=True)
    init_store(cfg)
    units = make_units(cfg, args.months, args.only)
    done = {k for k, r in read_manifest(cfg).items() if r['status'] in DONE_STATES}
    todo = [u for u in units if u['id'] not in done]
    log.info(f'{len(units)} units in selection, {len(units) - len(todo)} already written, {len(todo)} to do')
    if not todo:
        return 0
    cds = cds_client() if any(u['src'] == 'cds' for u in todo) else None
    t_run = time.time()
    n_ok = n_fail = 0
    month_units = {}
    for u in make_units(cfg):                      # a month is complete when ALL its units (PLEV + SURF) are written
        for ym in u['months']:
            month_units.setdefault(ym, set()).add(u['id'])

    def fetch(u):
        if u['src'] == 'cds':
            return download_cds(cfg, u, cds)
        return (download_ncar if u['kind'] == 'plev' else download_ncar_surf)(cfg, u, cds)

    def n_workers(src):
        return cfg['cds']['workers'] if src == 'cds' else cfg['ncar']['workers']

    # one pool for PLEV and one for SURF, so neither waits for the other (NCAR SURF reads are serialised by
    # _DAP_LOCK anyway; a CDS SURF job queues while PLEV runs)
    todo.sort(key=lambda u: (u['months'][0], u.get('day', 0), u['id']))
    with ThreadPoolExecutor(n_workers(cfg['source']['plev']), thread_name_prefix='plev') as ex_p, \
            ThreadPoolExecutor(1 if cfg['source']['surf'] == 'ncar' else n_workers('cds'),
                               thread_name_prefix='surf') as ex_s:
        futs = {(ex_p if u['kind'] == 'plev' else ex_s).submit(fetch, u): u for u in todo}
        for fut in as_completed(futs):
            u = futs[fut]
            rec = {'id': u['id'], 'kind': u['kind'], 'src': u['src'], 'vars': u['vars'],
                   'months': [f'{y}-{m:02d}' for y, m in u['months']]}
            try:
                path, st = fut.result()
                rec['download'] = st
                t0 = time.time()
                conv = {('cds', 'plev'): convert_cds_plev, ('cds', 'surf'): convert_cds_surf,
                        ('ncar', 'plev'): convert_ncar, ('ncar', 'surf'): convert_ncar_surf}[(u['src'], u['kind'])]
                ds = conv(path, u, cfg)
                rec['ranges'] = check_unit(ds, u, cfg)
                rec['store_index'] = write_region(cfg, ds)
                rec['write_s'] = round(time.time() - t0, 1)
                path.unlink()                                      # only after a verified write
                rec['status'] = 'written'
                n_ok += 1
                log.info(f'{u["id"]}: written and verified (time index {rec["store_index"]}, {rec["write_s"]}s), raw deleted')
            except Exception as e:  # noqa: BLE001 - one bad unit must not stop the run
                rec.update(status='failed', error=f'{type(e).__name__}: {e}'[:800])
                n_fail += 1
                log.error(f'{u["id"]}: FAILED: {rec["error"]}')
            append_manifest(cfg, rec)
            # month complete? -> validate it
            done = {k for k, r in read_manifest(cfg).items() if r['status'] in DONE_STATES}
            for ym in u['months']:
                if month_units[ym] <= done and (rec['status'] == 'written'):
                    rep = validate_month(cfg, *ym)
                    log.info(f'month {ym[0]}-{ym[1]:02d} complete: validation {"OK" if rep["ok"] else "FAILED"}')
    log.info(f'run finished in {(time.time() - t_run) / 60:.1f} min: {n_ok} written, {n_fail} failed')
    return 1 if n_fail else 0


def cmd_status(cfg, args):
    units = make_units(cfg)
    man = read_manifest(cfg)
    rows = {}
    for u in units:
        st = man.get(u['id'], {}).get('status', 'missing')
        for y, m in u['months']:
            rows.setdefault(f'{y}-{m:02d}', {}).setdefault(st, []).append(u['id'])
    vdir = cfg['paths']['state'] / 'months'
    print(f'store: {cfg["paths"]["store"]} ({"exists" if cfg["paths"]["store"].exists() else "not created yet"})')
    print(f'{"month":8s} {"written":>8s} {"failed":>7s} {"missing":>8s}  validation')
    tot = {'written': 0, 'failed': 0, 'missing': 0}
    for mth in sorted(rows):
        r = rows[mth]
        f = vdir / f'{mth}.json'
        val = ('OK' if json.loads(f.read_text())['ok'] else 'FAILED') if f.exists() else '-'
        print(f'{mth:8s} {len(r.get("written", [])):8d} {len(r.get("failed", [])):7d} {len(r.get("missing", [])):8d}  {val}')
    for u in units:
        tot[man.get(u['id'], {}).get('status', 'missing')] = tot.get(man.get(u['id'], {}).get('status', 'missing'), 0) + 1
    print(f'units: {tot} of {len(units)} (SURF units span several months and are counted in each)')
    for k, r in man.items():
        if r['status'] == 'failed':
            print(f'  failed {k}: {r.get("error", "")[:200]}')
    raw = list(cfg['paths']['raw'].glob('*')) if cfg['paths']['raw'].exists() else []
    if raw:
        print(f'raw files waiting/leftover: {len(raw)}')
    return 0


def spot_check(cfg, client, months, n=1, seed=None):
    """Independent single-item CDS requests (1 var, 1 level, 1 hour, drawn from `months`) compared with the store."""
    rng = random.Random(seed)
    ds = xr.open_zarr(cfg['paths']['store'])
    hours = np.concatenate([month_hours(y, m, cfg).values for y, m in months])
    out = []
    for _ in range(n):
        v = rng.choice(cfg['plev_vars'])
        lev = rng.choice(cfg['levels'])
        t = pd.Timestamp(rng.choice(hours))
        unit = {'kind': 'plev', 'vars': [v], 'months': [(t.year, t.month)]}
        name, req = cds_request(cfg, unit)
        req.update(day=[f'{t.day:02d}'], time=[f'{t.hour:02d}:00'], pressure_level=[str(int(lev))])
        f = cfg['paths']['raw'] / f'spotcheck_{v}_{int(lev)}_{t:%Y%m%dT%H}.grib'
        t0 = time.time()
        client.retrieve(name, req, str(f))
        ref = _std_grid(xr.open_dataset(f, engine='cfgrib', backend_kwargs={'indexpath': ''}), cfg)[v].squeeze()
        got = ds[v].sel(time=t, level=lev).load()
        diff = float(np.abs(got.values - ref.values.astype('float32')).max())
        r = {'var': v, 'level': lev, 'time': str(t), 'max_abs_diff': diff, 'identical': diff == 0.0,
             'ref_range': [float(ref.min()), float(ref.max())], 'request_s': round(time.time() - t0)}
        log.info(f'spot check {r}')
        out.append(r)
        f.unlink()
    return out


def cmd_verify(cfg, args):
    months = [ym for ym in months_in(cfg) if args.months is None or ym[1] in args.months]
    ok = True
    for y, m in months:
        rep = validate_month(cfg, y, m)
        bad = {v: r for v, r in rep['vars'].items() if r['nan']}
        log.info(f'{y}-{m:02d}: {"OK" if rep["ok"] else "FAILED"} hours {rep["hours"]}/{rep["hours_expected"]}, '
                 f'levels {rep["levels"]}' + (f', NaN in {list(bad)}' if bad else ''))
        ok &= rep['ok']
    res = {'months': {f'{y}-{m:02d}': validate_month(cfg, y, m, write=False)['ok'] for y, m in months}}
    edh = cfg['paths'].get('edh_store')
    if edh and Path(edh).exists():
        res['edh_comparison'] = compare_edh(cfg, edh, months)
    if args.spot_checks:
        good = [ym for ym in months if res['months'][f'{ym[0]}-{ym[1]:02d}']]   # only months that passed
        if not good:
            raise ValueError('no complete month to spot-check')
        res['spot_checks'] = spot_check(cfg, cds_client(), good, args.spot_checks, args.seed)
        ok &= all(r['identical'] or r['max_abs_diff'] < 1e-3 * max(1, abs(r['ref_range'][1])) for r in res['spot_checks'])
    f = cfg['paths']['state'] / f'verify_{time.strftime("%Y%m%d_%H%M%S")}.json'
    f.write_text(json.dumps(res, indent=1, default=str))
    log.info(f'verify {"OK" if ok else "FAILED"}; report {f}')
    return 0 if ok else 1


def unit_hours(unit, cfg):
    if 'day' in unit:
        y, m = unit['months'][0]
        return pd.date_range(f'{y}-{m:02d}-{unit["day"]:02d}', periods=24, freq='1h')
    return pd.DatetimeIndex(np.concatenate([month_hours(y, m, cfg).values for y, m in unit['months']]))


def cmd_import(cfg, args):
    """Fill units from another store that already holds them (same grid; its levels must include ours),
    e.g. the 37-level test store -> the 600-1000 hPa store. Same checks, read-back and manifest as a download;
    units the source doesn't hold completely are left for `download`."""
    for k in ('raw', 'state'):
        cfg['paths'][k].mkdir(parents=True, exist_ok=True)
    init_store(cfg)
    src = xr.open_zarr(args.source)
    lat, lon = grid(cfg)
    if not (np.allclose(src.latitude, lat) and np.allclose(src.longitude, lon)):
        raise ValueError(f'{args.source}: other grid than the config')
    done = {k for k, r in read_manifest(cfg).items() if r['status'] in DONE_STATES}
    todo = [u for u in make_units(cfg, args.months, args.only) if u['id'] not in done]
    log.info(f'import from {args.source}: {len(todo)} units to try')
    n = 0
    touched = set()
    for u in todo:
        t0 = time.time()
        try:
            ds = src[u['vars']].sel(time=unit_hours(u, cfg))
            if u['kind'] == 'plev':
                ds = ds.sel(level=cfg['levels'])
            ds = ds.astype('float32').load()
            ranges = check_unit(ds, u, cfg)                 # raises if anything is missing (NaN)
        except (KeyError, ValueError) as e:
            log.info(f'{u["id"]}: not complete in the source ({type(e).__name__}: {str(e)[:120]}), skipped')
            continue
        idx = write_region(cfg, ds)
        append_manifest(cfg, {'id': u['id'], 'kind': u['kind'], 'src': u['src'], 'vars': u['vars'],
                              'months': [f'{y}-{m:02d}' for y, m in u['months']], 'status': 'written',
                              'imported_from': str(args.source), 'ranges': ranges, 'store_index': idx,
                              'write_s': round(time.time() - t0, 1)})
        touched.update(u['months'])
        n += 1
    log.info(f'imported {n} units')
    done = {k for k, r in read_manifest(cfg).items() if r['status'] in DONE_STATES}
    month_units = {}
    for u in make_units(cfg):
        for ym in u['months']:
            month_units.setdefault(ym, set()).add(u['id'])
    for ym in sorted(touched):
        if month_units[ym] <= done:
            rep = validate_month(cfg, *ym)
            log.info(f'month {ym[0]}-{ym[1]:02d} complete: validation {"OK" if rep["ok"] else "FAILED"}')
    return 0


def compare_edh(cfg, edh_path, months):
    """Differences to the EDH store at the shared levels/hours (EDH holds the same ERA5 analysis)."""
    a = xr.open_zarr(cfg['paths']['store'])
    b = xr.open_zarr(edh_path)
    t = pd.DatetimeIndex(np.concatenate([month_hours(y, m, cfg).values for y, m in months]))
    t = t.intersection(pd.DatetimeIndex(b.time.values))
    lev = sorted(set(a.level.values.tolist()) & set(b.level.values.tolist()))
    out = {'levels': lev, 'hours': len(t)}
    for v in cfg['plev_vars'] + cfg['surf_vars']:
        if v not in b:
            continue
        x = a[v].sel(time=t)
        y = b[v].sel(time=t)
        if 'level' in x.dims:
            x, y = x.sel(level=lev), y.sel(level=lev)
        d = np.abs(x.values - y.values)
        out[v] = {'max_abs_diff': float(np.nanmax(d)), 'mean_abs_diff': float(np.nanmean(d)),
                  'frac_identical': float((d == 0).mean())}
        log.info(f'EDH vs store {v}: {out[v]}')
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('config')
    p.add_argument('command', choices=['download', 'status', 'verify', 'import'])
    p.add_argument('--from', dest='source', help='import: the store to copy from')
    p.add_argument('--months', type=int, nargs='+', help='only these months (1-12) of the period')
    p.add_argument('--only', choices=['plev', 'surf'])
    p.add_argument('--spot-checks', type=int, default=0, help='verify: independent single CDS requests')
    p.add_argument('--seed', type=int, default=None)
    args = p.parse_args(argv)
    cfg = load_config(args.config)
    if args.command != 'status':
        setup_logging(cfg, args.command)
    if args.command == 'import' and not args.source:
        p.error('import needs --from STORE')
    return {'download': cmd_download, 'status': cmd_status, 'verify': cmd_verify,
            'import': cmd_import}[args.command](cfg, args)


if __name__ == '__main__':
    sys.exit(main())
