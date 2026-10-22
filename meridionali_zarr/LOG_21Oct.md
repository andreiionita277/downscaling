# Log: overnight multi-year run, 2026-10-21 → 22 (Meridional Carpathians, year by year)

Goal (user, 2026-10-21 ~15:45): downscale as many years as possible until the next morning, year by year,
each followed by the SURFEX-Crocus export, reusing the already computed DEM parameters. Debug errors
automatically. Lines starting with a time (`- HH:MM:SS`) are written by the driver script itself; the
sections in between are my explanations.

## Setup (15:45-15:55)

### What runs, and why this way
- **Script:** `scripts/run_years_overnight.sh` (new), started detached from VS Code (`setsid nohup`), so it keeps
  running if the editor or this chat is closed. The PC must stay on and awake.
- **Years:** 2023, 2022, 2021, 2020, 2019, 2018, newest first, so the most recent years are done first.
- **Per year:**
  1. config = `config_meridionali_2024.yml` with 2024 → year (only the name, description, output folder,
     start and end change; checked with `diff` for 2023);
  2. terrain = **hard links** to the 2025 `ds_param.nc`, `df_centroids.pck`, `da_horizon.nc`, the already computed
     DEM parameters, 2000 clusters and horizon (same DEM and mask as 2024/2025). A hard link is a second name
     for the same file on disk, so no extra space. Safe because TopoPyScale never writes into `ds_param.nc` in place:
     when it loads it, it writes a new copy to `outputs/tmp/` and moves it over the link (`topo_param.compute_dem_param`),
     which leaves the 2025 file untouched. `df_centroids.pck` and `da_horizon.nc` are only read;
  3. `run_year_public.py` full chain: ERA5 from EDH → solar geometry → downscaling (20 cores) → daily statistics →
     36 GeoTIFFs + checks (5 processes, as in the 2024 products run);
  4. `export_surfex.py` → `FORCING_<Y>010100_<Y>123123.nc` + checks.
- **Automatic error handling:**
  - the run is tried up to 3 times; every step resumes (finished clusters and months are skipped), and each
    retry uses one GeoTIFF worker fewer (5 → 4 → 3) in case memory was the cause; half-written temporary
    files are removed between attempts; the failure reason and any out-of-memory kill are logged;
  - the SURFEX export is tried twice;
  - a year that still fails is logged and skipped, and the next year starts.
- **Disk:** ~95 GB per year (87 GB GeoTIFFs, 1.6 GB hourly store, 1.8 GB SURFEX, 4.4 GB own copy of ds_param.nc
  after TopoPyScale re-saves it). 671 GB free at the start. A year starts only if ≥ 155 GB are free, so ≥ ~60 GB
  always remain → **about 6 years fit (2023-2018)**, ~1 h each. Disk, not memory or time, is the limit.
- Before the start: stopped a forgotten helper loop of this morning's test (a shell sampling memory every 5 s).

## Driver output

