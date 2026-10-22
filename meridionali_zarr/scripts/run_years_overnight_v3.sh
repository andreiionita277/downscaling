#!/usr/bin/env bash
# Version 3 of run_years_overnight.sh (see LOG_21Oct.md for why). Runs years one after another, each with
# GeoTIFFs and the SURFEX-Crocus export, reusing the 2025 terrain. The GeoTIFFs are written straight to the
# D: drive (each year's outputs/geotiff is a symbolic link to D:), because the Linux disk is a file on C:
# (ext4.vhdx) that grows with every new write and C: has little space; the FORCING file is moved to D:
# after its checks. Unattended: every step is retried, a failing year is logged and skipped.
# Progress is appended to LOG_21Oct.md.
#
#   setsid nohup bash meridionali_zarr/scripts/run_years_overnight_v3.sh 2023 2022 ... \
#       > meridionali_zarr/data/logs/overnight_driver_v3.log 2>&1 < /dev/null &
#
# Run from ~/TopoPyScale-repo-github/downscaling. Stop it with: pkill -f run_years_overnight_v3.sh
set -u
cd "$(dirname "$0")/../.."                     # -> downscaling/
MZ=meridionali_zarr
PY=../venv/bin/python
LOGMD=$MZ/LOG_21Oct.md
TERRAIN=$MZ/data/meridionali_2025/outputs      # DEM parameters, clusters, horizon (same DEM and mask every year)
TEMPLATE=$MZ/config/config_meridionali_2024.yml
DEST=/mnt/d/Downscaling/meridionali_zarr       # D:\Downscaling\meridionali_zarr
MIN_FREE_C_GB=40                               # real free space on C: (holds the Linux disk file) to start a year:
                                               # ~4 GB new local data per year + temporary GeoTIFFs (~12 GB) + margin

log() { echo "- $(date '+%H:%M:%S') $*" | tee -a "$LOGMD"; }
free_gb() { df -BG --output=avail /mnt/c | tail -1 | tr -dc '0-9'; }   # C:, not the Linux disk's own figure

