# System design: multi-year downscaling wrapper

This file explains **why** the multi-year downscaling tool is shaped the
way it is. It is not a changelog (that's `PROGRESS.md`, chronological
narrative/decisions) and not a run log (that's `logs.md`, the tool's own
append-only operational output). If you're trying to understand a design
choice rather than "what happened when," this is the file to read — and to
keep updated when a design choice changes, so it doesn't go stale.

How the code itself works (every module and step of the original TopoPyScale, then of our
modified Zarr version, side by side) is in `TOOL_ARCHITECTURE.md`.

## Current design (updated 2026-10-13); supersedes parts of the sections below

The sections below date from 2026-09-30 and describe the CDS/NetCDF plan. Since then the design changed;
details and evidence are in the fork's `PROGRESS.md`. In short:

| Topic | 2026-09-30 plan (below) | Current design |
|---|---|---|
| ERA5 source | CDS, batched requests (SURF monthly, PLEV 3-day) | **Earth Data Hub** Zarr (`fetch_era5_edh.py`): a year in ~5 min, ~280 requests. ARCO-ERA5 (Google) checked: all 37 levels but chunks are 1 h x whole globe, ~4 TB per year, so only usable for short test periods |
| Pressure levels | 14 CDS levels | EDH's 5 (600/700/850/925/1000). Known effect: high terrain ~0.5 K colder in January (1-1.7 K in inversions) |
| Downscaler | classic `topo_scale` (NetCDF per point), Zarr only as export | **`topo_scale_zarr`**: bugs fixed, parity-tested against the classic path (1e-5), resumable via progress markers |
| Output | per-year `output.nc`, concat to Zarr at the end | hourly `down.zarr` + daily `daily.zarr`, then daily GeoTIFFs (one file per month, one band per day) |
| Splitting in time | one Topoclass per year | **one yearly store filled month by month** (option B): resume per month (to be implemented) |
| Terrain caches | NetCDF/pickle, symlinked per year | to be stored as Zarr (`ds_param`, `ds_solar`, `da_horizon`); computed once, shared by all months/years |
| Clustering | not fixed | **k = 500, elevation weight 4, other features 1**, `mask.tif` (rectangle AOI) |
| Location | `bulgaria_rila_pirin/` | new self-contained folder `zarr_workflow/` (code + configs in git, `data/` ignored) |

Why each change was made:
- EDH instead of CDS: the CDS queue made a year take days; EDH gives the identical ERA5 (checked for
  January 2025, differences = archive quantisation) much faster. The price is fewer pressure levels.
- Zarr downscaler instead of classic: the reason for avoiding it (not resumable, untested) no longer
  holds. It was fixed and tested, and a full year takes ~45 s for 500 clusters.
- Month-by-month filling of one store: a crash loses only the current month; one store per year is
  easier to read than 12 pieces, and it scales to 1980-2025.
- Weight 4 / k = 500: station test (clustering error 0.54 K with weight 4 vs 0.96 K with weight 1) and
  the cluster search (diminishing returns beyond k = 500). See PROGRESS.md.

## The problem this solves

TopoPyScale's own quickstart workflow (`pipeline.py`, 6 steps) is built to
run once, for one date range, in the foreground, and only exercises the
scale it's tested at: a single day or two. Getting a full 1980-2025 hourly
record out of it needs something layered on top that handles: fetching 46
years of ERA5 without getting rate-limited, running the compute across that
whole span without one crash losing everything, and doing all of that
unattended over what turns out to be a multi-day-to-multi-week job. None of
that is provided by the library itself — this system is the layer that
adds it, while deliberately reusing TopoPyScale's own methods for every
actual computation.

## The three pieces

- **`pipeline.py`** (repo root) — the canonical single-run 6-step template,
  annotated tutorial-style. Not used directly for the multi-year run; it's
  the reference every other pipeline script (including this system) is
  built from.
- **`bulgaria_rila_pirin/scripts/era5_batch_fetch.py`** — fetches one
  year's ERA5 data (SURF + PLEV) in efficiently-batched CDS requests
  instead of TopoPyScale's own day-by-day fetch.
- **`bulgaria_rila_pirin/scripts/run_downscaling.py`** — the per-year
  orchestrator: for each year, ensure that year's ERA5 exists (calling the
  fetch module above), then run the proven downscaling pipeline for just
  that year, then (once as many years as wanted are done) export everything
  to one combined Zarr store.

## Key design decisions, and why

### One year at a time, not one 46-year job
Every phase (fetch, downscale) operates on a single year and checks "does
this year's expected output already exist?" before doing anything. This
means a crash on year 23 doesn't lose years 1-22, and — just as
important — it means progress is *visible* rather than one opaque job that
either finishes in three weeks or doesn't. The library's own attempt at
this kind of chunking (`project.split.IO`) was found in the source and
explicitly rejected: its own comment calls it "prone to bugs... Not
compatible with Zarr system." Ours is simpler (whole extra Topoclass
instance per year) at the cost of some redundant setup work per year, which
is cheap compared to the actual compute.

