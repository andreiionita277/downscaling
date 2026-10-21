# Log: Meridional Carpathians 2025 pilot (native, no Docker)

Goal (2026-10-15): downscale the whole year 2025 for the Southern Carpathians rectangle DEM
(`C:\Projects\NewDownscale`), with daily statistics into Zarr and daily GeoTIFFs. Test repo, run natively
(no Docker). Each entry: what was done, why, and the result.

---

## 2026-10-15

### 1. Inputs received
- `C:\Projects\NewDownscale\DEM_AOI_Meridionali.tif`: FABDEM, ArcGIS `ProjectRaster` + `Clip` export.
  9522 x 4832 px, pixel 29.998 x 29.999 m (not square), CRS "Dealul Piscului 1970 / Stereo 70"
  (EPSG:31700, deprecated, now EPSG:3844), Float32, no nodata, 64.5-2519.6 m.
- `AOI_Meridionali.shp`: one polygon, almost exactly the DEM rectangle (corners differ by < 140 m).

### 2. CRS check
- The CRS embedded in the file converts to lat/lon **~126 m** away from EPSG:3844, measured at 3 points.
  126 m is the typical size of a Stereo70 datum shift, so one of the two definitions is wrong.
- The new DEM's values match the old `~/Meridionali/inputs/dem/FABDEM_Meridionali2.tif` (correct for
  EPSG:3844) at the same Stereo70 coordinates, give or take one pixel: mean |diff| 0.00 m after a ≤1-pixel shift.
  So the coordinates are Stereo70 / EPSG:3844, and the embedded definition is what puts lat/lon off.
- **Decision:** treat the DEM as EPSG:3844 (`-s_srs EPSG:3844`).
- **Why it matters:** TopoPyScale converts cluster positions to lat/lon for the ERA5 interpolation and
  the solar geometry. 126 m is small next to ERA5 cells (~30 km), but there is no reason to keep a known error.

### 3. DEM regrid: exact 30 m, EPSG:3844, bilinear
- First version used nearest neighbour (keeps original values exactly). **Replaced** after the user
  pointed out that bilinear is the usual choice for resampling a continuous surface.
  - Rationale: nearest can duplicate or drop rows/columns where the grids drift. That creates
    artificial steps, which go straight into slope/aspect/svf, the clustering features. Bilinear avoids
    them; its smoothing over sub-pixel shifts is negligible. Nandhni's Rila-Pirin DEM is also bilinear.
- Target grid: exact 30 m square pixels, aligned to the original FABDEM 30 m lattice (origin of
  FABDEM_Meridionali2), snapped **inward** so the grid never extends past the source (no nodata edges,
  which would create false slopes and horizons).
  ```
  gdalwarp -s_srs EPSG:3844 -t_srs EPSG:3844 -te 271346.027 361013.492 556976.027 505943.492 -tr 30 30 \
           -r bilinear -dstnodata -9999 -co COMPRESS=DEFLATE -co PREDICTOR=3 -co TILED=YES \
           DEM_AOI_Meridionali.tif data/dem/meridionali_2025/dem.tif
  ```
- Result: 9521 x 4831 px (46.0 M), no nodata, 64.5-2518.5 m. Compared with nearest: max -1.1 m,
  mean slope 12.92° vs 13.15°.
- The DEM is copied to the Linux filesystem: reading from `/mnt/c` under WSL is several times slower.

### 4. Mask
- TopoPyScale needs a 0/1 raster on the DEM grid (`clustering_mask`), not a shapefile.
  `gdal_rasterize -burn 1 -init 0` on the same grid → `data/dem/meridionali_2025/mask.tif`.
- Result: 45,976,091 px = 1 (99.95 %); none on nodata. **Note:** the polygon is the rectangle, so
  lowlands (down to 64 m) are clustered too.

### 5. Config
- `config/config_meridionali_2025.yml`: 2025, k = 2000, features as in Rila-Pirin (elevation weight 4),
  EDH ERA5 → `ERA5.zarr`, all outputs under `data/meridionali_2025/outputs`.

