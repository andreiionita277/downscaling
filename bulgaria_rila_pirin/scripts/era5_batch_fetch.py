"""
Custom batched ERA5 fetch for multi-year TopoPyScale runs.

TopoPyScale's own fetch_era5.py (retrieve_era5 / era5_request_surf / era5_request_plev)
issues one CDS API request PER DAY. That's fine for a one/two-day validation
run, but impractical at multi-year scale: 1980-2025 is ~16,800 days x 2
datasets (surf, plev) = ~33,600 individual requests, which risks CDS
rate-limiting/throttling independent of actual compute cost.

Empirically found (2026-09-29 pilot, see PROGRESS.md/logs.md) that CDS's
per-request "cost" limit for PLEV sits below a full calendar month
(~62,500 fields: 6 vars x 14 levels x 744h -- rejected outright with "403
cost limits exceeded") and even below a 7-day chunk (~14,112 fields --
also rejected). Our only previously-proven-safe PLEV request was actually a
single DAY (2,016 fields: 6 vars x 14 levels x 24h, from the original
2022-11-27/28 validation run -- note that run's "2 days" was always two
separate 1-day requests, never tested combined). 3-day chunks (~6,048
fields) DID succeed (confirmed 2026-09-30, resuming after the prior
session ended mid-pilot) -- so that's the current default. SURF has no
pressure-level dimension (7 vars x 1 level), so a full month (~5,200
fields) fetches fine at monthly granularity.

So: SURF is batched MONTHLY (~30x fewer requests than daily, proven to
work), PLEV is batched in 3-DAY chunks (~6,048 fields/request, proven to
work -- still ~2.5x fewer requests than daily). Both are merged into the
same yearly PLEV_{year}.nc / SURF_{year}.nc format the rest of the pipeline
already expects, via the same `cdo.mergetime` call TopoPyScale's own
retrieve_era5() uses -- so nothing downstream (downscale_climate's glob,
the symlink convention) needs to change.

Reuses TopoPyScale's own `unzip_file()` helper directly (identical CDS
zip-response handling) rather than reimplementing it. Also matches
TopoPyScale's own concurrency pattern for the actual requests: a thread
pool (`multiprocessing.dummy.Pool`, the same "ThreadPool" class
`retrieve_era5()` itself uses) submits several chunks to CDS at once
instead of waiting for each one's queue time sequentially -- each request
is I/O-bound (waiting on CDS), not CPU-bound, so threads (not processes)
are the right tool here, same as the library's own choice.
"""
import calendar
from multiprocessing.dummy import Pool as ThreadPool
from pathlib import Path

import cdsapi
from cdo import Cdo
from TopoPyScale.fetch_era5 import unzip_file

SURF_VAROI = [
    "geopotential",
    "2m_dewpoint_temperature",
    "surface_thermal_radiation_downwards",
    "surface_solar_radiation_downwards",
    "surface_pressure",
    "total_precipitation",
    "2m_temperature",
]
PLEV_VAROI = [
    "geopotential",
    "temperature",
    "u_component_of_wind",
    "v_component_of_wind",
    "specific_humidity",
    "relative_humidity",
]
ALL_HOURS = [f"{h:02d}:00" for h in range(24)]


def _week_chunks_for_month(year, month, chunk_days=3):
    """Split a month's days into ~chunk_days-sized groups, e.g. [1-3],[4-6],..."""
    n_days = calendar.monthrange(year, month)[1]
    chunks = []
    day = 1
    while day <= n_days:
        end = min(day + chunk_days - 1, n_days)
        chunks.append([f"{d:02d}" for d in range(day, end + 1)])
        day = end + 1
    return chunks


def _fetch_chunk(dataset, year, month, days, bbox, target, varoi, plevels=None, log=print):
    if target.exists():
        log(f"    {target.name} already exists, skipping fetch")
        return
    c = cdsapi.Client()
    request = {
        "product_type": ["reanalysis"],
        "variable": varoi,
        "area": bbox,
        "year": [str(year)],
        "month": [f"{month:02d}"],
        "day": days,
        "time": ALL_HOURS,
        "grid": "0.25/0.25",
        "data_format": "netcdf",
        "download_format": "unarchived",
    }
    if plevels is not None:
        request["pressure_level"] = [str(p) for p in plevels]
    log(f"    requesting {dataset} {year}-{month:02d} days {days[0]}-{days[-1]} -> {target.name}")
    c.retrieve(dataset, request, str(target))
    log(f"    {target.name} downloaded")
    unzip_file(str(target))


