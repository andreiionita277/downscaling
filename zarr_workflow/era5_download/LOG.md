# LOG: ERA5 with all 37 pressure levels, 2025, Rila-Pirin

Running log of the overnight session of 2026-10-26/27 (times local, WSL host). Every test, number and
error goes here, newest at the bottom. The summary with recommendations is `REPORT.md`.

## Task (from the user, 2026-10-26 evening)
- Hourly ERA5, **all 37 pressure levels**, year **2025**, Rila-Pirin box, into Zarr in the same layout as
  `zarr_workflow/data/era5/ERA5.zarr`.
- Find the CDS request-size limit empirically (largest to smallest, exact threshold).
- Test the workarounds (NCAR S3, ...).
- Not lifted: "don't start the full download without my confirmation" -> 1-month test only.

## Environment
- Host: WSL2, 32 cores, 62 GB RAM, 819 GB free on /.
- Container `toposcale-rila-pirin` was stopped; started with `docker compose up -d` (20:1x).
- Installed into the running container (NOT in the image, lost on rebuild): `s3fs 2026.9.0`,
  `h5py 3.16.0`, `kerchunk 0.2.10`. Already there: cdsapi 0.7.7, ecmwf-datastores-client 0.5.1,
  cfgrib 0.9.15.1, eccodes 2.47.0, xarray 2026.7.0, zarr 3.2.1.
- CDS credentials: `~/.cdsapirc` on the host is an (empty, root-owned) directory; the real file is
  `~/.cdsapirc.d/credentials`, mounted read-only into the container as `/home/icam/.cdsapirc`.
  All CDS work therefore runs inside the container.

## Step 1.1: existing store `zarr_workflow/data/era5/ERA5.zarr` (target layout)
- Zarr v3, consolidated, written by `TopoPyScale.fetch_era5_edh.assemble_zarr`.
- dims: `time` 10080 (2024-12-05 00:00 .. 2026-01-28 23:00, hourly; padding comes from EDH chunk alignment),
  `level` 5 (600, 700, 850, 925, 1000; float64, **ascending**), `latitude` 10 (43.00 .. 40.75, **descending**),
  `longitude` 11 (22.25 .. 24.75, **-180..180**, ascending), 0.25 deg.
- PLEV vars (time, level, latitude, longitude) float32: `z t u v q r`.
- SURF vars (time, latitude, longitude) float32: `z_surf d2m sp strd ssrd tp t2m`.
- Chunks: (8760, all levels, 10, 11); Blosc lz4 clevel 5 bitshuffle; fill NaN; GRIB_* attrs from cfgrib.
- Accumulations (tp, ssrd, strd): value at valid time = previous 1 h (CDS convention).
- Variables required by `topo_scale_zarr.py`: `PLEV_VARS = ['z','t','u','v','q','r']` (line 71) + the SURF set.
- CDS area for the same grid: `[N, W, S, E] = [43.0, 22.25, 40.75, 24.75]` -> 10 x 11 points.

## Step 1.3a: CDS request-size limit (20:20-20:30)

### Debug: CDS client config
- `ecmwf.datastores.Client()` failed: `FileNotFoundError: /home/icam/.ecmwfdatastoresrc`. The new client
  does not read `~/.cdsapirc`. Fix: read url/key from `/home/icam/.cdsapirc` and pass them to
  `Client(url=..., key=...)` (`probe_costs.py: cds_client()`). Authentication then OK.

### Server-side cost estimates (`probe_costs.py`, `probe_costs2.py`; `Client.estimate_costs`, nothing submitted)
The CDS reports the cost and the limit for a request without running it. Cost = number of items
(variable x level x valid date x hour). Results:

| dataset / request | format | cost | limit |
|---|---|---:|---:|
| PLEV 1 var x 1 lev x 1 h | GRIB | 1 | 60,000 |
| PLEV 1 var x 37 lev x Jan (744 h) | GRIB | 27,528 | 60,000 |
| PLEV 2 vars x 37 lev x Jan | GRIB | **55,056** | 60,000 |
| PLEV 3 vars x 37 lev x Jan | GRIB | 82,584 | 60,000 |
| PLEV 6 vars x 37 lev x Jan | GRIB | 165,168 | 60,000 |
| PLEV 1 var x 37 lev x Jan | **NetCDF** | 165,168 (**6x GRIB**) | 60,000 |
| PLEV 2 vars x 37 lev x Jan | NetCDF | 330,336 | 60,000 |
| PLEV 1 var, Jan, **no area** | GRIB | 27,528 (area does not change cost) | 60,000 |
| PLEV 2 vars, Feb with day 01..31 | GRIB | 49,728 (= 28 days: invalid dates are not counted) | 60,000 |
| SURF 7 vars x Jan | GRIB | 5,208 | **121,000** |
| SURF 7 vars x 12 months | GRIB | 61,320 | 121,000 |
| SURF 7 vars x Jan | NetCDF | 31,248 (6x GRIB) | 121,000 |

