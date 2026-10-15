# =============================================================================
# TopoPyScale — base single-run pipeline (tutorial)
# =============================================================================
# This is the canonical 6-step TopoPyScale workflow, straight from the
# project's own quickstart docs (https://topopyscale.readthedocs.io/latest/).
# It runs ONE config (one date range, one set of output points/clusters) from
# start to finish. Every project-specific pipeline in this repo
# (bulgaria_rila_pirin/scripts/pipeline.py, pipeline_point.py) is a copy of
# this same template, just pointed at a different config.yml.
#
# HOW TO RUN THIS FILE DIRECTLY (single date range, e.g. a one-off test):
#   docker compose exec -T toposcale python3 pipeline.py
# (edit config_file below, or the config.yml it points to, before running)
#
# FOR A MULTI-YEAR RUN (e.g. 1980-2025), don't loop this file by hand — use
# the dedicated wrapper instead, which handles per-year ERA5 fetching in
# efficient monthly batches (not the day-by-day fetch this file's get_era5()
# step uses, impractical at multi-year scale), resumability (skips any
# year/phase that's already done, safe to re-run after a crash), logging to
# bulgaria_rila_pirin/logs.md, and a final combined Zarr export:
#
#   docker compose exec -T toposcale python3 \
#       bulgaria_rila_pirin/scripts/run_downscaling.py \
#       --config bulgaria_rila_pirin/config_point.yml \
#       --start-year 1980 --end-year 2025
#
# See bulgaria_rila_pirin/scripts/run_downscaling.py for the full flag list
# (--phase fetch|downscale|export|all, --years for specific years, --dry-run,
# --force to redo a year that's already marked complete).
# =============================================================================

from TopoPyScale import topoclass as tc
from matplotlib import pyplot as plt

config_file = './config.yml'
mp = tc.Topoclass(config_file)

# Step 1: DEM-derived parameters (slope, aspect, sky-view factor).
# No network needed. Cached to outputs/ds_param.nc — if that file already
# exists this step just loads it instead of recomputing (this is TRUE for
# every step below: TopoPyScale checks "does my output file already exist?"
# before doing any work, which is what makes re-running this script cheap
# once the expensive steps have completed once).
mp.compute_dem_param()

# Step 2: cluster the DEM (k-means, "toposub" mode) into a manageable number
# of representative terrain points, OR — if config.yml has
# `sampling.method: points` — sample DEM parameters at exact station/point
# coordinates instead of clustering (point mode; that's what
# pipeline_point.py and this project's config_point.yml use, for validating
# against real weather stations rather than a coarser cluster grid).
# Cached to outputs/df_centroids.pck.
mp.extract_topo_param()

# Step 3: solar geometry (sun position over the configured date range — this
# genuinely depends on the dates in config.yml, unlike steps 1/2/4 which are
# terrain-only and date-independent) and horizon angles (purely geometric,
# from the DEM — also date-independent, and by far the slowest single step
# on a full-resolution grid, which is why it's cached so aggressively).
# No network needed for either. Cached to outputs/ds_solar.nc and
# outputs/da_horizon.nc respectively.
mp.compute_solar_geometry()
mp.compute_horizon()

# Step 4: fetch ERA5 reanalysis climate forcing from the Copernicus Climate
# Data Store (CDS) — the only network-heavy step, and the one that does NOT
# scale well as written for multi-year date ranges (see the wrapper-script
# note at the top of this file). Needs a valid ~/.cdsapirc with your CDS API
# key. Downloaded to inputs/climate/ (daily/yearly subfolders + symlinks).
mp.get_era5()

# Step 5: the actual TopoScale downscaling — horizontal IDW interpolation
# from the ERA5 grid to each point/cluster, vertical pressure-level
# interpolation for the lapse-rate correction, plus radiation/horizon
# geometry corrections. This is the compute-heavy step (scales with
# number of points x number of timesteps).
mp.downscale_climate()

# Step 6: export the downscaled result to a single merged NetCDF file
# (outputs/output.nc), plus per-point files under outputs/downscaled/.
mp.to_netcdf()