def _run_jobs_concurrently(jobs, num_threads, log):
    """jobs: list of kwargs-dicts for _fetch_chunk. Runs up to num_threads at once."""
    if not jobs:
        return
    pool = ThreadPool(min(num_threads, len(jobs)))
    try:
        pool.map(lambda kw: _fetch_chunk(**kw), jobs)
    finally:
        pool.close()
        pool.join()


def ensure_year_climate(year, climate_dir, bbox, plevels, months=range(1, 13), log=print, num_threads=4):
    """
    Ensure PLEV_{year}.nc and SURF_{year}.nc exist under climate_dir/yearly/
    (plus the top-level symlinks downscale_climate() globs for), fetching and
    merging batched requests only for whatever is still missing. Safe to call
    repeatedly / resume -- every step checks for existing output first.
    Fetches (both SURF months and PLEV chunks) run up to num_threads at a
    time via a thread pool, rather than waiting on each CDS queue turn
    sequentially -- matches TopoPyScale's own retrieve_era5() concurrency
    pattern.

    Returns (plev_path, surf_path).
    """
    climate_dir = Path(climate_dir)
    monthly_dir = climate_dir / "monthly"
    yearly_dir = climate_dir / "yearly"
    monthly_dir.mkdir(parents=True, exist_ok=True)
    yearly_dir.mkdir(parents=True, exist_ok=True)

    yearly_surf = yearly_dir / f"SURF_{year}.nc"
    yearly_plev = yearly_dir / f"PLEV_{year}.nc"
    months = list(months)

    if not yearly_surf.exists():
        jobs = [
            dict(dataset="reanalysis-era5-single-levels", year=year, month=month,
                 days=[f"{d:02d}" for d in range(1, calendar.monthrange(year, month)[1] + 1)],
                 bbox=bbox, target=monthly_dir / f"mSURF_{year}{month:02d}.nc",
                 varoi=SURF_VAROI, log=log)
            for month in months
        ]
        log(f"  fetching SURF {year} ({len(jobs)} month(s), monthly batches, {num_threads} concurrent)")
        _run_jobs_concurrently(jobs, num_threads, log)
        cdo = Cdo()
        cdo.mergetime(input=str(monthly_dir / f"mSURF_{year}*.nc"), output=str(yearly_surf))
        log(f"  merged -> {yearly_surf.name}")
    else:
        log(f"  {yearly_surf.name} already exists, skipping SURF fetch")

    if not yearly_plev.exists():
        jobs = [
            dict(dataset="reanalysis-era5-pressure-levels", year=year, month=month, days=chunk,
                 bbox=bbox, target=monthly_dir / f"mPLEV_{year}{month:02d}_d{chunk[0]}-{chunk[-1]}.nc",
                 varoi=PLEV_VAROI, plevels=plevels, log=log)
            for month in months
            for chunk in _week_chunks_for_month(year, month)
        ]
        log(f"  fetching PLEV {year} ({len(jobs)} chunk(s), 3-day batches, {num_threads} concurrent)")
        _run_jobs_concurrently(jobs, num_threads, log)
        cdo = Cdo()
        cdo.mergetime(input=str(monthly_dir / f"mPLEV_{year}*.nc"), output=str(yearly_plev))
        log(f"  merged -> {yearly_plev.name}")
    else:
        log(f"  {yearly_plev.name} already exists, skipping PLEV fetch")

    # Top-level symlinks: downscale_climate() globs directly in climate_dir root.
    for name, path in [(f"SURF_{year}.nc", yearly_surf), (f"PLEV_{year}.nc", yearly_plev)]:
        link = climate_dir / name
        if not link.exists():
            link.symlink_to(Path("yearly") / name)

    return yearly_plev, yearly_surf