Findings: NetCDF is charged 6x GRIB (so NetCDF can't even fit 1 variable x 37 levels x 1 month); the area doesn't
matter; the single-level limit is about twice as high, and a whole year of all 7 surface variables fits in **one** request.

### Real submissions, largest first (`probe_limit_submit.py` -> `data/era5_37/probe/limit_search.json`)
Each request was submitted for real; accepted jobs were deleted right away (nothing downloaded).

| case | items | result |
|---|---:|---|
| 6 vars x 37 lev x Jan | 165,168 | rejected, HTTP 403 "cost limits exceeded / Your request is too large" |
| 3 vars x 37 lev x Jan | 82,584 | rejected, 403 |
| 2 vars x 31 lev x 2 months x 22 days x 22 h (the smallest count > 60,000 that a request can express) | 60,016 | rejected, 403 |
| 2 vars x 25 lev x 2 months x 25 days x 24 h | **60,000** | **accepted** (job deleted) |
| 2 vars x 37 lev x Jan (planned request) | 55,056 | accepted (job deleted) |

**Exact threshold: a pressure-level GRIB request may hold at most 60,000 items (cost <= limit).**
The rejection comes immediately at submission (0.1-0.8 s), not after queueing.
Consequence for 37 levels: **2 variables x 1 month (<= 55,056)** is the biggest monthly request; 6 PLEV
variables -> 3 requests per month -> **36 PLEV requests for 2025**, plus **1 SURF request** for the whole year.
(Alternatives with the same item count: 1 var x 37 lev x 67 days, but whole months keep things simple.)

## Step 1.3b: tiny CDS request, timed (20:18) - `probe_tiny.py`
T, 700 hPa, 2025-01-15 12:00, our box, GRIB: accepted 0.2 s -> running 46.8 s -> successful 51.9 s,
download 0.6 s (328 bytes), **total 52.5 s**. cfgrib opens it; T 261.3-263.8 K. CDS access works.
File: `data/era5_37/probe/tiny_t700_20250115T12.grib` (kept as an independent spot-check reference).

## Step 1.2: NCAR ERA5 on AWS, `s3://nsf-ncar-era5` (20:19-20:22) - `probe_ncar.py`, `probe_ncar_speed.py`
- `e5.oper.an.pl/202501/`: 496 files, **one file per variable per day**, ~1.35 GB each
  (e.g. `e5.oper.an.pl.128_130_t.ll025sc.2025010100_2025010123.nc`). 2025 is there.
- HDF5 layout of `T`: shape (24, 37, 721, 1440) float32, **chunks (1, 37, 721, 1440)** = one hour x all
  levels x the whole globe, gzip level 1 + shuffle, ~53 MB per compressed chunk; lat 90..-90, lon 0..359.75.
- => **Byte-range reading of only our box is not possible** (same problem as ARCO): every hour costs one
  53 MB chunk. Kerchunk/VirtualiZarr would not help. Option A as written is not viable.
- Measured throughput S3 (us-west-2) -> this machine: 1 chunk 2.6 MB/s; 8/16/24 parallel ranges
  16.3/14.4/16.8 MB/s (link limit around 16 MB/s).
  Full-globe streaming for 2025 = 6 vars x 8760 h x 53 MB = **~2.8 TB, ~48 h at 16 MB/s**. Not attractive.

## Step 1.2b: NCAR THREDDS NetcdfSubset (server-side crop) - extra workaround (20:22-20:30)
NCAR's GDEX THREDDS server (`https://thredds.rda.ucar.edu/thredds/`, same as `tds.gdex.ucar.edu`) publishes
the same files (d633000) with the **NetcdfSubset (NCSS)** service: the server reads the file and returns
only our box. Anonymous, no account, no queue.
- Debug: `accept=netcdf4` -> HTTP 400 "Format netcdf4 is not supported for Grid data request"; `accept=netcdf` works.
- 1 request = 1 variable x 1 day x 37 levels x our box: HTTP 200, 394 kB, **exactly our grid**
  (lat 43.0..40.75 desc, lon 22.25..24.75, 37 levels 1..1000 ascending), 20.7 s.
- Parallel (`probe_ncss_speed.py`, T, Jan 2..16): 1 -> 45.8 s; 4 -> 38.4 s wall; 8 -> 43.6 s wall
  (per request 26-44 s). Scales almost linearly up to 8 at least.
- Estimate for 2025 PLEV: 6 vars x 365 days = 2,190 requests; at 8 parallel ~44 s per 8 -> **~3.5 h**.
- Single levels on NCAR are awkward (accumulations in forecast-step files `e5.oper.fc.sfc.accumu`, tp = lsp + cp);
  CDS does all 7 SURF variables for 2025 in one request -> use CDS for SURF in every option.

## Step 1.3c: CDS one-month timing test started 20:23 - `probe_cds_month.py`
Jan 2025: 3 PLEV requests (z-t, u-v, q-r; 55,056 items each) + SURF whole 2025 (61,320 items), submitted together.
GRIBs -> `data/era5_37/raw/`. Log `data/era5_37/probe/cds_month.log`. 20:24 u-v already running (46 s in queue).

## Step 2: the downloader `era5_37.py` + `config_era5_37_2025.yml` (20:27-20:31)
Both options in one tool: `source.plev: cds | ncar`; SURF always CDS. Commands `download`, `status`, `verify`.
- Store `data/era5_37/ERA5.zarr`: pre-allocated (metadata only, NaN), time 2025-01-01 00:00 .. 2025-12-31 23:00
  (8760 h; `topo_scale_zarr` uses exactly start..end day), 37 levels ascending, lat desc, lon -180..180,
  float32, Blosc lz4 bitshuffle (as the EDH store), **chunks (744, 37, 10, 11)** = ~1 month x all levels x box.
- Units: CDS PLEV = 1 month x 2 vars (z-t, u-v, q-r) x 37 levels (36 requests/yr); CDS SURF = all 7 vars x as many
  months as fit under 121,000 items (2025: one request); NCAR = 1 var x 1 day (2,190 requests/yr).
  The item count is checked before submission.
- Workers (threads) only download (to `*.part`, renamed when complete). The main thread alone converts, checks
  (no NaN, all levels, all hours), writes with `to_zarr(region={'time': ...})`, reads back and compares, appends
  to `state/manifest.jsonl`, then deletes the raw file. One writer = month-long chunks that are only partly covered are
  never written by two processes at once.
- Resumable: units recorded as `written` are skipped; a raw file already on disk is reused instead of being downloaded again.
- Retries: exponential backoff (CDS 120 s x 2^n, 5 retries; NCAR 60 s x 2^n); "cost limits exceeded" is not
  retried; a CDS job still queued after 24 h is deleted and retried; a unit that keeps failing is recorded as `failed` and the run continues.
- After every complete month: `state/months/YYYY-MM.json` (hours 744/720/696/672, all 37 levels, no NaN, no
  NaN-only hour/level, min/max per variable).
- `verify`: re-checks months, compares with the EDH store at the 5 shared levels, and `--spot-checks N` makes N
  independent single-item CDS requests (random var/level/hour) and compares them with the store.
- Note: log timestamps from inside the container are **UTC** (local = UTC+2).

### Debug
- First NCAR test config made with `sed` failed (`#` in the text clashed with the sed delimiter) -> empty YAML ->
  `TypeError: 'NoneType' object does not support item assignment` in load_config. Regenerated with Python.

## Step 2a: NCAR 1-month test started 20:31 (`config_era5_37_2025_ncar_test.yml`, separate store `data/era5_37/ncar_test/`)
`download --months 1 --only plev`: 186 units (6 vars x 31 days). First units written + read-back verified OK.

### NCAR 1-month result (20:31-21:02 local)
- **186/186 units written and read-back verified in 31.3 min** (8 workers), 0 failed.
- Retries: 1x HTTP 404 on an existing file (01-06 u, succeeded on retry: THREDDS hiccup) and 4x
  `TimeoutError: The read operation timed out` after the 900 s timeout (01-02 q, 01-03 t, 01-17 r, 01-20 u),
  all succeeded on retry. Those hung requests cost ~15 min each -> **timeout lowered to 180 s** in both configs.
- `state/months/2025-01.json` = "FAILED" **as expected**: this test is PLEV-only (`--only plev`), so the 7 SURF variables
  are NaN in the test store. PLEV: 744/744 h, 37 levels, 0 NaN, no NaN-only hour/level for z t u v q r.
  r ranges -0.7..133 % (ERA5's r is computed w.r.t. water/ice and is known to go beyond 0-100; compared with CDS below).
- Throughput: ~6 units/min including the hung ones -> 2025 (2,190 units) ~ 6 h as it was; ~4 h with the shorter timeout.

### Code fixes made during the test (do not affect the running test)
- `make_units`: SURF units are now always packed from the whole period, so `--months 1` gives the same unit id
  (`cds_surf_2025-01_2025-12`) as the full run (before: `cds_surf_2025-01_2025-01`, a different request).
- A month counts as complete only when ALL its units (PLEV + SURF) of the whole run are written; before, `--only plev`
  validated months that could not be complete.

## Step 2b: CDS January test, first results (21:10)
- `plev_2025-01_u-v` (55,056 items): accepted 0 s -> running 46 s -> **successful 2,801 s (47 min)**; GRIB 18.1 MB,
  downloaded at ~4 MB/s. The other 3 jobs waited in the queue until the first finished (q-r started running at
  2,816 s): **the CDS ran only one of my jobs at a time** (per-user concurrency limit, see below).
- Debug: `convert_cds_plev` failed with `ValueError: the new name 'time' conflicts`: cfgrib analysis files have
  dim `time` AND coordinate `valid_time` (identical, step 0). Fix in `_std_grid`: if both exist and are
  equal, drop `valid_time`. After the fix: conversion + checks 29 s, 744 h x 37 levels, 0 NaN.
- u, v min/max are **identical** to the NCAR test store (u -32.427..136.309, v -53.864..48.485).

### CDS per-user concurrency limit (confirmed 21:13 via `Client.get_jobs()`)
The job metadata says, for every queued job: `qos.status.user: "The maximum number of per-user requests that access
the CDS-MARS data is 1", queued 2, running 1`. So **only one of my CDS requests runs at a time**, whatever the
number of workers. More workers only keep the next job queued so it starts without a gap.
=> CDS PLEV for 2025 = 36 requests x ~47 min = **~28 h minimum** (+ SURF), if each request runs as long as u-v did.

## Step 2c: NCAR February with 16 workers + 180 s timeout (21:03-21:15 local)
- `download --months 2 --only plev` into the NCAR test store: **168/168 units written + verified in 12.0 min**, 0 failed.
- 4 retries (2x HTTP 404 on existing files, 2x 180 s read timeout), all OK on retry.
- Throughput 14 units/min (vs 6/min with 8 workers + 900 s timeout).
  => **all of 2025 (2,190 units) ~ 2.6 h** at 16 workers. No sign of throttling by the server at 16.
- Month validation correctly did NOT run (SURF units of the full run are not written in this PLEV-only test).

## Step 2d: CDS January test continued (22:18)
| job | queued -> running | running -> successful | download |
|---|---:|---:|---:|
| plev 2025-01 u-v | 46 s | 2,755 s (46 min) | 6.0 s, 18.1 MB |
| plev 2025-01 q-r | 2,816 s (waited for u-v) | 3,990 s (**66 min**) | 2.5 s, 18.1 MB |
| plev 2025-01 z-t | 6,824 s (waited for q-r) | 2,597 s (43 min) | ok |
| surf 2025 (whole year) | 9,430 s (waited for z-t) | ... | |
So far: **47-66 min of MARS work per 2-variable month request**, strictly one after the other.
Extrapolated, CDS-only 2025 = 36 PLEV x ~55 min ~ **33 h** + SURF.

### CDS vs NCAR, January 2025, all 37 levels x 744 h x 110 points (22:20)
CDS GRIBs (converted by `convert_cds_plev`) vs NCAR test store:
| var | max abs diff | share of values bit-identical | max diff relative to max abs value |
|---|---:|---:|---:|
| u | 1.5e-05 m/s | 99.88 % | 1.1e-07 |
| v | 0 | 100 % | 0 |
| q | 3.7e-09 kg/kg | 65.1 % | 4.2e-07 |
| r | 1.5e-05 % | 40.0 % | 1.1e-07 |
=> **Same data**: differences are one float32 rounding step (1e-7 relative), from GRIB decoding
(cfgrib/eccodes) vs NCAR's GRIB1 -> NetCDF conversion. Physically irrelevant (temperature equivalent < 0.0001 K).

## Step 2e: CDS SURF (whole 2025) done; January written to the main store (23:52-00:06)
- `surf_2025`: waited 9,430 s for the 3 PLEV jobs, then ran **3,050 s (51 min)**; GRIB 21.1 MB, download 6 s.
- **CDS 1-month test total: 3 h 28 min wall** (20:23 submit -> 23:51 last file), all 4 jobs strictly one after the other.
- Raw files renamed to the tool's unit ids (`cds_plev_2025-01_<pair>.grib`, `cds_surf_2025-01_2025-12.grib`) and
  ingested with `download --months 1` (raw files reused, nothing re-requested):
  - 3 PLEV units: converted + checked + written + read back in ~27 s each, raw deleted.
  - SURF unit **FAILED: `ValueError: strd: 963050 NaN values`**; the run continued and the raw file was kept (as designed).

### Debug: SURF accumulations all NaN
- cfgrib gives the accumulations (ssrd, strd, tp) as (time = forecast start 06/18 UTC, step 1..12 h): 731 x 12
  (raw data fine: only the 12 (time, step) pairs outside the request are NaN).
- Bug: `Dataset.stack(vt=('time','step'))` puts the new dim LAST; my "drop all-NaN valid times" mask assumed time
  first, so it kept 10 random entries -> the merge with the instant fields left almost all hours NaN.
- Fix: `.transpose('time', ...)` after stacking. Rerun: SURF unit written + verified (8,760 h, 173 s, cfgrib is slow),
  **month 2025-01 validation OK** (744/744 h, 37 levels, 0 NaN in all 13 variables).
- Store size with January PLEV + full-year SURF: 45 MB.

## Step 2f: verify, January (00:07-00:15)
- Debug 1: spot check crashed with `the new name 'time' conflicts` again: a 1-hour GRIB has `time` as a SCALAR coord,
  not a dim. Fix: drop `valid_time` whenever `time` exists as a coord (dim or scalar) and both are equal.
- Debug 2: spot checks drew random hours from the whole year -> NaN (months 2-12 not filled) -> "FAILED".
  Fix: spot checks are drawn only from months that passed validation.
- Final `verify --months 1 --spot-checks 3 --seed 7`: **OK**. Report `state/verify_20261026_221523.json`.
  | independent single CDS request | store | diff |
  |---|---|---|
  | u, 70 hPa, 2025-01-17 20 UTC | CDS store | **0 (bit-identical)** |
  | r, 5 hPa, 2025-01-04 02 UTC | CDS store | **0** |
  | q, 20 hPa, 2025-01-16 14 UTC | CDS store | **0** |
  Each single request took 37-56 s.

## Step 2g: all sources compared, January (`compare_sources.py` -> `probe/compare_sources_2025-01.json`)
| comparison (Jan, all hours) | z [m2/s2] | t [K] | u [m/s] | v | q [kg/kg] | r [%] |
|---|---:|---:|---:|---:|---:|---:|
| CDS vs NCAR, max abs, 37 levels | 0.016 | 0.00012 | 1.5e-5 | 0 | 3.7e-9 | 1.5e-5 |
| CDS vs EDH, max abs (5 levels) | 16 | 0.125 | 0.021 | 0.018 | 3.8e-6 | 0.031 |
| CDS vs EDH, mean abs | 3.1 | 0.062 | 0.0024 | 0.0022 | 5.1e-7 | 0.012 |
- Single independent CDS request (T 700 hPa 2025-01-15 12 UTC): CDS store **identical**, NCAR 3e-5 K, EDH 0.125 K max.
- **EDH values are rounded** (t steps of 0.125 K, z of 16 m2/s2, sp up to 36 Pa, strd/ssrd up to 512 J/m2): EDH stores
  ERA5 with reduced precision. Small, but EDH is not bit-equal to the CDS; NCAR is (to float rounding).
- SURF CDS vs EDH: t2m/d2m max 0.125 K, sp max 36 Pa, tp max 1e-6 m (99.5 % identical), ssrd/strd max 512 J/m2.

## Step 2h: TopoPyScale end-to-end on the new store (`test_downscale_jan.py`, 00:15-00:17)
`topo_scale_zarr.ClimateDownscaler` with the settings of `config_2025.yml` (idw, LW terrain, precip lapse rate),
all 500 clusters of the existing run (`outputs/df_centroids.pck`, `ds_solar.zarr`), January, 10 cores.
- Debug: first try `BrokenPipeError` in multiprocessing: Python 3.13 uses forkserver, so the script needs the
  `if __name__ == '__main__'` guard. Fixed.
- **Runs without change: 500 clusters x 744 h in 18 s (EDH run: 29 s with 20 cores), 0 NaN.** The 37-level store is
  read by the existing code as is (it only requires ascending levels).
- 37 levels (CDS) minus 5 levels (EDH), same clusters and hours (`probe/compare_downscaled_jan.json`):
  | var | mean diff | mean abs | p99 abs | max abs |
  |---|---:|---:|---:|---:|
  | t [K] | +0.15 | 0.36 | 2.7 | 4.9 |
  | p [Pa] | -10.9 | 12.8 | 91 | 159 |
  | LW [W/m2] | +0.38 | 1.6 | 12.1 | 23.7 |
  | ws [m/s] | +0.016 | 0.37 | 2.8 | 7.2 |
  | tp [m/h] | -3.6e-7 | 7.3e-6 | 1.6e-4 | 1.5e-3 |
  Mean t difference per elevation band: 0-1000 m +0.12 K, 1000-1500 m -0.06 K, 1500-2000 m +0.27 K,
  **2000-3000 m +0.51 K**; 9.6 % of all hours differ by more than 1 K.
  This confirms the earlier finding in DESIGN.md (EDH 5 levels: high terrain ~0.5 K too cold in January).
- Note: some points log "lower than the 1000 hPa geopotential => sampled from Psurf and lowest level": same
  behaviour as before (low valley points), not new.

## End of session (00:25)
- REPORT.md written (recommendation: NCAR THREDDS for PLEV, CDS for SURF).
- State: main store has January complete + verified, SURF for all of 2025; Feb-Dec PLEV missing.
  NCAR test store has PLEV Jan + Feb. No downloads running. **Full download NOT started** (waits for the user's OK).
- GIT_PENDING.md entry 23 added (the user commits).

# Session 2026-10-27: 600-1000 hPa, wired into the zarr workflow

## User decision (morning)
Levels: **all ERA5 levels from 600 to 1000 hPa** (14: 600 650 700 750 775 800 825 850 875 900 925 950 975 1000),
not 37. Adjust naming, check the whole workflow, run it with zarr_workflow, then the full year 2025 (downscaled as Zarr).

## Renaming (09:00 local)
- Folder `zarr_workflow/era5_37levels/` -> **`zarr_workflow/era5_download/`**, tool `era5_37.py` -> **`era5_download.py`**.
- Probe/test scripts and the two 37-level test configs -> `era5_download/probes/` (history; they still point at
  `data/era5_37/`, which holds the 37-level test data).
- New main config **`config_era5_2025.yml`**: 14 levels, `source.plev: ncar`, `source.surf: cds`,
  store **`data/era5_600-1000hPa/ERA5.zarr`**.

## Tool changes
- CDS requests are grouped automatically as few and as even as the limit allows (longest month):
  37 levels -> 3 x 2 vars (55,056 items); **14 levels -> 2 x 3 vars (31,248 items)**. `cds.vars_per_request` overrides.
- NCAR NCSS always returns 37 levels; `convert_ncar` now keeps the configured levels.
- New command **`import --from STORE`**: fills units from a store that already holds them (same grid, superset
  of levels), with the same checks/read-back/manifest as a download. Used to avoid re-downloading:
  - Jan PLEV + all 2025 SURF from `data/era5_37/ERA5.zarr` (CDS): 187 units, 7 s -> **2025-01 validation OK**
  - Feb PLEV from the NCAR test store: 168 units, 6 s -> **2025-02 validation OK**
- 600 hPa geopotential height over the box, Jan-Feb: **4,090-4,369 m** (highest cluster 2,554 m, Musala 2,925 m):
  the column is always bracketed, no extrapolation.

## Workflow changes (`zarr_workflow/scripts/`, `config/`)
- `run_year.py`: with `climate.era5.data_repository: local`, step 5 `get_era5` no longer fetches; it calls
  `workflow.check_era5_store` (all hours of the months being run present, no NaN, levels ascending and equal to
  `climate.era5.plevels`). Without it (EDH configs) nothing changes.
- `workflow.py`: **bug fix in `RANGES`**: `'SW'`, `'SW_direct'`, `'SW_diffuse'` bounds were written after the `#`
  comment of the `'wd'` line, so they were never in the dict and the SW range check never ran ("SW values above
  bound: 0" in all earlier month reports was not a real check). Now they are checked (reported, not failing, as designed).
- New run config **`config/config_2025_era5_600-1000hPa.yml`** = `config_2025.yml` with only: name/description/date,
  `project.directory: /app/zarr_workflow/data/runs/2025_era5_600-1000hPa`, `climate.era5.path:
  /app/zarr_workflow/data/era5_600-1000hPa`, `data_repository: local` (EDH keys removed), plevels comment.
- Caches copied from `data/outputs/` into the new run's `outputs/` (ds_param.zarr, df_centroids.pck, ds_solar.zarr,
  da_horizon.zarr, 5 s): **the same 500 clusters** as the EDH run, so the comparison is one to one. (Clustering, solar
  geometry and horizon depend only on the DEM, not on ERA5.)

## Workflow test: run_year.py --months 1 2 (09:00-09:05)
`run_year.py zarr_workflow/config/config_2025_era5_600-1000hPa.yml --months 1 2` -> log
`data/runs/2025_era5_600-1000hPa/logs/test_jan_feb.log`: **4 min 51 s, "YEAR COMPLETE, ALL CHECKS PASSED"**
(for the 2 months). Caches loaded, store check OK (14 levels, 13 vars, no NaN), downscale 21 s / 20 s per month,
QA OK (t -11.8..16.0 degC Jan, -18.0..15.2 Feb; SW above bound now counted: 4 and 1 values), daily store,
GeoTIFFs and their checks OK. (A loky "leaked semaphore" warning at exit is harmless.)

### Results
- **January downscaled from 14 levels vs from 37 levels: bit-identical** (max diff 0 for t, p, q, LW, SW, tp, ws).
  Levels above 600 hPa are never used for this terrain, so 600-1000 hPa loses nothing.
- vs the EDH (5-level) run, mean t difference per elevation band:
  | month | 0-1000 m | 1000-1500 m | 1500-2000 m | 2000-3000 m |
  |---|---:|---:|---:|---:|
  | Jan | +0.12 | -0.06 | +0.27 | **+0.51** |
  | Feb | +0.07 | -0.02 | -0.14 | **-0.29** |
  The 5-level error is not a constant bias: its sign changes with the weather (inversions in January).

## CDS timing with 14 levels: March test started 09:02
`probes/config_era5_2025_cdstest.yml` (separate store `data/era5_600-1000hPa_cdstest/`), `download --months 3 --only plev`:
2 requests (z-t-u, v-q-r), 31,248 items each.

## Single levels from EDH instead of the CDS? (`probes/test_edh_surf.py`, 09:2x)
January, 500 clusters, same 600-1000 hPa PLEV; SURF from the EDH store (rounded storage) vs from the CDS:
| downscaled var | max abs diff | mean abs diff |
|---|---:|---:|
| t, p, q, ws | **0** (identical) | 0 |
| LW [W/m2] | 0.83 | 0.14 |
| SW [W/m2] | 0.45 | 0.007 |
| tp [mm/h] | 0.0015 | 7e-6 |
TopoSCALE takes t, p, q and wind from the pressure levels only; the single levels enter LW (strd), SW (ssrd) and
precipitation (tp), where EDH's rounding is far below any physical relevance. => EDH SURF + NCAR PLEV is a valid,
fast combination. (`import --from data/era5/ERA5.zarr --only surf` fills SURF from an EDH store: same grid and names.)

## Single levels from NCAR? (09:3x)
NCAR has all of them, organised differently (S3 listing for 2025-01):
| needed | NCAR folder / file | layout |
|---|---|---|
| t2m, d2m, sp | `e5.oper.an.sfc` `128_167_2t`, `128_168_2d`, `128_134_sp` | 1 file per var per month, (time, lat, lon) |
| ssrd, strd | `e5.oper.fc.sfc.accumu` `128_169_ssrd`, `128_175_strd` | 2 files per var per month (06 UTC of day 1 -> 06 UTC of day 16 -> 06 UTC of day 1 next month), (forecast_initial_time 06/18, forecast_hour 1..12, lat, lon) |
| tp | not stored: `128_142_lsp` + `128_143_cp` (ERA5's definition of tp) | as above |
| z_surf | `e5.oper.invariant` `128_129_z` | one file, constant |
Access tests:
- NCSS grid subset of the analysis file `2t` (whole January, our box): **HTTP 200, 6 s**, 744 h x 10 x 11.
  vs CDS t2m: max diff 0.00024 K (float rounding), 20 % bit-identical.
- NCSS on an `accumu` file: **HTTP 500** (`java.lang.NullPointerException ... TimeHelper.getRefDate()`): the grid
  subset service can't handle the 2-D forecast time axis.
- **OPeNDAP** (`https://thredds.rda.ucar.edu/thredds/dodsC/files/g/d633000/...`) on the same file works: our box for a
  half month in 11 s. `quantization_info` is empty; "NCO precision-preserving compression" = lossless.
  ssrd vs CDS, Jan 1-16 (360 h): **bit-identical**.
- tp = lsp + cp vs CDS tp (360 h): max diff 1.2e-6 m (0.0012 mm), 88 % identical: same within GRIB packing.
=> A NCAR-only setup (no CDS at all) is possible: per year ~36 NCSS (an.sfc) + ~96 OPeNDAP (accumu) requests,
   each a few seconds. Not implemented in era5_download.py yet: needs stacking of the forecast axis, the first
   hours of each month from the previous month's file, tp = lsp + cp, and z from the invariant file.

# 2026-10-27 (late morning): NCAR for everything; 2020 full-workflow test

## User decision
"We stick with NCAR forward." Test the whole workflow for **2020** (downscaled results as Zarr and GeoTIFF).

## NCAR single levels in era5_download.py (`source.surf: ncar`)
- Units: 1 per variable per month (84 for a year). Read with **OPeNDAP** (server-side box subset):
  t2m/d2m/sp from the monthly analysis files; ssrd/strd/tp from the forecast files (3 per month: the previous month's
  last file reaches 00-06 UTC of day 1), stacked to valid hours; tp = lsp + cp; z_surf from the invariant file,
  repeated over the hours. Each unit is written as a small NetCDF to raw/, then the usual convert/check/write/read-back.
- netCDF4/HDF5 is not thread-safe: all NetCDF reads/writes in the tool go through one lock (`_DAP_LOCK`); SURF units
  get their own 1-thread pool, PLEV keeps its 16, so they run side by side.
- Data dirs per year: `data/era5_600-1000hPa/2025/` (moved, configs updated) and `.../2020/`.

### Debug: NCAR SURF test hung after 1-2 variables
- First test run: d2m written, then nothing for 17 min. I wrongly concluded the process had died (there is no `ps` in
  the container) and started a second run on the same test store: it wrote sp, then hung as well. On the host
  (`ps`, /proc) both were alive, sleeping. Both killed, test store deleted.
- The same reads work in the main thread (ssrd month 41.6 s) and in a worker thread (30.7 s), so not a thread issue as such.
- Cause: the converters opened NetCDF files without closing them; the garbage collector closes them later from
  whatever thread runs, possibly a download thread that is already inside xarray's netCDF lock -> self-deadlock.
- Fix: every NetCDF opened by the tool is closed explicitly (`with xr.open_dataset(...) as f`).
- Rerun: **7/7 SURF variables of January 2025 in 2.4 min.**

### NCAR vs CDS single levels, January 2025 (744 h x 110 points)
| var | max abs diff | share bit-identical |
|---|---:|---:|
| z_surf | 0 | 100 % |
| ssrd | 0 | 100 % |
| strd | 0.75 J/m2 (of 1.3e6) | 99.6 % |
| t2m / d2m | 0.00024 K | 18-20 % |
| sp | 0.06 Pa | 11 % |
| tp | 1.4e-6 m | 90 % |
=> Same data (float/GRIB-packing rounding). **NCAR alone can supply everything TopoPyScale needs.**

## CDS March 14-level timing test
Submitted 09:02 local... first job "running" since 09:02:56 and still not finished at 11:37 (> 2.5 h for 31,248 items;
the CDS is slow today). I tried to stop it (it also blocks CDS spot checks, one job per user) but the kill/delete was
refused by the session's permission rules -> left running; **the user decides**. 2020 `verify` therefore runs without
CDS spot checks (NCAR = CDS was shown above for all 13 variables).

## 2020 full workflow started 11:38 local
- Configs: `era5_download/config_era5_2020.yml` (plev ncar, surf ncar, 2020, store `data/era5_600-1000hPa/2020/`),
  `config/config_2020_era5_600-1000hPa.yml` (run dir `data/runs/2020_era5_600-1000hPa`; caches ds_param,
  df_centroids, da_horizon copied = same 500 clusters; ds_solar NOT copied: computed for 2020).
- Runner `scripts/run_full_year.sh 2020`: download (+1 retry pass) -> status -> verify -> run_year.py.
- 2,280 units (2,196 PLEV day-files + 84 SURF var-months), store 8,784 h (leap year).
- Log: `data/runs/2020_era5_600-1000hPa/logs/full_year_2020.out` (+ download log under `data/era5_600-1000hPa/2020/logs/`).

### 2020 run, debug: slow start (11:38-11:48 local)
- First 6 min: only 12 units written, 5 retries after `TimeoutError` (180 s). A single NCSS request took 97 s
  (20-45 s yesterday night): NCAR is slower now. -> timeout raised to **600 s** (configs 2020 and 2025).
- Restart (by PID; note: `pkill -f "run_full_year.sh 2020"` also matched and killed my own shell command).
- Real bottleneck found after the restart: each PLEV unit took **17-20 s to write** (0.1 s in the Feb test). The SURF
  thread holds `_DAP_LOCK` during its OPeNDAP reads (10-40 s); the main thread took the same lock to read each raw
  file -> every write waited for a SURF read; 82 finished raw files queued up.
- Fix: NCSS raw files are NetCDF-3 classic (`CDF\x01`) -> read with xarray's `scipy` engine (no netCDF4 library, no
  lock); SURF raw files are now written as NetCDF-3 with scipy too. `_DAP_LOCK` guards only the OPeNDAP reads.
  The 8 SURF raw files written in NetCDF-4 by the old code were deleted (re-downloaded).
- After the fix (restart 11:48 local): **102 units written in 3.5 min, writes 0.0 s, 0 retries**.

### 2020 ERA5 download: result (finished 15:01 local)
- `run finished in 192.8 min: 2255 written, 0 failed` (+25 units from the first two attempts = 2,280). Average
  ~11.7 units/min with 16 NCSS workers + 1 OPeNDAP thread; NCAR was slow all day (busy period).
- 76 first attempts failed and **all** succeeded on retry: 52 read timeouts (600 s), 11 HTTP 504, 3 HTTP 502,
  5 URLError, 2 HTTP 404 (transient! same URL OK 60 s later), 3 OPeNDAP `NetCDF: I/O failure` (SURF). -> retries
  with backoff are required, not optional; a 404 from THREDDS must not be treated as permanent.
- Every month validated as written (all hours, 14 levels, no NaN-only slices); `status` 2,280/2,280; `verify` OK for
  all 12 months (744/696/744/720/744/720/744/744/720/744/720/744 h). No CDS spot checks (CDS slot still taken by the
  March test; NCAR = CDS shown above for all 13 variables).
- Store `data/era5_600-1000hPa/2020/ERA5.zarr`: 182 MB, 8,784 h x 14 levels x 10 x 11; raw folder empty (all deleted
  after verified writes).

### 2020 downscaling (run_year.py, 15:01-15:30 local)
- 12 months x 127-144 s, total 1,767 s; `YEAR COMPLETE, ALL CHECKS PASSED`; summary `outputs/year_summary.json`.
- `down.zarr` 500 x 8,784 h, 13 vars, 0 NaN; `daily.zarr` 500 x 366; 36 GeoTIFFs (COG, 4797 x 5079, 25 m, EPSG:32634,
  ~530 MB each, 19 GB total), all checked against the Zarr.
- QA t range per month from -16.4 degC (Jan/Feb) to 35.7 degC (Jul). SW > 1,400 W/m2: 46 values (max 2,026 W/m2), 14
  clusters, only 15-17 UTC = the known TopoSCALE sunset spike (reported, not a failure, see workflow.qa_hourly).
- Harmless at exit: loky "leaked semaphore objects" warning (joblib workers, after the run finished).
