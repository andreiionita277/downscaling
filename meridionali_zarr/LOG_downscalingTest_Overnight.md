# Log: overnight downscaling, setup E, 2025 -> 1990 (started 2026-10-27 ~21:10)

User request (20:45-21:05, then away): downscale every year separately from 2025 back to 1990, all night until stopped,
with the 3000 grouped clusters (setup E), raw Zarr only (no GeoTIFF, no Crocus). ERA5 first from NCAR; only if NCAR
keeps failing (5 failed tries / timeouts per unit), switch to the CDS with <= 60,000 values per request, gently.
Agisoft runs on Windows for ~1 h more: do not overload the computer meanwhile. Log everything here, debug everything.
Background and all decisions leading here: `LOG_27Oct.md`. Lines starting with a time (`- HH:MM:SS`) are written by the
driver itself; sections in between are my notes.

## Setup (my notes)

### What is reused (nothing recomputed)
- Clusters of setup E, computed today 18:15-18:59 in `data/meridionali_F_2025/outputs/`: 3001 clusters in 4 elevation
  groups (<800 / 800-1500 / 1500-2000 / >=2000 m: 750 / 1125 / 563 / 563), terrain parameters (slope, aspect, svf of
  15 Oct) and horizon. Per year: `ds_param.nc` and `da_horizon.nc` hard-linked (second name for the same file, no space),
  `df_centroids.pck` copied (TopoPyScale writes horizon columns into it). Only `ds_solar.nc` (sun position, ~2 min) is
  computed per year. TopoPyScale re-saves ds_param.nc as a new file each run; the driver relinks it afterwards if
  byte-identical (as for 2006-2025).

### Driver `scripts/overnight_E.py` (new), three lanes running side by side
- **A: ERA5 from NCAR**, one year after the other, Nandhni's `zarr_workflow/era5_download/era5_download.py`, 8 parallel
  requests (half of her 16; NCAR was overloaded this evening). The downloader retries each unit 5 times with growing
  waits. If units still fail (exit 1), or no unit arrives for 45 min (NCAR down), the year's incomplete months go to
  lane B, and lane A pauses 30 min before the next year (to not hammer NCAR).
- **B: CDS fallback**, only for those months, into the same store (a CDS unit rewrites its whole month, so NCAR days
  already written are simply overwritten with the same values). 2 jobs at a time (the CDS runs only ONE per user; the
  2nd waits in the queue), pressure levels 1 month x 3 variables x 14 levels = 31,248 values per request, single
  levels capped at 60,000 values per request. Up to 3 attempts per year.
