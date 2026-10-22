#!/usr/bin/env bash
# Run several years of the Meridional Carpathians workflow one after another, each followed by its
# SURFEX-Crocus export, reusing the 2025 terrain (DEM parameters, clusters, horizon). Unattended:
# every step is retried, a year that keeps failing is logged and skipped, and a year is only started
# when enough disk space is left. Progress is appended to LOG_21Oct.md.
#
#   setsid nohup bash meridionali_zarr/scripts/run_years_overnight.sh 2023 2022 2021 2020 2019 2018 \
#       > meridionali_zarr/data/logs/overnight_driver.log 2>&1 &
#
# Run from ~/TopoPyScale-repo-github/downscaling.
set -u
cd "$(dirname "$0")/../.."                     # -> downscaling/
MZ=meridionali_zarr
PY=../venv/bin/python
LOGMD=$MZ/LOG_21Oct.md
TERRAIN=$MZ/data/meridionali_2025/outputs      # DEM parameters, clusters, horizon (same DEM and mask for every year)
TEMPLATE=$MZ/config/config_meridionali_2024.yml
MIN_FREE_GB=155                                # ~95 GB per year (87 GB GeoTIFFs) + margin

log() { echo "- $(date '+%H:%M:%S') $*" | tee -a "$LOGMD"; }
free_gb() { df -BG --output=avail "$MZ/data" | tail -1 | tr -dc '0-9'; }

for Y in "$@"; do
    echo -e "\n### Year $Y" >> "$LOGMD"
    OUT=$MZ/data/meridionali_$Y/outputs
    CFG=$MZ/config/config_meridionali_$Y.yml
    STAMP=$(date +%Y%m%d_%H%M)

    if [ "$(free_gb)" -lt "$MIN_FREE_GB" ]; then
        log "**Stopped before $Y: only $(free_gb) GB free (< $MIN_FREE_GB GB needed for a year + margin).**"
        break
    fi

    # 1. config: the 2024 config with the year replaced (DEM, mask, k, ERA5 settings unchanged)
    if [ ! -f "$CFG" ]; then
        sed "s/2024/$Y/g" "$TEMPLATE" > "$CFG"
        log "config \`$CFG\` written (2024 config with 2024 -> $Y; $(diff "$TEMPLATE" "$CFG" | grep -c '^>') lines differ)"
    fi

    # 2. terrain: hard links to the 2025 files (no extra disk). TopoPyScale replaces ds_param.nc with a new file
    #    when it loads it (writes outputs/tmp/ds_param.nc, then moves it over), so the 2025 file is never modified.
    mkdir -p "$OUT"
    for f in ds_param.nc df_centroids.pck da_horizon.nc; do
        [ -e "$OUT/$f" ] || ln "$TERRAIN/$f" "$OUT/$f"
    done
    log "terrain linked from 2025 (ds_param.nc, df_centroids.pck, da_horizon.nc); $(free_gb) GB free"

    # 3. full chain: ERA5 -> solar geometry -> downscaling -> daily statistics -> GeoTIFFs (+ checks).
    #    Resumable: a retry skips finished clusters and finished months. Fewer GeoTIFF workers on each retry.
    ok_run=0
    for attempt in 1 2 3; do
        workers=$((6 - attempt))                # 5, 4, 3
        RL=$MZ/data/logs/run_${Y}_${STAMP}_try$attempt.log
        log "run attempt $attempt ($workers GeoTIFF workers) -> \`$RL\`"
        t0=$(date +%s)
        /usr/bin/time -v $PY $MZ/scripts/run_year_public.py "$CFG" --product-workers $workers > "$RL" 2>&1
        rc=$?
        if grep -q "YEAR COMPLETE, ALL CHECKS PASSED" "$RL"; then
            log "run OK in $(( ($(date +%s) - t0) / 60 )) min: $(grep -E '^Downscaling|points done this run' "$RL" | tail -1 | sed 's/^---> //')"
            ok_run=1
            break
        fi
        reason=$(grep -E "Traceback|Error|signal|Killed|FAIL|NOT COMPLETE" "$RL" | tail -3 | tr '\n' ' ')
        oom=$(dmesg 2>/dev/null | tail -50 | grep -c "Out of memory")
        log "run attempt $attempt FAILED (exit $rc, out-of-memory kills in dmesg: $oom): ${reason:0:400}"
        rm -rf "$OUT"/tmp_* "$OUT"/geotiff/*.part* 2>/dev/null   # half-written temporaries only
        sleep 30
    done
    if [ $ok_run -eq 0 ]; then
        log "**$Y: run failed 3 times, skipped (see the run logs).**"
        continue
    fi

    # 4. SURFEX-Crocus forcing (one file, the calendar year, 23 h end-of-year gap fixed)
    ok_sfx=0
    for attempt in 1 2; do
        SL=$MZ/data/logs/surfex_${Y}_${STAMP}_try$attempt.log
        /usr/bin/time -v $PY $MZ/scripts/export_surfex.py "$CFG" --start $Y-01-01 --end $Y-12-31 > "$SL" 2>&1
        if grep -q "ALL SURFEX FILES OK" "$SL"; then
            log "SURFEX OK: $(grep '^OK' "$SL" | sed 's/^OK *//')"
            ok_sfx=1
            break
        fi
        log "SURFEX attempt $attempt FAILED: $(grep -E 'Error|FAIL' "$SL" | tail -2 | tr '\n' ' ')"
        sleep 30
    done
    [ $ok_sfx -eq 1 ] || log "**$Y: SURFEX export failed twice (see \`$SL\`).**"

    log "**$Y done**; $(du -sh "$OUT" | cut -f1) in \`$OUT\`; $(free_gb) GB free"
done
log "**Overnight driver finished.**"
