#!/usr/bin/env bash
# Print one line per new event, from marker files only (the loop/lane stdout logs can go silent when the session
# that started them closes). Host side, run from anywhere:
#   bash rila_pirin_zarr/scripts/watch_markers.sh                  # every event, runs until killed
#   bash rila_pirin_zarr/scripts/watch_markers.sh --until-problem  # exits at the first RETRIED/FAILED (incl. ERA5_FAILED)
# Events: NCAR/EDH year DONE, RETRIED (first failure), FAILED; ERA5_OK / ERA5_FAILED per year (run_era5_queue.sh).
D=$(cd "$(dirname "$0")/../data" && pwd)
snap() {
  (cd "$D" && ls runs/*/DONE runs/*/RETRIED runs/*/FAILED runs_edh/*/DONE runs_edh/*/RETRIED runs_edh/*/FAILED \
     era5_600-1000hPa/*/ERA5_OK era5_600-1000hPa/*/ERA5_FAILED 2>/dev/null)
  # old fixed-list lanes (run_era5_lane.sh) write no ERA5_FAILED: a failed year shows as a lane that moved on
  for lane in "2020 2025 1985 1986 1987 1988 1989 1990 1991 1992 1993 1994 1995 1996 1997 1998 1999 2000 2001 2002 2003 2004" \
              "2005 2006 2007 2008 2009 2010 2011 2012 2013 2014 2015 2016 2017 2018 2019 2021 2022 2023 2024"; do
    open=()
    for y in $lane; do
      [ -d "$D/era5_600-1000hPa/$y" ] && [ ! -f "$D/era5_600-1000hPa/$y/ERA5_OK" ] && [ ! -d "$D/era5_600-1000hPa/$y.lock" ] && open+=("$y")
    done
    [ ${#open[@]} -gt 1 ] && echo "lane FAILED: ERA5 ${open[0]} unverified while ${open[-1]} downloads"
  done
}
prev=$(snap)
while true; do
  sleep 60
  cur=$(snap)
  new=$(comm -13 <(echo "$prev" | sort) <(echo "$cur" | sort) | sed 's#/\(DONE\|RETRIED\|FAILED\|ERA5_OK\|ERA5_FAILED\)$# \1#; s#^runs/#NCAR #; s#^runs_edh/#EDH #; s#^era5_600-1000hPa/#NCAR #')
  [ -n "$new" ] && echo "$new"
  [ "${1:-}" = --until-problem ] && echo "$new" | grep -qE "RETRIED|FAILED" && exit 0
  prev=$cur
done
