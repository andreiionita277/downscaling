# REPORT: ERA5 on all 37 pressure levels for Rila-Pirin 2025, best options and findings

Night of 2026-10-26/27. The full chronological record, with every number, error and fix, is `LOG.md` in this folder.
**Nothing beyond the tests was downloaded.** The full 2025 download waits for your go (as the brief asked).

## 0. Update 2026-10-27 (afternoon): final setup = NCAR for everything, 600-1000 hPa; 2020 tested end to end

Decisions taken after the night report (sections 1-6 below are the original 37-level night report, kept as written):
- **Levels: the 14 ERA5 levels 600-1000 hPa** (not 37): bit-identical downscaling to 37 levels for January 2025
  (600 hPa is >= 4,090 m, the highest cluster is at 2,554 m). Tool renamed `era5_37.py` -> `era5_download.py`.
- **Source: NCAR THREDDS for pressure AND single levels** (`source.plev: ncar`, `source.surf: ncar`). NCAR has all 7
  single-level variables (2t, 2d, sp from `e5.oper.an.sfc`; ssrd, strd, lsp+cp=tp from `e5.oper.fc.sfc.accumu` via
  OPeNDAP; z from `e5.oper.invariant`), equal to the CDS within GRIB packing (LOG.md). No CDS account or queue needed.

**2020 full workflow test** (`scripts/run_full_year.sh 2020`, 2026-10-27 11:48-15:30 local):

| step | time | result |
|---|---|---|
| ERA5 download, NCAR only (2,280 units, 16 workers) | 192.8 min | 0 failed; 76 transient errors all fixed by retries |
| status + verify | 4 s | all 12 months OK: every hour (8,784), 14 levels, no NaN |
| run_year.py (TopoSUB/TopoSCALE, 500 clusters, month by month) | 29.5 min | **YEAR COMPLETE, ALL CHECKS PASSED** |

Outputs in `zarr_workflow/data/runs/2020_era5_600-1000hPa/outputs/`:
- `downscaled/down.zarr` (403 MB): hourly, 500 clusters x 8,784 h, 13 variables, no NaN.
- `downscaled/daily.zarr` (27 MB): 500 x 366 days, 18 daily statistics.
- `geotiff/`: 36 COGs `t_{mean,min,max}_daily_2020-MM.tif` (25 m, EPSG:32634, one band per day; 19 GB in total),
  each checked against the Zarr (max |diff| 4.8e-7 degC).
- SW: 46 hourly values > 1,400 W/m2 (max 2,026), at 14 clusters, 15-17 UTC only: the known TopoSCALE sunset spike,
  reported by the QA and not changed (as in the EDH runs).

Speed: NCAR was busy all day (single request ~100 s instead of 20-45 s at night): ~3.2 h per year download. A
night run should be ~2-2.5 h. Plus ~30 min downscaling per year.

## 1. Recommendation

**Pressure levels from NCAR's THREDDS server, single levels from the CDS.**
In the config: `source.plev: ncar`, `source.surf: cds`. Both paths are implemented and tested in `era5_37.py`.

| | CDS only (`plev: cds`) | **NCAR PLEV + CDS SURF (`plev: ncar`)** |
|---|---|---|
| requests for 2025 | 36 PLEV + 1 SURF | 2,190 small NCAR requests + 1 SURF |
| measured speed | 43-66 min per request, **one at a time** | 168 requests in 12 min (16 parallel) |
| 2025, estimated wall time | **~30-35 h** | **~2.6 h** PLEV (+ ~1 h SURF, already done) |
| data | ERA5 (reference) | the same ERA5 values: max diff 1 float32 rounding step |
| account / queue | CDS account, CDS queue | none / none |

SURF for all of 2025 is **already in the store** (one CDS request made during the January test), so the
remaining work is the PLEV of February-December: about **2.4 h with NCAR**, or about **30 h with the CDS**.

## 2. Findings

### 2.1 The CDS request limit (measured exactly)
| dataset | limit | how it was measured |
|---|---:|---|
| pressure levels, GRIB | **60,000 items** | real submissions, largest first: 165,168, 82,584 and **60,016 rejected** (HTTP 403 "cost limits exceeded / Your request is too large"); **60,000 accepted**, 55,056 accepted (accepted jobs deleted at once) |
| single levels, GRIB | 121,000 items | the CDS's own cost estimate |
| NetCDF | same limits, but **every item costs 6x** | 1 var x 37 levels x 1 month = 27,528 items in GRIB, 165,168 in NetCDF -> doesn't fit |