# Move a finished year's GeoTIFFs and FORCING file to D: (rsync checks every file it copies, and deletes the
# local copy only after that file has arrived), then verify file count and total size on D:.
move_year() {
    local Y=$1 SRC=$MZ/data/meridionali_$1/outputs
    local D=$DEST/meridionali_$Y
    mkdir -p "$D"
    local items=()
    [ -L "$SRC/geotiff" ] || [ ! -d "$SRC/geotiff" ] || items+=("$SRC/geotiff")    # local GeoTIFFs only (not a link to D:)
    ls "$SRC"/FORCING*.nc > /dev/null 2>&1 && items+=("$SRC"/FORCING*.nc)
    [ ${#items[@]} -gt 0 ] || { log "move $Y: nothing to move"; return 0; }
    local n_src=$(find "${items[@]}" -type f \( -name '*.tif' -o -name '*.nc' \) | wc -l)
    local b_src=$(find "${items[@]}" -type f \( -name '*.tif' -o -name '*.nc' \) -print0 | du -cb --files0-from=- | tail -1 | cut -f1)
    local t0=$(date +%s)
    if rsync -a --remove-source-files "${items[@]}" "$D"/ ; then
        local n_d=$(ls "$D"/geotiff/*.tif "$D"/FORCING*.nc 2>/dev/null | wc -l)
        log "moved $Y to \`D:\\Downscaling\\meridionali_zarr\\meridionali_$Y\`: $n_src files, $((b_src / 1000000000)) GB in $(( ($(date +%s) - t0) / 60 )) min; $n_d GeoTIFF/FORCING files there now"
    else
        log "**move $Y FAILED** (rsync exit $?); files that did not arrive are still in \`$SRC\`"
    fi
}

# Replace the year's copy of ds_param.nc / da_horizon.nc with a link to the 2025 file if byte-identical.
relink_terrain() {
    local OUT=$1
    for f in ds_param.nc da_horizon.nc; do
        if [ -f "$OUT/$f" ] && [ ! "$OUT/$f" -ef "$TERRAIN/$f" ] && cmp -s "$TERRAIN/$f" "$OUT/$f"; then
            ln -f "$TERRAIN/$f" "$OUT/$f"
        fi
    done
}

# Wait for any run or export started earlier (e.g. by version 1 of this script).
while pgrep -f "run_year_public.py|export_surfex.py" > /dev/null; do sleep 60; done
log "driver v3 started (years: $*); C: has $(free_gb) GB free"

# GeoTIFFs of 2025 (still on the Linux disk) go to D: first, in the background.
( move_year 2025 ) &
MOVE_PID=$!

for Y in "$@"; do
    echo -e "\n### Year $Y" >> "$LOGMD"
    OUT=$MZ/data/meridionali_$Y/outputs
    CFG=$MZ/config/config_meridionali_$Y.yml
    STAMP=$(date +%Y%m%d_%H%M)

    if [ "$(free_gb)" -lt "$MIN_FREE_C_GB" ]; then
        log "**Stopped before $Y: C: has only $(free_gb) GB free (< $MIN_FREE_C_GB GB). The Linux disk file on C: grows with new data.**"
        break
    fi

    # 1. config: the 2024 config with the year replaced (DEM, mask, k, ERA5 settings unchanged)
    if [ ! -f "$CFG" ]; then
        sed "s/2024/$Y/g" "$TEMPLATE" > "$CFG"
        log "config \`$CFG\` written (2024 config with 2024 -> $Y)"
    fi

    # 2. terrain. ds_param.nc and da_horizon.nc: hard links (TopoPyScale replaces ds_param.nc by moving a new
    #    file over it, and only reads da_horizon.nc). df_centroids.pck: a COPY, because compute_horizon rewrites
    #    it in place (to_pickle), which through a link would also rewrite the 2025 file.
    mkdir -p "$OUT"
    for f in ds_param.nc da_horizon.nc; do
        [ -e "$OUT/$f" ] || ln "$TERRAIN/$f" "$OUT/$f"
    done
    if [ -e "$OUT/df_centroids.pck" ] && [ "$OUT/df_centroids.pck" -ef "$TERRAIN/df_centroids.pck" ]; then
        cp --remove-destination "$TERRAIN/df_centroids.pck" "$OUT/df_centroids.pck"
    fi
    [ -e "$OUT/df_centroids.pck" ] || cp "$TERRAIN/df_centroids.pck" "$OUT/df_centroids.pck"
    # GeoTIFFs straight to D: (outputs/geotiff = symbolic link)
    GD=$DEST/meridionali_$Y/geotiff
    mkdir -p "$GD"
    [ -e "$OUT/geotiff" ] || ln -s "$GD" "$OUT/geotiff"
    log "terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); GeoTIFFs -> \`$GD\`; C: $(free_gb) GB free"

    # 3. full chain. Resumable; fewer GeoTIFF workers on each retry.
    ok_run=0
    for attempt in 1 2 3; do
        workers=$((6 - attempt))                # 5, 4, 3
        RL=$MZ/data/logs/run_${Y}_${STAMP}_v2try$attempt.log
        log "run attempt $attempt ($workers GeoTIFF workers) -> \`$RL\`"
        t0=$(date +%s)
        /usr/bin/time -v $PY $MZ/scripts/run_year_public.py "$CFG" --product-workers $workers > "$RL" 2>&1
        rc=$?
        if grep -q "YEAR COMPLETE, ALL CHECKS PASSED" "$RL"; then
            log "run OK in $(( ($(date +%s) - t0) / 60 )) min. $(grep -E 'points done this run' "$RL" | tail -1 | sed 's/^---> //')"
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

    # 4. SURFEX-Crocus forcing (calendar year, 23 h end-of-year gap fixed)
    ok_sfx=0
    for attempt in 1 2; do
        SL=$MZ/data/logs/surfex_${Y}_${STAMP}_v2try$attempt.log
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

    # 5. free local space: terrain copy back to a link, products to D: (in the background, one move at a time)
    relink_terrain "$OUT"
    wait $MOVE_PID 2>/dev/null
    move_year $Y &
    MOVE_PID=$!
    log "**$Y done** (checks passed; FORCING file moving to D: in the background); C: $(free_gb) GB free"
done
wait $MOVE_PID 2>/dev/null
log "**Overnight driver v3 finished.**"