### Resumability is filesystem-based, not a state file
`year_is_done()` just checks whether `<year>/output.nc` exists and opens
validly. No separate JSON/database tracking what's done — because a
separate state file can drift from reality (say a file gets deleted by hand,
or a run gets killed mid-write), and then the state file lies. Checking the
actual filesystem is always truthful, and it's exactly the same philosophy
TopoPyScale itself uses for every one of its own cached steps
(`ds_param.nc`, `da_horizon.nc`, etc. — "if the file's there, trust it and
skip recomputing").

### Zarr is the final export format, not the live processing path (SUPERSEDED 2026-10-13: see "Current design")
TopoPyScale has a real, purpose-built dask+zarr downscaling path
(`ClimateDownscaler` in `topo_scale_zarr.py`, activated via
`climate.era5.zarr_store` in config) — the "fancier" option. We
deliberately don't use it for the live 46-year compute. Reasoning: this
project has hit a new bug on essentially every first-time-exercised code
path so far (6 in the original toposub run, 3 more in the first point-mode
run, another found by reading `topo_scale_zarr.py`'s source before ever
running it). Deliberately routing a job that needs to run unattended for
weeks through a barely-exercised, **not actually resumable** code path
(`ClimateDownscaler`'s own store setup calls `.to_zarr(mode='w')`
unconditionally — re-running it to "resume" would wipe prior progress) is
the wrong risk tradeoff. Instead: reuse the classic multicore/NetCDF path
(proven at 242s for 28 stations x 2 days in the 2022-11-27/28 validation
run) once per year, and treat Zarr purely as a **final export step** — a
plain `xr.concat` + `.to_zarr()` over completed years' `output.nc` files.
Still delivers Zarr as the end format; doesn't add compute-path risk to get
there.

### Shared terrain caches are symlinked into each year, not recomputed
`ds_param.nc` (DEM elevation/slope/aspect/svf), `da_horizon.nc` (horizon
angles), and `df_centroids.pck` (point/cluster definitions) don't depend on
the date range at all — confirmed by reading `Topoclass.compute_horizon`'s
actual source, which only ever references the DEM and azimuth increments.
So each year's own output directory gets symlinks back to the one shared
copy of these three files, rather than either (a) recomputing them 46
times (wasteful — `da_horizon.nc` alone is 7GB and took real time to
compute) or (b) copying them 46 times (46 x ~9.3GB ≈ 428GB of pure
duplication for files that never change). Symlinks (not hardlinks): chosen
deliberately over hardlinks for the *output-side* caches earlier in this
project specifically because a hardlink silently breaks if something ever
rewrites the target via unlink-then-recreate rather than truncate-in-place
— a symlink at least fails *visibly* (broken link) rather than silently
serving stale data. These three files are genuinely static once computed,
so the risk is lower here than it was for that earlier decision, but the
same reasoning applies.

### ERA5 fetch is batched, not TopoPyScale's day-by-day default (SUPERSEDED 2026-10-13: EDH replaces CDS)
1980-2025 is ~16,800 days. TopoPyScale's own `fetch_era5.py` requests one
CDS API call **per day per dataset** — literal reproduction of that
pattern would be ~33,600 requests, which risks CDS rate-limiting
independent of actual compute cost. Empirically found (see `logs.md` for
the full trial-and-error): CDS's own per-request "cost" limit rejects a
full month of PLEV data (6 variables x 14 pressure levels x ~744 hours,
"403 cost limits exceeded") but accepts SURF at that same monthly
granularity (no pressure-level dimension, much smaller). So: **SURF is
batched monthly, PLEV is batched in 3-day chunks** (empirically the largest
chunk size found to actually clear CDS's limit) — both merged into the same
yearly `PLEV_{year}.nc`/`SURF_{year}.nc` format the rest of the pipeline
already expects, so nothing downstream changes.

### Fetch requests run concurrently, not one-at-a-time
CDS queue wait is the dominant real-world cost of the fetch phase, and it's
I/O-bound (waiting on an external server), not CPU-bound. So
`era5_batch_fetch.py` uses a thread pool (`multiprocessing.dummy.Pool`) to
have several requests in CDS's queue simultaneously — the same concurrency
tool TopoPyScale's own `retrieve_era5()` already uses for exactly this
reason, just applied to our batched version too. Default: 4 concurrent
requests.

### Every library patch is verified against a pristine install, not just the live container
Six-plus bugs have been found in the installed `TopoPyScale==0.3.3` package
across this project's sessions, all fixed via live `sed`/Python patches
inside the running container *and* baked into `docker/Dockerfile` so they
survive a rebuild. Before trusting any Dockerfile patch, it gets tested
against a **freshly `pip download`'d copy** of the exact pinned version (not
the already-patched live container) — confirms the patch will actually
reproduce a working setup on a real rebuild, not just "looks like the same
sed command."

## Known constraints (things to remember, not just interesting trivia)

- **CDS's real cost-limit boundary is unconfirmed beyond "3-day PLEV chunks
  work, 7-day chunks don't."** If the account's limits or the dataset
  change, this may need re-tuning — check `logs.md` for the empirical
  trail if a `403 cost limits exceeded` reappears.
- **Full-year downscale-compute timing is still unknown** as of this
  writing — only a 2-day window has ever been measured (242s for 28
  stations). A single-year pilot is the planned way to get a real number
  before trusting any estimate for the full 46 years.
- **`clean_outputs` must stay `False`** in every per-year generated config
  — `Topoclass.__init__` does `shutil.rmtree(outputs.path)` unconditionally
  if it's `True`, which would delete a year's directory (symlinks and all)
  on every instantiation.