- **C: downscaling**, any year whose store is complete (every hour, 14 levels, 13 variables, no NaN), newest first:
  `run_year_public.py <config> --no-geotiff` -> hourly `down.zarr` + `daily.zarr` + month QA (`months/*.json`, status
  `ok (no GeoTIFF)`). Up to 3 attempts (resumable). Not before 21:57 (Agisoft); then 6 workers while Agisoft uses
  more than one core (measured from Windows' process list, 2 min apart), otherwise 14.
- Per-year files: ERA5 store `data/era5_600-1000hPa/<Y>/ERA5.zarr`, ERA5 configs `config/era5/config_era5_meridionali_<Y>[_cds].yml`,
  run config `config/config_meridionali_E_<Y>.yml`, outputs `data/meridionali_E_<Y>/outputs/`, all logs `data/logs/overnight_E/`.
- Note: the driver rewrote `config/era5/config_era5_meridionali_2025.yml` (the template) without its comments;
  same content except NCAR workers 8.

### Checks before the start
- CDS account works: a 1-value request (t 700 hPa, 2024-01-15 12 UTC, our box) accepted in 2 s, done in 22 s,
  values -11.6..-8.1 degC.
- Agisoft visible from Linux (`metashape`, ~10 GB, busy).
- Driver dry test: ERA5 configs (NCAR and CDS variants) and run config written correctly (run config = 1 Jan test config
  except name, folder, full year, CPU cores). Bug found and fixed in the test: the `directory:` pattern matched both
  the project folder and `outputs:` -> now only the absolute path.
- The January 2025 NCAR download running since 19:05 was stopped at an idle moment (no write for 61 s): 29 units
  written so far. The driver continues 2025 from there (resumable).
- 18 GB RAM free, 817 GB disk free.

### How to follow / stop
```bash
cd ~/TopoPyScale-repo-github/downscaling
tail -f meridionali_zarr/LOG_downscalingTest_Overnight.md        # this file (driver lines)
ls -t meridionali_zarr/data/logs/overnight_E/ | head             # detailed logs of each step
pkill -f overnight_E.py; pkill -f run_year_public.py; pkill -f era5_download.py     # stop everything
```
Running the same start command again resumes (finished units, clusters and months are skipped).

## Driver output
- 20:59:14 driver started (pid 1224352); years 2025..1990 (36); downscaling not before 21:59 (Agisoft)
- 20:59:16 **2025 ERA5 NCAR** started (8 requests at a time) -> `data/logs/overnight_E/era5_2025_ncar_20261027_2059.log`

## 23:00-23:10: NCAR effectively down -> driver version 2 (my notes)
- 2025 on NCAR, 20:59-23:00: only **24 units written in 2 h** (a year has 2280), 94 retried attempts, **8 units failed
  their 5 tries** (connection timeouts, read timeouts, OPeNDAP "NetCDF: I/O failure" for single levels). The 45-min
  silence watchdog never fired because a unit still arrived now and then, and version 1 switched to the CDS only when
  the whole NCAR download command ended: at this pace that would have taken days. Design gap of mine.
- Version 2 (`scripts/overnight_E.py`): a year's NCAR download stops as soon as ONE unit has failed its 5 tries
  (the user's rule), and its incomplete months go to the CDS. NCAR is still tried first for every other year
  (after a 30 min pause following each fallback). 2025 goes to the CDS directly (`CDS_NOW=2025`): tried 2 h already.
- Stopped: driver first, then its downloader at an idle moment (no write for 85 s). 53 units of 2025 in the store.
- Downscaling start time fixed to 21:57 (it was "1 h after the driver start", which a restart would have moved).
- Expect: CDS ~25-30 h per year at 1 job at a time (Nandhni: 43-66 min per request, 24 requests + 2 for single
  levels). So 2025 from the CDS will not be finished by morning; years where NCAR recovers will be faster.
- 23:01:20 driver started (pid 1276008); years 2025..1990 (36); downscaling not before 23:01 (Agisoft)
- 23:01:21 2025 ERA5: already tried on NCAR (units failed 5 tries) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 23:01:21 **2024 ERA5 NCAR** started (8 requests at a time) -> `data/logs/overnight_E/era5_2024_ncar_20261027_2301.log`
- 23:01:21 **2025 ERA5 CDS** attempt 1, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] (2 jobs at a time, <= 60,000 values per request) -> `data/logs/overnight_E/era5_2025_cds_try1_20261027_2301.log`
- (note 23:03) CDS accepted all 2025 requests (pressure levels 31,248 values each, single levels 56,112 and 5,208:
  all <= 60,000); first job running after 33 s. The downloader has separate pools for pressure and single levels
  (2 each), so 4 jobs are submitted at once; the CDS still runs one per user at a time, the others wait in its queue.
- 00:21:33 2024 ERA5 NCAR: 1 unit(s) failed 5 tries (14 written so far, 56 retried attempts): NCAR too slow/unreliable, download stopped
- 00:21:33 2024 ERA5 NCAR finished in 80 min (exit -15): 14 units written this run, 1 given up after 5 attempts, 56 retried attempts
- 00:21:34 2024 ERA5: months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] incomplete after NCAR -> CDS fallback queue
- 00:21:34 NCAR lane pauses 30 min before the next year
- (note 00:22) Fallback v2 works as intended: 2024 NCAR stopped at its first unit that failed 5 tries (single-level
  OPeNDAP "NetCDF: I/O failure"; 14 units in 80 min), months queued for the CDS after 2025. CDS 2025: January pressure
  levels (2 requests) written and verified at 23:59, ~29 min per request; February running. Estimate 2025 complete
  ~12-13 h after start (~11:00-12:00), then downscaled right away (~15 min).
