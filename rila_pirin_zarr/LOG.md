# Log: rila_pirin_zarr (clean end-to-end Rila-Pirin run)

## 2026-11-05: folder set up, clustering design decided before the run

### Why a new folder
A clean end-to-end run that uses everything learnt in `zarr_workflow/` (Rila-Pirin, k = 500, Oct 2026) and
`meridionali_zarr/` (Southern Carpathians, elevation groups, LOG_27Oct.md). `zarr_workflow/` stays as it is.

### What was copied
- `scripts/run_year.py`, `workflow.py`, `recheck_qa.py`: from `zarr_workflow/scripts/` (Docker fork, Zarr caches,
  month-by-month store, `data_repository: local`; `workflow.RANGES` already has the SW range-check fix).
- Data, logs and test outputs go to `data/` (git-ignored, `.gitignore` in this folder).

### Starting point (current production, `zarr_workflow/config/config_2025_era5_600-1000hPa.yml`)
- DEM 25 m EPSG:32634, 5079 x 4797 px, rectangle mask: 13,945,288 px, 81-2905 m.
- k = 500, no groups, features x1 y1 elevation4 slope1 aspect_cos1 aspect_sin1 svf1, minibatch k-means, seed 2.
- Area per band inside the mask: <800 m 30.3 %, 800-1000 12.9 %, 1000-1500 30.5 %, 1500-2000 16.8 %,
  2000-2500 8.5 %, >=2500 1.0 % (>= 2000 m: 832 km2; in the Meridionali domain only 1 %).

### Bug found: centroid aspect (fork)
- `aspect` is not a clustering feature, so `extract_topo_cluster_param` fills the centroid's aspect with the
  ARITHMETIC mean of the members' aspect (`df_param.groupby('cluster_labels').mean()`). Aspect is circular
  (0..2pi): a north-facing cluster with pixels at 10 deg and 350 deg gets 180 deg = south.
- `topo_scale_zarr.py:242` uses `row.aspect` for the illumination angle (direct SW on the slope).
- Current Rila-Pirin run (500 clusters): 47 centroids have aspect > 90 deg away from the circular mean
  atan2(aspect_sin, aspect_cos); 46 of the 121 north-facing clusters. Their direct SW is computed for the
  wrong slope orientation. (Possible link with the known SW sunset spikes, not checked.)
- Fix planned for this run (no change in the fork): after `extract_topo_param`, set
  `df_centroids.aspect = atan2(aspect_sin, aspect_cos) mod 2pi` before the horizon/downscaling.

### Design test `scripts/k_design_test.py` (clustering only, nothing downscaled)
- Reproduces the fork's per-group clustering on a 6 M px sample (43 % of the mask), k scaled; terrain read
  read-only from `zarr_workflow/data/runs/2025_era5_600-1000hPa/outputs/ds_param.zarr`.
- Group weights from density coefficients c_g: weight_g = area_g x c_g / sum(area x c) (clusters per km2 in a
  group proportional to c_g), rounded to multiples of 1/1024 so that the fork's `sum == 1` check holds exactly.
- Run inside Docker:
  `docker exec -w /app toposcale-rila-pirin python rila_pirin_zarr/scripts/k_design_test.py zarr_workflow/data/runs/2025_era5_600-1000hPa/outputs/ds_param.zarr zarr_workflow/data/dem/mask.tif rila_pirin_zarr/data/k_design`
  -> `data/k_design/{results.csv, summary.json}`, log `data/logs/k_design_<time>.log`.

