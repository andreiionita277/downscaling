#!/usr/bin/env bash
# ERA5 600-1000 hPa, several lanes sharing ONE queue of years (replaces the fixed lists of run_era5_lane.sh,
# 2026-11-06, so a third lane can be added without two lanes ever writing the same year). Inside the container:
#   bash rila_pirin_zarr/scripts/run_era5_queue.sh A 1985 2025 >> rila_pirin_zarr/data/logs/era5_queue_A.log 2>&1
# Each lane takes the next year that has no ERA5_OK and is not claimed, claims it with an atomic `mkdir YYYY.lock`
# (owner inside), runs the same steps as run_era5_lane.sh (import / download x2 / status / verify -> ERA5_OK) and
# releases the lock. Years already started (folder exists) come first. A year that does not verify gets an
# ERA5_FAILED line and is retried once by the lane's second pass (when the queue is empty).
# Stale locks: after killing lanes, remove data/era5_600-1000hPa/*.lock before starting them again.
set -uo pipefail
R=rila_pirin_zarr; E5=$R/data/era5_600-1000hPa
NAME=${1:?lane name}; Y0=${2:?first year}; Y1=${3:?last year}
stamp() { date -u +'%Y-%m-%d %H:%M:%S UTC'; }
order() {   # started years first, then the rest, ascending
  for y in $(seq "$Y0" "$Y1"); do [ -d "$E5/$y" ] && echo "$y"; done
  for y in $(seq "$Y0" "$Y1"); do [ -d "$E5/$y" ] || echo "$y"; done
}
one_year() {
  local Y=$1 E=$R/config/era5/config_era5_$1.yml D=$E5/$1
  echo "[$(stamp)] $NAME: $Y start"
  local OLD=zarr_workflow/data/era5_600-1000hPa/$Y/ERA5.zarr
  if [ -d "$OLD" ]; then python zarr_workflow/era5_download/era5_download.py "$E" import --from "$OLD"; fi
  python zarr_workflow/era5_download/era5_download.py "$E" download \
    || python zarr_workflow/era5_download/era5_download.py "$E" download
  python zarr_workflow/era5_download/era5_download.py "$E" status
  if python zarr_workflow/era5_download/era5_download.py "$E" verify; then
    date -u > "$D/ERA5_OK"; echo "[$(stamp)] $NAME: $Y VERIFIED"; return 0
  fi
  mkdir -p "$D"; date -u >> "$D/ERA5_FAILED"; echo "[$(stamp)] $NAME: $Y NOT complete/verified"; return 1
}
mkdir -p "$E5"
for pass in 1 2; do
  for Y in $(order); do
    [ -f "$E5/$Y/ERA5_OK" ] && continue
    [ "$pass" = 1 ] && [ -f "$E5/$Y/ERA5_FAILED" ] && continue     # failed years wait for the second pass
    [ "$pass" = 2 ] && [ "$(wc -l < "$E5/$Y/ERA5_FAILED" 2>/dev/null || echo 0)" -ge 2 ] && continue
    mkdir "$E5/$Y.lock" 2>/dev/null || continue                     # claimed by another lane
    echo "$NAME pid $$ since $(stamp)" > "$E5/$Y.lock/owner"
    one_year "$Y"
    rm -rf "$E5/$Y.lock"
  done
done
echo "[$(stamp)] $NAME: lane done (queue empty)"