### 6. Environment (native, no Docker)
- Docker is not installed here, and `uv` is not either. Used miniconda's Python 3.13 instead:
  ```
  ~/miniconda3/bin/python3.13 -m venv ~/TopoPyScale-repo-github/venv
  venv/bin/pip install -r downscaling/docker/requirements.lock.txt    # Nandhni's pinned set (numpy 2.4.6, zarr 3.2.1, topocalc commit)
  venv/bin/pip install pytest seaborn python-dotenv                    # as in the Dockerfile
  venv/bin/pip install --no-deps -e fork-original-code/TopoPyScale_Bulgaria_Rila_Pirin_Mountains
  ```
- Why this layout: `downscaling/CLAUDE.md` expects `../venv`, and docker-compose expects the fork at
  `../fork-original-code/`, so the same relative paths work later in Docker.
- Result: imports OK (TopoPyScale from the fork, topocalc C extension compiled).

### 7. Gap between the public fork and `zarr_workflow/`
- The fork on GitHub (last commit 7d54b9a, 2026-10-01) **lacks** Nandhni's later changes that
  `run_year.py` needs: `topo_utils.open_dataset/open_dataarray/dataset_exists`, Zarr caches for
  `ds_param/ds_solar/da_horizon`, and `zarr_store_start/end` (one yearly store filled month by month).
- What the public fork **does** have: `topo_scale_zarr.ClimateDownscaler` writing **one Zarr store for
  the whole period**, resumable per cluster, plus `topo_daily.daily_stats`.
- **Plan for today:** don't wait for her and don't re-implement her changes. Instead, run the whole year in
  one `downscale_climate` call (caches stay NetCDF), then make the daily statistics and GeoTIFFs month by
  month from the hourly store with the existing `workflow.py` functions.

### 8. Decisions asked of the user (13:35)
- **ERA5 source → EDH** (user will provide a key). Rationale: a year in minutes, written straight to
  `ERA5.zarr`, the path Nandhni validated. Known cost: 5 pressure levels (600/700/850/925/1000 hPa); her
  January 2025 comparison found high terrain ~0.5 K colder (1-1.7 K in inversions) than with CDS's 14 levels.
