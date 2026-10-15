"""
Multi-year TopoPyScale point-mode downscaling wrapper.

Orchestrates TopoPyScale's own methods (nothing about the downscaling
science is reimplemented here) across an arbitrary year range, one year at
a time, so a multi-decade run is resumable and debuggable rather than one
all-or-nothing job:

  1. FETCH  — ensure that year's ERA5 PLEV/SURF data exists (monthly-batched
     CDS requests via era5_batch_fetch.py, ~30x fewer requests than
     TopoPyScale's own day-by-day fetch_era5.py at this scale).
  2. DOWNSCALE — run the proven classic multicore/NetCDF path (the same one
     validated for 2022-11-27/28) for just that year, writing into its own
     outputs_points/yearly/<year>/ directory. Terrain-only caches (ds_param.nc,
     da_horizon.nc, df_centroids.pck) are symlinked in from the shared
     outputs_points/ directory rather than recomputed — they don't depend on
     the date range.
  3. EXPORT — (run once, any time, over however many years are done so far)
     combine every year's output.nc into one Zarr store via plain xarray —
     the final deliverable format, kept as a simple/low-risk conversion
     rather than routing the live compute through TopoPyScale's own
     ClimateDownscaler dask/zarr path, which is far less exercised by this
     project and has its own separate bugs found only by reading the source
     (see PROGRESS.md Bug #12).

Resumability is filesystem-based (does the expected output file already
exist?), matching TopoPyScale's own caching philosophy, rather than a
separate state file that could drift from reality.

Usage:
    docker compose exec -T toposcale python3 \\
        bulgaria_rila_pirin/scripts/run_downscaling.py \\
        --config bulgaria_rila_pirin/config_point.yml \\
        --start-year 1980 --end-year 2025

    # Specific years only:
    ... --years 1980,1981,1990

    # Just one phase (e.g. to fetch data overnight before downscaling):
    ... --phase fetch

    # Preview without doing anything:
    ... --dry-run

    # Redo a year even though it looks complete:
    ... --years 1985 --force
"""
import argparse
import logging
import sys
import time
import traceback
from pathlib import Path

import yaml
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from era5_batch_fetch import ensure_year_climate

REPO_ROOT = Path("/app")
if not REPO_ROOT.exists():
    REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_FILE = REPO_ROOT / "bulgaria_rila_pirin" / "logs.md"

logger = logging.getLogger("run_downscaling")
logger.setLevel(logging.INFO)


def setup_logging():
    logger.handlers.clear()
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(console)

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(LOG_FILE, mode="a")
    file_handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(file_handler)


def log(msg):
    logger.info(msg)


def log_header(msg):
    logger.info("")
    logger.info(f"## {msg}")


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def write_yaml(data, path):
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def build_year_config(base_config, year, shared_outputs_dir, year_dir):
    """Return a config dict for one year, derived from the base config."""
    cfg = load_yaml(base_config)
    cfg["project"]["start"] = f"{year}-01-01"
    cfg["project"]["end"] = f"{year}-12-31"
    # outputs.directory is resolved relative to project.directory by Topoclass.
    cfg["outputs"]["directory"] = str(year_dir.relative_to(REPO_ROOT / "bulgaria_rila_pirin"))
    # Never let a per-year run wipe another year's directory.
    cfg["outputs"]["file"]["clean_outputs"] = False
    return cfg


def symlink_shared_caches(shared_dir, year_dir):
    for fname in ["ds_param.nc", "da_horizon.nc", "df_centroids.pck"]:
        link = year_dir / fname
        target = shared_dir / fname
        if not link.exists() and target.exists():
            link.symlink_to(Path("..") / ".." / fname)


def year_is_done(year_dir):
    output_nc = year_dir / "output.nc"
    if not output_nc.exists():
        return False
    try:
        ds = xr.open_dataset(output_nc)
        ok = "time" in ds.dims and ds.sizes["time"] > 0
        ds.close()
        return ok
    except Exception:
        return False


def run_fetch(year, mp, plevels, dry_run):
    bbox = [
        mp.config.project.extent["latN"],
        mp.config.project.extent["lonW"],
        mp.config.project.extent["latS"],
        mp.config.project.extent["lonE"],
    ]
    if dry_run:
        log(f"  [dry-run] would ensure ERA5 PLEV/SURF for {year} (bbox={bbox})")
        return
    ensure_year_climate(year, mp.config.climate.path, bbox, plevels, log=log)


def run_downscale(mp, dry_run):
    if dry_run:
        log("  [dry-run] would run compute_dem_param -> extract_topo_param -> "
            "compute_solar_geometry -> compute_horizon -> downscale_climate -> to_netcdf")
        return
    mp.compute_dem_param()
    mp.extract_topo_param()
    mp.compute_solar_geometry()
    mp.compute_horizon()
    mp.downscale_climate()
    mp.to_netcdf()


