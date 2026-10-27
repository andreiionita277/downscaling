#!/usr/bin/env bash
# Whole chain for one year, inside the container (cd /app first):
#   1 ERA5 600-1000 hPa + single levels -> data/era5_600-1000hPa/YYYY/ERA5.zarr   (era5_download.py, resumable)
#   2 status + verify of that store (every month: all hours, 14 levels, no NaN)
#   3 run_year.py: TopoSUB/TopoSCALE month by month -> runs/YYYY_era5_600-1000hPa/outputs
#     (hourly down.zarr, daily.zarr, daily GeoTIFFs, month checks)
# Usage:  bash zarr_workflow/scripts/run_full_year.sh 2020 [--spot-checks N]
# Needs zarr_workflow/era5_download/config_era5_YYYY.yml and zarr_workflow/config/config_YYYY_era5_600-1000hPa.yml.
# Stops at the first failing step; rerunning resumes (finished units / months are skipped).
set -euo pipefail
Y=${1:?year}
SPOT=${3:-0}
E=zarr_workflow/era5_download/config_era5_${Y}.yml
C=zarr_workflow/config/config_${Y}_era5_600-1000hPa.yml
LOGDIR=zarr_workflow/data/runs/${Y}_era5_600-1000hPa/logs
mkdir -p "$LOGDIR"
stamp() { date -u +'%Y-%m-%d %H:%M:%S UTC'; }

echo "[$(stamp)] 1/3 ERA5 download $Y"
# a unit that still fails after its retries makes download exit 1: one more pass retries only those
python zarr_workflow/era5_download/era5_download.py "$E" download \
  || python zarr_workflow/era5_download/era5_download.py "$E" download
echo "[$(stamp)] 2/3 status + verify"
python zarr_workflow/era5_download/era5_download.py "$E" status
python zarr_workflow/era5_download/era5_download.py "$E" verify --spot-checks "$SPOT"
echo "[$(stamp)] 3/3 run_year $Y"
python zarr_workflow/scripts/run_year.py "$C" > "$LOGDIR/run_year_$(date -u +%Y%m%d_%H%M).log" 2>&1
echo "[$(stamp)] DONE $Y"