- 1 item = 1 variable x 1 level x 1 hour. **The area doesn't reduce the cost**; impossible dates (31 Feb) aren't counted.
- The check happens at submission (under 1 s), so a too-large request is never queued.
- With 37 levels, the largest monthly request is **2 variables x 1 month** (31 days: 55,056 items).
  -> 3 requests per month, 36 for the year. All 7 surface variables for the whole year fit in **one** request (61,320 items).
- The cost of a request can be checked without submitting it: `ecmwf.datastores.Client.estimate_costs(dataset, request)`.

### 2.2 CDS speed: one job at a time per user
- Every queued job says: *"The maximum number of per-user requests that access the CDS-MARS data is 1"*.
  More workers do NOT make it faster; they only keep the next job queued.
- January 2025 test, 4 jobs submitted together at 20:23:
  | job | waited | ran |
  |---|---:|---:|
  | PLEV u-v | 46 s | 46 min |
  | PLEV q-r | 47 min | 66 min |
  | PLEV z-t | 1 h 54 min | 43 min |
  | SURF all 2025 | 2 h 37 min | 51 min |
  **Total: 3 h 28 min for one month (+ the year's SURF).** A 1-item request takes ~40-55 s.
- Extrapolation: 36 PLEV requests x ~52 min = **~31 h** for 2025, longer when the CDS is busy.

### 2.3 NCAR ERA5 on AWS (`s3://nsf-ncar-era5`): byte-range reading is not possible
- One NetCDF-4 file per variable per day (1.35 GB). HDF5 chunks **(1 hour, 37 levels, 721, 1440)** = the whole globe
  per hour (~53 MB compressed, gzip 1). Any read of our 10 x 11 box fetches whole global chunks; Kerchunk/VirtualiZarr
  can't avoid that. Option A from the brief is **not viable**.
- Measured S3 -> this machine: ~16 MB/s (8-24 parallel ranges) -> 2025 would mean ~2.8 TB, ~48 h.

### 2.4 NCAR THREDDS NetcdfSubset: the workaround that works
- NCAR serves the same files (dataset d633000) through THREDDS with **server-side subsetting** (NCSS):
  `https://thredds.rda.ucar.edu/thredds/ncss/grid/files/g/d633000/e5.oper.an.pl/YYYYMM/<file>?var=T&north=43&south=40.75&west=22.25&east=24.75&time_start=...&time_end=...&accept=netcdf`
- The server reads the 1.35 GB file and returns only our box (394 kB per variable-day, exactly our grid).
  Anonymous, no account, **no queue**. 20-45 s per request; scales linearly up to at least 16 parallel requests.
- Measured with the real tool: January 186 requests in 31 min (8 workers, a 900 s timeout cost 15 min per hung request);
  February **168 requests in 12 min** (16 workers, 180 s timeout). 2025 = 2,190 requests -> **~2.6 h**.
- Glitches, all fixed by the automatic retries: ~1-2 % of requests got HTTP 404 on files that exist, or hung until the timeout.
- Only `accept=netcdf` works (NetCDF-4 is refused for grid subsets). NCAR has all of 2025 (and 2026 up to at least June).
- NCAR's single-level fields are split into analysis files and forecast-accumulation files (tp = lsp + cp), so SURF is
  simpler from the CDS: the whole year in one request.

### 2.5 Is the data the same? (January 2025, every hour, all 37 levels)
| comparison | result |
|---|---|
| store (CDS) vs **3 independent single CDS requests** (random var/level/hour) | **bit-identical** (diff 0) |
| CDS vs NCAR, z t u v q r | max diffs 0.016 m2/s2, 0.00012 K, 1.5e-5 m/s, 0, 3.7e-9 kg/kg, 1.5e-5 %: **one float32 rounding step**, 40-100 % of values bit-identical |
| CDS vs EDH (5 shared levels) | t max 0.125 K (mean 0.06), z max 16 m2/s2, sp max 36 Pa: **EDH stores ERA5 rounded** (quantized), small but not bit-equal |

### 2.6 What the 37 levels change in the downscaling (January, all 500 clusters)
`topo_scale_zarr` reads the new store **without any code change** (it only requires ascending levels): 500 clusters
x 744 h in 18 s, 0 NaN. Compared with the existing EDH (5-level) run, same clusters and settings:

| elevation band | mean t difference (37 levels minus 5 levels) |
|---|---:|
| 0-1000 m | +0.12 K |
| 1000-1500 m | -0.06 K |
| 1500-2000 m | +0.27 K |
| **2000-3000 m** | **+0.51 K** |

9.6 % of all cluster-hours differ by more than 1 K (99th percentile 2.7 K, max 4.9 K); LW +0.4 W/m2 on average,
pressure -11 Pa. This confirms the earlier finding in DESIGN.md (EDH's 5 levels: high terrain ~0.5 K too cold in January)
and is the argument for the 37 levels in the thesis.

### 2.7 Other points
- ARCO-ERA5 (Google): not used (global chunks, brief + earlier session).
- EDH has 5 levels in our current store (600/700/850/925/1000), not 19 as the brief says.
- Store size: ~40 MB per month of PLEV + 4 MB SURF per year -> **~0.5 GB for 2025**. Disk is not a concern.

## 3. What was built (`zarr_workflow/era5_37levels/`)
| file | what |
|---|---|
| `era5_37.py` | the tool: `download`, `status`, `verify` |
| `config_era5_37_2025.yml` | the one config: box, period, levels, variables, sources, workers, retries, paths |
| `config_era5_37_2025_ncar_test.yml` | test variant (NCAR PLEV into the separate store `data/era5_37/ncar_test/`) |
| `probe_*.py` | the probes behind sections 2.1-2.4 |
| `compare_sources.py`, `test_downscale_jan.py` | sections 2.5 and 2.6 |
| `LOG.md` | everything in order, incl. the 9 bugs found and fixed during testing |

The tool:
- **Store** `zarr_workflow/data/era5_37/ERA5.zarr`, same layout as the EDH store `topo_scale_zarr` already reads:
  time (8760 h of 2025), level ascending 1..1000 hPa, latitude 43.0..40.75 descending, longitude 22.25..24.75,
  13 variables float32, Blosc lz4. Chunks (744 h, 37, 10, 11) = ~1 month x all levels x the whole box. Pre-allocated once.
- **Resumable**: `state/manifest.jsonl` records every unit; written units are skipped; a raw file already downloaded is reused.
- **Robust**: retries with exponential backoff; a unit that keeps failing is recorded as failed and the run goes on;
  "too large" is never retried; CDS jobs stuck for 24 h are deleted and resubmitted.
- **One writer**: workers only download; the main process converts, checks, writes (`to_zarr(region=...)`), reads back,
  compares, and only then deletes the raw file.
- **Validation** after every complete month (`state/months/YYYY-MM.json`): 744/720/696/672 hours, 37 levels,
  0 NaN, no NaN-only hour or level. `verify` adds the EDH comparison and `--spot-checks N` independent CDS requests.

## 4. Current state of the data
`status` output:
```
month     written  failed  missing  validation
2025-01         4       0        0  OK
2025-02..12     1       0        3  -        (SURF written for the whole year, PLEV missing)
```
- January 2025: complete, validated, verified (3 spot checks identical), downscaled successfully.
- SURF: complete for all of 2025.
- Test store `data/era5_37/ncar_test/ERA5.zarr`: NCAR PLEV for January + February (used for the comparison only).

## 5. To finish 2025 (needs your OK)
Inside the container (`docker compose up -d`, then `docker exec -it toposcale-rila-pirin bash`, `cd /app`):
```bash
C=zarr_workflow/era5_37levels/config_era5_37_2025.yml
# 1. recommended: set   source: plev: ncar   in $C   (one line), then
python zarr_workflow/era5_37levels/era5_37.py $C download > zarr_workflow/data/era5_37/logs/run_full.out 2>&1   # ~2.4 h
python zarr_workflow/era5_37levels/era5_37.py $C status
python zarr_workflow/era5_37levels/era5_37.py $C verify --spot-checks 5
```
- If you switch to NCAR, January is downloaded once more from NCAR (its unit names differ) and overwritten with
  the same values; that costs 10 min and keeps the run in one consistent source. Or leave `plev: cds` for a CDS-only
  store (~30 h; it can run in the background and resumes after interruptions).
- Then point the zarr workflow at the new store: in `config_2025.yml` (or a copy), `climate.era5.path:
  /app/zarr_workflow/data/era5_37`, `zarr_store: ERA5.zarr`, and skip `get_era5` (EDH) in `run_year.py`. Not done yet:
  that's a change to your pipeline, for you to decide.

## 6. Open points / decisions for you
1. **NCAR or CDS for PLEV?** My recommendation: NCAR (12x faster, same values; cite NCAR GDEX d633000 together with
   Copernicus in the thesis).
2. Start the full 2025 download (section 5).
3. Note for the thesis/pipeline: `s3fs`, `h5py` and `kerchunk` were pip-installed into the **running container**
   only, for the S3 probes. The tool doesn't need them (it needs only what is in the image).