def run_export(yearly_dir, out_zarr, dry_run):
    if not yearly_dir.exists():
        log("  no completed years found, nothing to export")
        return
    years_done = sorted(
        int(p.name) for p in yearly_dir.iterdir()
        if p.is_dir() and p.name.isdigit() and year_is_done(p)
    )
    if not years_done:
        log("  no completed years found, nothing to export")
        return
    log(f"  combining {len(years_done)} completed year(s): {years_done}")
    if dry_run:
        log(f"  [dry-run] would write combined Zarr store to {out_zarr}")
        return
    datasets = [xr.open_dataset(yearly_dir / str(y) / "output.nc") for y in years_done]
    combined = xr.concat(datasets, dim="time").sortby("time")
    combined = combined.chunk({"time": 8760, "point_name": -1})
    combined.to_zarr(out_zarr, mode="w")
    for ds in datasets:
        ds.close()
    log(f"  wrote {out_zarr} (years: {years_done[0]}-{years_done[-1]})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="Path to the base point-mode config.yml")
    ap.add_argument("--start-year", type=int)
    ap.add_argument("--end-year", type=int)
    ap.add_argument("--years", help="Comma-separated specific years, overrides --start-year/--end-year")
    ap.add_argument("--phase", choices=["fetch", "downscale", "export", "all"], default="all")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="Redo years even if already marked complete")
    ap.add_argument("--zarr-out", default=None, help="Output path for the combined Zarr store (export phase)")
    args = ap.parse_args()

    setup_logging()

    if args.years:
        years = [int(y) for y in args.years.split(",")]
    elif args.start_year and args.end_year:
        years = list(range(args.start_year, args.end_year + 1))
    else:
        ap.error("Provide --years, or both --start-year and --end-year")

    base_config = Path(args.config).resolve()
    base_config_data = load_yaml(base_config)
    shared_outputs_dir = (REPO_ROOT / "bulgaria_rila_pirin" / base_config_data["outputs"]["directory"]).resolve()
    yearly_dir = shared_outputs_dir / "yearly"
    if not args.dry_run:
        yearly_dir.mkdir(parents=True, exist_ok=True)
    plevels = base_config_data["climate"]["era5"]["plevels"]
    zarr_out = Path(args.zarr_out) if args.zarr_out else shared_outputs_dir / f"output_{years[0]}_{years[-1]}.zarr"

    log_header(f"run_downscaling.py started — years={years[0]}-{years[-1]} phase={args.phase} "
               f"dry_run={args.dry_run} force={args.force}")

    if args.phase in ("fetch", "downscale", "all"):
        for year in years:
            year_dir = yearly_dir / str(year)
            if not args.dry_run:
                year_dir.mkdir(parents=True, exist_ok=True)

            if not args.force and year_is_done(year_dir) and args.phase != "fetch":
                log(f"Year {year}: already complete (output.nc present and valid), skipping")
                continue

            log_header(f"Year {year}")
            start_time = time.time()
            try:
                cfg = build_year_config(base_config, year, shared_outputs_dir, year_dir)
                per_year_config_path = year_dir / "config_point.yml"
                if not args.dry_run:
                    write_yaml(cfg, per_year_config_path)
                    symlink_shared_caches(shared_outputs_dir, year_dir)

                if args.dry_run:
                    log(f"  [dry-run] would write config to {per_year_config_path}")
                    log(f"  [dry-run] would symlink shared caches into {year_dir}")
                    from TopoPyScale import topoclass as tc  # noqa: F401 (import check only)
                    continue

                from TopoPyScale import topoclass as tc
                mp = tc.Topoclass(str(per_year_config_path))

                if args.phase in ("fetch", "all"):
                    run_fetch(year, mp, plevels, args.dry_run)

                if args.phase in ("downscale", "all"):
                    run_downscale(mp, args.dry_run)

                elapsed = time.time() - start_time
                log(f"Year {year}: done in {elapsed:.0f}s")
            except Exception as e:
                elapsed = time.time() - start_time
                log(f"Year {year}: FAILED after {elapsed:.0f}s — {type(e).__name__}: {e}")
                log(traceback.format_exc())
                log(f"Year {year}: stopping here. Re-run the same command to resume — "
                    f"completed years are skipped automatically.")
                sys.exit(1)

    if args.phase in ("export", "all"):
        log_header("Export to Zarr")
        run_export(yearly_dir, zarr_out, args.dry_run)

    log_header("run_downscaling.py finished")


if __name__ == "__main__":
    main()