### Year 2023
- 16:11:00 config `meridionali_zarr/config/config_meridionali_2023.yml` written (2024 config with 2024 -> 2023; 9 lines differ)
- 16:11:00 terrain linked from 2025 (ds_param.nc, df_centroids.pck, da_horizon.nc); 671 GB free
- 16:11:00 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2023_20261021_1611_try1.log`

## Changes at 16:13-16:25 (my notes)

### User: continue past 2018 until told to stop
- Disk would have stopped the first plan around 2018. Two measures:
  - after each year, its copy of `ds_param.nc` (4.4 GB, re-saved by TopoPyScale) is replaced by a link to the 2025
    file **if byte-identical**. Checked on 2024: `cmp` → identical, so TopoPyScale's re-save writes the same bytes.
    Done for 2024 already (ds_param.nc and da_horizon.nc → links, 7 GB freed).
  - option `--no-geotiff` added to `run_year_public.py` (daily statistics only; months marked `ok (no GeoTIFF)`
    so a later `--products-only` run makes their maps). Kept as a fallback; not needed after the next change.

### Found and fixed: the df_centroids.pck link would have rewritten the 2025 file
- `Topoclass.compute_horizon` adds the horizon columns to `df_centroids.pck` and saves it with `to_pickle`, which
  **writes into the existing file**. Through a hard link this would also rewrite `meridionali_2025/outputs/df_centroids.pck`
  (2024's re-saved copy is 572 KB vs 540 KB, so the bytes do change).
- Caught before 2023 reached that step (it was computing solar geometry): 2023's link replaced by a real copy
  (`cp --remove-destination`). 2025 file unchanged (md5 e43e66eb… before and after).
- `ds_param.nc` links stay safe (TopoPyScale replaces that file by moving a new one over it) and `da_horizon.nc`
  is only read.

### User: not enough local space, move results to D:\Downscaling (6 TB free)
- `/mnt/d` = D: (6.5 TB free). Write speed from WSL measured: **77 MB/s** (2 GB test file) → ~20 min per year of
  products. Writing GeoTIFFs directly there would be slow (GDAL writes in many small pieces over the Windows
  bridge), so: compute on the Linux disk, then move the big products.
- Moved per year (after all checks passed): `geotiff/*.tif` (87 GB) and `FORCING_*.nc` (1.8 GB) →
  `D:\Downscaling\meridionali_zarr\meridionali_<Y>\`. With `rsync --remove-source-files`, each local file is deleted only
  after it has arrived; file count and size on D: are logged. Stay on Linux: `down.zarr`, `daily.zarr`, months/, logs
  (≈ 1.7 GB per year) and the terrain, so anything can be re-exported quickly.
- The move runs in the background while the next year computes, so it costs no time. 2025 and 2024 are moved first.
- No disk limit any more → **every year with GeoTIFFs**, from 2023 back to 1990 (or until stopped).

### Driver version 2
- Version 1 was stopped (`kill` on the bash script only; the 2023 computation it had started kept running).
  A running bash script must not be edited, hence a new file: `scripts/run_years_overnight_v2.sh`.
- Differences from version 1: df_centroids.pck copied (not linked); ds_param.nc relinked after each year; finished
  years moved to D:; a year starts if ≥ 120 GB are free locally (otherwise it first waits for the move).
- It first waits for the 2023 run of version 1 to finish, then takes 2023 again: everything already done is skipped
  (finished clusters and months), then the 2023 SURFEX export and move follow.
- Started:
  ```
  setsid nohup bash meridionali_zarr/scripts/run_years_overnight_v2.sh 2023 2022 ... 1990 \
      > meridionali_zarr/data/logs/overnight_driver_v2.log 2>&1 < /dev/null &
  ```
  To stop it: `pkill -f run_years_overnight_v2.sh` (then, if a year is running: `pkill -f run_year_public.py`).

## Driver v2 output

## 16:17-16:30: C: has little space → GeoTIFFs written straight to D:, driver version 3 (my notes)
- The user asked to move 2024 to D: "to free up space on C". Checking C: showed the real limit: **C: has 189 GB
  free**, while Linux reports 675 GB. The Linux disk is a single file on C:
  (`C:\Users\andre\AppData\Local\Packages\CanonicalGroupLimited.Ubuntu_…\LocalState\ext4.vhdx`, now 326 GB),
  which **grows when Linux writes new data and does not shrink when files are deleted**. Writing 87 GB of GeoTIFFs
  per year on the Linux side and moving them afterwards would have filled C: within ~2 years, and a full C:
  can damage the Linux disk.
- Fix: **the GeoTIFFs are written directly to D:**. Each year's `outputs/geotiff` is a symbolic link (a shortcut)
  to `/mnt/d/Downscaling/meridionali_zarr/meridionali_<Y>/geotiff`, so `run_year_public.py` writes "into its folder"
  as before, and the data lands on D:. Only the temporary GeoTIFFs (written in `outputs/tmp_*` and deleted after
  conversion, ~12 GB at once with 5 workers) stay on C:.
  - Done right away for **2023** (16:17), which was still downloading ERA5, so all its GeoTIFFs go to D:.
- Driver v2 stopped (it was only waiting for 2023). **Driver v3** (`scripts/run_years_overnight_v3.sh`), differences from v2:
  - GeoTIFF folder of every year linked to D: as above;
  - a year starts only if **C: has ≥ 40 GB free** (real C: free space, not Linux's own figure);
  - the FORCING file is written locally (1.8 GB), checked, then moved to D:; the 2025 GeoTIFFs (still local) are
    moved to D: at the start.
- **2024 moved to D: by hand** (user request), with `rsync -a --remove-source-files` (each local file deleted
  only after it arrived), in the background from 16:18.
- Note: deleting files inside Linux frees space for Linux (new files reuse it) but does not give it back to C:.
  Shrinking `ext4.vhdx` needs WSL to be shut down (e.g. `wsl --shutdown` then compacting it, or
  `wsl --manage Ubuntu --set-sparse true`): only when nothing is running.
- Started:
  ```
  setsid nohup bash meridionali_zarr/scripts/run_years_overnight_v3.sh 2023 2022 ... 1990 \
      > meridionali_zarr/data/logs/overnight_driver_v3.log 2>&1 < /dev/null &
  ```
  To stop it: `pkill -f run_years_overnight_v3.sh` and, if a year is running, `pkill -f run_year_public.py`.

## Driver v3 output
- 16:21:39 driver v3 started (years: 2023 2022 2021 2020 2019 2018 2017 2016 2015 2014 2013 2012 2011 2010 2009 2008 2007 2006 2005 2004 2003 2002 2001 2000 1999 1998 1997 1996 1995 1994 1993 1992 1991 1990); C: has 189 GB free

### Year 2023
- 16:21:39 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); GeoTIFFs -> `/mnt/d/Downscaling/meridionali_zarr/meridionali_2023/geotiff`; C: 189 GB free
- 16:21:39 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2023_20261021_1621_v2try1.log`

## 16:22-16:45: GeoTIFF compression question; 2023 run killed by my test and resumed (my notes)
- **My mistake (again):** a compression test (reading 2 bands of a 2025 GeoTIFF, ~1.5 GB) ran while 2023 was in its
  downscaling step, which alone uses most of the RAM (20 workers × ~1.3 GB + the main process). Linux's OOM killer
  stopped the 2023 run (16:20, `signal 9`; the following `Traceback`/`BrokenPipeError` lines are its workers losing
  the main process). **Driver v3 restarted it on its own** at 16:21; the 424 clusters already finished were kept
  (`1576 points done this run (424 earlier), 0 failed`). Rule from now on: **no tests while a year is downscaling.**
- Checked whether the workers' memory grows with the length of the shared `ERA5.zarr` (it gains a year per year
  run): no, the downscaler selects the year before loading (`topo_scale_zarr.py:297`, `ERA.sel(time=tvec)`).
- Compression test, 2 full bands (lossless options read back pixel-identical): now DEFLATE+predictor 112 MB;
  ZSTD-19 85 MB; **LERC lossless 37 MB (3×)**; LZMA 28 MB (4×); int16 0.01 °C 28 MB (max error 0.005 °C).
- One whole file converted to LERC lossless (`t_max_daily_2024-01.tif`, after the 2023 downscaling had finished):
  2.91 → 1.93 GB only (1.5×), 280 s, 1.5 GB RAM. Reason: the overviews (averaged, so new decimal values that hardly
  compress) take most of the space; the 3× applies to the full-resolution data only.
- User: overviews must stay **bilinear/average, never nearest** for continuous data (saved as a standing preference).
  Then: **keep the GeoTIFFs as they are (DEFLATE, unchanged code); further compression to be decided tomorrow.**
  Test file `D:\Downscaling\_lerc_test\t_max_daily_2024-01.tif` NOT deleted: Windows refused (the file was open in
  a GIS program, which had just written its `.aux.xml` at 16:44); left for the user. Nothing in the products code was changed.
- C: free space rose from 189 to 363 GB during the moves: Windows does give back space freed inside the Linux disk.

## 16:45-17:05: writing GeoTIFFs straight to D: was far too slow → staged locally, copied to D: when checked (my notes)
- 2023's GeoTIFF stage had produced no file after 15 min: the 5 files on D: grew by only **~0.3 MB/s each**.
  GDAL writes a COG in many small pieces (overviews first, into `.ovr.tmp`), and each piece crosses the
  Linux↔Windows bridge (`p9_client_rpc` in the process state); fine for big sequential copies (77 MB/s), very
  slow for this. The two background moves (2024, 2025) use the same bridge.
- Stopped driver v3 and the 2023 run (signal 15 in its log = my `kill`; downscaling and daily statistics of 2023
  were finished and are kept). Deleted the 5 local temporary folders and the 5 unfinished `.part.ovr.tmp` on D:.
- **Code change, `scripts/run_year_public.py` (`_product_task`):** when `outputs/geotiff` is a symbolic link
  (as for every year since 2023, pointing to D:), each GeoTIFF is written and checked in a local
  `outputs/geotiff_staging/`, then copied to D: in one go (as `.part`, size compared, renamed), and the local
  copy deleted. A file already on D: is checked there and not rewritten. Without a link nothing changes.
  C: holds at most the files in progress (~5 × 3 GB).
- **Test** (nothing else running): 2023 January mean, 1 worker, `--products-only --months 1 --stats mean`:
  export 158 s, checks OK (sample max |diff| 8e-7, band-1 field max |diff| 0), file on D: (2.85 GB), staging
  folder empty afterwards. Its month marker (written as complete with only the mean) was deleted, so the real run
  makes January's min/max and re-checks the mean.
- 17:02:56 driver v3 restarted with the staged GeoTIFF export (same command)
- 17:03:58 driver v3 started (years: 2023 2022 2021 2020 2019 2018 2017 2016 2015 2014 2013 2012 2011 2010 2009 2008 2007 2006 2005 2004 2003 2002 2001 2000 1999 1998 1997 1996 1995 1994 1993 1992 1991 1990); C: has 363 GB free

### Year 2023
- 17:03:58 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); GeoTIFFs -> `/mnt/d/Downscaling/meridionali_zarr/meridionali_2023/geotiff`; C: 363 GB free
- 17:03:58 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2023_20261021_1703_v2try1.log`
- 17:09:26 **move 2025 FAILED** (rsync exit 20); files that did not arrive are still in `meridionali_zarr/data/meridionali_2025/outputs`
- 17:09:29 (note) the 'move 2025 FAILED' line just above/below is my stopping a duplicate 2025 move started by the restarted driver; the first 2025 move (since 16:21) continues
- 17:16:43 (note) **2024 move to D: complete and verified**: 36 GeoTIFFs + FORCING file, 94,719,952,707 bytes before = after (rsync OK, 59 min, sharing the bridge with the 2025 move); nothing left locally in `meridionali_2024/outputs/geotiff`
- 17:52:19 (note) **2025 move to D: complete**: 36 GeoTIFFs (92.8 GB = 86 GiB, as before) + FORCING file on D:, none left locally, no rsync temp files; every GeoTIFF opens with the right number of bands (3×28, 12×30, 21×31). The move's own log line is missing because its parent driver had been stopped at 16:50. 2023 GeoTIFFs: 22/36 done and on D:, all checks OK so far.
- 18:15:36 run OK in 71 min. 
- 18:21:08 SURFEX OK: FORCING_2023010100_2023123123.nc: 2000 points x 8760 h (2023-01-01 00:00:00 .. 2023-12-31 23:00:00), 1.82 GB; Tair -19.8..33.8 degC, snowfall share 15.2%, ZS 82..2346 m
- 18:21:13 **2023 done** (checks passed; FORCING file moving to D: in the background); C: 363 GB free

### Year 2022
- 18:21:13 config `meridionali_zarr/config/config_meridionali_2022.yml` written (2024 config with 2024 -> 2022)
- 18:21:14 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); GeoTIFFs -> `/mnt/d/Downscaling/meridionali_zarr/meridionali_2022/geotiff`; C: 363 GB free
- 18:21:14 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2022_20261021_1821_v2try1.log`
- 18:21:25 (note) **2023 verified**: 36 GeoTIFFs on D: (no leftovers), staging folder empty, all 12 month markers `ok`, ds_param.nc relinked to the 2025 file (same inode, 4.4 GB freed). 2022 started: terrain linked, df_centroids.pck an independent copy, GeoTIFF folder -> D:.
- 18:21:42 moved 2023 to `D:\Downscaling\meridionali_zarr\meridionali_2023`: 1 files, 1 GB in 0 min; 37 GeoTIFF/FORCING files there now
- 18:32:32 (note) memory warning during the 2022 downscaling (full 2000 points): swap 26.6-27.3 of 32 GB, RAM available 3.5-7 GB, **stable over a minute, not rising**. Swap holders: the idle main process (9.3 GB swapped out, it only waits for the workers) and ~0.9 GB per worker of pages shared with it. No action; the driver retries (resuming) if the OOM killer ever stops a run.

## 19:30-19:40: copying each GeoTIFF to D: inside the workers made 2022 twice as slow → driver version 4 (my notes)
- 2022's GeoTIFF stage: 10 of 36 files after 37 min (2023: 22). Export and checks take the usual time; the
  copy to D: inside each worker took **400-1000 s per file** (2023: 50-150 s), and while a worker copies it does not
  compute. Measured throughput to D: varies **~20-74 MB/s** in total (5 copies at once: 74 MB/s at 19:30), probably
  Windows-side work on the new files (e.g. antivirus scanning). Projected: ~2-2.5 h per year instead of ~1 h.
- Re-evaluated the C: concern of 16:17: C: went **back up from 189 to 363 GB** while 2024/2025 were moved off, so
  Windows does return the space of deleted files from the Linux disk file. So the simpler plan is safe after all:
  **make each year's GeoTIFFs on the Linux disk at full speed, then move the finished year to D: in the
  background while the next year computes** (copying fully overlapped with computing).
- **Driver v4** (`scripts/run_years_overnight_v4.sh`), differences from v3:
  - no link to D:: `outputs/geotiff` is a normal local folder again (so `run_year_public.py` writes there directly;
    the staging code only applies when the folder is a link);
  - after a year's checks and SURFEX export, `geotiff/` and the FORCING file are moved to D: with rsync in the
    background (one move at a time);
  - a year starts right away if C: has ≥ 200 GB free (room for one year being moved + one being made);
    otherwise it first waits for the running move (+2 min for Windows to return the space); it stops if C: is
    still < 120 GB.
- v3 stopped (only the bash script; the 2022 run continues). v4 waits for it, finishes 2022 (SURFEX export, move
  of its FORCING file; its GeoTIFFs are already on D: through the link), then 2021 onwards with the new scheme.
  ```
  setsid nohup bash meridionali_zarr/scripts/run_years_overnight_v4.sh 2022 2021 ... 1990 \
      > meridionali_zarr/data/logs/overnight_driver_v4.log 2>&1 < /dev/null &
  ```
  To stop it: `pkill -f run_years_overnight_v4.sh` and, if a year is running, `pkill -f run_year_public.py`.

## Driver v4 output
- 20:24:51 driver v4 started (years: 2022 2021 2020 2019 2018 2017 2016 2015 2014 2013 2012 2011 2010 2009 2008 2007 2006 2005 2004 2003 2002 2001 2000 1999 1998 1997 1996 1995 1994 1993 1992 1991 1990); C: has 363 GB free

### Year 2022
- 20:24:51 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 363 GB free
- 20:24:51 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2022_20261021_2024_v2try1.log`
- 20:28:09 run OK in 3 min. 
- 20:32:57 SURFEX OK: FORCING_2022010100_2022123123.nc: 2000 points x 8760 h (2022-01-01 00:00:00 .. 2022-12-31 23:00:00), 1.82 GB; Tair -21.4..36.1 degC, snowfall share 11.8%, ZS 82..2346 m
- 20:33:01 **2022 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 363 GB free

### Year 2021
- 20:33:01 config `meridionali_zarr/config/config_meridionali_2021.yml` written (2024 config with 2024 -> 2021)
- 20:33:01 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 363 GB free
- 20:33:01 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2021_20261021_2033_v2try1.log`
- 20:33:27 moved 2022 to `D:\Downscaling\meridionali_zarr\meridionali_2022`: 1 files, 1 GB in 0 min; 37 GeoTIFF/FORCING files there now
- 21:47:21 run OK in 74 min. Downscaling: 2000 points done this run (0 earlier), 0 failed, 316s wall, 2.9s per point per worker
- 21:52:28 SURFEX OK: FORCING_2021010100_2021123123.nc: 2000 points x 8760 h (2021-01-01 00:00:00 .. 2021-12-31 23:00:00), 1.82 GB; Tair -23.0..35.2 degC, snowfall share 19.7%, ZS 82..2346 m
- 21:52:34 **2021 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 362 GB free

### Year 2020
- 21:52:34 config `meridionali_zarr/config/config_meridionali_2020.yml` written (2024 config with 2024 -> 2020)
- 21:52:34 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 362 GB free
- 21:52:34 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2020_20261021_2152_v2try1.log`
- 22:18:41 moved 2021 to `D:\Downscaling\meridionali_zarr\meridionali_2021`: 37 files, 95 GB in 26 min; 37 GeoTIFF/FORCING files there now
- 22:18:53 (note) **2021 move verified**: 36 GeoTIFFs open on D: with the right band counts (3×28, 12×30, 21×31) + FORCING file; no rsync leftovers; local geotiff folder empty. Move took 26 min (≈ 61 MB/s), fully overlapped with 2020's computation, as planned for v4. C: still 363 GB free.
- 23:08:44 run OK in 76 min. Downscaling: 2000 points done this run (0 earlier), 0 failed, 323s wall, 3.0s per point per worker
- 23:13:56 SURFEX OK: FORCING_2020010100_2020123123.nc: 2000 points x 8784 h (2020-01-01 00:00:00 .. 2020-12-31 23:00:00), 1.83 GB; Tair -15.6..31.6 degC, snowfall share 11.6%, ZS 82..2346 m
- 23:14:02 **2020 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 363 GB free

### Year 2019
- 23:14:02 config `meridionali_zarr/config/config_meridionali_2019.yml` written (2024 config with 2024 -> 2019)
- 23:14:02 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 363 GB free
- 23:14:02 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2019_20261021_2314_v2try1.log`
- 23:25:08 run attempt 1 FAILED (exit 137, out-of-memory kills in dmesg: 1): Command terminated by signal 9 
- 23:25:39 run attempt 2 (4 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2019_20261021_2314_v2try2.log`

## 23:25: 2019 run attempt 1 killed by out-of-memory → downscaling workers 20 → 14 (my notes)
- 2019's downscaling (20 worker processes) was stopped by the OOM killer at 541/2000 points, while the
  2020 move to D: ran in the background. The move itself survived. The downscaling step alone was already at the
  limit (2022: swap 27 of 32 GB); the move's extra pressure on memory tipped it over.
- With 20 workers this could repeat on every retry and a year would then be skipped after 3 attempts. Fix: **`CPU_cores`
  20 → 14** in `config_meridionali_2019.yml` and in the template `config_meridionali_2024.yml` (so 2018 and earlier get
  it too), edited before attempt 2 started. ~30 % less memory for this step; downscaling ~7 min instead of ~5.
  Results do not depend on the number of workers (each point is computed independently; clustering is cached).
- Attempt 2 resumes from the 541 finished points.
- 23:39:27 (note) 2019 attempt 2: downscaling OK with 14 workers, 1459 points (+541 from attempt 1), 0 failed, **177 s wall, 1.6 s per point per worker** (20 workers: ~320 s, 3.0 s). Fewer workers are faster here too (less competition for memory and disk), so 14 is better on both counts.
- 23:44:32 moved 2020 to `D:\Downscaling\meridionali_zarr\meridionali_2020`: 37 files, 95 GB in 30 min; 37 GeoTIFF/FORCING files there now
- 23:44:41 (note) **2020 move verified**: 36 GeoTIFFs open on D: with the right band counts, FORCING there, no leftovers, local folder empty.
- 00:37:04 run OK in 71 min. Downscaling: 1459 points done this run (541 earlier), 0 failed, 177s wall, 1.6s per point per worker
- 00:41:49 SURFEX OK: FORCING_2019010100_2019123123.nc: 2000 points x 8760 h (2019-01-01 00:00:00 .. 2019-12-31 23:00:00), 1.82 GB; Tair -19.3..33.1 degC, snowfall share 13.4%, ZS 82..2346 m
- 00:41:56 **2019 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 362 GB free

### Year 2018
- 00:41:56 config `meridionali_zarr/config/config_meridionali_2018.yml` written (2024 config with 2024 -> 2018)
- 00:41:56 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 362 GB free
- 00:41:56 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2018_20261022_0041_v2try1.log`
- 01:07:49 moved 2019 to `D:\Downscaling\meridionali_zarr\meridionali_2019`: 37 files, 94 GB in 25 min; 37 GeoTIFF/FORCING files there now
- 01:07:59 (note) **2019 move verified**: 36 GeoTIFFs open on D: with the right band counts, FORCING there, no leftovers, local folder empty.
- 01:54:50 run OK in 72 min. Downscaling: 2000 points done this run (0 earlier), 0 failed, 314s wall, 2.1s per point per worker
- 01:59:50 SURFEX OK: FORCING_2018010100_2018123123.nc: 2000 points x 8760 h (2018-01-01 00:00:00 .. 2018-12-31 23:00:00), 1.82 GB; Tair -20.4..29.8 degC, snowfall share 21.9%, ZS 82..2346 m
- 01:59:56 **2018 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 363 GB free

### Year 2017
- 01:59:56 config `meridionali_zarr/config/config_meridionali_2017.yml` written (2024 config with 2024 -> 2017)
- 01:59:56 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 363 GB free
- 01:59:56 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2017_20261022_0159_v2try1.log`
- 02:24:26 moved 2018 to `D:\Downscaling\meridionali_zarr\meridionali_2018`: 37 files, 94 GB in 24 min; 37 GeoTIFF/FORCING files there now
- 02:24:37 (note) **2018 move verified**: 36 GeoTIFFs open on D: with the right band counts, FORCING there, no leftovers, local folder empty.
- 03:12:16 run OK in 72 min. Downscaling: 2000 points done this run (0 earlier), 0 failed, 173s wall, 1.1s per point per worker
- 03:17:09 SURFEX OK: FORCING_2017010100_2017123123.nc: 2000 points x 8760 h (2017-01-01 00:00:00 .. 2017-12-31 23:00:00), 1.82 GB; Tair -24.1..36.3 degC, snowfall share 14.8%, ZS 82..2346 m
- 03:17:15 **2017 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 363 GB free

### Year 2016
- 03:17:15 config `meridionali_zarr/config/config_meridionali_2016.yml` written (2024 config with 2024 -> 2016)
- 03:17:15 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 363 GB free
- 03:17:15 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2016_20261022_0317_v2try1.log`
- 03:43:14 moved 2017 to `D:\Downscaling\meridionali_zarr\meridionali_2017`: 37 files, 95 GB in 25 min; 37 GeoTIFF/FORCING files there now
- 03:43:27 (note) **2017 move verified**: 36 GeoTIFFs open on D: with the right band counts, FORCING there, no leftovers, local folder empty.
- 04:32:56 run OK in 75 min. Downscaling: 2000 points done this run (0 earlier), 0 failed, 310s wall, 2.0s per point per worker
- 04:38:12 SURFEX OK: FORCING_2016010100_2016123123.nc: 2000 points x 8784 h (2016-01-01 00:00:00 .. 2016-12-31 23:00:00), 1.83 GB; Tair -19.7..33.2 degC, snowfall share 10.1%, ZS 82..2346 m
- 04:38:18 **2016 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 363 GB free

### Year 2015
- 04:38:18 config `meridionali_zarr/config/config_meridionali_2015.yml` written (2024 config with 2024 -> 2015)
- 04:38:18 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 363 GB free
- 04:38:18 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2015_20261022_0438_v2try1.log`
- 05:04:11 moved 2016 to `D:\Downscaling\meridionali_zarr\meridionali_2016`: 37 files, 95 GB in 25 min; 37 GeoTIFF/FORCING files there now
- 05:04:23 (note) **2016 move verified**: 36 GeoTIFFs open on D: with the right band counts (Feb 29 days), FORCING there, no leftovers, local folder empty.
- 05:52:08 run OK in 73 min. Downscaling: 2000 points done this run (0 earlier), 0 failed, 315s wall, 2.1s per point per worker
- 05:57:23 SURFEX OK: FORCING_2015010100_2015123123.nc: 2000 points x 8760 h (2015-01-01 00:00:00 .. 2015-12-31 23:00:00), 1.82 GB; Tair -20.5..34.8 degC, snowfall share 13.6%, ZS 82..2346 m
- 05:57:30 **2015 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 362 GB free

### Year 2014
- 05:57:30 config `meridionali_zarr/config/config_meridionali_2014.yml` written (2024 config with 2024 -> 2014)
- 05:57:30 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 362 GB free
- 05:57:30 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2014_20261022_0557_v2try1.log`
- 06:23:18 moved 2015 to `D:\Downscaling\meridionali_zarr\meridionali_2015`: 37 files, 94 GB in 25 min; 37 GeoTIFF/FORCING files there now
- 06:23:29 (note) **2015 move verified**: 36 GeoTIFFs open on D: with the right band counts, FORCING there, no leftovers, local folder empty.

---

## MORNING SUMMARY (written 2026-10-22 07:00; the run continues, newer lines follow below)

**Finished, checked and on D: (11 years): 2015-2025.** For each year: all 12 months `ok` (hourly QA + all GeoTIFF
checks), 36 GeoTIFFs on `D:\Downscaling\meridionali_zarr\meridionali_<Y>\geotiff\` (every one opens with the right number
of daily bands) and the SURFEX-Crocus file `D:\...\meridionali_<Y>\FORCING_<Y>010100_<Y>123123.nc` (all hours, checks OK).
Hourly `down.zarr` and `daily.zarr` stay on Linux in `meridionali_zarr/data/meridionali_<Y>/outputs/downscaled/`.

| year | 12 months | GeoTIFFs on D: | SURFEX on D: | Tair range (°C) | snowfall share |
|---|---|---|---|---|---|
| 2025 | ok | 36 | yes | −20.2 .. 35.4 | 9.0 % |
| 2024 | ok | 36 | yes | −14.6 .. 36.5 | 12.5 % |
| 2023 | ok | 36 | yes | −19.8 .. 33.8 | 15.2 % |
| 2022 | ok | 36 | yes | −21.4 .. 36.1 | 11.8 % |
| 2021 | ok | 36 | yes | −23.0 .. 35.2 | 19.7 % |
| 2020 | ok | 36 | yes | −15.6 .. 31.6 | 11.6 % |
| 2019 | ok | 36 | yes | −19.3 .. 33.1 | 13.4 % |
| 2018 | ok | 36 | yes | −20.4 .. 29.8 | 21.9 % |
| 2017 | ok | 36 | yes | −24.1 .. 36.3 | 14.8 % |
| 2016 | ok | 36 | yes | −19.7 .. 33.2 | 10.1 % |
| 2015 | ok | 36 | yes | −20.5 .. 34.8 | 13.6 % |

- **In progress at 07:00: 2014** (GeoTIFFs 25/36). Then 2013, 2012, ... down to 1990, ~80 min per year, until stopped.
- Space: D:\Downscaling\meridionali_zarr = 974 GB (5.6 TB left on D:); C: 362 GB free (unchanged all night).
- **Problems during the night, all solved without losing data** (details in the sections above):
  1. 16:20, my compression test ran next to the 2023 downscaling → OOM; the driver resumed it.
  2. 16:13, the shared `df_centroids.pck` link would have rewritten the 2025 file → replaced by a copy before use.
  3. 16:45, GeoTIFFs written directly to D: crawled at 0.3 MB/s → staged locally (v3), then (19:35) whole years
     made locally and moved in the background (v4): ~75 min per year, the move fully overlapped.
  4. 23:25, 2019 downscaling OOM with 20 workers next to a background move → 14 workers (also faster per point).
- Code changed this session (not committed to git yet): `scripts/run_year_public.py` (parallel GeoTIFFs,
  `--products-only`, `--no-geotiff`, staged copy to a linked folder), `scripts/workflow.py` (shared check lookups),
  `scripts/export_surfex.py` (new), `scripts/run_years_overnight*.sh` (v1-v4; v4 is the one running),
  configs 2014-2023 (new) and 2024 (template, CPU_cores 14).
- To stop: `pkill -f run_years_overnight_v4.sh; pkill -f run_year_public.py`.
- Open for today: GeoTIFF compression (LERC lossless etc.), git commit, shrinking the Linux disk file on C: (only with
  WSL shut down).
- 07:12:23 run OK in 74 min. Downscaling: 2000 points done this run (0 earlier), 0 failed, 318s wall, 2.1s per point per worker
- 07:17:22 SURFEX OK: FORCING_2014010100_2014123123.nc: 2000 points x 8760 h (2014-01-01 00:00:00 .. 2014-12-31 23:00:00), 1.82 GB; Tair -22.1..32.2 degC, snowfall share 12.9%, ZS 82..2346 m
- 07:17:28 **2014 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 362 GB free

### Year 2013
- 07:17:28 config `meridionali_zarr/config/config_meridionali_2013.yml` written (2024 config with 2024 -> 2013)
- 07:17:28 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 362 GB free
- 07:17:28 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2013_20261022_0717_v2try1.log`
- 07:43:06 moved 2014 to `D:\Downscaling\meridionali_zarr\meridionali_2014`: 37 files, 94 GB in 25 min; 37 GeoTIFF/FORCING files there now
- 07:43:16 (note) **2014 move verified**: 36 GeoTIFFs open on D: with the right band counts, FORCING there, no leftovers, local folder empty.
- 08:31:53 run OK in 74 min. Downscaling: 2000 points done this run (0 earlier), 0 failed, 325s wall, 2.1s per point per worker
- 08:36:36 SURFEX OK: FORCING_2013010100_2013123123.nc: 2000 points x 8760 h (2013-01-01 00:00:00 .. 2013-12-31 23:00:00), 1.82 GB; Tair -20.2..34.6 degC, snowfall share 13.8%, ZS 82..2346 m
- 08:36:42 **2013 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 362 GB free

### Year 2012
- 08:36:42 config `meridionali_zarr/config/config_meridionali_2012.yml` written (2024 config with 2024 -> 2012)
- 08:36:43 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 362 GB free
- 08:36:43 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2012_20261022_0836_v2try1.log`
- 09:02:39 moved 2013 to `D:\Downscaling\meridionali_zarr\meridionali_2013`: 37 files, 95 GB in 25 min; 37 GeoTIFF/FORCING files there now
- 09:02:47 (note) **2013 move verified**: 36 GeoTIFFs open on D: with the right band counts, FORCING there, no leftovers, local folder empty. 13 years done (2013-2025).
- 09:52:33 run OK in 75 min. Downscaling: 2000 points done this run (0 earlier), 0 failed, 322s wall, 2.1s per point per worker
- 09:57:28 SURFEX OK: FORCING_2012010100_2012123123.nc: 2000 points x 8784 h (2012-01-01 00:00:00 .. 2012-12-31 23:00:00), 1.83 GB; Tair -19.8..35.8 degC, snowfall share 21.6%, ZS 82..2346 m
- 09:57:35 **2012 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 362 GB free

### Year 2011
- 09:57:35 config `meridionali_zarr/config/config_meridionali_2011.yml` written (2024 config with 2024 -> 2011)
- 09:57:35 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 362 GB free
- 09:57:35 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2011_20261022_0957_v2try1.log`
- 10:23:24 moved 2012 to `D:\Downscaling\meridionali_zarr\meridionali_2012`: 37 files, 94 GB in 25 min; 37 GeoTIFF/FORCING files there now
- 10:23:34 (note) **2012 move verified**: 36 GeoTIFFs open on D: with the right band counts (Feb 29 days), FORCING there, no leftovers, local folder empty. 14 years done (2012-2025).
- 11:12:52 run OK in 75 min. Downscaling: 2000 points done this run (0 earlier), 0 failed, 321s wall, 2.1s per point per worker
- 11:18:14 SURFEX OK: FORCING_2011010100_2011123123.nc: 2000 points x 8760 h (2011-01-01 00:00:00 .. 2011-12-31 23:00:00), 1.82 GB; Tair -18.7..32.9 degC, snowfall share 14.8%, ZS 82..2346 m
- 11:18:19 **2011 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 362 GB free

### Year 2010
- 11:18:19 config `meridionali_zarr/config/config_meridionali_2010.yml` written (2024 config with 2024 -> 2010)
- 11:18:19 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 362 GB free
- 11:18:19 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2010_20261022_1118_v2try1.log`
- 11:50:26 moved 2011 to `D:\Downscaling\meridionali_zarr\meridionali_2011`: 37 files, 95 GB in 32 min; 37 GeoTIFF/FORCING files there now
- 11:50:36 (note) **2011 move verified**: 36 GeoTIFFs open on D: with the right band counts, FORCING there, no leftovers, local folder empty. 15 years done (2011-2025).
- 12:33:25 run OK in 75 min. Downscaling: 2000 points done this run (0 earlier), 0 failed, 276s wall, 1.8s per point per worker
- 12:38:35 SURFEX OK: FORCING_2010010100_2010123123.nc: 2000 points x 8760 h (2010-01-01 00:00:00 .. 2010-12-31 23:00:00), 1.82 GB; Tair -20.4..32.5 degC, snowfall share 16.6%, ZS 82..2346 m
- 12:38:40 **2010 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 363 GB free

### Year 2009
- 12:38:40 config `meridionali_zarr/config/config_meridionali_2009.yml` written (2024 config with 2024 -> 2009)
- 12:38:40 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 363 GB free
- 12:38:40 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2009_20261022_1238_v2try1.log`
- 13:05:55 moved 2010 to `D:\Downscaling\meridionali_zarr\meridionali_2010`: 37 files, 94 GB in 27 min; 37 GeoTIFF/FORCING files there now
- 13:06:05 (note) **2010 move verified**: 36 GeoTIFFs open on D: with the right band counts, FORCING there, no leftovers, local folder empty. 16 years done (2010-2025).
- 13:54:05 run OK in 75 min. Downscaling: 2000 points done this run (0 earlier), 0 failed, 329s wall, 2.2s per point per worker
- 13:59:12 SURFEX OK: FORCING_2009010100_2009123123.nc: 2000 points x 8760 h (2009-01-01 00:00:00 .. 2009-12-31 23:00:00), 1.82 GB; Tair -17.9..32.8 degC, snowfall share 16.9%, ZS 82..2346 m
- 13:59:17 **2009 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 363 GB free

### Year 2008
- 13:59:17 config `meridionali_zarr/config/config_meridionali_2008.yml` written (2024 config with 2024 -> 2008)
- 13:59:17 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 363 GB free
- 13:59:17 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2008_20261022_1359_v2try1.log`
- 14:25:41 moved 2009 to `D:\Downscaling\meridionali_zarr\meridionali_2009`: 37 files, 94 GB in 26 min; 37 GeoTIFF/FORCING files there now
- 14:25:53 (note) **2009 move verified**: 36 GeoTIFFs open on D: with the right band counts, FORCING there, no leftovers, local folder empty. 17 years done (2009-2025).
- 15:19:52 run OK in 80 min. Downscaling: 2000 points done this run (0 earlier), 0 failed, 328s wall, 2.2s per point per worker
- 15:25:20 SURFEX OK: FORCING_2008010100_2008123123.nc: 2000 points x 8784 h (2008-01-01 00:00:00 .. 2008-12-31 23:00:00), 1.83 GB; Tair -23.5..32.8 degC, snowfall share 14.2%, ZS 82..2346 m
- 15:25:26 **2008 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 360 GB free

### Year 2007
- 15:25:26 config `meridionali_zarr/config/config_meridionali_2007.yml` written (2024 config with 2024 -> 2007)
- 15:25:26 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 360 GB free
- 15:25:26 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2007_20261022_1525_v2try1.log`
- 15:54:06 moved 2008 to `D:\Downscaling\meridionali_zarr\meridionali_2008`: 37 files, 95 GB in 28 min; 37 GeoTIFF/FORCING files there now
- 15:54:15 (note) **2008 move verified**: 36 GeoTIFFs open on D: with the right band counts (Feb 29 days), FORCING there, no leftovers, local folder empty. 18 years done (2008-2025).

## 16:00: user: stop after 20 years (2006-2025)
- Driver v4 (with years down to 1990) stopped (bash script only; the 2007 run continues; no move was running).
  Restarted with only the two remaining years, so it ends by itself after 2006:
  ```
  setsid nohup bash meridionali_zarr/scripts/run_years_overnight_v4.sh 2007 2006 \
      > meridionali_zarr/data/logs/overnight_driver_v4b.log 2>&1 < /dev/null &
  ```
  It waits for the running 2007 run, finishes 2007 (SURFEX, move to D:), then runs 2006 and waits for its move.

## Driver v4 output (2007, 2006)
- 16:42:16 driver v4 started (years: 2007 2006); C: has 364 GB free

### Year 2007
- 16:42:16 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 364 GB free
- 16:42:16 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2007_20261022_1642_v2try1.log`
- 16:45:58 run OK in 3 min. 
- 16:51:21 SURFEX OK: FORCING_2007010100_2007123123.nc: 2000 points x 8760 h (2007-01-01 00:00:00 .. 2007-12-31 23:00:00), 1.82 GB; Tair -19.7..37.1 degC, snowfall share 13.9%, ZS 82..2346 m
- 16:51:26 **2007 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 364 GB free

### Year 2006
- 16:51:26 config `meridionali_zarr/config/config_meridionali_2006.yml` written (2024 config with 2024 -> 2006)
- 16:51:26 terrain ready (ds_param.nc and da_horizon.nc linked to 2025, df_centroids.pck copied); C: 364 GB free
- 16:51:26 run attempt 1 (5 GeoTIFF workers) -> `meridionali_zarr/data/logs/run_2006_20261022_1651_v2try1.log`
- 17:19:55 moved 2007 to `D:\Downscaling\meridionali_zarr\meridionali_2007`: 37 files, 95 GB in 28 min; 37 GeoTIFF/FORCING files there now
- 17:20:16 (note) **2007 move verified**: 36 GeoTIFFs open on D: with the right band counts, FORCING there, no leftovers, local folder empty. 19 years done (2007-2025).
- 18:22:49 (note) 2006 GeoTIFF tasks ~2x slower since ~17:45 (export ~450 s, check ~460 s instead of ~230/190 s): the user is running Agisoft on Windows at the same time, which shares the CPU. Linux sees no overload (load ~7 of 24, no I/O wait), so the time is lost on the Windows side. No action; 2006 just finishes later.
- 18:51:53 run OK in 120 min. Downscaling: 2000 points done this run (0 earlier), 0 failed, 337s wall, 2.2s per point per worker
- 19:04:37 SURFEX OK: FORCING_2006010100_2006123123.nc: 2000 points x 8760 h (2006-01-01 00:00:00 .. 2006-12-31 23:00:00), 1.82 GB; Tair -20.5..32.6 degC, snowfall share 15.1%, ZS 82..2346 m
- 19:04:42 **2006 done** (checks passed; GeoTIFFs + FORCING file moving to D: in the background); C: 362 GB free
- 20:24:21 moved 2006 to `D:\Downscaling\meridionali_zarr\meridionali_2006`: 37 files, 94 GB in 79 min; 37 GeoTIFF/FORCING files there now
- 20:24:21 **Overnight driver v4 finished.**

---

## FINAL SUMMARY (2026-10-22 20:30): 20 years done, everything stopped

- **2006-2025 complete**: every year has all 12 months `ok` (hourly QA + GeoTIFF checks), 36 GeoTIFFs and the
  SURFEX-Crocus FORCING file in `D:\Downscaling\meridionali_zarr\meridionali_<Y>\` (checked 20:25: 20 × (36 + 1)
  files, every GeoTIFF opens with the right number of daily bands, no rsync leftovers). Total on D: **1.8 TB**.
- Hourly `down.zarr` and `daily.zarr` of every year stay in `meridionali_zarr/data/meridionali_<Y>/outputs/downscaled/`
  (Linux, ~1.7 GB per year), with `months/*.json` (QA and check results) and `year_summary.json`.
- Years done after the morning summary (07:00): 2014-2006, each ~75-80 min; 2006 took 120 min (+ 79 min move)
  because Agisoft was running on Windows at the same time. No failures after the 23:25 change to 14 downscaling workers.
- SURFEX Tair range and snowfall share of the added years: 2014 −22.1..32.2 °C / 12.9 %; 2013 −20.2..34.6 / 13.8 %;
  2012 −19.8..35.8 / 21.6 %; 2011 −18.7..32.9 / 14.8 %; 2010 −20.4..32.5 / 16.6 %; 2009 −17.9..32.8 / 16.9 %;
  2008 −23.5..32.8 / 14.2 %; 2007 −19.7..37.1 / 13.9 %; 2006 −20.5..32.6 / 15.1 %.
- **Stopped at the user's request (20:25)**: the driver ended by itself after moving 2006; my log watcher was stopped;
  checked with `ps`: no pipeline, rsync or watcher process left; RAM free (22 GB available). C: 362 GB free.
- Still open: GeoTIFF compression, git commit of the code changes, shrinking the Linux disk file on C:.

---

## HOW TO RESTART OR CONTINUE THE PROCESSING

All commands are run in an Ubuntu terminal, from the `downscaling` folder:
```bash
cd ~/TopoPyScale-repo-github/downscaling
```

### 0. Before starting (each time)
```bash
free -g                      # RAM: the run needs most of the 23 GB, so close Agisoft/other heavy programs
df -h /mnt/c /mnt/d          # C: should have > 200 GB free, D: room for ~95 GB per year
ps aux | grep -E "run_year|run_years|rsync" | grep -v grep    # must print nothing (no run already going)
```
Keep the PC on and awake. VS Code and the chat can be closed: the script runs on its own.

### 1. Continue with more years (e.g. 2005 back to 1990)
```bash
setsid nohup bash meridionali_zarr/scripts/run_years_overnight_v4.sh $(seq 2005 -1 1990) \
    > meridionali_zarr/data/logs/overnight_driver_v4_$(date +%Y%m%d).log 2>&1 < /dev/null &
```
- Give the years newest first; any list works, e.g. `2005 2004 2003` or `$(seq 2005 -1 2000)`.
- For each year it: writes `config/config_meridionali_<Y>.yml` (from the 2024 config), links the 2025 terrain
  (DEM parameters, clusters, horizon: nothing is recomputed), downloads ERA5 from EDH, downscales (14 cores),
  makes daily statistics + 36 GeoTIFFs (5 processes) with all checks, exports SURFEX-Crocus, then moves
  GeoTIFFs + FORCING to `D:\Downscaling\meridionali_zarr\meridionali_<Y>\` in the background while the next year runs.
- Every step is retried (up to 3×, resuming where it stopped); a year that keeps failing is skipped and logged.
- Each step is written as a line in this file (`LOG_21Oct.md`), ~80 min per year.
- `setsid nohup ... &` = runs detached from the terminal, so closing it does not stop the run.

### 2. Follow the progress
```bash
tail -f meridionali_zarr/LOG_21Oct.md                              # driver steps (Ctrl+C stops watching only)
tail -f $(ls -t meridionali_zarr/data/logs/run_*.log | head -1)    # details of the year in progress
htop                                                               # CPU and memory
```

### 3. Stop
```bash
pkill -f run_years_overnight_v4.sh; pkill -f run_year_public.py; pkill -f export_surfex.py
```
A move to D: (`rsync`) may still be running; let it finish (`ps aux | grep rsync`), or stop it with `pkill rsync`.
Nothing is lost when stopping: running the same command again resumes (finished clusters, months and files are skipped).

### 4. Redo or repair a single year by hand (example 2010)
```bash
# whole chain (skips everything already done)
../venv/bin/python meridionali_zarr/scripts/run_year_public.py meridionali_zarr/config/config_meridionali_2010.yml --product-workers 5
# only daily statistics + GeoTIFFs again from the hourly store (e.g. another compression later)
../venv/bin/python meridionali_zarr/scripts/run_year_public.py meridionali_zarr/config/config_meridionali_2010.yml --products-only --redo-products --product-workers 5
# only the SURFEX-Crocus file (also for other periods, e.g. hydrological years if the data covers them)
../venv/bin/python meridionali_zarr/scripts/export_surfex.py meridionali_zarr/config/config_meridionali_2010.yml --start 2010-01-01 --end 2010-12-31
# move the results to D: (each local file is deleted only after it arrived)
rsync -a --remove-source-files meridionali_zarr/data/meridionali_2010/outputs/geotiff meridionali_zarr/data/meridionali_2010/outputs/FORCING*.nc /mnt/d/Downscaling/meridionali_zarr/meridionali_2010/
```
Note: months already marked `ok` are skipped unless `--redo-products` is given; with it, the GeoTIFFs are written again
into the local `outputs/geotiff/` (the copies on D: are not touched) and then need moving with the rsync line above,
which would overwrite the old ones on D:.

### 5. Prompt for a new Claude Code session (copy and paste)
```
Continue the Meridional Carpathians downscaling in ~/TopoPyScale-repo-github/downscaling/meridionali_zarr.
Read LOG_21Oct.md first (setup, problems found, and the "HOW TO RESTART" section), then LOG.md and LOG_2024.md
if needed. 2006-2025 are done and on D:\Downscaling\meridionali_zarr. Run the years <e.g. 2005 to 1990> with
scripts/run_years_overnight_v4.sh (same setup: reuse the 2025 terrain, 14 downscaling cores, 5 GeoTIFF
workers, GeoTIFFs made locally then moved to D:, SURFEX-Crocus export per year). Check memory and C: free space
first, run one heavy job at a time, never run tests while a year is downscaling, use bilinear (never nearest)
for any resampling of continuous data, explain every step for a beginner and log everything in a new
LOG_<date>.md.
```

### 6. Save the code in git (not done by me)
```bash
cd ~/TopoPyScale-repo-github/downscaling
git status                                   # what changed (data/ is ignored, results stay out of git)
git add meridionali_zarr/LOG_21Oct.md meridionali_zarr/config/ meridionali_zarr/scripts/
git commit -m "Meridionali 2006-2023: overnight multi-year driver (v1-v4), staged GeoTIFF copy, 14 downscaling cores, LOG_21Oct"
git push
```
