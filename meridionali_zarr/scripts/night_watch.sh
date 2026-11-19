#!/bin/bash
# Wakes Claude (exits) when something in the overnight pipeline happens (15 Nov 2026):
#   new "DONE" / "FAILED" / "ERA5 complete" / "given up" / decision lines in the driver, NCAR lane, Crocus or CDS-policy
#   outputs; a key process gone (driver, NCAR lanes, Crocus while not finished); or MAX_MIN minutes without events.
# Prints what happened. Usage: night_watch.sh [MAX_MIN]
cd $HOME/TopoPyScale-repo-github/downscaling/meridionali_zarr/data/logs
MAX_MIN=${1:-60}
FILES="overnight_E/driver_v5d_restart2.out overnight_E/driver_v5e_nocds.out overnight_E/ncar1_v5f.out overnight_E/ncar2_v5f.out overnight_E/ncar3_v5f.out crocus_all/driver_resume.out crocus_all/driver_1993.out crocus_all/driver_1994.out crocus_all/driver_1995.out crocus_all/driver_1996.out crocus_all/driver_1997.out crocus_all/driver_1998.out crocus_all/driver_1999.out crocus_all/driver_2000.out crocus_all/driver_2001.out crocus_all/driver_2003.out crocus_all/driver_2002.out crocus_all/driver_2008.out crocus_all/driver_2007.out crocus_all/driver_2006.out crocus_all/driver_2005.out crocus_all/driver_2004.out overnight_E/cds_policy.out"
PAT='DONE|FAILED|ERA5 complete|given up|gave up|decision|restarted|STOPPED|Traceback'
count() { cat $FILES 2>/dev/null | grep -cE "$PAT"; }
procs() {
  d=$(ps -eo args | awk '$2 ~ /overnight_E\.py$/' | wc -l)
  n=$(ps -eo args | awk '$2 ~ /ncar_lane\.py$/' | wc -l)
  c=$(ps -eo args | awk '$2 ~ /crocus_all_hy\.sh$/' | wc -l)
  echo "$d $n $c"
}
c0=$(count); p0=$(procs); t0=$(date +%s); s0=$(grep -HE "$PAT" $FILES 2>/dev/null)   # 08:48 19 Nov: snapshot, so EVENT prints only the new lines
while :; do
  sleep 60
  c=$(count); p=$(procs)
  if [ "$c" != "$c0" ]; then echo "EVENT $(date "+%H:%M"):"; diff <(echo "$s0") <(grep -HE "$PAT" $FILES 2>/dev/null) | sed -n "s/^> //p"; break; fi
  set -- $p
  if [ "$1" = 0 ] || [ "$2" -lt 3 ] || { [ "$3" = 0 ] && ! cat crocus_all/driver_1993.out | grep -qE "ALL DONE|STOPPED|FAILED"; }; then
    echo "PROCESS CHANGE $(date '+%H:%M'): driver/lanes/crocus now $p (was $p0)"; break; fi
  i=$((${i:-0} + 1))   # 01:32 17 Nov: every 5 min, wake if the VPN state (route to NCAR) changed
  if [ $((i % 5)) = 0 ]; then
    v=$(timeout 30 /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command "(Get-VpnConnection -Name VD514B).ConnectionStatus" 2>/dev/null | tr -d '\r')
    [ -n "$v" ] && [ -n "${v0:-}" ] && [ "$v" != "$v0" ] && { echo "VPN CHANGE $(date '+%H:%M'): $v0 -> $v"; break; }
    [ -n "$v" ] && v0=$v; fi
  cl=$(ls -t overnight_E/era5_*_cds_try*.log 2>/dev/null | head -1)   # 20:05 15 Nov: CDS log silent; 20:35 threshold 3 h (queue waits are silent)
  if [ -n "$cl" ] && ps -eo args | grep -q '[e]ra5_download.py.*_cds.yml' && [ $(( $(date +%s) - $(stat -c %Y "$cl") )) -ge 10800 ]; then
    q=0; key=$(awk '/^key/{print $2}' ~/.cdsapirc)   # 22:37: stay quiet while any job of this log is queued at ECMWF
    for j in $(grep -oE 'request [0-9a-f-]{36}' "$cl" | awk '{print $2}' | sort -u); do
      curl -s -m 20 -H "PRIVATE-TOKEN: $key" https://cds.climate.copernicus.eu/api/retrieve/v1/jobs/$j | grep -q '"status": *"accepted"' && q=1; done
    [ $q = 0 ] && { echo "CDS STALL $(date '+%H:%M'): $cl unchanged for 3+ h and no job queued"; break; }; fi
  [ $(( $(date +%s) - t0 )) -ge $((MAX_MIN * 60)) ] && { echo "HEARTBEAT $(date '+%H:%M') (no events for $MAX_MIN min)"; break; }
done
echo "processes driver/lanes/crocus: $(procs)"