- **Memory → "process them batch by batch"** (user's answer).

### 9. Where the memory goes, and the batched horizon
- Read the fork's code to find the memory peak at 46 M px:
  - `compute_dem_param`: the sky-view factor (topocalc `viewf`, 72 directions) loops one direction at a
    time, so a few GB. Not a problem, only slow (single-threaded).
  - `compute_horizon`: builds all 36 azimuths in float64, keeps a second copy, then `arccos` makes a
    third. That is ~13 GB x 3 = **~40 GB**. This is the problem.
- **New `scripts/horizon_batched.py`:**
  - computes 6 azimuths at a time, with the same azimuths, the same `topocalc.horizon.horizon` call, the same
    `pi/2 - arccos` conversion and the same coordinates (`topo_param.open_dem`);
  - writes each batch straight into `da_horizon.nc` as float32;
  - records finished azimuths in a sidecar `da_horizon.nc.done.json`, so an interrupted run resumes.
  - The sidecar is a separate file because Topoclass opens the horizon with `xr.open_dataarray`, which
    refuses files with more than one data variable.
- **Validation** on the Bucegi DEM (0.65 M px), against the fork's own `compute_horizon`: identical
  coordinates and NaN pattern, max |diff| **6e-8 rad** (float32 rounding), peak RSS 1.8 GB.
- Kept `horizon_increments: 10` (36 directions, as in Rila-Pirin). No loss of detail.

### 10. Making `workflow.py` run on the public fork
- Replaced `tu.open_dataset` / `tu.dataset_exists` (not in the public fork) with local equivalents in
  `workflow.py` (`.zarr` → `xr.open_zarr`, otherwise `xr.open_dataset`). Nothing else changed.

### 11. New runner `scripts/run_year_public.py`
- Same steps and products as `run_year.py`, adapted to the public fork:
  - terrain caches are NetCDF (`ds_param.nc`, `ds_solar.nc`, `da_horizon.nc`), because the public fork writes NetCDF only;
  - the horizon goes through `horizon_batched` (step 9);
  - the hourly `down.zarr` is filled for the **whole year in one call** (`topo_scale_zarr`, resumable per
    cluster). The public fork has no `zarr_store_start/end`, so it cannot fill a yearly store month by month.
  - Then, **per month**: hourly QA → `topo_daily.daily_stats` (UTC days) → `daily.zarr` → GeoTIFFs
    (t mean/min/max, one file per month, one band per day) → checks → `months/YYYY-MM.json`.
- `--stop-after terrain|era5|downscale` runs the chain in stages. Every step skips existing outputs, so a
  crash is resumed by running the same command again.
- Config updated accordingly: NetCDF cache names, `zarr_store_start/end` removed (unused by the public fork).

### 12. Terrain stage started (13:43)
```
cd ~/TopoPyScale-repo-github/downscaling
/usr/bin/time -v ../venv/bin/python meridionali_zarr/scripts/run_year_public.py \
    meridionali_zarr/config/config_meridionali_2025.yml --stop-after terrain \
    > meridionali_zarr/data/logs/terrain_20261015_1343.log 2>&1
```
- Project extent from the DEM: 44.71-46.05 N, 22.11-25.73 E. That is the Southern Carpathians
  rectangle, which also confirms the EPSG:3844 georeferencing.

### 13. EDH key (14:00)
- The key goes in `~/.netrc` (the standard file HTTP clients read credentials from). The fork's
  `fetch_era5_edh` reads it from there, or from the `EDH_TOKEN` environment variable. Format:
  ```
  machine data.earthdatahub.destine.eu
    login edh
    password <key>
  ```
- The user added it. Check: `curl --netrc` on the EDH single-levels `zarr.json` returns **200** with the
  key and **401** without it.
- Fix: the file was `-rw-r--r--` (readable by other users). In bash, the leading `!` of
  `! printf ... && chmod 600 ...` negates the exit status, so `chmod` was skipped. Ran `chmod 600 ~/.netrc`.
- The key was pasted into the chat session. Regenerating it on the EDH site later would be the safe option.

### 14. ERA5 download started in parallel (14:02)
- New `scripts/fetch_era5_only.py` (Topoclass + `get_era5()` only). Why a separate script: ERA5 does
  not depend on the terrain, and running `run_year_public.py --stop-after era5` now would start the
  terrain steps again while the first run is still writing their caches.
  ```
  ../venv/bin/python meridionali_zarr/scripts/fetch_era5_only.py meridionali_zarr/config/config_meridionali_2025.yml \
      > meridionali_zarr/data/logs/era5_20261015.log 2>&1
  ```
- EDH request: 2025-01-01..2025-12-31, 1 h, bbox 44.31-46.45 N, 21.71-26.13 E (DEM extent + 0.4°).

### 15. ERA5 downloaded (14:08)
- `get_era5` done in **332 s** (~280 chunk requests): `data/era5_meridionali/ERA5.zarr`, 88 MB.
- Grid 10 lat x 20 lon (0.25°), levels 600/700/850/925/1000 hPa, all 13 variables the downscaler needs
  (z, t, u, v, q, r on levels; z_surf, d2m, sp, strd, ssrd, tp, t2m at the surface).
- Time 2024-12-05 .. 2026-01-28 hourly. EDH blocks are padded beyond the year; the downscaler only reads
  2025. First 744 h checked: no NaN, t2m 260.0-289.8 K, plausible.
- `edh_blocks/` (81 MB) holds the resumable download pieces and can be deleted later.

### 16. Choice of k (user question, 14:05)
- Question: is k = 2000 right, and should it be larger now that lowlands are included?
- Answer given: lowlands are homogeneous (few clusters describe them), but x/y are clustering
  features, so they still take some clusters. A fixed k therefore gives the mountains fewer clusters
  than a mountain-only mask would. Downscaling cost grows ~linearly with k, but is small (minutes for
  thousands of clusters), so k need not be small.
- Plan: once `ds_param.nc` exists, run a quick test: elevation RMSE (pixel vs its cluster centre) for
  k = 500..4000 on a 2 M-pixel sample, per elevation band. This is the metric Nandhni used for Rila-Pirin
  (gains 49/20/12/7 m per step → k = 500 for ~24 M px, mountains only).

### 17. k test prepared: `scripts/k_quick.py` (14:20)
- Reproduces `Topoclass.extract_topo_cluster_param` (checked in the fork's code): masked pixels,
  StandardScaler, features x weights, MiniBatchKMeans (batch 256 x CPU_cores, seed 2). Centre values are the
  members' means. Metric: elevation RMSE pixel vs centre, overall and per elevation band, plus the number of
  clusters each band receives. Temperature equivalent = elevation RMSE x 6.5 K/km.
- Runs on a random **10 M-pixel sample** (22 %), with k scaled by the same fraction, so pixels per cluster are
  as in the real run. A first version used 2 M px, but then k = 500 shrinks to ~22 clusters in the sample:
  too noisy.
- It reads a **snapshot copy** of `ds_param.nc` (`--ds-param`). Why: after clustering, Topoclass rewrites
  `ds_param.nc` in place (adds the cluster map). If the k test still had the file open, HDF5 file
  locking could make the main run fail.
- Note: `topo_export.to_netcdf` packs ds_param into int16 with 10 bits → elevation steps ~2.4 m
  (±1.2 m). The main run clusters on the fresh, unpacked values; the k test reads the packed file.
  The difference is negligible against cluster errors of tens of metres.

### 18. Progress check (14:40): sky-view factor runs on one core
- `compute_dem_param` has been running ~1 h at ~92 % of ONE core, 9 GB RSS. No error; TopoPyScale prints
  no progress for this step.
- Why one core: topocalc `viewf` computes the horizon in 72 directions in a plain sequential loop. The
  authors parallelised `compute_horizon`, but not this step.
- Measured: one direction on the full DEM (`topocalc.horizon.horizon`) takes **59 s (0°) and 82 s (45°)**.
  So 72 directions ≈ **75-100 min**. Expected end ~14:55-15:25.
- Not restarted: that would lose ~1 h of work to save ≤ 30 min.
- **Improvement for later runs:** compute the directions in parallel (they are independent), about 5-10 min
  on 20 cores. Also, the 36 horizon azimuths (-175, -165, ...) are a subset of viewf's 72 (every 5°), so
  both steps compute them twice (TopoPyScale's own comment in topo_param.py says so). A combined
  step could compute each direction once, write it to da_horizon.nc and add it to the svf. To be
  implemented and validated against viewf after this run.

### 19. Threads do not parallelise topocalc → batched horizon now uses processes (14:50)
- Test on the Bucegi DEM, 12 azimuths: serial **3.2 s**, 6 threads **3.4 s**, 6 processes **0.7 s**.
  topocalc's `horizon` holds Python's GIL, so a thread pool runs one azimuth at a time. This also applies
  to TopoPyScale's own `compute_horizon`: its ThreadPool has never actually run in parallel.
- `horizon_batched.py`: `multiprocessing` fork pool, 8 azimuths at a time (`--horizon-batch 8`). The DEM
  is set in a module global before forking, so workers share it (copy-on-write) instead of each
  receiving a pickled copy. ~1 GB per worker at 46 M px.
- Re-validated against the fork's `compute_horizon` (Bucegi): identical coordinates, max |diff| 6e-8 rad.
- Expected at full size: 36 azimuths x 60-80 s / 8 processes ≈ **5-6 min** (threaded: ~40 min).
- The running terrain job imported the old threaded module at start. A watcher stops it as soon as it
  enters the horizon step, deletes the partial `da_horizon.nc.part`, and restarts the **full chain**
  (no `--stop-after`) into `data/logs/run_<time>.log`. The finished caches (ds_param, df_centroids,
  ds_solar) and ERA5 are reused.
- Why not stop it now: it is ~1.2 h into the single-core sky-view factor, which cannot be resumed.

### 20. Progress (15:18): sky-view factor slower than estimated
- Still in `compute_dem_param` after 1 h 43 min (single core, RSS ~9 GB stable, no error).
- The estimate in §18 counted only the horizon call. Per direction, `topocalc.viewf` also runs ~10
  whole-array numpy operations on 46 M px (arccos, cos, sqrt, masked add). Diagonal directions are
  slower (82 s at 45° vs 59 s at 0°). My timing tests ran alongside it, which also cost some time.
  Revised: ~95-110 min, plus writing ds_param.nc (complevel 9).
- This single step costs ~2 h at 46 M px, so parallelising svf (§18) is the first optimisation for the next run.

### 21. Git (user)
- The user committed `meridionali_zarr/` (data/ excluded by its .gitignore; 14 small files checked
  with `git diff --cached --stat`) and pushed to github.com/andreiionita277/downscaling.
- 403 at first: the push authenticated as GitHub user `andreiionita27` (via VS Code), but the repo
  belongs to `andreiionita277`. Changing `user.name`/`user.email` does not affect authentication; the user resolved it.
- Notes for later: 76 files differed only in line endings (Windows CRLF in the working copy vs LF in the
  repo), so they should not be committed as changes. A 97 MB DEM is already in the repo's history
  (GitHub limit: 100 MB per file).

### 22. Terrain parameters done (16:14)
- `compute_dem_param`: **9077 s (2 h 31 min)**, almost all of it the single-core sky-view factor.
  `ds_param.nc` 321 MB (int16-packed).
- Sanity check: 9521 x 4831, no NaN; elevation 64.5-2518.5 m, slope 0-1.27 rad (0-73°),
  svf 0.455-1.000. Plausible for the Southern Carpathians at 30 m.
- The main run moved on to `extract_topo_param` (k = 2000) at 16:14.

### 23. k test started (16:16)
- Snapshot `outputs/k_quick/ds_param_snapshot.nc` (copy, see §17), then:
  ```
  OMP_NUM_THREADS=8 ../venv/bin/python meridionali_zarr/scripts/k_quick.py \
      meridionali_zarr/config/config_meridionali_2025.yml \
      --ds-param meridionali_zarr/data/meridionali_2025/outputs/k_quick/ds_param_snapshot.nc
  ```
- 8 threads, so it does not slow the main clustering much (24 threads in total).

### 24. Config comment fixed (user asked to see the config)
- The `data_repository` comment still said there was no EDH key; changed to "EDH key in ~/.netrc
  (set up 2026-10-15)". Comment only; the running job read the config at start.

### 25. k test result (16:31): **k = 2000 kept**
10 M-pixel sample (21.75 % of 45.98 M masked px), 14 min 41 s, peak RSS 4.7 GB.
Figure: `data/meridionali_2025/outputs/k_quick/k_quick.png`; numbers: `results.csv`, `summary.json`.

| k | elev. RMSE all (m) | ≈ K (6.5 K/km) | 0-500 | 500-1000 | 1000-1500 | 1500-2000 | 2000-3000 m |
|---|---|---|---|---|---|---|---|
| 500 | 63.6 | 0.41 | 53.0 | 65.8 | 75.1 | 82.3 | 105.1 |
| 1000 | 55.3 | 0.36 | 46.5 | 57.2 | 65.7 | 69.9 | 89.1 |
| **2000** | **47.2** | **0.31** | 39.2 | 48.7 | 55.9 | 60.8 | 82.7 |
| 3000 | 43.4 | 0.28 | 36.0 | 45.2 | 50.7 | 56.4 | 73.0 |
| 4000 | 40.7 | 0.26 | 33.6 | 42.3 | 47.8 | 52.8 | 71.3 |

- Gain per additional 1000 clusters: 16.6 m (500→1000), 8.1 (1000→2000), **3.8 (2000→3000)**,
  2.7 (3000→4000). The curve flattens after 2000: doubling to 4000 gains 6.5 m (~0.04 K).
- That residual is small next to the other errors: the model-vs-station error in Rila-Pirin was ~4 K
  hourly, and dropping 9 pressure levels (EDH) cost ~0.5 K at high elevation in January.
- **Decision: keep k = 2000** (already running). Same choice as the old 2024 Meridionali config.
- **Lowlands take most clusters** (k = 2000): pixels by band 45 / 34 / 14 / 6 / 1 %; clusters by
  band (centre elevation) 671 / 726 / 363 / 189 / 51. So 0-1000 m gets ~70 % of the clusters. That
  is because x/y are clustering features, which spread clusters by area.
- **Mountains have the largest errors:** 61 m (1500-2000 m) and 83 m (2000-3000 m) ≈ 0.4-0.54 K.
  Raising k helps them only slowly (83 → 71 m at k = 4000). Better options for a later run:
  (a) a mask limited to the mountains, (b) `clustering_groups` (TopoPyScale's per-group clustering) with
  more clusters for the high bands, or (c) a higher elevation weight. **To discuss with the user.**

### 26. Clustering memory (16:32)
- `extract_topo_param` (k = 2000 on 46 M px): after 16 min, RSS **17.2 GB**, 2.7 GB in swap,
  ~5.6 GB RAM still available.
- Known heavy end of this step (fork code, topoclass.py ~465-480): cluster labels → zero-padded
  strings for 46 M pixels (pandas object strings), stored twice in ds_param (`cluster_labels`,
  `point_name`), then rewritten to ds_param.nc. Expect several more GB, partly in swap (32 GB).
- Not interrupted: the running process cannot be changed. If it is killed (OOM), the watcher reports it.
  Fallback: make that step lighter (e.g. integer labels, strings built only at write time), then restart.
  Terrain parameters (2.5 h) are cached and would not be recomputed.

### 27. Terrain stage finished; full chain restarted (17:22)
- Clustering survived: Mini-Batch K-means **3805 s** (63 min), whole `extract_topo_param` 3909 s;
  ds_param.nc is now 4.4 GB (with cluster labels). `compute_solar_geometry` 103 s.
- The watcher stopped the old job at the horizon step as planned (it still had the threaded horizon
  code loaded), but the watcher itself was killed (exit 144) before it could restart. Restarted by hand:
  `../venv/bin/python meridionali_zarr/scripts/run_year_public.py meridionali_zarr/config/config_meridionali_2025.yml`
  → `data/logs/run_20261015_1722.log`. Cached steps loaded in ~2 min (the fork's `compute_dem_param`
  re-opens ds_param.nc and saves it again through `outputs/tmp/`, values unchanged; costs ~2 min).

### 28. Horizon, ERA5, downscaling (17:24-17:48)
- `compute_horizon_batched`, 8 processes: 4 batches of 8 azimuths at ~260 s, last batch of 4 in 95 s,
  **1141 s** (19 min) total; ~18 GB RAM used during a batch. Slower than the small-DEM estimate
  (5-6 min), still far below the fork's ~40 GB single-process version. Sampling at centroids: 26 s.
- `get_era5`: all 17 EDH blocks already on disk, 28 s.
- `downscale_climate`: 2000 points × 8760 h on 20 cores in **234 s** (2.2 s per point per worker),
  0 failed. Up to ~12 GB in swap, mostly the idle parent (it holds the terrain data); no slowdown.
- 308 points (lowlands, Danube/Olt side) lie below the 1000 hPa geopotential at some hours: TopoPyScale
  then uses surface pressure + the lowest level without vertical interpolation (its standard fallback).
  Expected for a domain reaching 64 m; reported, not changed.

### 29. Monthly products: January OK (17:48-18:04)
- QA hourly OK: t −14.5..15.4 °C, no NaN in any variable.
- **4 hourly SW values > 1400 W/m²** (max 1559, all SW_direct): TopoSCALE's known low-sun spike on steep
  slopes facing the sun (direct beam divided by a small cos(zenith)). Reported only, as in Rila-Pirin.
- daily_stats 3 s, write daily.zarr 1 s, pixel index 61 s (once), GeoTIFF export **287 s**,
  GeoTIFF checks **550 s** (compare to daily.zarr at sample pixels and full band 1: max |diff| 4.8e-7 K).
- t_mean/t_min/t_max GeoTIFFs, 31 bands each. **≈15 min per month → year done ≈ 21:00.**
- The checks take the most time (they re-read the 46 M px band and compare every pixel). They could be
  sampled for production runs; left as is for this pilot.

### 30. Visual check of January (outputs/jan_check.png)
- t_mean on 1 and 15 January next to the DEM (8× downsampled): the temperature field follows the
  terrain (corr(t, elevation) = −0.76 on 15 Jan); ridges of Retezat/Parâng/Făgăraș are the coldest (≈ −10 °C
  on 15 Jan); no cluster artefacts or NaN holes; valleys (Olt, Jiu) are visible.
- 15 Jan shows an **inversion**: the Transylvanian basin (north, ~400-600 m) is colder (≈ −6 °C) than the
  southern foothills at similar elevation (≈ −1 °C). That comes from the ERA5 pressure-level profile,
  i.e. TopoSCALE is doing what it should, not a bug.
- **Size: each monthly GeoTIFF is ~2.8 GB** (float32, 31 bands, DEFLATE without predictor), so the year is
  ~100 GB for t mean/min/max. Fits (WSL disk 857 GB free, but it lives on C:, which has 252 GB free).
  Not changed mid-run, so that all months are written the same way. For later: PREDICTOR=3 or int16 in
  0.01 °C (scale/offset) would make the files ~3-5× smaller.

### 31. RESULT: 2025 complete, all checks passed (20:24)
`YEAR COMPLETE, ALL CHECKS PASSED`, exit 0. Run 17:22 → 20:24 (3 h 02 min wall), peak RSS of the main
process 14.0 GB. Every month has status `ok` in `outputs/months/YYYY-MM.json`; summary in
`outputs/year_summary.json`.

| month | t hourly min..max (°C) | SW hours > 1400 W/m² (max) | time (s) |
|---|---|---|---|
| 01 | −14.5 .. 15.4 | 4 (1559) | 901 |
| 02 | −18.2 .. 13.6 | 3 (2007) | 669 |
| 03 | −15.5 .. 22.6 | 13 (1824) | 770 |
| 04 | −16.6 .. 26.6 | 6 (1950) | 731 |
| 05 | −6.5 .. 24.9 | 0 (993) | 760 |
| 06 | 1.8 .. 35.3 | 14 (1859) | 720 |
| 07 | 2.4 .. 35.4 | 6 (1630) | 757 |
| 08 | 1.3 .. 34.2 | 13 (1915) | 756 |
| 09 | −4.4 .. 30.8 | 0 (1320) | 716 |
| 10 | −5.2 .. 20.6 | 7 (1757) | 761 |
| 11 | −8.1 .. 20.7 | 0 (1305) | 729 |
| 12 | −20.2 .. 16.1 | 0 (853) | 770 |

- No NaN anywhere; all GeoTIFF bands match daily.zarr (max |diff| ~5e-7 K).
- SW spikes: 66 point-hours out of 17.5 M (2000 points × 8760 h), all low-sun hours on steep slopes
  (known TopoSCALE behaviour, reported only). Worth clipping/handling if SW is used later.
- Outputs (`data/meridionali_2025/outputs/`):
  - `downscaled/down.zarr` hourly, 2000 points × 8760 h, all variables, 1.6 GB
  - `downscaled/daily.zarr` daily t mean/min/max (and the other daily stats), 95 MB
  - `geotiff/t_{mean,min,max}_daily_2025-MM.tif`, 36 files, 30 m, EPSG:3844, one band per day, **87 GB**
- Total time of the pilot including terrain: terrain params 2 h 31 min (svf, single core) + clustering
  65 min + horizon 19 min + downscaling 4 min + products 2 h 30 min.
- End-of-run warning "20 leaked semaphore objects" comes from joblib/loky workers of the downscaler at
  shutdown: harmless.

### 32. Suggested next steps (to discuss)
1. GeoTIFF size: PREDICTOR=3 or int16 (0.01 °C) → ~3-5× smaller; sample-based checks → products ~2× faster.
2. Mountain accuracy: mountain-only mask, `clustering_groups`, or higher elevation weight (§25).
3. Speed: parallel svf (viewf) sharing directions with the horizon (§20).
4. Validation against stations (as in Rila-Pirin).
5. Get Nandhni's updated fork (Zarr caches, month-by-month filling) and drop the local shims.

---

## 2026-10-21

### 33. SURFEX-Crocus forcing for 2025 (`scripts/export_surfex.py`)
- Request: the same export as `mp.to_surfex(year_start=..., year_end=...)` in the old Galesu script, for 2025 only.
- New `scripts/export_surfex.py`: calls TopoPyScale's own `topo_export.to_surfex` with the arguments
  `Topoclass.to_surfex` passes, on `downscaled/down.zarr` and `df_centroids.pck`. Nothing is re-run.
  TopoPyScale itself is not changed; three local adaptations, all in the script:
  1. `to_surfex` drops `vp, cse, cos_illumination, precip_lapse_rate`, intermediate variables of the NetCDF
     downscaler that the Zarr store does not have → added as zeros so they can be dropped (never exported).
     (First attempt failed on this, 13 s, nothing written.)
  2. It reads LAT/LON from df_centroids columns `latitude/longitude`; this fork names them `lat/lon` →
     copied under the expected names. (Second attempt failed on this.) Then tested on 5 points in a scratch folder.
  3. **Bug in `topo_utils.time_vecs`: each yearly window ends at 00:00 of its last day, so `to_surfex` drops
     the last 23 hours** (2025: 8737 of 8760 h). Here windows end at 23:00. `--fork-year-end` restores the
     old behaviour. The old Galesu files have this gap: `~/GalesuDownscaling/outputs/FORCING_2022100100_2023093000.nc`
     has 8737 h and ends on 2023-09-30 00:00.
- Command:
  ```
  ../venv/bin/python meridionali_zarr/scripts/export_surfex.py meridionali_zarr/config/config_meridionali_2025.yml \
      --start 2025-01-01 --end 2025-12-31 > meridionali_zarr/data/logs/surfex_2025_20261021_1532.log 2>&1
  ```
- Result: `outputs/FORCING_2025010100_2025123123.nc`, **2000 points × 8760 h**, 1.82 GB, NETCDF3_CLASSIC,
  5 min 19 s, peak 2.4 GB RAM. Checks (in the script): all hours present, no NaN, Tair = store t exactly,
  (Rainf + Snowf) × 3600 = tp, ZS = cluster elevations. Tair −20.2..35.4 °C (as the hourly QA),
  snowfall 9.0 % of precipitation, ZS 82-2346 m.
- Compared with the old Galesu forcing and with snowtools' reference `FORCING_test_base.nc`: same 22
  variables, names, units, dims, float64 and format. Every reference variable is present. Differences:
  `reference_time` absent (not read anywhere in `~/git/surfex/src`; not in the snowtools reference either),
  `point_ind` added (cluster number; ignored by SURFEX).
- As in all TopoPyScale SURFEX exports: `DIR_SWdown` = total SW and `SCA_SWdown` = 0 (the direct/diffuse split
  exists in the store as SW_direct/SW_diffuse, if wanted later).
