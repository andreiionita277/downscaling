#!/bin/bash
# Crocus for every hydrological year (1 Sep - 31 Aug) with the FIXED forcing (13 Nov 2026). User decision: "run the
# 3-hourly full only" -> per HY one run, all 3001 points, all output variables (snowtools default options) every 3 h
# (~/SurfexCrocus/aspect_fix/namelists/OPTIONS_3hourly_full.nam: only XTSTEP_OUTPUT = 10800 differs), 12 chunks x 12.
# Steps per HY: export forcing (fixed TopoPyScale main) -> Crocus on the WSL disk -> compress each PRO file losslessly
# (compress_pro.py: zlib, every value checked identical) -> remove scratch symlinks (work/*, NTFS cannot store them) and the
# per-chunk forcing copies (subsets of the kept surfex_Efix forcing) -> rsync to D: -> verify (file count + bytes) ->
# delete the local copy, leave a symlink at the old path.
# Resumable (data/logs/crocus_all/<HY>.ok); stops at the first failure (nothing deleted for that year).
#   cd ~/TopoPyScale-repo-github/downscaling
#   setsid nohup meridionali_zarr/scripts/crocus_all_hy.sh 2024 2023 ... 2009 > meridionali_zarr/data/logs/crocus_all/driver.out 2>&1 < /dev/null &
# Arguments: start years of the hydrological years (2023 = HY 2023-24). Progress: data/logs/crocus_all/progress.tsv
set -u
cd $HOME/TopoPyScale-repo-github/downscaling
MZ=meridionali_zarr
PY=../venv/bin/python
CROCUS=$HOME/SurfexCrocus/aspect_fix/scripts/crocus_parallel_nam.py
NAM_3H=$HOME/SurfexCrocus/aspect_fix/namelists/OPTIONS_3hourly_full.nam
LOCAL=$MZ/data/crocus_Efix
DEST=/mnt/d/Downscaling/meridionali_zarr/crocus_Efix
L=$MZ/data/logs/crocus_all
mkdir -p $L $DEST $MZ/data/surfex_Efix
[ -f $L/progress.tsv ] || printf "HY\tstart\tend\tminutes\tcrocus_3h\tcompressed\tsize_on_D\n" > $L/progress.tsv
note() { echo "- $(date '+%H:%M:%S') [crocus-all] $*" | tee -a $MZ/LOG_28Oct2026.md; }

bytes() { find "$1" -type f -printf '%s\n' | awk '{s+=$1} END {print s+0}'; }
files() { find "$1" -type f | wc -l; }
move_to_d() {   # move_to_d <local dir>: clean, rsync, verify, delete local, symlink
  local src=$1 name; name=$(basename $1)
  [ -L $src ] && return 0
  find $src -path '*/work/*' -type l -delete
  find $src -maxdepth 2 -path '*/chunk_*/FORCING_*.nc' -type f -delete
  rsync -a $src/ $DEST/$name/ || return 1
  if [ "$(files $src)" = "$(files $DEST/$name)" ] && [ "$(bytes $src)" = "$(bytes $DEST/$name)" ]; then
    rm -rf $src && ln -s $DEST/$name $src
  else
    note "copy of $name to D: does not match (files/bytes); local copy kept"; return 1
  fi
}

note "started for hydrological years starting $* (3-hourly full output, compressed); output -> D:\\Downscaling\\meridionali_zarr\\crocus_Efix"
for y in "$@"; do
  hy=$y-$((y + 1)); out=$LOCAL/${hy}_3hourly
  [ -f $L/$hy.ok ] && { note "HY $hy already done, skipped"; continue; }
  start=$(date '+%F %H:%M'); t0=$(date +%s)
  F=$MZ/data/surfex_Efix/FORCING_${y}090100_$((y + 1))083123.nc
  if [ ! -f $F ]; then
    note "HY $hy: export forcing"
    $PY $MZ/scripts/export_surfex.py $MZ/config/config_meridionali_E_$y.yml $MZ/config/config_meridionali_E_$((y + 1)).yml \
        --start $y-09-01 --end $((y + 1))-08-31 --out $MZ/data/surfex_Efix > $L/export_$hy.log 2>&1 \
      && [ -f $F ] || { note "**HY $hy export FAILED** -> $L/export_$hy.log; STOPPED"; exit 1; }
  fi
  if [ ! -L $out ] && ! { [ -f $out/run_summary.csv ] && ! grep -q False $out/run_summary.csv; }; then
    note "HY $hy: Crocus 3-hourly"
    $PY $CROCUS $F --out $out --chunks 12 --parallel 12 --namelist $NAM_3H > $L/crocus_${hy}_3hourly.log 2>&1 \
      || { note "**HY $hy Crocus FAILED** -> $L/crocus_${hy}_3hourly.log; STOPPED (local output kept)"; exit 1; }
  fi
  if [ ! -L $out ]; then
    raw=$(du -sh --apparent-size $out | cut -f1)
    # 3 files at a time: 12 at once ran out of memory (23 GB RAM) on 13 Nov; already compressed files are skipped
    # 17 Nov: compression + a downscaling (peak 11 GB) ran out of memory (signal 9) -> wait until no downscaling runs
    while ps -eo args | awk '$2 ~ /run_year_public\.py$/' | grep -q .; do sleep 60; done
    okc=1; ls $out/chunk_*/output/pro/PRO_*.nc | xargs -P 3 -n 1 $PY $MZ/scripts/compress_pro.py >> $L/compress_$hy.log 2>&1 || okc=0
    [ $okc = 1 ] || { note "**HY $hy compression FAILED** -> $L/compress_$hy.log; STOPPED (uncompressed output kept)"; exit 1; }
    note "HY $hy: Crocus OK, compressed $raw -> $(du -sh --apparent-size $out | cut -f1) (all values checked identical)"
    move_to_d $out || { note "**HY $hy move to D: FAILED**; STOPPED"; exit 1; }
  fi
  size=$(du -sh --apparent-size $DEST/${hy}_3hourly | cut -f1)
  touch $L/$hy.ok
  min=$(( ($(date +%s) - t0) / 60 ))
  printf "%s\t%s\t%s\t%s\tOK\tOK\t%s\n" $hy "$start" "$(date '+%H:%M')" $min "$size" >> $L/progress.tsv
  note "**HY $hy DONE** in $min min (3-hourly full, $size on D:)"
done
note "ALL DONE: $*"
echo ALL DONE
