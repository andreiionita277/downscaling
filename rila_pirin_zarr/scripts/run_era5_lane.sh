#!/usr/bin/env bash
# ERA5 600-1000 hPa for a list of years, one after the other (inside the container, cd /app):
#   bash rila_pirin_zarr/scripts/run_era5_lane.sh 2025 1985 1986 ...
# Per year: import what an older store already holds (zarr_workflow 2020 / 2025), download the rest from NCAR
# (two passes: the second retries units that failed after their retries), status, verify. A verified year gets
# data/era5_600-1000hPa/YYYY/ERA5_OK, which run_downscale_all.sh waits for. Years that fail are retried once at
# the end of the lane. Rerunning resumes (written units are skipped).
set -uo pipefail
R=rila_pirin_zarr
stamp() { date -u +'%Y-%m-%d %H:%M:%S UTC'; }
one_year() {
  local Y=$1 E=$R/config/era5/config_era5_$1.yml D=$R/data/era5_600-1000hPa/$1
  [ -f "$D/ERA5_OK" ] && { echo "[$(stamp)] $Y already verified"; return 0; }
  echo "[$(stamp)] $Y start"
  local OLD=zarr_workflow/data/era5_600-1000hPa/$Y/ERA5.zarr
  if [ -d "$OLD" ]; then python zarr_workflow/era5_download/era5_download.py "$E" import --from "$OLD"; fi
  python zarr_workflow/era5_download/era5_download.py "$E" download \
    || python zarr_workflow/era5_download/era5_download.py "$E" download
  python zarr_workflow/era5_download/era5_download.py "$E" status
  if python zarr_workflow/era5_download/era5_download.py "$E" verify; then
    date -u > "$D/ERA5_OK"; echo "[$(stamp)] $Y VERIFIED"; return 0
  fi
  echo "[$(stamp)] $Y NOT complete/verified"; return 1
}
failed=()
for Y in "$@"; do one_year "$Y" || failed+=("$Y"); done
for Y in "${failed[@]}"; do one_year "$Y"; done
echo "[$(stamp)] lane done: $*"
