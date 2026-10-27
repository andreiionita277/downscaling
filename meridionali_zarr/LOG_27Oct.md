# Log 2026-10-27: number of clusters with TopoPyScale's `search_number_of_clusters` (no downscaling)

## What was run
- New script `scripts/k_search_helper.py`: calls `TopoPyScale.topo_sub.search_number_of_clusters` (fork,
  `topo_sub.py:215`) on the 2025 terrain (`data/meridionali_2025/outputs/ds_param.nc`, read only), pixels inside
  `mask.tif`, config features `x, y, elevation x4, slope, aspect_cos, aspect_sin, svf`, mini-batch k-means, seed 2.
- Not `Topoclass.search_optimum_number_of_clusters`: that wrapper ignores the mask and the seed.
- Random 10 M-pixel sample (21.75 % of 45,976,091 masked px), k scaled to the sample (same pixels per cluster as
  the real run), as `k_quick.py` (LOG.md §17, §25). Results reported for the real k.
- Command (from `downscaling/`):
  ```
  ../venv/bin/python meridionali_zarr/scripts/k_search_helper.py meridionali_zarr/config/config_meridionali_2025.yml
  ```
- 2 h 34 min, peak 6.8 GB RAM (Agisoft was running on Windows at the same time, so it was slower).
- Output: `data/meridionali_2025/outputs/k_search/{results.csv, summary.json, k_search.png}`,
  log `data/logs/k_search_20261027.log`.

## Result
| k | WCSS | Davies-Bouldin | Calinski-Harabasz | elev. RMSE (m) |
|---|---|---|---|---|
| 250 | 2.18e7 | 29.9 | 398,109 | 77.2 |
| 500 | 1.61e7 | 20.8 | 278,680 | 63.8 |
| 750 | 1.36e7 | 24.8 | 244,388 | 59.0 |
| 1000 | 1.19e7 | 33.8 | 221,585 | 55.9 |
| **1500** | 1.00e7 | 32.9 | 178,564 | 50.4 |
| 2000 | 8.89e6 | 30.2 | 158,272 | 47.5 |
| 2500 | 8.15e6 | 36.4 | 138,874 | 46.0 |
| 3000 | 7.56e6 | 35.6 | 124,261 | 43.7 |
| 4000 | 6.76e6 | 34.8 | 108,586 | 41.1 |
| 5000 | 6.12e6 | 37.2 | 99,013 | 39.1 |

- Elbow of WCSS: **k = 1500**; elbow of elevation RMSE: **k = 1500** (point farthest below the straight line
  from the first to the last point; depends on the tested range 250-5000).
- Davies-Bouldin min at 500 and Calinski-Harabasz max at 250: **not usable here**. The helper computes both on
  the unscaled, unweighted columns, where x/y in metres (range ~285 km) outweigh elevation; CH also falls
  steadily with k for terrain, which has no natural clusters.
- Elevation RMSE at 2000 (47.5 m) matches the earlier k test (47.2 m, LOG.md §25).
- 1500 -> 2000 gains 2.9 m RMSE (~0.02 K at 6.5 K/km). The 2000 used in the 2006-2025 runs is just past the elbow.
- Nothing in the downscaling outputs or configs was changed.

