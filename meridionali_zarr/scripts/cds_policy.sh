#!/bin/bash
# 15 Nov 2026 night policy, replaces cds_off_after_2008.sh. User: "CDS only as last last resource" (NCAR first).
# At 18:30-18:44 NCAR on the wired network delivered 0 units (79 failed attempts, mostly 504), so switching the CDS
# off right after 2008 would stop all downloading. Rule: at every decision point (a year finished by the driver,
# i.e. "**YYYY DONE**", from 2008 on) count the NCAR pressure-level units written in the last 2 h (all lanes).
#   >= THRESH units/h  -> NCAR works: stop the driver (and its CDS downloader) when no downscaling is running and
#                         restart it with NO_CDS=1 (downscaling only, years 2008..1940).
#   <  THRESH units/h  -> NCAR stalled: CDS lane stays on (last resort), decision logged; checked again next year.
#   cd ~/TopoPyScale-repo-github/downscaling
#   setsid nohup meridionali_zarr/scripts/cds_policy.sh <driver pid> <driver output> [THRESH] > meridionali_zarr/data/logs/overnight_E/cds_policy.out 2>&1 < /dev/null &
set -u
cd $HOME/TopoPyScale-repo-github/downscaling
PID=$1; OUT=$2; THRESH=${3:-300}
note() { echo "- $(date '+%H:%M:%S') [cds-policy] $*" | tee -a meridionali_zarr/LOG_28Oct2026.md; }

ncar_rate() {   # NCAR plev units written in the last 2 h, per hour, all lanes
  ../venv/bin/python - <<'EOF'
import datetime as dt, glob, json
cut, n = dt.datetime.now() - dt.timedelta(hours=2), 0
for f in glob.glob('meridionali_zarr/data/era5_600-1000hPa_ncar/*/state/manifest.jsonl'):
    for line in open(f):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get('status') == 'written' and r.get('kind') == 'plev' and r.get('logged') \
                and dt.datetime.fromisoformat(r['logged']) > cut:
            n += 1
print(n // 2)
EOF
}
downscaling_running() { ps -eo args | awk '$2 ~ /run_year_public\.py$/' | grep -q .; }

note "started (driver pid $PID, threshold $THRESH NCAR units/h); decision at every '**YYYY DONE**' from 2008 on"
seen=0
while kill -0 $PID 2>/dev/null; do
  if grep -q '\*\*2008 DONE\*\*' $OUT; then
    n=$(grep -cE '\*\*[0-9]{4} DONE\*\*' $OUT)
    if [ $n -gt $seen ]; then
      r=$(ncar_rate)
      if [ $r -ge $THRESH ]; then
        while downscaling_running; do sleep 60; done          # never interrupt a downscaling
        note "decision: NCAR delivers $r units/h (>= $THRESH) -> CDS lane OFF (last resort no longer needed)"
        kill $PID; sleep 10
        for p in $(ps -eo pid,args | awk '$3 ~ /era5_download\.py$/ && $4 ~ /config\/era5\/config_era5_meridionali_[0-9]+_cds\.yml$/ {print $1}'); do kill $p; done
        sleep 5
        NO_CDS=1 CDS_ONLY=1 OVERNIGHT_LOG=LOG_28Oct2026.md setsid nohup ../venv/bin/python meridionali_zarr/scripts/overnight_E.py \
            $(seq 2008 -1 1940) > meridionali_zarr/data/logs/overnight_E/driver_v5e_nocds.out 2>&1 < /dev/null &
        note "driver restarted without CDS (pid $!), downscaling only, years 2008..1940 -> data/logs/overnight_E/driver_v5e_nocds.out"
        exit 0
      fi
      note "decision: NCAR delivers $r units/h (< $THRESH) -> CDS lane stays ON (last resort while NCAR is stalled); next check after the next finished year"
      seen=$n
    fi
  fi
  sleep 300
done
note "driver $PID ended; policy script stops"