- 00:51:42 **2023 ERA5 NCAR** started (8 requests at a time) -> `data/logs/overnight_E/era5_2023_ncar_20261028_0051.log`
- 02:10:50 2023 ERA5 NCAR: 1 unit(s) failed 5 tries (31 written so far, 55 retried attempts): NCAR too slow/unreliable, download stopped
- 02:10:50 2023 ERA5 NCAR finished in 79 min (exit -15): 31 units written this run, 1 given up after 5 attempts, 55 retried attempts
- 02:10:51 2023 ERA5: months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] incomplete after NCAR -> CDS fallback queue
- 02:10:51 NCAR lane pauses 30 min before the next year
- (note 02:11) Check: no errors in the driver. 2023 NCAR stopped the same way as 2024 (one single-level unit failed 5
  tries; 31 units in 79 min) -> CDS queue (order: 2025, 2024, 2023). CDS 2025: month 01 complete 00:47, month 02 01:37
  (validation OK) -> ~50 min per month, 2025 complete ~11:00. RAM 17 GB free.
- 02:41:21 **2022 ERA5 NCAR** started (8 requests at a time) -> `data/logs/overnight_E/era5_2022_ncar_20261028_0241.log`
- 03:42:47 2022 ERA5 NCAR: 1 unit(s) failed 5 tries (16 written so far, 49 retried attempts): NCAR too slow/unreliable, download stopped
- 03:42:47 2022 ERA5 NCAR finished in 61 min (exit -15): 16 units written this run, 1 given up after 5 attempts, 49 retried attempts
- 03:42:47 2022 ERA5: months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] incomplete after NCAR -> CDS fallback queue
- 03:42:47 NCAR lane pauses 30 min before the next year
- 04:13:42 **2021 ERA5 NCAR** started (8 requests at a time) -> `data/logs/overnight_E/era5_2021_ncar_20261028_0413.log`
- 05:07:35 2021 ERA5 NCAR: 1 unit(s) failed 5 tries (10 written so far, 42 retried attempts): NCAR too slow/unreliable, download stopped
- 05:07:35 2021 ERA5 NCAR finished in 54 min (exit -15): 10 units written this run, 1 given up after 5 attempts, 42 retried attempts
- 05:07:36 2021 ERA5: months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] incomplete after NCAR -> CDS fallback queue
- 05:07:36 NCAR lane pauses 30 min before the next year
- 05:38:53 **2020 ERA5 NCAR** started (8 requests at a time) -> `data/logs/overnight_E/era5_2020_ncar_20261028_0538.log`
- (note 06:51) Check: no driver errors. NCAR failed the same way for 2022 (03:42) and 2021 (05:07): one unit failed
  5 tries each time -> CDS queue is now 2025 (running), 2024, 2023, 2022, 2021. 2020 on NCAR since 05:38.
  CDS 2025: months 01-06 complete and validated (06:30). The CDS slowed down at night: 50 min/month at first,
  now ~80 min/month -> 2025 complete ~14:00-15:00 (not 11:00), then downscaled right away.
