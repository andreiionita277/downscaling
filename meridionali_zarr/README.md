# Zarr workflow: Meridional Carpathians (Southern Carpathians) downscaling

Adaptation of `../zarr_workflow/` (Rila-Pirin, Bulgaria) to the Romanian Southern Carpathians. Same chain:
ERA5 from the Earth Data Hub → TopoSUB clustering → `topo_scale_zarr`, processed **month by month into one
yearly Zarr store**, then daily statistics and **daily GeoTIFFs**. See `../zarr_workflow/README.md` for the
step-by-step description and output table; everything there applies here.

## Folder

```
meridionali_zarr/
  config/config_bucegi_2017.yml       PILOT: Bucegi subdomain, 2017 (same year/grid/mask as ~/Bucegi old run)
  config/config_meridionali_2024.yml  full domain: FABDEM_Meridionali2 (8787 x 4441 px), 2024
  config/config_meridionali_2025.yml  MAIN RUN: new rectangle AOI + FABDEM from C:\Projects\NewDownscale, 2025
                                      (how the DEM/mask were prepared: see the header of the config)
  scripts/run_year.py                 copy of zarr_workflow's runner (only docstrings changed)
  scripts/workflow.py                 copy of zarr_workflow's helpers (+ SW QA bounds fix, see below)
  scripts/k_analysis.py, recheck_qa.py  copies, unchanged (their usage lines still say zarr_workflow/)
  data/                               NOT in git
    dem/bucegi/{dem,mask}.tif         symlinks -> ~/Bucegi/inputs/dem/FABDEM_Bucegi.tif, Bucegi_Boolean.tif
    dem/meridionali/{dem,mask}.tif    symlinks -> ~/Meridionali/inputs/dem/FABDEM_Meridionali2.tif, Meridionali_Boolean.tif
    dem/meridionali_2025/             dem.tif (30 m, EPSG:3844), mask.tif, AOI_Meridionali.shp (real files)
    era5_<domain>/ERA5.zarr           created by get_era5
    <domain>_<year>/outputs/          everything the run computes
    logs/
```

## DEM choice: FABDEM (not ARCDEM)

`ARCDEM_Bucegi.tif` is **not** ArcticDEM: its metadata says it was extracted from `arc_stn_30m_EU_DEM.tif`,
i.e. **EU-DEM** (25 m, SRTM+ASTER blend) resampled to 30 m Stereo70 in ArcGIS. `FABDEM_Bucegi.tif` is
FABDEM (Copernicus GLO-30 with forests and buildings removed = bare-earth). Both are on exactly the same grid
(575 × 1132 px, 30 m, Stereo70, no nodata), so the comparison is pixel-to-pixel:

| | EU-DEM ("ARCDEM") | FABDEM |
|---|---|---|
| max elevation | 2484 m | 2504 m (Omu peak is 2505 m) |
| 3×3 roughness (noise), in mask | 3.03 m | 1.68 m |
| Laplacian (curvature noise), in mask | 12.5 | 5.6 |
| local pits (3×3 minima), in mask | 3916 | 330 |
| mean slope, in mask | 20.4° | 22.1° |
| EU-DEM minus FABDEM, 900–1300 m (forest belt) | +10.7 m | |
| EU-DEM minus FABDEM, >2100 m (alpine) | −3.3 m (summits flattened) | |

FABDEM is better for TopoPyScale. It has about half the high-frequency noise and 12× fewer spurious pits,
both of which feed straight into the slope, aspect and sky-view factor clustering features. Its relief is
sharper where it is real (summits, steeper mean slope), and the forest canopy is removed, so the elevation
used for the lapse-rate adjustment is the ground and not the treetops. It is also the DEM of the full
Meridionali domain, so the pilot and the full run are consistent. FABDEM's licence is CC BY-NC-SA 4.0:
fine for research, cite Hawker et al. (2022).

## What changed vs. your old NetCDF workflow (~/Bucegi)