## New project: redo all years from scratch (user, 2026-10-27)
- New pressure-level data: all 14 levels 600-1000 hPa (instead of EDH's 5).
- Focus: the mountains (whole-range climate analysis) + detailed SURFEX-Crocus for the alpine zone (>= 2000 m).
  Lowlands not of interest.
- Proposal: **mountain-only mask** (no clusters spent on lowlands) + **clustering groups by elevation band**
  (fork's `clustering_groups` + `clustering_group_weights`, topoclass.py:359-454) with extra clusters for >= 2000 m.

### Area above candidate floors (DEM, 46.0 M px)
| floor | share | km2 | patches | 3 largest patches (share of area) |
|---|---|---|---|---|
| 600 m | 42.5 % | 17,589 | 837 | 56 / 35 / 2 % |
| 700 m | 35.7 % | 14,770 | 572 | 53 / 34 / 5 % |
| 800 m | 30.2 % | 12,502 | 543 | 34 / 33 / 22 % |
| 1000 m | 21.2 % | 8,764 | 460 | 38 / 35 / 22 % |
- Bands: 1000-1500 m 5,815 km2; 1500-2000 m 2,540 km2; >= 2000 m 408 km2 (453,561 px).
- From 800 m the range splits into 3 blocks (Olt and Jiu gorges cut through); ~540 small isolated hills.
- Proposed mask: DEM >= 800 m, patches < 5 km2 dropped, enclosed holes filled (keeps intramontane basins,
  e.g. Petrosani, Loviștea: cold-air pools matter for the climate).

### Test `scripts/k_groups_test.py` (started ~same time, log `data/logs/k_groups_20261027.log`)
Elevation RMSE (pixel vs cluster centre) per band, slope and svf RMSE, clusters and km2 per cluster per band, for:
A rectangle k=2000 (current) | B mountains k=2000 | C mountains, groups <1500/1500-2000/>=2000 with weights
0.5/0.25/0.25, k=2000 | D same as C with k=3000. 6 M-pixel samples, k scaled. Mask written to
`data/meridionali_2025/outputs/k_groups/mask_mountains_800.tif` (open it in QGIS to check).
Note: the fork requires group weights summing to exactly 1 (`gw.weight.sum() != 1`), 0.5/0.25/0.25 does.

### Test result (6.5 min, peak 5.9 GB). Mountain mask >= 800 m: 13,793,520 px (12,414 km2)
Elevation RMSE (m) by band / clusters per band (real-k equivalent) / km2 per cluster at >= 2000 m:
| scenario | 800-1000 | 1000-1500 | 1500-2000 | >= 2000 | clusters >= 2000 | km2/cluster >= 2000 |
|---|---|---|---|---|---|---|
| A rectangle k=2000 (current) | 61.1 | 63.7 | 68.3 | 90.8 | 54 | 7.6 |
| B mountains k=2000 | 35.1 | 36.8 | 38.8 | 50.2 | 97 | 4.2 |
| C mountains + groups k=2000 | 24.4 | 24.4 | 19.3 | 13.8 | 499 | 0.82 |
| D mountains + groups k=3000 | 22.6 | 22.4 | 17.8 | 12.5 | 749 | 0.55 |
- Slope RMSE (deg) 800-1000 / 1000-1500 / 1500-2000 / >= 2000: A 4.9/5.0/5.2/6.6, B 3.7/3.6/3.8/4.7,
  C 4.2/4.2/4.6/5.0, D 3.9/3.9/4.3/4.7. Groups favour elevation (per-group scaling stretches elevation within
  a narrow band); k=3000 restores slope. svf RMSE 0.021-0.026 in B-D (A up to 0.033).
- Filled basins < 800 m inside the mask get no own clusters: elevation RMSE 60-80 m there.
- **Recommended: D** (mask >= 800 m, k = 3000, groups floor-1500 / 1500-2000 / >= 2000 with weights 0.5/0.25/0.25
  -> 1500/750/750 clusters). Awaiting user confirmation; nothing changed in configs yet.

## User: keep the whole rectangle (lowlands possibly for dendro studies), but favour the mountains
- Instead of a mountain mask: whole rectangle, 4 groups < 800 / 800-1500 / 1500-2000 / >= 2000 m, weights
  0.25 / 0.375 / 0.1875 / 0.1875 (sum exactly 1). E: k = 3000 (750/1125/563/563); F: k = 4000 (1000/1500/750/750).
- `k_groups_test.py` got `--scenarios` and `--tag`. Run (6.4 min, peak 6.7 GB):
  `../venv/bin/python meridionali_zarr/scripts/k_groups_test.py meridionali_zarr/config/config_meridionali_2025.yml --scenarios A E F --n-sample 10000000 --tag _rect`
  -> `data/meridionali_2025/outputs/k_groups/results_rect.csv`

| scenario | elev RMSE (m) <800 / 800-1000 / 1000-1500 / 1500-2000 / >=2000 | slope RMSE (deg) same bands | km2 per cluster same bands |
|---|---|---|---|
| A rectangle k=2000 (current) | 42.2 / 52.9 / 55.9 / 60.8 / 82.7 | 3.6 / 4.6 / 4.7 / 4.9 / 6.0 | 25 / 14 / 16 / 13 / 8.1 |
| E rectangle groups k=3000 | 22.9 / 27.0 / 27.9 / 22.0 / 15.8 | 4.0 / 4.6 / 4.6 / 5.1 / 5.5 | 39 / 8.3 / 8.6 / 4.5 / 0.73 |
| F rectangle groups k=4000 | 21.3 / 25.9 / 26.0 / 20.8 / 14.6 | 3.9 / 4.4 / 4.4 / 4.9 / 5.3 | 29 / 6.4 / 6.3 / 3.4 / 0.55 |
- (A differs from the first test, 47 vs 42 m etc., because of the different sample size: sampling noise of a few m.)
- Lowlands also improve with groups (42 -> 21-23 m) despite fewer clusters: per-band scaling makes elevation
  count more within each band. svf slightly worse than A at 800-2000 m (0.026 vs 0.023), negligible.
- F in the mountains is within 2-4 m of D (mountain-only, k=3000), with every lowland pixel still covered.
- **Recommended: F** (whole rectangle, k = 4000, 4 elevation groups). Awaiting user confirmation.

## User: test setup F with Nandhni's new ERA5 code, January 2025, GeoTIFFs of the daily mean only, no Crocus (17:15-)

### Nandhni's update (merged today: 2ac6bee, a438d47)
- `zarr_workflow/era5_download/era5_download.py`: builds the ERA5 Zarr store topo_scale_zarr reads, with all 14 levels
  600-1000 hPa. Pressure levels from **NCAR THREDDS** (d633000, same values as the CDS, ~2.6 h per year, no queue),
  single levels from NCAR (OPeNDAP, tp = lsp + cp) or the CDS. Resumable; every unit written, read back and compared;
  month validation (all hours, all levels, no NaN). Her 2020 run (both sources NCAR) passed all checks.
- Her `run_year.py` gained `data_repository: local` (only check the prepared store, `workflow.check_era5_store`).

### What I set up
1. ERA5 config `config/era5/config_era5_meridionali_2025.yml`: copy of her 2020 config (plev + surf from NCAR),
   area of our EDH store (N 46.5, S 44.25, W 21.5, E 26.25; 10 x 20 cells), store for all of 2025 in
   `data/era5_600-1000hPa/2025/ERA5.zarr`, filled month by month. January started 17:25:
   `../venv/bin/python zarr_workflow/era5_download/era5_download.py meridionali_zarr/config/era5/config_era5_meridionali_2025.yml download --months 1`
   (log `data/logs/era5_ncar_2025-01_20261027.log`).
2. `scripts/workflow.py`: `check_era5_store` copied unchanged from hers. `scripts/run_year_public.py`: with
   `data_repository: local` it calls that check instead of `get_era5` (EDH).
3. Setup F inputs in `data/dem/meridionali_F/`: `groups.tif` (1 <800, 2 800-1500, 3 1500-2000, 4 >=2000 m, from
   dem.tif; in the mask 32.09 M / 10.61 M / 2.82 M / 0.45 M px) and `group_weights.csv` (0.25/0.375/0.1875/0.1875).
4. Project folder `data/meridionali_F_2025/outputs/`: `ds_param.nc` = the 2025 terrain parameters without the old
   cluster map and mask (same stored integers: checked identical), `da_horizon.nc` = hard link to the 2025 file
   (only read). Slope/aspect/svf (2.5 h) and horizon are reused; clustering, solar geometry are new.
5. Config `config/config_meridionali_F_2025-01.yml` (from the 2024 one): Jan 2025, ERA5 path + `data_repository: local`,
   `n_clusters: 4000`, `clustering_groups` + `clustering_group_weights`. Nothing else differs (checked with diff).

### Bug found in the fork (would have emptied ds_param.nc) and fixed in our runner
- With a cached `ds_param.nc`, `topo_param.compute_dem_param` reopens it lazily (topo_param.py:275), and
  `extract_topo_cluster_param` then rewrites the same file while it is open (topoclass.py:487): HDF5 lock error,
  **file left at 0 bytes**. Never hit before: in 2025 the terrain was freshly computed (in memory), and later
  years reused the clusters. Found on a test copy in the scratchpad, no real file affected.
- Fix in `run_year_public.py`: when the clusters still have to be computed, load ds_param into memory and close
  the file before `extract_topo_param` (~2 GB).
- Group clustering tested with the fork's own code on an 800 x 800 px crop around the Fagaras ridge (k = 40):
  10/15/8/8 clusters (fork rounds up: 7.5 -> 8), centres inside their band (e.g. group 4: 2052-2400 m), 0 pixels
  assigned to a cluster of another group, ds_param rewritten with cluster map + `cluster_group`.

### User: is 4000 too much? Is 3000 much better than 2000? -> scenario G (rectangle + groups, k = 2000)
`k_groups_test.py ... --scenarios G --n-sample 10000000 --tag _rect_k2000` (2 min) -> `results_rect_k2000.csv`
| scenario | elev RMSE (m) <800 / 800-1000 / 1000-1500 / 1500-2000 / >=2000 | slope RMSE (deg) same bands |
|---|---|---|
| A no groups k=2000 (today) | 42.2 / 52.9 / 55.9 / 60.8 / 82.7 | 3.6 / 4.6 / 4.7 / 4.9 / 6.0 |
| G groups k=2000 | 25.1 / 29.4 / 30.4 / 23.8 / 17.1 | 4.5 / 5.0 / 5.1 / 5.5 / 5.9 |
| E groups k=3000 | 22.9 / 27.0 / 27.9 / 22.0 / 15.8 | 4.0 / 4.6 / 4.6 / 5.1 / 5.5 |
| F groups k=4000 | 21.3 / 25.9 / 26.0 / 20.8 / 14.6 | 3.9 / 4.4 / 4.4 / 4.9 / 5.3 |
- The big gain is the groups (A -> G). 2000 -> 3000 with groups: 1.3-2.5 m elevation (~0.01-0.02 K), 0.4-0.5 deg slope.
- With 2000 + groups, slope is WORSE than today below 2000 m (groups spend the clusters on elevation); from 3000
  on, slope is back to today's level or better in every band. Cost per year: ~5 / 8 / 10 min downscaling,
  1.6 / 2.4 / 3.2 GB hourly store (k = 2000 / 3000 / 4000).
- NCAR download slow this evening: 14 units in 15 min, read timeouts (retried automatically).

### User's uses: (1) climate evolution of the whole range, (2) Crocus snow for the alpine zone, (3) yearly 6.5 degC
### growing-season isotherm (thermal treeline, ~1700-2100 m). -> scenario H: no group boundary inside the treeline zone
H = groups <800 / 800-1600 / >=1600 m, weights 0.25/0.375/0.375, k = 3000 (750/1125/1125), vs E. Reporting bands
around the treeline, plus mean bias (pixel - centre; > 0 = centres too low). `--scenarios E H --bands 0 800 1500
1700 1800 1900 2000 2100 2300 2600 --tag _treeline` (4 min) -> `results_treeline.csv`.
| | 1500-1700 | 1700-1800 | 1800-1900 | 1900-2000 | 2000-2100 | 2100-2300 | 2300-2600 |
|---|---|---|---|---|---|---|---|
| E elev RMSE (m) | 21.2 | 21.6 | 23.6 | 25.2 | 14.5 | 15.9 | 20.3 |
| H elev RMSE (m) | 27.9 | 22.9 | 24.8 | 25.6 | 27.0 | 29.9 | 34.9 |
| E bias (m) | -1.8 | 0.6 | 1.6 | **6.0** | -1.8 | 1.0 | 4.6 |
| H bias (m) | 3.9 | 0.5 | 2.2 | 0.9 | 1.3 | 4.9 | **13.4** |
| E slope RMSE (deg) | 4.9 | 5.3 | 5.4 | 5.5 | 5.3 | 5.5 | 6.6 |
| H slope RMSE (deg) | 4.5 | 4.4 | 4.7 | 4.8 | 4.9 | 5.2 | 5.8 |
| E clusters | 290 | 110 | 92 | 69 | 207 | 285 | 69 |
| H clusters | 326 | 262 | 161 | 143 | 115 | 147 | 51 |
- E's 2000 m boundary is visible: cluster density jumps 3x at 2000 m, and 1900-2000 m pixels get centres 6 m too
  low on average (~0.04 K). Small, but it sits exactly where the isotherm moves.
- H: treeline zone 1700-2000 m gets ~2x more clusters, no jump, bias <= 2 m, slope 0.5-0.9 deg better at
  1500-2300 m; but above 2000 m elevation RMSE 27-35 m (E 15-20 m) and +13 m bias on the highest summits.
- NCAR download: only 15 units in ~45 min (read timeouts, retried).

### Crocus: points or clusters? (user question)
- Cluster run: every pixel gets its cluster's series, computed for the cluster centre (mean elevation, slope,
  aspect, svf, horizon). A chosen pixel can be looked up (`point_name` map in ds_param.nc), but it gets the
  cluster's forcing, not its own terrain. Fine on average (alpine: 15-20 m, ~5 deg slope); atypical sites
  (summit/ridge stations, cirques) can differ much more, especially in horizon (timing of sun on snow).
- Point run (`sampling: method: points` + CSV of coordinates): exact terrain per site, same ERA5 store, minutes for
  tens of points. Every alpine pixel as a point (453k) would be ~80 GB/year of hourly forcing: not practical.
- **User: clusters are good for a first assessment of snow evolution with Crocus.** A point run at snow/weather
  stations (validation) can be added later at any time without redoing the cluster run.

### FINAL SETUP (user agreed, ~18:15): **E**
- Whole rectangle (current mask); groups <800 / 800-1500 / 1500-2000 / >=2000 m (`data/dem/meridionali_F/groups.tif`),
  weights 0.25/0.375/0.1875/0.1875 (`group_weights.csv`), **k = 3000** -> 750/1125/563/563 (fork rounds up: 3001).
- Features, seed, mask unchanged; ERA5 14 levels 600-1000 hPa from NCAR; terrain parameters and horizon reused.
- Why E over H: alpine accuracy (15-20 vs 27-35 m) for Crocus; its 2000 m boundary effect on the isotherm is ~6 m
  (~0.04 K), well below the year-to-year signal. H stays the option if the isotherm becomes the main product.
- (Folder/config names still say "F" from the earlier plan: `meridionali_F_2025/`, `config_meridionali_F_2025-01.yml`.)

### First product test: 1 January 2025, daily mean temperature GeoTIFF only, no Crocus
- `config_meridionali_F_2025-01.yml`: `n_clusters: 3000`, `end: 2025-01-01` (start 2025-01-01), name/description updated.
- Run in two stages, because ERA5 for Jan 1 was still incomplete (u-wind of 01-01 retrying after a timeout;
  all 7 single-level variables of January done):
  1. terrain now (no ERA5 needed), started ~18:20:
     `../venv/bin/python meridionali_zarr/scripts/run_year_public.py meridionali_zarr/config/config_meridionali_F_2025-01.yml --stop-after terrain`
     log `data/logs/run_E_20250101_terrain_<time>.log`
  2. when Jan 1 is complete: the same command without `--stop-after`, plus `--stats mean` (cached terrain reused).
- `compute_dem_param` took 35 s: it only loaded and re-saved the cached file. **The DEM parameters (slope, aspect,
  svf) are NOT recomputed**, here or for any later year. Per year only `ds_solar.nc` (sun position, ~2 min) is new;
  ds_param.nc and da_horizon.nc are linked, df_centroids.pck copied (TopoPyScale writes horizon columns into it),
  as for 2006-2025.
- Memory at 2 min into the clustering: process 16.7 GB, 2 GB RAM available, swap 31 GB free. Asked the user not
  to run Agisoft during this hour.
- Note for the downscaling stage: the ERA5 downloader writes January as ONE time chunk (744 h). It must not
  write while the downscaler reads that chunk -> pause the download (it resumes) during the downscaling.

### 18:28-18:32: NCAR timeouts are on NCAR's side -> download restarted with 4 parallel requests
- Evidence: 80 timeouts vs 21 written units since 17:25; recent attempts "connection timed out" (no connection
  accepted). NCAR's own THREDDS catalog page answered **HTTP 504 Gateway Timeout after 16 s**, google.com 0.5 s from
  here at the same moment. Every unit that did arrive was written and verified; no other error type.
- The 1 Jan u-wind unit had failed 5 times (waiting 16 min for its next try); all other 1 Jan data in the store.
- User agreed: `ncar: workers: 16 -> 4` in `config/era5/config_era5_meridionali_2025.yml` (fewer timeouts on an
  overloaded server; also kinder to a free service). Downloader stopped at an idle moment (no write in the
  previous 7 s; a unit write takes ~0.1 s), restarted with the same command:
  `193 units in selection, 22 already written, 171 to do` -> log `data/logs/era5_ncar_2025-01_20261027_restart4.log`.
  `verify --months 1` will re-check the stored January data before use.
- My mistake: the `pkill -f` pattern also matched my own shell command (exit 144). Checked right after with `ps`:
  only the downloader and my shell were stopped; the clustering run was untouched (still running, 16.5 GB).

### 18:41: NCAR health check; 1 January complete
- Since the restart (18:31): 1 unit written, 11 connection timeouts. NCAR THREDDS catalog: no answer in 30 s, then
  HTTP 302 after 20-23 s (normally < 1 s); rda.ucar.edu and gdex.ucar.edu main sites: no answer in 30 s. NCAR is
  heavily overloaded or partly down tonight. Downloader keeps retrying (resumable).
- **1 Jan u-wind arrived at 18:32** -> 1 January complete: `check_era5_store` OK (24 h, 14 levels 600-1000 hPa,
  13 variables, no NaN). t on levels -12.8..13.9 degC, t2m -6.0..10.6 degC. NCAR vs old EDH store at the 5 shared
  levels: max |diff| 0.125 K, mean -0.0003 K (= EDH's rounding, as Nandhni found: "t max 0.125 K").

### 18:59: terrain stage done (setup E)
- 43 min 59 s, peak 21.0 GB RAM, exit 0. `compute_dem_param` 35 s (cached, only re-saved), `extract_topo_param`
  2525 s (42 min), solar geometry for 1 Jan 9 s, horizon load + sampling 33 s.
- **3001 clusters: 750 / 1125 / 563 / 563** (groups 1-4). Centre elevations per group: 88-778, 821-1478, 1517-1983,
  2011-2468 m (all inside their band). Files in `data/meridionali_F_2025/outputs/`: ds_param.nc (4.7 GB, with the
  new cluster map and `cluster_group`), df_centroids.pck (with horizon), ds_solar.nc (1 Jan).

### 19:00: 1 January run
- ERA5 download paused (no write in the previous 78 s; `kill` on its PID only, found with
  `pgrep -f '^\.\./venv/bin/python zarr_workflow/era5_download/era5_download\.py'`): 24 units written. To resume:
  `../venv/bin/python zarr_workflow/era5_download/era5_download.py meridionali_zarr/config/era5/config_era5_meridionali_2025.yml download --months 1`
- Run: `../venv/bin/python meridionali_zarr/scripts/run_year_public.py meridionali_zarr/config/config_meridionali_F_2025-01.yml --stats mean --product-workers 1`
  -> `data/logs/run_E_20250101_<time>.log`

### 19:04: 1 January 2025 done (setup E, NCAR 14 levels): all checks OK
- 4 min 59 s, peak 11.2 GB. ERA5 store check OK; downscaling 3001 points in 52 s (0 failed); hourly QA OK
  (t -0.6..12.8 degC, no value outside bounds, SW 0 above bound); daily mean; GeoTIFF export 4 s + check 1 s: OK.
- Product: `data/meridionali_F_2025/outputs/geotiff/t_mean_daily_2025-01.tif` (110 MB, 1 band "2025-01-01",
  EPSG:3844, 30 m); `months/2025-01.json` status ok. ERA5 download resumed 19:05 (24 written, 169 to do;
  log `data/logs/era5_ncar_2025-01_20261027_restart4b.log`).
- Map: 0.75..12.19 degC, mean 7.04 (old run, EDH + 2000 clusters: 0.95..11.13, mean 7.16).
  Mean by elevation band, new - old: <500 m +0.03, 500-1500 m -0.3, 1500-1800 +0.16, 1800-2000 +0.38,
  2000-2200 +0.41, 2200-2600 +0.31 K. High terrain warmer, as Nandhni found for EDH's 5 levels (too cold aloft).
  Two changes at once (ERA5 levels + clustering), so this comparison does not separate them.
  Visible in the overview: the Transylvanian depression (north) is colder in the new map (winter inversion).
- Figure `data/meridionali_F_2025/outputs/compare_20250101.png` (new vs old, whole area + 30 km zoom on Fagaras
  with the 800/1500/2000 m contours).
- **Group-boundary steps** (mean T in 25 m slices; centre elevation of the pixels' clusters):
  800 m: 7.52 -> 7.07 degC (centres 769 -> 831 m), **0.45 K step**; 1500 m: 5.02 -> 4.88 (1468 -> 1525 m), 0.14 K;
  2000 m: 3.18 -> 2.93 (1972 -> 2015 m), 0.24 K; normal change per 25 m slice 0.03-0.1 K.
  Cause: below a boundary the clusters can only hold lower pixels (centres ~19 m below), above only higher ones
  (~19 m above), so centre elevation jumps 40-60 m at the boundary. Not visible by eye in the zoom at a 0-12 degC
  scale, but it is systematic along the contour. Fix options to discuss: per-pixel elevation (lapse-rate)
  adjustment when mapping (pixel T = cluster T + lapse x (z_centre - z_pixel)); or accept.

### 19:20: visual check by the user (QGIS, a ridge, 1 Jan 2025 daily mean): groups confirmed
- Old map (EDH, 2000 clusters, no groups): "very random, salt and pepper": patches and speckle. Cause: ~50 clusters
  above 2000 m (~8 km2 each), each spanning several hundred m of elevation; neighbouring pixels that differ a little
  in slope/aspect fall into different clusters with centres at quite different elevations -> jumps between
  neighbours that are not real climate.
- New map (E: 3000 clusters, 4 groups): smooth, follows the terrain (ridges cold, valleys warm). 563 alpine
  clusters (~0.7 km2), each within a narrow elevation band, so neighbouring clusters have near-equal centre elevations.
- Group-boundary steps (0.14-0.45 K, see above) not visible in the user's view; the old map had steps of that size
  everywhere. -> boundary steps accepted for now; lapse-rate adjustment when mapping stays an optional refinement.
- To check later: SW radiation / Crocus still distinguish north and south slopes (aspect), not only elevation.
- Note for map comparisons in QGIS: set the same fixed min/max on both layers (default stretch is per layer).
