#!/bin/bash
# Re-downscale setup-E years with the FIXED TopoPyScale (main = bbd0296, 11 Nov 2026; see
# ~/SurfexCrocus/aspect_fix/CHANGELOG_aspect_fix.md). User: "re-do all years ... keep just one year as example
# for how we downscaled before the fix" -> 2025 is kept as meridionali_E_2025_pre_aspect_fix; for the other years
# the old folder is deleted ONLY after the new year passes every check (check_year_fixed.py).
# Resumable: a year with data/logs/rerun_fix/<year>.ok is skipped; a half-done year resumes (down.zarr.progress).
# Stops at the first failing year (its old folder stays).
#   cd ~/TopoPyScale-repo-github/downscaling
#   setsid nohup meridionali_zarr/scripts/rerun_fixed_years.sh $(seq 2025 -1 2011) > meridionali_zarr/data/logs/rerun_fix/driver.out 2>&1 < /dev/null &
# Progress table: meridionali_zarr/data/logs/rerun_fix/progress.tsv
set -u
cd $HOME/TopoPyScale-repo-github/downscaling
MZ=meridionali_zarr
PY=../venv/bin/python
TERRAIN=$MZ/data/meridionali_F_2025/outputs
L=$MZ/data/logs/rerun_fix
KEEP=2025
mkdir -p $L
[ -f $L/progress.tsv ] || printf "year\tstart\tend\tminutes\tresult\tDJF_SW\told_folder\n" > $L/progress.tsv
note() { echo "- $(date '+%H:%M:%S') [rerun-fix] $*" | tee -a $MZ/LOG_28Oct2026.md; }

note "rerun with fixed TopoPyScale ($(git -C ../fork-original-code/TopoPyScale_Bulgaria_Rila_Pirin_Mountains rev-parse --short HEAD)) started for years $*"
for y in "$@"; do
  [ -f $L/$y.ok ] && { note "$y already done, skipped"; continue; }
  d=$MZ/data/meridionali_E_$y; bak=${d}_pre_aspect_fix
  if [ -d $d ] && [ ! -d $bak ]; then mv $d $bak; note "$y: old outputs moved to $(basename $bak)"; fi
  mkdir -p $d/outputs
  for f in ds_param.nc da_horizon.nc; do [ -e $d/outputs/$f ] || ln $TERRAIN/$f $d/outputs/$f; done
  [ -e $d/outputs/df_centroids.pck ] || cp -p $TERRAIN/df_centroids.pck $d/outputs/df_centroids.pck
  cfg=$MZ/config/config_meridionali_E_$y.yml
  sed -i -E 's/^(        CPU_cores: )[0-9]+/\114/' $cfg
  while pgrep -f "run_year_public.py" > /dev/null; do sleep 60; done        # never two downscalings at once (RAM)
  start=$(date '+%F %H:%M'); t0=$(date +%s)
  note "**$y re-downscaling** started (fixed code, 14 workers)"
  rc=1
  for a in 1 2; do
    /usr/bin/time -v $PY $MZ/scripts/run_year_public.py $cfg --no-geotiff > $L/run_${y}_try$a.log 2>&1; rc=$?
    [ $rc -eq 0 ] && break
    note "$y attempt $a exit $rc, see $L/run_${y}_try$a.log"
  done
  min=$(( ($(date +%s) - t0) / 60 ))
  if [ $rc -eq 0 ] && $PY $MZ/scripts/check_year_fixed.py $y > $L/check_${y}.out 2>&1; then
    # TopoPyScale re-saves ds_param.nc: relink if identical (saves 4.7 GB per year)
    if cmp -s $d/outputs/ds_param.nc $TERRAIN/ds_param.nc; then rm $d/outputs/ds_param.nc; ln $TERRAIN/ds_param.nc $d/outputs/ds_param.nc; fi
    if [ "$y" = "$KEEP" ]; then old="kept ($(basename $bak))"; else rm -rf $bak; old="deleted"; fi
    touch $L/$y.ok
    sw=$(grep -o 'DJF SW S [0-9-]*, N [0-9-]*' $L/check_${y}.out)
    printf "%s\t%s\t%s\t%s\tALL PASS\t%s\t%s\n" $y "$start" "$(date '+%H:%M')" $min "$sw" "$old" >> $L/progress.tsv
    note "**$y DONE** in $min min, all checks pass ($sw W m-2); old outputs $old"
  else
    printf "%s\t%s\t%s\t%s\tFAILED\t-\tkept\n" $y "$start" "$(date '+%H:%M')" $min >> $L/progress.tsv
    note "**$y FAILED** (run exit $rc; checks: $L/check_${y}.out); old outputs kept in $(basename $bak); STOPPED"
    exit 1
  fi
done
note "ALL YEARS DONE: $*"
echo ALL DONE
