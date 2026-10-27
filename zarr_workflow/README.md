# Zarr workflow: Rila–Pirin downscaling, month by month

TopoPyScale (TopoSUB clustering + TopoSCALE downscaling) for the Rila and Pirin mountains, with ERA5 from
the Earth Data Hub, every intermediate file and result in **Zarr**, processed **one month at a time into
one yearly store**, and published as **daily GeoTIFF maps**. The decision log (what was chosen and why)
is the fork's `PROGRESS.md`; the design overview is `bulgaria_rila_pirin/DESIGN.md`.

## Folder

```
zarr_workflow/
  config/config_2025.yml      all settings of the 2025 run (each one commented with its reason)
  config/config_2025_era5_600-1000hPa.yml  the same run with ERA5 on all 14 levels 600-1000 hPa (CDS/NCAR)
  era5_download/              downloads that ERA5 store (era5_download.py + config_era5_2025.yml;
                              LOG.md/REPORT.md: CDS limit, NCAR workaround, timings, comparisons)
  scripts/run_year.py         runs the whole chain for one year, month by month (resumable)
  scripts/workflow.py         building blocks: QA, daily store, GeoTIFF export, GeoTIFF checks
  scripts/run_full_year.sh    ERA5 download + verify + run_year for one year (600-1000 hPa configs)
  notebooks/view_results.ipynb  interactive viewer (maps per day, time series, monthly means)
  notebooks/make_viewer_notebook.py  generates the notebook (its code, reviewable in git)
  data/                       NOT in git (.gitignore)
    dem/                      symlinks: 25 m DEM, mask.tif, AOI rectangle, station list
    era5/                     ERA5.zarr (+ edh_blocks/, the resumable download pieces)  [EDH, 5 levels]
    era5_600-1000hPa/YYYY/    ERA5.zarr from era5_download (14 levels) + state/ (manifest, month checks), one folder per year
    runs/YYYY_era5_600-1000hPa/outputs/   outputs of the 600-1000 hPa runs (same layout as outputs/)
    outputs/                  everything the run computes (below)
    logs/                     one log file per run
```

`data/outputs/` after a run:

| path | what | format |
|---|---|---|
| `ds_param.zarr` | terrain parameters (elevation, slope, aspect, svf) + cluster map (`point_name` per pixel) | Zarr |
| `df_centroids.pck` | the 500 clusters: centre terrain values, lat/lon, horizon angles | pickle (authors' format) |
| `ds_solar.zarr` | sun position per cluster and hour, whole year | Zarr |
| `da_horizon.zarr` | horizon angles per pixel and azimuth (~7 GB) | Zarr |
| `downscaled/down.zarr` | **hourly** downscaled climate, 500 clusters × 8760 h | Zarr |
| `downscaled/down.zarr.progress/<month>/` | which clusters are finished for each month (resume markers) | empty files |
| `downscaled/daily.zarr` | **daily** statistics (UTC days), 500 clusters × 365 days | Zarr |
| `geotiff/t_{mean,min,max}_daily_YYYY-MM.tif` | daily maps at 25 m (EPSG:32634), one file per month, **one band per day** | COG GeoTIFF |
| `months/YYYY-MM.json` | per month: status, timings, QA of hourly values, GeoTIFF checks | JSON |
| `year_summary.json`, `timings.json` | run summary | JSON |

## Run

Inside the container (`docker compose up -d` in the repo root first, if it isn't running):

```bash
docker exec -it toposcale-rila-pirin bash
cd /app
python zarr_workflow/scripts/run_year.py zarr_workflow/config/config_2025.yml \
       > zarr_workflow/data/logs/run_$(date +%Y%m%d_%H%M).log 2>&1
```

- **Resumable at every level:** terrain/cluster/solar/horizon caches and ERA5 blocks are reused; a
  finished month (`months/YYYY-MM.json` with status `ok`) is skipped; inside a month, finished clusters are
  skipped. After a crash, run the same command again.
- Only some months: `--months 1 2 3`. Rewrite the daily store/GeoTIFFs of done months: `--redo-products`.
- Other variable for the GeoTIFFs: `--var tp --stats sum`.
- To redo a step from scratch, delete its output (e.g. `df_centroids.pck` and `ds_param.zarr` to recluster).

### Run with ERA5 on 600-1000 hPa (all 14 levels) instead of EDH's 5

```bash
# 1. ERA5 store (resumable; pressure levels from NCAR THREDDS, single levels from the CDS)
python zarr_workflow/era5_download/era5_download.py zarr_workflow/era5_download/config_era5_2025.yml download
python zarr_workflow/era5_download/era5_download.py zarr_workflow/era5_download/config_era5_2025.yml status
python zarr_workflow/era5_download/era5_download.py zarr_workflow/era5_download/config_era5_2025.yml verify --spot-checks 5
# 2. the workflow on it (get_era5 only checks the store: data_repository: local)
python zarr_workflow/scripts/run_year.py zarr_workflow/config/config_2025_era5_600-1000hPa.yml \
       > zarr_workflow/data/runs/2025_era5_600-1000hPa/logs/run_$(date +%Y%m%d_%H%M).log 2>&1
```
Its terrain/cluster/solar/horizon caches are copies of the EDH run's, so both runs use the same 500 clusters.

Whole chain for one year in one command (ERA5 download -> status/verify -> run_year), e.g. 2020, entirely from NCAR
(`config_era5_2020.yml` + `config_2020_era5_600-1000hPa.yml`; ds_solar is computed for that year, the DEM-only caches
ds_param/df_centroids/da_horizon are copied into the run's outputs/ first):
```bash
bash zarr_workflow/scripts/run_full_year.sh 2020 > zarr_workflow/data/runs/2020_era5_600-1000hPa/logs/full_year_2020.out 2>&1
```

## What happens, step by step

Once for the year:
1. `compute_dem_param`: slope, aspect, sky-view factor from the DEM → `ds_param.zarr`
2. `extract_topo_param`: TopoSUB k-means on the pixels inside `mask.tif` (k = 500; features x, y,
   elevation ×4, slope, aspect cos/sin, svf) → `df_centroids.pck`, cluster map into `ds_param.zarr`
3. `compute_solar_geometry`: sun position per cluster, every hour of the year → `ds_solar.zarr`
4. `compute_horizon`: horizon angles → `da_horizon.zarr`, sampled per cluster into `df_centroids`
5. `get_era5`: ERA5 hourly from the Earth Data Hub (levels 600/700/850/925/1000 hPa + surface) → `ERA5.zarr`;
   with `data_repository: local` only a check of the store prepared by `era5_download` (all hours, no NaN, levels)

For each month:

6. `downscale_climate` (topo_scale_zarr): every cluster, every hour of the month → its slice of `down.zarr`
7. QA: no missing values, physical bounds (SW can exceed its bound at sunrise/sunset: a known TopoSCALE
   sensitivity, counted and reported, not changed)
8. daily statistics (`topo_daily`, UTC days): t mean/min/max, tp sum, SW/LW mean, wind, humidity → `daily.zarr`
9. daily GeoTIFFs: each pixel gets its cluster's value (as TopoPyScale's `map_variable`), K → °C
10. checks: grid = DEM, nodata = outside mask, 1000 random pixels = their cluster's value, band 1 = the
    authors' mapping expression → `months/YYYY-MM.json`

## Look at the results

- **Notebook** (on the host, no Docker needed; uses the separate viewer environment `venv-viewer` next to
  `venv`):
  ```bash
  cd ~/nandhni/downscaling-topopyscale-project/repo-github/rila-pirin-downscaling/zarr_workflow
  ../../../venv-viewer/bin/jupyter lab notebooks/view_results.ipynb
  ```
  Day slider + variable menu for maps, time series at each station's cluster, monthly means, temperature
  vs elevation, and a GeoTIFF-vs-Zarr cross-check.
- **QGIS**: open a GeoTIFF from `data/outputs/geotiff/` (from Windows: `\\wsl$\<distro>\home\icam_labs\...`).
  Each band is one day (the band name is the date): *Properties → Symbology → Band*.

## Python

```python
import xarray as xr
hourly = xr.open_zarr('data/outputs/downscaled/down.zarr').swap_dims(point_ind='point_name')
daily  = xr.open_zarr('data/outputs/downscaled/daily.zarr')
daily.t_mean.sel(point_name='042', time='2025-07-15') - 273.15     # one cluster, one day, degC
```