| | old (`config_2017_Bucegi.yml` + 6-line script) | zarr workflow |
|---|---|---|
| TopoPyScale | PyPI 0.3.3 (conda env `downscale`) | Rila-Pirin **fork** (fixes + `topo_scale_zarr`, `topo_daily`, EDH fetch, Zarr caches) |
| ERA5 | CDS, monthly `PLEV_/SURF_YYYYMM.nc` in `inputs/climate` | Earth Data Hub → one `ERA5.zarr` (a year in minutes, but only levels 600/700/850/925/1000) |
| caches | `ds_param.nc`, `ds_solar.nc`, `da_horizon.nc` | same, as `.zarr` (lazy, reusable) |
| downscaling | `topo_scale` → one `down_pt_*.nc` per cluster, whole period at once | `topo_scale_zarr` → one hourly `down.zarr`, filled **month by month**, resumable per cluster |
| products | none (you mapped by hand, e.g. `air_temp_map_20170101_mean.tif`) | `daily.zarr` + daily GeoTIFFs (one file per month, one band per day) + QA/check JSON per month |
| driver | call Topoclass steps in a script | `scripts/run_year.py` (same steps + loop, QA, resume) |
| config keys | `extent` set by hand, `CPU_cores` under project, `timestep: 1H`, `dem_resol`, `pt_sampling_method` | `extent` empty (from DEM), `parallelization:` block, `timestep: 1h`, `solar_position_method`, `outputs.file.zarr_store*` |

Leftover in the old configs: `extent: [45.33, 22.36, 45.12, 23.14]` is the **Retezat** box, copied into
both Bucegi (25.3–25.6 E) and Meridionali (22.2–25.6 E). It only sets the ERA5 download area. The NetCDFs
actually on disk cover 21.75–27.75 E / 44.25–46.75 N, so they were not affected. The new configs leave
`extent` empty, so it is derived from the DEM. Also note: `~/Bucegi/outputs/downscaled/` and
`~/Meridionali/outputs/downscaled/` are empty, so there are no old downscaled results on disk to compare against.

## Before the first run (not done yet)

1. **Environment.** This machine has neither Docker nor the fork installed. Your `downscale` env has PyPI
   0.3.3, which lacks `topo_daily` and the Zarr caches. Make a new env (Python 3.13) and install the fork in
   editable mode, following `../SETUP.md`:
   `git clone https://github.com/Nandhni03/TopoPyScale_Bulgaria_Rila_Pirin_Mountains` →
   `pip install -r ../docker/requirements.lock.txt` → `pip install -e <fork>`.
2. **EDH key.** Put it in `~/.netrc` (`machine data.earthdatahub.destine.eu login edh password <key>`) or
   `EDH_TOKEN`. Without it, set `data_repository: cds`. The CDS NetCDFs then have to be converted to `ERA5.zarr`
   (`fetch_era5.convert_netcdf_stack_to_zarr`). That path has not been tested in this workflow.
3. **Pressure levels.** EDH gives 600/700/850/925/1000 hPa. The highest Bucegi/Făgăraș peaks (~2500 m)
   are near 750 hPa, inside that range.

## Run

```bash
cd ~/TopoPyScale-repo-github/downscaling
python meridionali_zarr/scripts/run_year.py meridionali_zarr/config/config_bucegi_2017.yml \
       > meridionali_zarr/data/logs/bucegi_2017_$(date +%Y%m%d_%H%M).log 2>&1
```

## Memory warning for the full domain

The Rila-Pirin DEM (~24 M px) gave a ~7 GB horizon and ~20 GB peak RSS. FABDEM_Meridionali2 is 39 M px,
so expect a ~11 GB horizon and ~30+ GB peak. This machine has 23 GB RAM (+32 GB swap). Run the Bucegi
pilot first and measure its peak (`/usr/bin/time -v`). For the full domain, use the faculty machine or
split the domain into tiles (e.g. Retezat–Godeanu / Parâng / Făgăraș / Bucegi), each with its own config.

## Fix vs. zarr_workflow

`workflow.py` `RANGES`: in the original, the `SW`, `SW_direct` and `SW_diffuse` bounds were inside a comment,
so shortwave was never range-checked (`n_outside_bounds` always 0). Fixed here; the original in
`../zarr_workflow/` still has the bug.