### Design test result (round 1: 14 scenarios, 319 s, peak 3.5 GB; round 2: 8 scenarios, 191 s; tag `_round2`)
Terrain of the OLD DEM (25 m bilinear); to be re-checked on FABDEM. Overall values (all bands), pixel minus
centre. T error ~ 6.5 K/km x elev. Dec rad = clear-sky direct-beam daily factor on the slope, RMSE in % of flat
ground, circular centroid aspect (fork's arithmetic aspect in brackets). dist = pixel to centre (ERA5 cell ~21x28 km).
jump = centre-elevation step across 800/1500/2000 m (~20 m without groups).

| scenario | k | elev RMSE m | slope deg | Dec rad % | dist km | jump 800/1500/2000 m |
|---|---|---|---|---|---|---|
| S0 today: no groups, e4 | 500 | 77.8 | 4.4 | 27.5 (51.6) | 13.4 | 29/17/21 |
| S1 no groups, e4 | 1000 | 66.9 | 3.9 | 23.3 (43.9) | 11.4 | 24/17/27 |
| G1 groups c=1/1/1/1, e4 | 1000 | 29.4 | 4.6 | 33.0 | 14.1 | 89/81/97 |
| G2 groups c=0.5/1/1.5/2 (ramp), e4 | 1000 | 30.3 | 4.6 | 32.5 (55.4) | 14.4 | 97/80/90 |
| G3 groups c=0.5/1/2/4 (steep), e4 | 1000 | 31.0 | 4.8 | 32.5 | 14.9 | 104/83/81 |
| G2 ramp, e4 | 500 | 35.2 | 5.1 | 39.1 | 17.1 | 111/92/98 |
| G2 ramp, e4 | 1500 | 27.6 | 4.3 | 29.2 | 13.2 | 86/72/80 |
| G5 ramp + group >=2500 m | 1000 | 29.7 | 4.7 | 33.4 | 14.5 | (2500: 65) |
| ramp, elevation 2 | 1000 | 51.2 | 4.2 | 27.5 | 12.4 | 161/134/145 |
| ramp, elevation 8 | 1000 | 17.5 | 5.1 | 40.4 | 16.8 | 58/45/50 |
| ramp, x/y 0.5 | 1000 | 24.1 | 3.9 | 25.8 | **28.0** | 77/65/70 |
| ramp, aspect 2 | 1000 | 36.4 | 5.3 | 21.4 (41.0) | 19.3 | 111/91/102 |
| ramp, elevation 2 + aspect 2 | 1000 | 61.7 | 4.8 | 19.0 | 15.3 | 188/152/169 |
| ramp, svf 2 | 1000 | 32.7 | 4.6 | 37.4 | 16.0 | 106/85/95 |
| ramp, **elevation 6 + aspect 2** | 1000 | 26.9 | 5.6 | 22.8 (42.6) | 21.5 | 87/65/76 |
| ramp, aspect 2 + slope 2 | 1000 | 39.8 | 3.4 | 19.8 | 22.4 | 124/105/109 |
| steep, aspect 2 | 1000 | 37.8 | 5.3 | 21.7 | 20.4 | 122/91/101 |
| ramp, aspect 2 | 1500 | 33.4 | 5.0 | 19.9 | 16.2 | 107/85/93 |
| ramp, elevation 6 + aspect 2 | 1500 | 24.4 | 5.3 | 21.3 | 18.9 | 80/63/66 |
| no groups, aspect 2 | 1000 | 80.3 | 4.4 | 16.8 | 13.6 | 25/17/20 |

Findings:
- Groups are the big gain for temperature: elevation RMSE 67-78 m -> 25-35 m (~0.45 K -> ~0.2 K), also in the
  lowlands (per-group scaling makes elevation count more inside each group).
- Price of groups with today's weights: direct-SW error up (23-27 % -> 32 %), aspect/slope fit worse, and a
  centre-elevation step of ~80-100 m (~0.5-0.6 K) at each group boundary (Meridionali: 40-60 m).
- Feature weights are a dial between temperature (elevation) and radiation (slope/aspect). Aspect 2 recovers the
  SW accuracy; elevation 6 with aspect 2 keeps the temperature gain too and makes the boundary steps smaller.
- x/y 0.5 looks good on terrain but doubles the pixel-centre distance to 28 km (more than an ERA5 cell): the
  pixel would get ERA5 interpolated at a place ~1 cell away. Not recommended. svf 2 and elevation 8: SW much worse.
- Coefficients: steep (0.5/1/2/4) gains little in the alpine band once aspect 2 is used, and costs the lowlands;
  ramp (0.5/1/1.5/2) is balanced (km2/cluster: <800 m 17.7, 800-1500 ~9, 1500-2000 6.0, >=2000 4.5).
- k 1000 -> 1500: 2-3 m elevation, 1.5 % SW. Diminishing returns.
- The centroid-aspect bug nearly doubles the SW error in every scenario (e.g. 22.8 % -> 42.6 %): fix it.

## User (mid-session): new run on FABDEM, Zarr products only (no GeoTIFFs; analysis to be improved first)
- FABDEM prepared by the user today in `~/fabdem/`: `fabdem_32634_25m.tif` (same grid as the old DEM, bounds
  rounded to mm, no nodata, 53-2919 m), `fabdem_32634_30m.tif` + `mask_30m.tif` (4233 x 3998, 9,686,304 px in mask,
  same band shares as 25 m).
- The fork compares DEM/mask/groups bounds with `==`: the old `mask.tif` (649842.7286942997) would be rejected by
  the FABDEM 25 m grid (649842.729) -> mask and groups rasters must be written on the FABDEM grid itself.

## User decisions (2026-11-05, ~15:30)
- DEM: **FABDEM 30 m** (`fabdem_32634_30m.tif` + `mask_30m.tif`).
- Years: **1985-2025**, ERA5 on all levels the domain needs (14 levels 600-1000 hPa: 600 hPa ~4.2 km is above
  Musala at all times; decision of 2026-10-27) from **NCAR** (d633000 THREDDS serves 1985: checked, HTTP 200);
  EDH only as fallback (5 levels).
- Clustering: **k = 1000, groups <800/800-1500/1500-2000/>=2000 m, coefficients 0.5/1/1.5/2, features
  x1 y1 elevation6 slope1 aspect_cos2 aspect_sin2 svf1**, minibatch k-means, seed 2.
- Centroid aspect fix in our runner (fork unchanged).
- Products: Zarr (hourly `down.zarr`, daily `daily.zarr`); no daily GeoTIFFs. For visual checks one GeoTIFF per
  year: `t_mean_monthly_YYYY.tif`, 12 bands = monthly mean of the daily mean temperature.
- "Run until we get the expected results": run unattended to the end.

## Setup (2026-11-05 15:40-15:45)
- `scripts/prepare_inputs.py` -> `data/dem/`: dem.tif + mask.tif (copies of ~/fabdem, which is not mounted in the
  container), groups.tif (1 <800, 2 800-1500, 3 1500-2000, 4 >=2000 m, on the DEM grid), group_weights.csv:

  | group | band | px in mask | share | coef | weight | clusters (k=1000) |
  |---|---|---|---|---|---|---|
  | 1 | <800 | 2,947,737 | 30.4 % | 0.5 | 0.148438 | 149 |
  | 2 | 800-1500 | 4,236,241 | 43.7 % | 1 | 0.426758 | 427 |
  | 3 | 1500-2000 | 1,586,604 | 16.4 % | 1.5 | 0.240234 | 241 |
  | 4 | >=2000 | 915,722 | 9.5 % | 2 | 0.184570 | 185 |
  (fork rounds up: 1002 clusters; weights are multiples of 1/1024, `sum == 1` checked with pandas)
- `scripts/make_configs.py` -> `config/config_terrain.yml`, `config/years/config_YYYY.yml`,
  `config/era5/config_era5_YYYY.yml` (1985-2025).
- `scripts/prepare_terrain.py`: terrain once in `data/terrain/outputs/` (dem_param; [design re-check on FABDEM];
  clustering; centroid aspect fix, fork value kept as `aspect_arith`; horizon) -> `terrain_summary.json`.
- `scripts/run_year.py` (from zarr_workflow) changed: ds_param.zarr/da_horizon.zarr symlinked from data/terrain,
  df_centroids.pck copied; `compute_dem_param` NOT called (the fork rewrites a cached Zarr ds_param via
  temp+rename, which would replace the shared link); stops if the centroids lack the aspect fix; daily GeoTIFFs
  only with `--daily-geotiff`; yearly monthly-mean GeoTIFF + check (`workflow.export_year_monthly`).
- `scripts/run_era5_lane.sh`: per year import from an older store (zarr_workflow 2020 complete, 2025 Jan-Feb),
  download (2 passes), status, verify -> `ERA5_OK`. Two lanes, 12 NCAR workers each.
- `scripts/run_downscale_all.sh 1985 2025`: waits for terrain + each ERA5_OK, runs years one at a time, `DONE`/`FAILED`.

## Started (13:42-13:44 UTC)
- Terrain: `prepare_terrain.py config/config_terrain.yml --stop-after dem_param` -> `data/logs/terrain_dem_param_20261105_1542.log`
- ERA5 lane A: `run_era5_lane.sh 2020 2025 1985..2004` -> `data/logs/era5_laneA_20261105.log`
- ERA5 lane B: `run_era5_lane.sh 2005..2019 2021..2024` -> `data/logs/era5_laneB_20261105.log`

## 14:13 UTC: FABDEM terrain parameters done (1820 s), design re-checked on FABDEM
- `ds_param.zarr` 97 MB (4233 x 3998 px). Design test on it (`results_fabdem.csv`, 142 s; 9,686,304 px in mask,
  6 M sample = 62 %; note the km2 columns of the script assume 25 m pixels, x 1.44 for 30 m):

  | scenario | elev RMSE m <800/800-1000/1000-1500/1500-2000/2000-2500/>=2500 | Dec rad % circ (fork) |
  |---|---|---|
  | S0 k500 no groups e4 (old setup) | 69.4 / 70.8 / 71.3 / 75.1 / 81.2 / 117.2 | 26.3 (52.0) |
  | G2 k1000 groups e4 | 28.9 / 28.2 / 28.5 / 22.0 / 28.7 / 34.0 | 31.4 (57.1) |
  | groups e4 aspect2 | 37.0 / 35.4 / 34.3 / 25.7 / 34.4 / 41.7 | 21.4 (42.5) |
  | **groups e6 aspect2 (chosen)** | 27.3 / 25.5 / 24.8 / 19.0 / 25.0 / 29.2 | 22.9 (44.1) |
  | groups e6 aspect2 k1500 | 24.4 / 23.2 / 22.9 / 17.4 / 22.9 / 28.0 | 21.2 (41.3) |
- Same ranking as on the old DEM: the chosen setup stands. Clustering + aspect fix + horizon started:
  `prepare_terrain.py config/config_terrain.yml` -> `data/logs/terrain_cluster_horizon_<time>.log`.
- ERA5: 2020 imported from zarr_workflow (lane A now on 2025); lane B on 2005 (February after 30 min).

## 14:32 UTC: terrain complete (`data/terrain/outputs/terrain_summary.json`)
- Clustering 66 s: **1002 clusters, 149 / 427 / 241 / 185** in groups 1-4; 0 clusters spanning two groups; centre
  elevations per group 120-772, 822-1478, 1517-1982, 2026-2833 m (all inside their band).
- Centroid aspect fix: 112 of 1002 centroids moved > 45 deg, **72 > 90 deg** (fork value kept as `aspect_arith`).
- Horizon (36 azimuths) 912 s. Total 985 s.
- (The second call re-saved the cached ds_param in 7 s before clustering, then the fork added the cluster map:
  only in data/terrain, before any year links it.)
- `run_downscale_all.sh 1985 2025` started 14:31 -> `data/logs/downscale_all_20261105.log`; 2020 first (ERA5_OK).

## 2020 run (first year, test of the new runner)
- Terrain linked, centroids loaded (0 s), solar geometry 52 s, horizon sampled 3 s, ERA5 store check 0.3 s.
- 2020-01: 1002 points downscaled in 40 s (0 failed), hourly QA OK (t -16.9..13.6 degC), month 45 s in total.
- SW above 1400 W/m2 in January: 20 values (max 1887), all **west-facing clusters (aspect 270-340 deg) at 15 UTC on
  7-10 Jan** = the known TopoSCALE sunset spike (direct beam from a tiny cos(zenith), projected on a slope facing
  the low sun); also in zarr_workflow's 2020 run. Unrelated to the aspect fix (aspect = aspect_arith for 19 of 20).
  Reported by QA, not a failure. **Known issue for the analysis**: cap or recompute SW at low sun elevation.
- **2020 complete 14:46 UTC: YEAR COMPLETE, ALL CHECKS PASSED**, 768 s (12 months x ~40-50 s downscaling).
  down.zarr 805 MB, daily.zarr 49 MB; `t_mean_monthly_2020.tif` 162 MB, 12 bands, check OK (1000 pixels x 12 bands
  vs daily.zarr: max diff 1e-6 degC; nodata exactly outside the mask), range -5.9..26.6 degC.
- ERA5 speed (first hour): ~450 units/h per lane (2,274 units per year -> ~5 h/year/lane), ~5 % first attempts
  failed (timeouts, URLError, a few HTTP/OSError), all retried. Remaining 39 years on 2 lanes: ~4 days.
  Kept at 2 lanes x 12 workers (NCAR is a free service and slow today; more requests mostly add timeouts).

## 2026-11-05 evening - 11-06 00:10 UTC
- **2025: ERA5 verified 17:59 (Jan-Feb imported, rest NCAR, 4.2 h), downscaled 18:01-18:14: DONE** (all checks).
- 1985 (lane A, from 17:59) and 2005 (lane B): pressure levels fine (NCSS, 430-530 units/h), but **NCAR OPeNDAP
  (single levels) overloaded since ~19 UTC**: "NetCDF: DAP server error", HTTP 504, answers after 13-49 s.
  2005: 2265 of 2274 units after 10 h; `ncar_surf_2005-11_tp` gave up after 6 attempts (retried by the lane's
  second pass). 1985: 2244 written, single-level units of Aug-Dec still pending/retrying.
- **Change: single levels (SURF) from the CDS for every year not started yet** (1986-2004, 2006-2019, 2021-2024):
  `source.surf: cds` (one request per year, ~51 min of CDS run time; the downloader's original design; same ERA5
  values: CDS vs NCAR/EDH differences = one float32 rounding step, zarr_workflow/era5_download/LOG.md).
  Pressure levels stay on NCAR NCSS. 1985/2005/2020/2025 keep NCAR SURF (`NCAR_SURF` in make_configs.py).
  CDS credentials checked from the container (jobs endpoint 200, queue empty). The lanes read each year's config
  when they reach it, so no restart was needed.
- Watchdog now only on year-level failures (lane "NOT complete/verified", downscaling "FAILED"/"will retry").

## 2026-11-06 07:40 UTC: status
- DONE (all checks): **2020, 2025, 2005** (2005 ERA5 verified 01:46, downscaled 01:50-02:03).
- CDS SURF works: 2006 whole-year single levels in one request, 65 min (queue 33 s), written + verified.
- NCAR NCSS (pressure levels) also struggling overnight: 2006 has 1506/2274 units after 6 h, 398 failed attempts,
  12 units gave up (retried by the second pass); hourly throughput 28-428 units.
- 1985: 2269/2274 after 13.7 h; the last NCAR SURF units fail repeatedly (OPeNDAP I/O failure, ~1 unit per 10 min).
  -> **1985 SURF moved to the CDS too** (`NCAR_SURF = {2005, 2020, 2025}`): the lane's second pass requests the
  whole 1985 single-level year from the CDS (overwrites the NCAR SURF values already written: same data).

## 2026-11-06 ~08:00 UTC: FABDEM parameter QA; EDH series added (user: EDH as fallback, faster; whole 1985-2025)
### FABDEM terrain QA (`data/terrain/outputs/`, inside the mask, 9,686,304 px)
- elevation 78-2917 m (median 1114), slope 0-65.5 deg (mean 16.6, p99 38.9), aspect 0-2pi, svf 0.47-1.00
  (mean 0.941): **no NaN** in any parameter. Old DEM: slope mean 14.8, svf 0.953, max 2905 m (FABDEM is steeper
  and keeps the summits, as expected for bare-earth GLO-30). Curvature noise (median |Laplacian|) 2.80 vs 2.79 m.
- Horizon (36 azimuths x full grid): 0-73.5 deg, no NaN. Centroids: 1002, no NaN column, elevation 120-2833 m,
  svf 0.75-1.00. Every masked pixel has a cluster, no pixel outside the mask has one.
### EDH series (separate product, `config/years_edh/`, `data/runs_edh/YYYY`, `data/era5_edh/YYYY`)
- Why separate, not mixed into the NCAR years: EDH's levels within the terrain are 600/700/850/925/1000 only;
  measured vs all levels (zarr_workflow/era5_download/REPORT.md 2.6): alpine (2000-3000 m) **~0.5 K colder** in
  January, 1500-2000 m ~0.27 K. Mixing sources between years would put artificial steps into the 1985-2025 series.
- EDH store (era5-pressure-levels-v0.zarr), checked now: **19 levels** 1000/925/850/700/600/500/400/300/250/200/
  150/100/70/50/30/20/10/5/1 hPa, 1940-01-01 .. 2026-10-15. **User: take all levels EDH has** -> `plevels` = all 19.
  `topo_scale_zarr` interpolates only between the level just above and just below each centroid
  (topo_scale_zarr.py:160-177); every level above 600 hPa lies above 2918 m, so the extra levels cost download
  only and do not change any value.
- `run_downscale_all.sh` got an `edh` variant (run_year.py fetches EDH itself via mp.get_era5, no ERA5_OK). The
  running NCAR loop was idle and was restarted from the edited script (bash reads scripts while running).
- Started: `run_downscale_all.sh 1985 2025 edh` -> `data/logs/downscale_all_edh_20261106.log`; NCAR loop restarted
  (same log as before). NCAR lanes keep running: the 14-level series is completed in parallel.
- **User correction (~08:15): only the EDH levels that are useful for us** -> EDH configs back to the 600-1000 hPa
  request; the fork resolves it to **600/700/850/925/1000 hPa** (checked with `resolve_plevels`; 600 hPa ~4.2 km is
  above the highest centroid, 2833 m, so nothing is extrapolated). Applies from 1986 (the loop reads each year's
  config when it starts the year).
- EDH 1985 was already fetched with all 19 levels (420 s, store 9720 h: the fork fetches whole EDH time chunks;
  the downscaler selects the year). Kept: only the two levels around each centroid enter the calculation, all
  within 600-1000 hPa, so its values are the same as with 5 levels. Its ERA5 store just holds 14 unused levels.

## 2026-11-06 08:05 UTC: session restarted (previous Claude session closed), both series still running
- Nothing was lost: loops and ERA5 lanes run inside the container. State at 08:05: NCAR series DONE 2005/2020/2025;
  1985 second pass (CDS single levels) since 07:53; 2006 plev 1683 parts written. EDH series: 1985 at month 6, ~110 s/month.
- **Results organised in `results/`** (user request): `scripts/update_results.py` links every finished year
  (`results/ncar_14levels/YYYY`, `results/edh_5levels/YYYY`, plus all monthly GeoTIFFs per series in
  `t_mean_monthly/`) and writes `results/STATUS.md` (all years x both series). Runs detached in the container every
  10 min (`docker exec -d`, log `data/logs/update_results.log`), so it survives a closed session.
- Watchers re-armed (finished/failed years in both series; watchdog on year-level failures).
- 08:40: the loop/lane stdout logs (`data/logs/downscale_all_*.log`, `era5_lane*_20261105.log`) stopped at the
  session close (their `docker exec` clients were in the closed session; the processes live on, stdout goes nowhere).
  Not restarted (would kill 1985's running CDS request). Per-year logs and markers are unaffected. Watching now via
  `scripts/watch_markers.sh` (DONE/RETRIED/FAILED/ERA5_OK + a lane that moved on from an unverified year);
  `update_results.py` detects "downscaling" from the run folder.
- **EDH speed with 5 levels: 1986 in 18 min** (08:19-08:37, download + 12 months). 1985 (19 levels) 33 min.
  -> remaining 39 EDH years ~12 h, **EDH series complete ~20:30 UTC today**.
- **NCAR 1985 DONE 09:2x** (ERA5 verified after the CDS single-level pass). First year done in both series.
- **EDH vs NCAR, 1985, daily t_mean per cluster (EDH minus NCAR), by centroid elevation band:**
  | band | n | year | Jan | Jul | days with abs diff > 1 K |
  |---|---:|---:|---:|---:|---:|
  | <800 m | 149 | -0.15 K | -0.26 | -0.10 | 5.4 % |
  | 800-1500 | 427 | -0.01 | -0.00 | -0.04 | 0.0 % |
  | 1500-2000 | 241 | +0.04 | -0.22 | +0.07 | 1.9 % |
  | >=2000 | 185 | +0.16 | -0.34 | +0.31 | 8.6 % |
  Seasonal, opposite in winter/summer in the alpine zone (EDH colder in Jan, warmer in Jul): consistent with the
  January finding (REPORT.md 2.6). Annual means differ little, but seasonal/daily values do -> keep the series apart.
- 09:50: watchdog: **NCAR 2006 not verified** (lane B moved on to 2007). One unit missing: `ncar_plev_2006-05-29_z`, a
  corrupt raw NetCDF that the second pass reused. Moved to `2006/raw_bad/`, re-downloaded (1.6 min), verify OK,
  ERA5_OK written by hand at 09:53 (same as the lane does). Downloader fixed so bad raw files are set aside
  (zarr_workflow/era5_download/LOG.md); applies to every year a lane starts from now on.

## 2026-11-06 17:00 UTC: status, third ERA5 lane prepared (user: yes, after EDH)
- NCAR 7/41 done (1985, 1986, 2005, 2006, 2007, 2020, 2025), EDH 29/41 (1985-2013; ETA ~20:30 UTC).
- NCAR NCSS slower this afternoon: 150-210 parts/h per lane (was 330-400), ~1 failed attempt per 2 written; all retried.
- Memory peak ~15:50 (59/62 GB) when an NCAR and an EDH year downscaled at the same time (~20 workers x 1.3 GB each);
  back to 19 GB used at 17:00. Disappears when the EDH series is finished.
- `scripts/run_era5_queue.sh`: lanes share one queue with an atomic per-year lock (`YYYY.lock`), logs written inside
  the container; needed for a 3rd lane (the fixed lists of run_era5_lane.sh would make two lanes write the same year).
  Switching lanes A/B to it means stopping them (downloads resume); **waiting for the user's OK** (blocked by the
  permission check as interfering with running work).
- `watch_markers.sh`: lane-order check replaced by the ERA5_FAILED marker of the queue script.
- **17:04: user OK ("go ahead") -> lanes switched to the queue.** Old lane scripts (pids 761, 767) and their
  downloaders (1987, 2008) stopped; the downloaders died on SIGTERM (left as zombies, state Z, no writes) before the
  queue lanes started. Queue lanes A and B (`docker exec -d`, logs `data/logs/era5_queue_{A,B}_20261106.log`)
  resumed 1987 (372/2191 units already written) and 2008 (84/2197). Locks `1987.lock`, `2008.lock` show the owners.
  An interrupted unit is redone (not in the manifest), and verify checks every month for missing hours/NaN.
- **Lane C scheduled** inside the container: waits until all 41 EDH years are DONE/FAILED, then runs
  `run_era5_queue.sh C 1985 2025` (log `data/logs/era5_queue_C_20261106.log`). To check after ~2 h of C: total
  parts/h of all lanes vs ~360-420/h with 2 lanes; if NCAR only times out more, stop C (and remove its .lock).

## Decisions of 2026-11-06 (summary; details in the entries above)
| # | decision | why |
|---|---|---|
| 1 | Single-level ERA5 variables from the CDS (one request/year) for all years except 2005/2020/2025 | NCAR OPeNDAP overloaded since 11-05 19 UTC; CDS = same values (one float32 rounding step); 5-7 min/year |
| 2 | EDH as a second, complete 1985-2025 series (`runs_edh/`), not mixed into the NCAR series | user: EDH as fast fallback; EDH has only 600/700/850/925/1000 hPa -> seasonal offsets in the alpine zone |
| 3 | EDH with only the useful levels 600/700/850/925/1000 (1985 has all 19, same values) | user correction; 600 hPa (~4.2 km) is above the highest centroid (2833 m) |
| 4 | NCAR series = final dataset | 1985 comparison: EDH minus NCAR at >=2000 m Jan -0.34 K, Jul +0.31 K, 8.6 % of days > 1 K |
| 5 | `results/` with links per series + auto STATUS.md (every 10 min, in the container) | user: results organised and easy to find |
| 6 | Watch marker files, not loop stdout | loop/lane stdout died with the closed session |
| 7 | Corrupt raw downloads moved to `raw_bad/` and re-downloaded | 2006-05-29 z: corrupt file was reused by every retry |
| 8 | ERA5 lanes on one shared queue with per-year locks; 3rd lane after EDH | user: yes to a 3rd lane; fixed lists would let two lanes write the same year; memory free only after EDH |

## 2026-11-07 10:45 UTC: status; lane C kept
- **EDH series complete: 41/41** (last year ~20:25 UTC 11-06), no FAILED/RETRIED.
- NCAR series **13/41** done (1985-1991, 2005-2008, 2020, 2025); no ERA5_FAILED, no RETRIED/FAILED.
- Lane C started 20:25 UTC when EDH finished. Time per year per lane with 3 lanes: A 1989 5.9 h, B 1990 6.3 h,
  C 1988 6.2 h / 1991 6.1 h, i.e. the same as with 2 lanes -> a third lane adds ~50 % throughput (~1,080 parts/h
  in total vs ~720). **Decision: keep C.**
- In progress: 1992 (A, 1835/2191), 1993 (B, 1621), 1994 (C, 749). 25 more years queued -> ~8-9 years per lane x
  ~6.1 h -> **NCAR series complete ~Mon 2026-11-09 afternoon UTC** if NCAR holds this speed.
- Memory: 11 GB used (only one series downscaling now).

## 2026-11-09 16:34 UTC: both series complete
- **NCAR series (final dataset): 41/41** (last: 2024 at 16:34 UTC). **EDH series: 41/41** (since 11-06 ~20:25 UTC).
  No RETRIED / FAILED / ERA5_FAILED in either series; every NCAR year has ERA5_OK (verify: no missing hours/NaN).
- Corrupt raw NCAR downloads caught by the `raw_bad/` fix (11-06) and re-downloaded automatically: 15 files in
  14 years (1992, 1993, 1994, 1997 x2, 2000, 2002, 2004, 2006, 2008, 2011, 2012, 2015, 2016, 2023); at most two per
  year, all on different days/variables -> isolated transfer errors, not a systematic problem.
- NCAR throughput with 3 queue lanes stayed ~6 h per year per lane until the end (2013-2024 in ~44 h).
- Yearly monthly-mean temperature range (QA, 30 m GeoTIFF): min -13.5 (2003) .. -5.2 degC (2014), max 24.4 (1991)
  .. 29.9 degC (2024); NCAR vs EDH ranges agree within 0.3 K for every year.
- Disk: `data/runs` 42 GB, `data/runs_edh` 42 GB, `data/era5_600-1000hPa` 7.3 GB.
- Queue lanes A/B/C ended by themselves (queue empty). Still running: `update_results.py --every 600` (harmless;
  stop it or leave it, results/ is final). Host watchdog stopped.