- 06:53:22 2020 ERA5 NCAR: 1 unit(s) failed 5 tries (17 written so far, 57 retried attempts): NCAR too slow/unreliable, download stopped
- 06:53:22 2020 ERA5 NCAR finished in 74 min (exit -15): 17 units written this run, 1 given up after 5 attempts, 57 retried attempts
- 06:53:22 2020 ERA5: months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] incomplete after NCAR -> CDS fallback queue
- 06:53:22 NCAR lane pauses 30 min before the next year
- 07:25:06 **2019 ERA5 NCAR** started (8 requests at a time) -> `data/logs/overnight_E/era5_2019_ncar_20261028_0725.log`
- 08:39:13 2019 ERA5 NCAR: 2 unit(s) failed 5 tries (15 written so far, 54 retried attempts): NCAR too slow/unreliable, download stopped
- 08:39:13 2019 ERA5 NCAR finished in 74 min (exit -15): 15 units written this run, 2 given up after 5 attempts, 54 retried attempts
- 08:39:13 2019 ERA5: months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] incomplete after NCAR -> CDS fallback queue
- 08:39:13 NCAR lane pauses 30 min before the next year
- 09:10:51 **2018 ERA5 NCAR** started (8 requests at a time) -> `data/logs/overnight_E/era5_2018_ncar_20261028_0910.log`
- 10:21:02 2018 ERA5 NCAR: 2 unit(s) failed 5 tries (36 written so far, 58 retried attempts): NCAR too slow/unreliable, download stopped
- 10:21:02 2018 ERA5 NCAR finished in 70 min (exit -15): 36 units written this run, 2 given up after 5 attempts, 58 retried attempts
- 10:21:02 2018 ERA5: months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] incomplete after NCAR -> CDS fallback queue
- 10:21:02 NCAR lane pauses 30 min before the next year
- 10:51:02 **2017 ERA5 NCAR** started (8 requests at a time) -> `data/logs/overnight_E/era5_2017_ncar_20261028_1051.log`
- (note 10:59) Check: no driver errors. NCAR failed for every year tried so far (2025-2018, 8 years; 10-36 units per
  ~1 h before the first unit failed 5 tries) -> CDS queue: 2025 (running), 2024 ... 2018. 2017 on NCAR since 10:51.
  CDS 2025: months 01-08 validated (09:19), ~80 min/month -> complete ~14:30-15:00, then downscaled.
  For the user to decide: the CDS gives ~1 year per ~16 h (one job per user), so the queue alone is ~5 days; NCAR is
  not recovering. Options: keep as is, or ask Nandhni whether NCAR's d633000 has known problems / another endpoint.

## 11:10: CDS only (user's decision) -> driver version 3 (my notes)
- User back at 11:05: "keep downloading from cds only for now and downscale the data as it arrives".
- `overnight_E.py`: new switch `CDS_ONLY=1` (every year goes straight to the CDS queue, NCAR not touched) and a guard:
  before a year's CDS download starts, the driver waits while a downloader from an earlier driver still writes that
  year's store (one writer per store). Tested: finds the running 2025 downloader (pid 1276138), none for 2016.
- Stopped: old driver (pid 1276008) and the NCAR 2017 downloader (at an idle moment, last write 8 min before; its
  units stay in the store, the CDS rewrites those months anyway). NOT stopped: the 2025 CDS downloader (months 09-12
  in flight, months 01-08 done); the new driver waits for it, then checks 2025 and downscales it.
- Order: CDS 2025 -> 2024 -> ... -> 1990, one year at a time; downscaling of each year as soon as its store is complete,
  in parallel with the next year's download.
- 11:12:05 driver started (pid 1592019); years 2025..1990 (36); downscaling not before 11:12 (Agisoft)
- 11:12:06 2025 ERA5: CDS only (user) -> CDS directly, months [10, 11, 12]
- 11:12:06 2025 ERA5: downloader from the previous driver still running (pid 1276138); waiting for it
- 11:12:07 2024 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:07 2023 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:08 2022 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:08 2021 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:09 2020 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:09 2019 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:09 2018 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2017 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2016 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2015 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2014 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2013 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2012 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2011 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2010 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2009 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2008 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2007 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2006 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2005 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2004 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2003 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2002 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2001 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 2000 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 1999 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 1998 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 1997 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 1996 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 1995 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 1994 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 1993 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 1992 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 1991 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 1990 ERA5: CDS only (user) -> CDS directly, months [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
- 11:12:10 NCAR lane: all years handled
- (note 11:40) User asked about ARCO-ERA5 (Google Cloud, gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3).
  Tested (metadata + 1 hour): 37 levels, all our variables except r (relative humidity; computable from t, q, p).
  Same values as our 2025 store: t at 14 levels, 2025-01-01 00 UTC, max |diff| 0.0008 K. But one chunk = one hour of
  the WHOLE globe, all 37 levels: our 10 x 20 box for 1 hour, 1 variable = 89 MB downloaded, 4 s -> one year
  ~3.9 TB / ~49 h sequential (~6 h with 8 parallel). Not used for now.
- (note 11:22) Driver v3 stopped and restarted with its lines going to LOG_28Oct2026.md; continue there.
