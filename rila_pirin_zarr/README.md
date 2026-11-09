# rila_pirin_zarr: clean end-to-end Rila-Pirin run on FABDEM (1985-2025)

ERA5 (NCAR, 14 levels 600-1000 hPa) -> TopoSUB (1002 clusters in 4 elevation groups) -> TopoSCALE
(`topo_scale_zarr`), one hourly Zarr store per year, plus daily statistics. Decisions and results with dates:
`LOG.md`. Runs inside the Docker container (`docker compose up -d` in the repo, then `cd /app`).

## Where are the results? -> `results/`

**`results/STATUS.md`**: every year x both series (done / downscaling / downloading / queued), refreshed every
10 min. Finished years only (all months + checks passed):

```
results/ncar_14levels/YYYY/              final dataset (ERA5 14 levels 600-1000 hPa)
results/ncar_14levels/t_mean_monthly/    t_mean_monthly_YYYY.tif, all years in one folder (QGIS)
results/edh_5levels/YYYY/                fast series (ERA5 Earth Data Hub, 600/700/850/925/1000 hPa)
results/edh_5levels/t_mean_monthly/
```
Each `YYYY/` holds `downscaled/down.zarr` (hourly), `downscaled/daily.zarr`, `geotiff/`, `year_summary.json`.
They are links into `data/runs/` and `data/runs_edh/` (nothing copied). Rebuild by hand:
`python rila_pirin_zarr/scripts/update_results.py`. Don't mix the two series in one time series (~0.5 K offset
in the alpine zone).

## Setup (decided 2026-11-05, see LOG.md for the tests behind each choice)

| | |
|---|---|
| DEM | FABDEM V1-2, 30 m, EPSG:32634 (4233 x 3998 px), rectangle mask 9,686,304 px |
| clusters | k = 1000 in groups <800 / 800-1500 / 1500-2000 / >=2000 m, density coefficients 0.5 / 1 / 1.5 / 2 -> 149 / 427 / 241 / 185 |
| features | x 1, y 1, elevation 6, slope 1, aspect_cos 2, aspect_sin 2, svf 1; minibatch k-means, seed 2 |
| centroid aspect | circular mean atan2(sin, cos) (the fork's arithmetic mean flips north-facing clusters; kept as `aspect_arith`) |
| ERA5 | pressure levels: NCAR d633000 NCSS, 600-1000 hPa (14 levels); 7 single-level variables: CDS, one request per year (NCAR OPeNDAP for 2005/2020/2025, overloaded since 2026-11-05), 1985-2025 |
| EDH series | same terrain and clusters, ERA5 from Earth Data Hub, 600/700/850/925/1000 hPa: fast, separate product (LOG.md 2026-11-06) |
| products | `down.zarr` (hourly), `daily.zarr` (daily stats), `t_mean_monthly_YYYY.tif` (12 bands, viewing only) |

## Folder

```
config/config_terrain.yml           terrain + clusters + horizon (once)
config/years/config_YYYY.yml        one year of downscaling
config/era5/config_era5_YYYY.yml    one year of ERA5 (zarr_workflow/era5_download/era5_download.py)
scripts/make_configs.py             writes all configs above
scripts/prepare_inputs.py           DEM, mask, groups.tif, group_weights.csv -> data/dem/
scripts/prepare_terrain.py          ds_param, clusters, aspect fix, horizon -> data/terrain/outputs/
scripts/k_design_test.py            clustering design test (k, groups, coefficients, feature weights)
scripts/run_year.py, workflow.py    one year, month by month (from zarr_workflow, changed: see docstring)
scripts/run_era5_queue.sh           ERA5 lanes sharing one queue of years, lock per year (used since 2026-11-06 17:04)
scripts/run_era5_lane.sh            older: ERA5 for a fixed list of years (lanes A/B until 2026-11-06 17:04)
scripts/run_downscale_all.sh        every year with ERA5_OK -> data/runs/YYYY (DONE / FAILED); 'edh' -> data/runs_edh
scripts/update_results.py           results/ links + STATUS.md (runs every 10 min in the container)
scripts/watch_markers.sh            host: events from DONE/RETRIED/FAILED/ERA5_OK/ERA5_FAILED markers
results/                            finished years of both series + STATUS.md (see top)
data/  (git-ignored)
  dem/                              dem.tif, mask.tif, groups.tif, group_weights.csv
  terrain/outputs/                  ds_param.zarr, da_horizon.zarr, df_centroids.pck, terrain_summary.json
  era5_600-1000hPa/YYYY/            ERA5.zarr, state/, logs/, ERA5_OK (ERA5_FAILED, raw_bad/ if a year/unit failed)
  era5_600-1000hPa/YYYY.lock/       the queue lane working on that year (owner file)
  runs/YYYY/outputs/                downscaled/{down.zarr, daily.zarr}, geotiff/, months/*.json, year_summary.json
  runs/YYYY/logs/                   run_year_<time>.log
  era5_edh/YYYY/, runs_edh/YYYY/    the same for the EDH series
  k_design/                         design test results
  logs/                             terrain, design tests, ERA5 lanes (era5_queue_<lane>_<date>.log), loops, update_results
```

## Commands (from /app in the container)

```bash
python rila_pirin_zarr/scripts/make_configs.py
python rila_pirin_zarr/scripts/prepare_inputs.py <fabdem_32634_30m.tif> <mask_30m.tif> rila_pirin_zarr/data/dem
python rila_pirin_zarr/scripts/prepare_terrain.py rila_pirin_zarr/config/config_terrain.yml
bash rila_pirin_zarr/scripts/run_era5_queue.sh A 1985 2025 >> rila_pirin_zarr/data/logs/era5_queue_A_<date>.log 2>&1
bash rila_pirin_zarr/scripts/run_era5_queue.sh B 1985 2025 >> ...   # as many lanes as wanted (C since EDH finished)
bash rila_pirin_zarr/scripts/run_downscale_all.sh 1985 2025          # NCAR series (waits for ERA5_OK)
bash rila_pirin_zarr/scripts/run_downscale_all.sh 1985 2025 edh      # EDH series (fetches EDH itself)
python rila_pirin_zarr/scripts/update_results.py --every 600         # results/ + STATUS.md
```
Every step resumes: rerun the same command after a crash. Start long jobs with `docker exec -d ... bash -c '... >> log 2>&1'`
so they and their logs do not depend on the terminal/session that started them. After killing queue lanes, remove
stale `data/era5_600-1000hPa/*.lock` before starting them again. Check what runs (no `ps` in the container):
`docker exec toposcale-rila-pirin bash -c 'for p in /proc/[0-9]*; do tr "\0" " " < $p/cmdline; echo; done' | grep -E "run_|era5_download"`.

## Known issues (for the analysis)
- SW sunset spike: a few hourly SW values > 1400 W/m2 on west-facing clusters at the last hour before sunset
  (TopoSCALE direct beam at tiny cos(zenith)). Reported by QA, not filtered.
- Group boundaries: the cluster centre elevation jumps across 800 / 1500 / 2000 m (~65-90 m in the design test,
  ~0.4-0.6 K). A per-pixel lapse-rate adjustment when mapping would remove it.
