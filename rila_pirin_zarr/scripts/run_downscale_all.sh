#!/usr/bin/env bash
# Downscale every year whose ERA5 store is verified (inside the container, cd /app):
#   bash rila_pirin_zarr/scripts/run_downscale_all.sh 1985 2025 [edh]
# With 'edh': the EDH series (config/years_edh, data/runs_edh); run_year.py fetches EDH itself, no ERA5_OK needed.
# Waits for the terrain (data/terrain/outputs/terrain_summary.json) and for each year's ERA5_OK; runs
# run_year.py one year at a time (log data/runs/YYYY/logs/), marks data/runs/YYYY/DONE when the year passed.
# A year that fails is retried once on a later pass, then left (FAILED file) for inspection.
# Ends when every year is DONE or FAILED. Rerunning resumes (finished months are skipped by run_year.py).
set -uo pipefail
R=rila_pirin_zarr
stamp() { date -u +'%Y-%m-%d %H:%M:%S UTC'; }
Y0=${1:?first year}; Y1=${2:?last year}; V=${3:-}
CONF=$R/config/years; RUNS=$R/data/runs
[ "$V" = edh ] && CONF=$R/config/years_edh && RUNS=$R/data/runs_edh
until [ -f $R/data/terrain/outputs/terrain_summary.json ]; do sleep 120; done
echo "[$(stamp)] terrain ready"
while true; do
  left=0; ran=0
  for Y in $(seq "$Y0" "$Y1"); do
    D=$RUNS/$Y
    [ -f "$D/DONE" ] || [ -f "$D/FAILED" ] && continue
    left=1
    [ "$V" = edh ] || [ -f "$R/data/era5_600-1000hPa/$Y/ERA5_OK" ] || continue
    mkdir -p "$D/logs"
    echo "[$(stamp)] $Y downscaling"
    if python $R/scripts/run_year.py $CONF/config_$Y.yml > "$D/logs/run_year_$(date -u +%Y%m%d_%H%M).log" 2>&1; then
      date -u > "$D/DONE"; echo "[$(stamp)] $Y DONE"
    elif [ -f "$D/RETRIED" ]; then
      date -u > "$D/FAILED"; echo "[$(stamp)] $Y FAILED (see $D/logs)"
    else
      date -u > "$D/RETRIED"; echo "[$(stamp)] $Y failed, will retry once"
    fi
    ran=1
  done
  [ $left -eq 0 ] && break
  [ $ran -eq 0 ] && sleep 600
done
echo "[$(stamp)] all years done: $(ls $RUNS/*/DONE 2>/dev/null | wc -l) DONE, $(ls $RUNS/*/FAILED 2>/dev/null | wc -l) FAILED"
