# Bulgaria Rila-Pirin TopoPyScale run — progress & plan

## How to resume next session

1. Start Docker Desktop / the Docker daemon (however you normally do on this machine).
2. From `rila-pirin-downscaling/`:
   ```bash
   docker compose up -d
   ```
   The image is already built (includes `gdal-bin` now) — this just restarts the existing container, it will NOT rebuild unless the Dockerfile changed again.
3. Get a shell back:
   ```bash
   docker compose exec toposcale bash
   ```
4. Everything under `/app` inside the container is bind-mounted to `rila-pirin-downscaling/` on the host, so **all files (DEM, mask, config.yml, any scripts, any outputs) persist across container/computer restarts automatically** — nothing is lost by shutting down. Only things written *outside* `/app` inside the container (there shouldn't be any) would be lost.
5. Re-read this file top to bottom, then continue from the first unchecked item below.

## Decisions made so far

- **DEM**: `bulgaria_rila_pirin/inputs/rila_pirin_dem_25m_buffered_32634_bilinear_resampling.tif` — EPSG:32634, 25m, 4797×5079 px, elevation range **48m–2905m** (mean ~1035m), geographic extent ~lon 22.79–24.28°E, lat 41.29–42.46°N.
- **Mask** (true AOI, smaller than DEM): `bulgaria_rila_pirin/inputs/mask/rila_pirin_PERFECT_RECTANGLE_32634.geojson` — extent ~lon 22.97–24.09°E, lat 41.43–42.32°N. Confirmed nested correctly inside the DEM (buffer of ~15-20km on all sides).
- **ERA5 area**: NOT manually specified — TopoPyScale docs confirm it's auto-derived from the DEM's own raster bounds (`extent: None — not yet implemented` as a separate config key). No action needed here.
- **ERA5 source**: `cds` (Copernicus CDS API), using the credentials already in `~/.cdsapirc` (mounted read-only into the container at `/root/.cdsapirc`).
- **plevels**: `[600,650,700,750,775,800,825,850,875,900,925,950,975,1000]` — 700 hPa (~3010m) alone already satisfies TopoPyScale's documented requirement (must sit above the DEM's 2905m peak / Musala's 2925m), but extended down to 600 hPa (~4200m) for a more comfortable ~1300m margin, since the cost is negligible for a single-day request.
- **Date range**: single day, **2023-03-15**, hourly (ERA5 native timestep). Superseded the original 2016-08-30 pick (no verified Rila/Pirin-specific event found for 2016 or 2019 — see "Station data" below) — this date instead sits safely in the middle of the verified overlap window (2022-10-25 to 2023-08-16) where multiple METER.AC stations across the elevation range are simultaneously online, per the station data investigation.
- **Sampling method**: `toposub` only for now (station points from METER.AC — see below — could be added later as a `sampling.points` validation set once the tool is understood).

### Station data (for validation)

- Source: **METER.AC** (https://meter.ac), a Bulgarian citizen-sensor network. CC0-licensed, `robots.txt` allows scraping, citable (Terziyski et al., 2020, *Data*, 5(2), 36). Fetch script: `bulgaria_rila_pirin/meteo-data.py`. Cleanup/summary script: `bulgaria_rila_pirin/meteo_data_convert.py` (raw `data.raw.php` dumps have zero-padded strings and bare Unix timestamps — the convert script produces a proper `*_clean.csv` with real UTC + Sofia-local datetime columns, saved next to each raw file in `data/meterac/`).
- **29 of the network's 228 total nodes fall inside the project mask** (verified: both Rila 42.167°N/23.583°E and Pirin 41.717°N/23.450°E reference points confirmed inside the mask bounds; returned node place-names — Musala, Malyovitsa_Hut, Rilski_ezera_Hut, Tevno_ezero_Hut, Makedonia_Hut, Bansko, etc. — corroborate genuine Rila/Pirin coverage).
- **Per-node coverage varies a lot** — nodes did NOT all come online together. Checked so far (4 of the 29):
  - N306 Musala (2925.4m): 2019-08-23 → 2024-04-03
  - N48 Bansko (931.4m): 2018-11-24 → 2023-08-16
  - N235 Boboshevo (375m): 2021-04-18 → still live
  - N27 Semkovo (1622.6m): 2022-10-25 → still live
  - The other 25 AOI nodes have not been checked yet — worth doing before finalizing the validation station set (see checklist).
- Node ID formatting in the metadata CSV is inconsistently zero-padded (`N27`, `N48` vs `N058`, `N306`) — use the exact ID string from the metadata list, don't assume 3-digit padding.
- **Resolution decision**: run at full 25m resolution (not downsampled), accepting that `compute_dem_param()` / `compute_horizon()` will process the full ~24.4M-pixel buffered DEM and may take a long time. `clustering_mask` (see below) does NOT speed up these two steps — it only affects which pixels become cluster centroids later. If this turns out to be impractically slow in practice, revisit downsampling.
- **n_clusters**: NOT yet decided. Plan is to use TopoPyScale's own built-in `TopoPyScale.topo_sub.search_number_of_clusters()` function (found in the installed source, `topo_sub.py` ~line 157) which scores candidate cluster counts via WCSS, Davies-Bouldin, Calinski-Harabasz, and Elevation RMSE — this is the objective method, not a guess. Given the full DEM has ~24M pixels, we will likely need to run this search on a random subsample of the terrain-parameter dataframe for tractability, then use the selected k for the real full-resolution run.
- **Docker image**: added `gdal-bin` to `docker/Dockerfile` (gives us `gdalinfo`, `ogrinfo`, `gdal_rasterize`, `gdalwarp`, etc. inside the container) — already built and working.
- Regenerate the CDS API key at some point (it was pasted in plaintext into the Claude Code chat transcript during setup — not urgent, just good hygiene): https://cds.climate.copernicus.eu/profile
- **Deferred**: decided to purge the old superseded root DEM (`inputs/dem/rila_pirin_DEM_FINAL_32634.tif`, ~93MB) from git history entirely via `git filter-repo` + force-push, to reclaim repo space — ran out of time to do it safely. Steps are known (commit clean state first, `git filter-repo --path inputs/dem/rila_pirin_DEM_FINAL_32634.tif --path inputs/dem/rila_pirin_DEM_FINAL_32634.tif.aux.xml --invert-paths`, re-add remote, `git gc --prune=now --aggressive`, force-push) — pick this up when there's a dedicated block of time, since it rewrites all commit hashes and needs a force-push.

## Remaining steps (checklist)

- [ ] (Optional) Check remaining 25 of 29 AOI METER.AC nodes' coverage windows, in case a better/wider overlap exists; otherwise proceed with the 4 already verified (Musala, Bansko, Boboshevo, Semkovo) as the validation set for `sampling.points`.
- [x] Confirm TopoPyScale's path convention for `dem.file`/`dem.path`: confirmed in `topoclass.py` — if `dem.path` is blank in config.yml, it defaults to `<project.directory>/inputs/dem/`, and critically, if the DEM isn't found there, TopoPyScale will silently try to **auto-download a Copernicus DEM from the internet** instead via `fetch_dem()`. So the DEM MUST live at `inputs/dem/`, not directly under `inputs/`.
- [x] Moved DEM (+ .aux.xml) into `bulgaria_rila_pirin/inputs/dem/`. Mask stays at `inputs/mask/` (untouched, not part of this convention).
- [ ] Rasterize the mask GeoJSON into a `mask.tif` aligned pixel-for-pixel with the DEM (same bounds/resolution) using `gdal_rasterize`, for use as `sampling.toposub.clustering_mask` — restricts which pixels become cluster centroids to the true AOI rather than the buffer.
- [x] Wrote `bulgaria_rila_pirin/config.yml` (adapted from the working root config.yml).
- [x] Ran `compute_dem_param()` (full 25m resolution, ~24.36M pixels, ~13.95M inside mask AOI) — took a while (SVF computation is the slow part) but completed and **cached to `outputs/ds_param.nc`** (won't need to recompute for the real pipeline run).
- [x] Ran TopoPyScale's own `search_number_of_clusters()` (library function, no custom subsampling — full masked population) across k=[100,300,500,700,900]. Results (`n_clusters_search_results` — not saved to a file, just printed; rerun the heredoc from this session's history if needed again):
  ```
  n_clusters   wcss_score  db_score      ch_score  rmse_elevation
         100 1.618099e+07 13.70    736534.78       244.51
         300 1.008343e+07 19.18    446007.63       195.49
         500 8.058977e+06 16.93    357954.56       175.74
         700 6.984001e+06 19.36    299372.90       164.03
         900 6.294301e+06 17.21    261584.92       156.86
  ```
  **Decision: n_clusters = 500.** Reasoning: Davies-Bouldin is non-monotonic/noisy (terrain features are a continuum, not discrete groups — DB isn't a reliable signal here). Calinski-Harabasz decreases monotonically with k as a known mechanical artifact of its formula, not a real "smaller is better" signal — also not reliable. **Elevation RMSE is the metric that matters** (translates ~directly to temperature error via lapse rate) and shows clear diminishing returns: gains of 49m/20m/12m/7m per step — most of the achievable improvement happens by k=500, and it matches the root project's already-tested config for comparability. **STILL NEEDS: update config.yml's n_clusters from 500 (currently already a placeholder value, now also the final decision — but re-verify the line doesn't still say "PLACEHOLDER" in a comment) before writing pipeline.py.**
- [ ] Write a small standalone script to: instantiate `Topoclass`, run `compute_dem_param()`, take a random subsample of the resulting terrain-parameter dataframe, run `search_number_of_clusters()` over a candidate range, and report the score table — to pick `n_clusters` objectively before committing to a full run.
- [ ] Once `n_clusters` is chosen, write `bulgaria_rila_pirin/pipeline.py` (compute_dem_param → extract_topo_param → compute_solar_geometry → compute_horizon → get_era5 → downscale_climate).
- [ ] Run the pipeline stage by stage inside the container, verifying outputs at each step (this will likely take a while at full 25m resolution — budget real time for `compute_dem_param()`/`compute_horizon()` especially).
- [ ] Extract the downscaled `t` (air temperature) variable at hourly timesteps for 2016-08-30 from the final output and sanity-check it.

## Session update — 2026-07-30 (faculty machine, ICAM-B-104)

**Machine context**: this session was run on the faculty lab machine
`ICAM-B-104` (Windows 11, build 10.0.26100, i9-13900KF), not the home
machine the original Docker setup was built on. WSL2 Ubuntu-24.04.4 LTS,
matching the home machine's native Ubuntu version.

### What got done this session
- Full environment audit on this machine (Claude Code, VS Code, WSL, QGIS
  versions — see SETUP.md).
- Installed `gdal-bin` natively in WSL (GDAL 3.8.4) for host-side GIS work,
  separate from the container's own GDAL (3.6.2).
- Installed Docker fresh on this machine (`docker.io` via apt, then added
  Docker's official repo to get `docker-compose-plugin`, which isn't in
  Ubuntu's default repos).
- Built the `toposcale-rila-pirin` image from scratch on this machine and
  confirmed the container runs correctly (`docker compose up -d`,
  `docker compose exec toposcale bash`), with the repo correctly
  bind-mounted at `/app`.
- Confirmed inside the container: TopoPyScale imports correctly (pip-installed,
  not editable — separate from the editable fork install on the WSL host
  used for library-code experimentation), GDAL present.
- **Confirmed `ds_param.nc` does NOT exist on this machine** (not in the
  container, not on the WSL host) — it only ever existed on the home
  machine's Docker setup. Located a backup copy **on Google Drive**.
  NOT yet copied into this machine's `bulgaria_rila_pirin/outputs/` —
  first task next session.

### Known issues still open (found this session, not yet fixed)
- ~~`dem.file`/`plevels` mismatch~~ — RE-VERIFIED 2026-07-30, both already correct in config.yml. Earlier note in this file was stale.
- `bulgaria_rila_pirin/pipeline.py` is still empty. The **repo-root**
  `pipeline.py` (different folder, older/generic setup) has a complete,
  correct 6-step template — adapt that for `bulgaria_rila_pirin/`, pointing
  at `bulgaria_rila_pirin/config.yml` and run from within that folder.
- `bulgaria_rila_pirin/inputs/dem/mask.tif` exists on disk but hasn't been
  verified as the real rasterized AOI mask (vs. a stale/empty artifact) —
  check with `gdalinfo` before trusting it as `clustering_mask`.

### Next session — pick up here
1. Copy `ds_param.nc` from Drive into `bulgaria_rila_pirin/outputs/` on
   this machine.
3. Verify `mask.tif`.
4. Write `bulgaria_rila_pirin/pipeline.py`.
5. Then proceed with the original checklist below.

## Status update — 2026-08-06

- The repository was reset back to the remote `main` branch after the earlier GitHub push failure caused by large geodata files.
- The large local DEM/NetCDF artifacts remain on disk for local use, but they are now covered by the repository ignore rules so they will not be pushed accidentally again.
- The current one-day test run is configured to use 20 CPU cores for the downscaling step, as defined in the project config.
- The next step is to keep the workflow changes small and commit only non-data files such as the progress log, Docker config, and any scripts or notes.
- The one-day trial will start with the 25 m DEM and the 2023-03-15 test date, then we will inspect the outputs before deciding whether to switch to the 10 m DEM.
- ERA5 download and yearly merge have now succeeded after creating the writable yearly climate output directory inside the container.
- The climate forcing files are now present at `bulgaria_rila_pirin/inputs/climate/daily/` and `bulgaria_rila_pirin/inputs/climate/yearly/`.
- The next command to continue the workflow beyond ERA5 is:

```bash
cd /home/icam_labs/nandhni/downscaling-topopyscale-project/rila-pirin-downscaling
docker compose exec -T toposcale bash -lc '
cd /app &&
python - <<'"'"'PY'"'"'
from TopoPyScale import topoclass as tc
mp = tc.Topoclass("/app/bulgaria_rila_pirin/config.yml")
mp.compute_dem_param()
mp.extract_topo_param()
mp.compute_solar_geometry()
mp.compute_horizon()
mp.downscale_climate()
mp.to_netcdf()
print("Pipeline finished")
PY
'
```

- While that long run is in progress, the easiest monitoring command is:

```bash
cd /home/icam_labs/nandhni/downscaling-topopyscale-project/rila-pirin-downscaling
find bulgaria_rila_pirin/outputs -maxdepth 2 -type f 2>/dev/null | sort
```

- If the outputs folder stays empty for a while, that means the heavy terrain/horizon/downscaling part is still running; once files appear, we can inspect them and decide whether to keep the 25 m DEM baseline or switch to the 10 m DEM for the next round.
- Follow-up from the latest run: the workflow had already produced a large set of artifacts before the shell was terminated, including `outputs/output.nc`, `outputs/era5_t2m_2023-03-15_resampled_25m.tif`, `outputs/t_2023-03-15_hourly_25m.tif`, and many `outputs/downscaled/down_pt_*.nc` files. This confirms the downscaling stage reached a successful output-writing state.
- The next useful inspection command is:

```bash
cd /home/icam_labs/nandhni/downscaling-topopyscale-project/rila-pirin-downscaling
python - <<'PY'
import os
from netCDF4 import Dataset
for path in ['bulgaria_rila_pirin/outputs/output.nc', 'bulgaria_rila_pirin/outputs/downscaled/down_pt_000.nc']:
    if os.path.exists(path):
        ds = Dataset(path)
        print(path)
        print(list(ds.variables.keys()))
        print(ds.dimensions)
PY
```

## Session update — 2026-08-06 (continued, Claude Code session)

**Goal for this session**: get the full 2023-03-15 one-day pipeline to a finished downscaled air-temperature result within a few hours, unattended if needed.

### Found: a full pipeline run already in progress
On starting this session, a pipeline (the exact command block above) was already
running inside the container (started ~15:21, PID 46883 on host / 344 in
container), using ~110% CPU and ~4.3GB RSS, but with `bulgaria_rila_pirin/outputs/downscaled/`
and `outputs/tmp/` both still empty after 10 minutes.

### Bug #1 found and fixed: `ds_param.nc` cache was in the wrong folder
- `bulgaria_rila_pirin/inputs/mask/ds_param.nc` (161MB, dated 14:19, i.e. from
  earlier this session/day) turned out to be a **valid, complete cached
  `compute_dem_param()` result** — verified via `xarray`: dims `(y: 5079, x: 4797)`,
  elevation range 48.28m–2905.02m, variables `elevation/slope/aspect/aspect_cos/aspect_sin/svf`.
  This matches this project's DEM exactly (per the "Decisions made so far" section above).
- TopoPyScale's actual cache-lookup path (confirmed by reading
  `topoclass.py` inside the installed package) is
  `<project.directory>/outputs/ds_param.nc` (from `config.yml`'s
  `outputs.directory: outputs` + `outputs.file.ds_param: ds_param.nc`) —
  **not** `inputs/mask/`. Because the file was in the wrong place, the
  already-running pipeline was silently ignoring the cache and recomputing
  `compute_dem_param()` (the slow SVF-over-24M-pixels step) from scratch.
- **Fix applied**: killed the in-progress run (`kill -TERM` on the container
  python process; confirmed gone), then `mv`'d the file to
  `bulgaria_rila_pirin/outputs/ds_param.nc`. Relaunched the pipeline — log
  confirmed `---> Dataset ds_param.nc found.`, i.e. `compute_dem_param()` was
  correctly skipped on the second run.

### Bug #2 found and fixed: `mask.tif` bounds didn't exactly match the DEM
- The relaunched run immediately failed inside `extract_topo_param()` →
  `extract_topo_cluster_param()` with:
  ```
  ValueError: The GeoTIFFS of the DEM and the MASK need to have the same bounds/resolution.
  mask bounds: (649842.729, ...) | dem bounds: (649842.7286942997, ...)
  ```
  i.e. `inputs/dem/mask.tif` (the file flagged as "not yet verified" in the
  checklist above) had bounds rounded to 3 decimal places (from however it was
  originally rasterized — likely a `gdal_rasterize -te` call with manually
  copy-pasted, rounded corner coordinates), while the DEM's real affine
  transform carries full floating-point precision. The mismatch was only
  ~0.0007m (well under a millimeter) but TopoPyScale's check is an exact `==`
  comparison, so it failed hard.
- **Fix applied**: backed up the old file to `inputs/dem/mask.tif.bak`, then
  wrote `bulgaria_rila_pirin/fix_mask.py` (uses `rasterio` to read the DEM's
  exact `transform`/`shape`/`crs` and `rasterio.features.rasterize` the AOI
  GeoJSON — `inputs/mask/rila_pirin_PERFECT_RECTANGLE_32634.geojson` — directly
  onto that grid, guaranteeing byte-identical bounds/resolution by
  construction rather than by re-deriving numbers). Verified after
  regeneration: `bounds match exactly: True`, `resolution match: True`, and
  the AOI pixel count (13,945,288) matches the figure already recorded above
  ("~13.95M inside mask AOI") — confirms the new mask is correct, not just
  bounds-compatible.
- Relaunched the pipeline a third time (this is the run currently in
  progress/being monitored — see below).

### Process notes for future sessions
- **Kill a stuck/wasteful run**: find the container-internal PID with
  `docker compose exec -T toposcale bash -lc 'for p in /proc/[0-9]*; do ...
  cat $p/cmdline; done'` (no `ps`/`pgrep` inside this container image), then
  `kill -TERM <pid>` inside the same `docker compose exec`.
- **Always place cache files under `bulgaria_rila_pirin/outputs/`**, matching
  `config.yml`'s `outputs.directory`/`outputs.file.*` keys exactly — not under
  `inputs/`.
- If `extract_topo_param()` ever complains about mask/DEM bounds again (e.g.
  after regenerating either file), rerun `fix_mask.py` rather than
  hand-rolling `gdal_rasterize -te/-tr` flags — exact float equality is
  required, and copy-pasted/rounded corner coordinates will not satisfy it.
- Current pipeline log (this session): `pipeline_run2.log` in the Claude Code
  scratchpad dir (not in the repo). A background `Monitor` is watching it for
  progress markers, warnings, errors, and the final `Pipeline finished` line.

### Bug #3: pandas frequency string `1H` no longer valid
- 3rd attempt got through `compute_dem_param()` (cache hit) and
  `extract_topo_param()` (bounds check passed after the mask fix), but failed
  entering `compute_solar_geometry()` with
  `ValueError: Invalid frequency: H ... Did you mean h?` — the installed
  pandas version deprecated the uppercase `'H'` hourly-frequency alias.
- Root cause traced to `config.yml`'s `climate.era5.timestep: 1H`, which
  `topoclass.py` passes straight through to `pd.date_range(freq=tstep)` in
  **multiple** places (`solar_geom.get_solar_geom`, `compute_horizon`,
  `downscale_climate` — confirmed via `grep` on `topoclass.py`, lines
  533/549/626/674/697), so this would have recurred at every one of those
  steps if not fixed centrally.
- **Fix**: changed `config.yml` `timestep: 1H` → `timestep: 1h` (lowercase).
  4th attempt got past solar geometry (`ds_solar.nc` saved) and into
  `compute_horizon()` cleanly.

### Bug #4: `compute_horizon()` succeeded; `downscale_climate()` looked for a Zarr store that doesn't exist
- 4th attempt: `compute_horizon()` completed and cached
  (`outputs/da_horizon.nc`, ~7GB, 10° azimuth increments), then
  `downscale_climate()` immediately failed:
  `FileNotFoundError: /app/bulgaria_rila_pirin/inputs/climate/ERA5.zarr does not exist`.
- Root cause: `config.yml` had `climate.era5.zarr_store: ERA5.zarr`. Reading
  `topoclass.py`'s `downscale_climate()` (~line 664-705) showed **two
  branches**: `zarr_store is None` → uses the legacy/standard
  `topo_scale.downscale_climate()` which reads plain NetCDF files directly;
  `zarr_store is not None` → uses the newer `ClimateDownscaler` class, which
  requires a **pre-built Zarr store** at `climate.path/<zarr_store>` — we
  never built one, we only have the plain NetCDF ERA5 files from the CDS
  download/merge step.
- **Fix**: set `climate.era5.zarr_store: null` in `config.yml` to route
  through the NetCDF-native path instead. (Note: there's a second,
  unrelated `zarr_store: down.zarr` key under `outputs.file` — that one is
  for output storage format, not touched.)

### Bug #5: `downscale_climate()` (NetCDF path) globs for files in the wrong subfolder
- 5th attempt: all four expensive steps loaded from cache correctly
  (`ds_param.nc`, `df_centroids.pck`, `ds_solar.nc`, `da_horizon.nc` all
  logged as "exists and loaded") — confirms bugs #1-4's fixes all hold.
  `downscale_climate()` started but immediately raised
  `OSError: no files to open`.
- Root cause: `topo_scale.py`'s `downscale_climate()` does
  `glob.glob(f'{climate_directory}/PLEV*.nc')` and same for `SURF*.nc`,
  directly in `climate_directory` (which `topoclass.py` resolves to
  `<project.directory>/inputs/climate/`, confirmed via `grep` — line ~96).
  Our merged yearly ERA5 files are one level down, at
  `inputs/climate/yearly/PLEV_2023.nc` / `SURF_2023.nc` (that's where the
  ERA5 fetch/merge step from earlier in this session put them) — so the glob
  in the climate root found nothing.
- **Fix**: symlinked the yearly files up into the climate root (non-destructive,
  originals untouched):
  ```bash
  cd /app/bulgaria_rila_pirin/inputs/climate
  ln -sf yearly/PLEV_2023.nc PLEV_2023.nc
  ln -sf yearly/SURF_2023.nc SURF_2023.nc
  ```
- 6th attempt launched with this fix; in progress — see status below.

### Status as of this edit
All five bugs found so far were dependency-version drift / path-convention
mismatches between this installed TopoPyScale version and its dependencies
(pandas frequency aliases) or between the ERA5 fetch step's output layout and
`downscale_climate()`'s expected input layout — not fundamental issues with
the DEM/mask/config design. All expensive terrain steps
(`compute_dem_param`, `compute_horizon`) are cached and reused correctly on
every relaunch, so each fix-and-retry cycle from here on is cheap (minutes,
not hours). 6th attempt is running now with all five fixes in place; a
background monitor is watching `pipeline_run5.log` for the next error or the
final `Pipeline finished` line. Next step once finished: open the output in
`outputs/downscaled/`, extract the `t` (air temperature) variable for
2023-03-15, sanity-check it (plausible range/lapse-rate pattern across
elevation), then decide 25m vs 10m DEM for the next, larger run.

### Bug #6: fix for bug #3 broke a *different* hardcoded case-sensitive lookup
- 6th attempt: all cached steps loaded correctly again, `downscale_climate()`
  started processing all 500 points (`Preparing plev/surf for point NNN`
  logged for every centroid), got all the way to ~point 461 of 500 doing the
  actual per-point downscaling (`Downscaling t,q,p,tp,ws,wd for point: NNN`),
  then crashed inside the multiprocessing pool:
  `TypeError: unsupported operand type(s) for /: 'float' and 'NoneType'` at
  `topo_scale.py:187` (`down_pt['tp'] = ... / meta.get('tstep') * ...`).
- Root cause: **this session's own fix for bug #3** (lowercasing
  `config.yml`'s `timestep: 1H` → `1h` to satisfy `pd.date_range`) broke a
  *second*, unrelated piece of the same library that assumes the old
  uppercase alias: `topo_scale.py:389` has
  `tstep_dict = {'1H': 1, '3H': 3, '6H': 6}` (hardcoded, case-sensitive), and
  `meta['tstep'] = tstep_dict.get(tstep)` — with `tstep = '1h'`, this lookup
  silently returned `None` (dict `.get()` doesn't raise on a miss), which
  then divided a float by `None` down the line. Two parts of the installed
  TopoPyScale disagree on case convention for the exact same config value —
  one needs lowercase for modern pandas (`pd.date_range(freq=...)` rejects
  uppercase `'H'`), the other hardcodes uppercase.
- **Fix**: patched the installed package directly (not a repo file — this
  lives in the venv/container image, at
  `/usr/local/lib/python3.13/site-packages/TopoPyScale/topo_scale.py:389`)
  to accept both cases:
  `tstep_dict = {'1H': 1, '3H': 3, '6H': 6, '1h': 1, '3h': 3, '6h': 6}`.
  Verified the *other* use of this same integer value later in the same file
  (`topo_scale.py:255`, `pd.Timedelta(f"{meta.get('tstep')}H").seconds`, used
  in the longwave-radiation downscaling step) still works fine even with the
  literal `"H"` suffix — `pd.Timedelta` (unlike `pd.date_range`'s `freq=`)
  still accepts the uppercase alias, just with a deprecation warning, not a
  hard error — so no further case-mismatch expected from this angle.
- **Note for future sessions**: this patch is inside the Docker image's
  installed package, not tracked by git. If the image is ever rebuilt from
  the `Dockerfile` (not just restarted), this one-line patch will be lost
  and bug #6 will resurface at the ~90%-through-500-points mark of
  `downscale_climate()`. If that happens, reapply the same `sed` fix to
  `topo_scale.py` line ~389, or (cleaner long-term) fork/patch the
  TopoPyScale source properly and rebuild.
- 7th attempt launched with this patch in place — in progress, see status.

### Status as of this edit
Six bugs found and fixed this session, all dependency-version drift /
internal-inconsistency issues in the installed TopoPyScale + its
dependencies (pandas, zarr, xarray) — not fundamental issues with the
DEM/mask/config/data itself. All four expensive terrain/climate-prep steps
(`compute_dem_param`, `compute_solar_geometry`, `compute_horizon`, and the
per-point `plev`/`surf` extraction) are cached/idempotent and have been
confirmed to reuse correctly across every relaunch, so each fix-and-retry
cycle is now cheap. 7th attempt is running now with all six fixes in place;
background monitor is watching `pipeline_run6.log`. Next step once finished:
open the output in `outputs/downscaled/`, extract the `t` (air temperature)
variable for 2023-03-15, sanity-check it (plausible range/lapse-rate pattern
across elevation), then decide 25m vs 10m DEM for the next, larger run.

### PIPELINE COMPLETED SUCCESSFULLY — 2026-08-07
7th attempt ran clean end to end: `downscale_climate()` finished in 2233s
(~37 min) processing all 500 cluster centroids × 24 hourly timesteps ×
`t,q,p,tp,ws,wd` + `LW,SW` radiation, then `to_netcdf()` wrote the final
merged result to **`bulgaria_rila_pirin/outputs/output.nc`** (361KB).
`Pipeline finished` printed; no traceback.

**Output structure**: `xarray.Dataset`, dims `(point_name: 500, time: 24)`,
covering the full 2023-03-15 hourly cycle. Variables: `t, u, v, q, p,
precip_lapse_rate, tp, ws, wd, theta_neg-derived wind dir, cse, LW,
SW_diffuse, SW_direct, SW, cos_illumination` (17 total) — i.e. air
temperature, wind, humidity, pressure, precipitation, and full
shortwave/longwave radiation budget, not just `t`.
Per-point files also retained in `outputs/downscaled/down_pt_*.nc` (500
files, pre-merge).

**Air temperature (`t`) sanity check — 2023-03-15**:
- Kelvin range across all 500 points × 24h: 268.57–284.25 K (**-4.58°C to
  +11.10°C**), mean 3.35°C.
- **Elevation vs. daily-mean-temp correlation: -0.98** (near-perfect inverse
  relationship, as physically expected).
- **Fitted environmental lapse rate: -5.40°C/km** — within the physically
  expected range (~-5 to -9°C/km) for a real atmospheric profile, not an
  artifact.
- Lowest-elevation points (~200-235m, e.g. point 034 at 203.8m): ~8.6-8.8°C
  daily mean.
- Highest-elevation points (~2320-2370m, e.g. point 250 at 2368.5m, near
  Musala/Rila peaks): ~-2.4 to -2.6°C daily mean.
- **Conclusion: the downscaled result is physically sound**, not just
  in-range by coincidence — cold-high/warm-low gradient matches the DEM
  exactly as it should.

**End-to-end pipeline validated for this test case.** All 6 bugs found this
session (see above) are now documented with fixes; 3 are in `config.yml`
(tracked in git), 1 is a mask-file regeneration (`fix_mask.py`, tracked),
1 was a misplaced cache file (moved, not a code change), and 1 (`tstep_dict`
patch) lives in the container image only — **remember to reapply that patch
if the Docker image is ever rebuilt** (see Bug #6 section above for the
exact `sed` command).

### Next decision point: 25m vs 10m DEM for the full run
This 25m/single-day/500-cluster test is now a working, validated reference
run. Before committing to the full multi-year run, decide:
- Whether the 25m DEM's terrain resolution is adequate for the thesis's
  spatial-detail needs, or whether the newer 10m DEM is worth the (likely
  much larger) `compute_dem_param()`/`compute_horizon()` runtime cost —
  those two steps scale with pixel count, and 10m vs 25m is ~6.25× more
  pixels for the same area.
- Not yet decided — pending discussion with the user.

### Bugs #7/#8: pandas 3.0 breaks the per-pixel cluster-grid write-back (does NOT affect the actual downscaled results)
- Goal: "paint back" the 500-cluster downscaled `t` values onto the full
  25m/13.9M-pixel grid, for visualization/GIS use. TopoPyScale stores a
  per-pixel `point_name`/`cluster_labels` grid inside `ds_param.nc`
  specifically for this purpose (built by `extract_topo_cluster_param()`).
  On inspection, that grid was **entirely `-9999`** (i.e. "no cluster") for
  every single pixel, and the `mask` variable stored in the same file was
  also wrong (all `1`, not the correct 13,945,288/24,364,563 AOI split).
- **Important distinction**: this does **not** affect the validated
  downscaled temperature results from the "PIPELINE COMPLETED SUCCESSFULLY"
  section above. `output.nc` and `df_centroids.pck` are built from a
  different code path (`.groupby()`-based aggregation, not the buggy
  write-back) and remain correct — confirmed again below.
- **Bug #7 root cause**: `topoclass.py`'s `extract_topo_cluster_param()`
  (~line 445, was) did a **chained assignment**:
  `df_param['point_name'].loc[~df_param.cluster_labels.isnull()] = ...`.
  Under pandas ≥3.0 (mandatory Copy-on-Write — this environment has pandas
  **3.0.5**), `df[col].loc[mask] = value` silently writes to a throwaway
  copy and never updates the real dataframe (raises
  `ChainedAssignmentError` as a *warning*, not a hard failure, so the
  pipeline just silently kept going with `point_name` stuck at its `'-9999'`
  default for every row). **Fixed** in the installed package (same
  container-image-only caveat as bug #6) by rewriting as a single
  non-chained `.loc[row_indexer, col_indexer]` assignment:
  ```python
  # before (broken under pandas 3.0 CoW):
  df_param['point_name'] = '-9999'
  df_param['point_name'].loc[~df_param.cluster_labels.isnull()] = df_param.cluster_labels.loc[~df_param.cluster_labels.isnull()].astype(int).astype(str).str.zfill(n_digits)
  # after:
  df_param['point_name'] = '-9999'
  not_null = ~df_param.cluster_labels.isnull()
  df_param.loc[not_null, 'point_name'] = df_param.loc[not_null, 'cluster_labels'].astype(int).astype(str).str.zfill(n_digits)
  ```
- **Bug #8, hit while re-running to verify the bug #7 fix**: re-invoking
  `extract_topo_cluster_param()` directly (the wrapper `extract_topo_param()`
  skips it entirely once `df_centroids.pck` exists — cost ~326s for
  Mini-Batch K-means on the masked population, not hours) hit a *second*,
  unrelated pandas 3.0 issue at
  `df_param.loc[subset_mask, 'cluster_labels'] = cluster_labels + i_clusters`:
  `TypeError: Invalid value for dtype 'str'` — pandas 3.0's new
  Arrow-backed default string dtype rejects assigning integers into a column
  it inferred as string. This confirms the installed TopoPyScale (0.3.3)
  has **multiple**, not just one, incompatibilities with pandas 3.0 in this
  code path.
- **Decision: did not keep patching `extract_topo_cluster_param()` bug by
  bug.** Instead, wrote a standalone script,
  `bulgaria_rila_pirin/paint_back.py`, that reproduces the pixel→cluster
  assignment **independently**, without touching TopoPyScale's buggy
  write-back code at all:
  1. Fit `sklearn.preprocessing.StandardScaler` on the full AOI-masked pixel
     population's clustering features (`x, y, elevation, slope, aspect_cos,
     aspect_sin, svf` — same features/weights as `config.yml`'s
     `clustering_features`), using the **known-good** grids in `ds_param.nc`
     (only the `mask`/`cluster_labels`/`point_name` variables were
     corrupted — `elevation`/`slope`/`aspect_*`/`svf` were computed by
     `compute_dem_param()` and are untouched by this bug) + `mask.tif`.
  2. Transform the 500 existing centroids (`df_centroids.pck.bak`, backed up
     before any of this session's re-clustering attempts — the file that
     produced the validated `output.nc` results) into the same scaled space.
  3. `scipy.spatial.cKDTree` nearest-centroid lookup for every one of the
     13,945,288 AOI pixels — this **is** what k-means cluster assignment
     means (nearest centroid in scaled feature space), so it reproduces the
     same logical assignment without re-running/depending on the buggy
     library call.
  4. Look up each pixel's assigned point's downscaled `t` value (all 24
     hourly timesteps) from `output.nc` and write a 24-band GeoTIFF.
- **Verification performed** (both passed cleanly):
  - Value-range check: painted raster's valid-pixel min/max (268.572–284.249 K)
    matches `output.nc`'s own min/max (268.57236–284.24861 K) to
    float32 precision — confirms no value corruption/mislookup.
  - Pixel-count check: exactly 13,945,288 valid pixels painted — matches the
    mask's AOI pixel count exactly, no leakage/gaps.
  - **Per-pixel spatial correlation, elevation vs. mean-temperature raster:
    -0.93** across all 13.9M pixels (stronger/more granular confirmation
    than the earlier 500-point-level check, which was -0.98) — physically
    coherent, not a coincidence.
- **Output**: `bulgaria_rila_pirin/outputs/t_2023-03-15_hourly_25m.tif` —
  24-band GeoTIFF (one band per hour of 2023-03-15), EPSG:32634, 25m,
  aligned exactly to the DEM, nodata=-9999 outside the AOI mask, LZW
  compressed. Band descriptions carry the ISO timestamp for each hour.
- **Note for future sessions**: `df_centroids.pck.bak` (backup taken before
  the bug #7/#8 investigation) is the file this raster and `output.nc` are
  both consistent with — don't delete it. `ds_param.nc`'s own
  `mask`/`cluster_labels`/`point_name` variables remain broken/unfixed (not
  worth patching further given bug #8 showed more issues lurk in that code
  path) — **use `paint_back.py`'s approach, not `ds_param.nc`'s own grid
  variables**, for any future "paint back to raster" need for other
  variables (`q`, `SW`, `LW`, etc. — just change the `variable` constant at
  the top of the script).

## Session update — 2026-08-07 (continued): meteo station validation prep begins

**Goal**: validate the downscaled output against real station observations
(METER.AC network). User added `ex1_norway_finse/` to the repo as a
reference example of TopoPyScale's own point-based validation workflow —
used it to understand the target methodology before starting.

### Confirmed: current pipeline uses classic multicore, NOT dask+zarr
Checked `config.yml` (`parallelization.downscaling_method: multicore`,
`climate.era5.zarr_store: null`) against `ex2_romania_retezat/config.yml`
(`downscaling_method: dask`, `zarr_store: ERA5.zarr`) — confirmed we
deliberately fixed onto the classic NetCDF+multiprocessing path (bug #4,
earlier in this file) rather than the newer dask/Zarr `ClimateDownscaler`
path Retezat uses. Working correctly as-is; dask/Zarr may be worth revisiting
for the eventual full multi-year run (lazier/chunked handling scales better),
but not needed for this single-day validation.

### Norway example (`ex1_norway_finse/`) — validation methodology learned
- `inputs/dem/station_list.csv`: `Name, stn_number, latitude, longitude, x, y`
  — `x`/`y` must be in the **DEM's projected CRS** (confirmed by reading
  `topoclass.py`'s `extract_pts_param()`: the CSV is read from
  `<project.directory>/inputs/dem/<csv_file>`, each row snapped to nearest
  DEM pixel via `tp.extract_pts_param(..., method='nearest')`).
- `config_point.yml`: separate config from the main clustering one, with
  `sampling.method: points` + `sampling.points.csv_file: station_list.csv`
  (optionally `sampling.points.name_column` to pick which CSV column becomes
  `point_name` — defaults to zero-padded row index if omitted). Downscales
  climate **directly at** each station's coordinates instead of at cluster
  centroids.
- `pipeline_point.py`: same 6-step pipeline, just pointed at
  `config_point.yml`.
- `compare_downscaling_to_observation.ipynb`: loads obs (MET Norway API
  pickles, long-format `sourceId/referenceTime/elementId/value`, pivoted to
  a wide `xarray.Dataset`), loads downscaled points
  (`xr.open_mfdataset('down_pt*.nc', concat_dim='point_id', ...)`), aligns
  time ranges, **resamples observations to the downscaled hourly timestep**
  (`.resample('1H').mean()` — mean-only since MET Norway data didn't need
  min/max) per station/variable, then plots scatter+regression (1:1 line)
  and time series + bias. TopoPyScale's own `topo_obs.py` module only has
  MET-Norway-Frost-API and WMO loaders — nothing METER.AC-specific, so the
  fetch/parse side is ours to build, but this comparison methodology is
  fully reusable.

### METER.AC fetch — run for real this session (previously only scripted, never executed)
Ran `meteo-data.py --mask inputs/mask/rila_pirin_PERFECT_RECTANGLE_32634.geojson --outdir data/meterac`
inside the container (network egress confirmed working; ran in background,
took a few minutes due to the deliberate 1.5s/node rate-limit delay).
- **Network total: 229 nodes** (was 228 when last checked pre-session — one
  new node registered since). **29 fall inside the AOI — reconfirmed, count
  unchanged.**
- All 29 nodes' raw 5-min history downloaded to
  `bulgaria_rila_pirin/data/meterac/<NodeID>_history_raw.txt` (~470MB total,
  1–35MB per station reflecting multi-year 5-min histories), plus
  `selected_stations_metadata.csv` (NodeID, Location, Altitude, Latitude,
  Longitude, sensor model info).
- **Ran the full coverage summary** (`meteo_data_convert.py --no-csv` across
  all 29 raw files) — this replaces the old "4 of 29 checked" state with
  complete coverage data for every node:

  ```
  node                     start                       end  n_records  avg_interval_min
   N48 2018-11-24 13:32:04+00:00 2023-08-16 19:07:48+00:00     486731               5.1
  N303 2019-01-26 15:51:53+00:00 2026-08-07 12:44:50+00:00     689242               5.7
   N32 2019-02-17 12:19:20+00:00 2026-08-07 12:41:04+00:00     698088               5.6
  N306 2019-08-23 08:45:26+00:00 2024-04-03 23:25:24+00:00     320587               7.6
  N221 2020-03-14 09:46:32+00:00 2026-08-07 12:41:21+00:00     644226               5.2
  N110 2020-05-09 15:26:05+00:00 2026-08-07 12:31:56+00:00     461408               7.1
  N155 2020-08-05 14:07:25+00:00 2026-08-07 12:43:10+00:00     528360               6.0
  N081 2020-08-06 14:00:55+00:00 2026-08-07 12:43:50+00:00     541683               5.8
  N164 2020-08-08 13:04:33+00:00 2026-08-03 18:29:26+00:00     324024               9.7
  N108 2020-12-06 16:09:45+00:00 2026-08-07 12:40:15+00:00     580898               5.1
  N058 2020-12-26 14:25:14+00:00 2026-08-07 12:43:04+00:00     250352              11.8
  N097 2021-02-12 10:47:19+00:00 2026-08-07 12:41:49+00:00     117727              24.5
  N122 2021-02-26 14:00:34+00:00 2026-08-07 12:42:50+00:00     255847              11.2
  N067 2021-02-26 15:36:03+00:00 2026-08-07 12:42:53+00:00     475502               6.0
  N142 2021-04-17 17:38:50+00:00 2026-08-07 12:42:00+00:00     537778               5.2
  N235 2021-04-18 15:26:52+00:00 2026-08-07 12:43:34+00:00     463131               6.0
  N096 2021-06-20 12:48:05+00:00 2025-04-06 19:28:26+00:00      19313             103.4
  N098 2021-12-05 06:50:38+00:00 2026-03-23 03:34:49+00:00     342488               6.6
  N072 2022-03-24 10:03:07+00:00 2026-08-07 12:37:52+00:00     381232               6.0
  N094 2022-07-17 12:42:33+00:00 2023-07-01 06:47:19+00:00      47555              10.6
   N27 2022-10-25 10:33:11+00:00 2026-08-07 12:40:45+00:00     332277               6.0
  N349 2023-01-28 14:16:10+00:00 2026-08-07 03:34:08+00:00     142451              13.0
  N313 2023-07-11 05:54:36+00:00 2026-08-07 12:45:48+00:00     310133               5.2
  N175 2023-08-10 04:30:20+00:00 2025-11-02 09:54:26+00:00      22535              52.1
  N323 2024-12-21 11:30:57+00:00 2026-08-07 12:41:48+00:00     163286               5.2
  N141 2025-08-05 07:11:08+00:00 2026-08-07 12:44:16+00:00      94424               5.6
  N321 2026-01-25 10:15:02+00:00 2026-08-07 12:45:52+00:00      50277               5.6
  N330 2026-05-10 13:44:55+00:00 2026-08-07 12:45:52+00:00      25181               5.1
  ```

- **N211 (Makedonia_Hut, 2165m) has zero data ever** — raw file is just the
  header row, no records. Registered node, never actually reported.
  Exclude from validation.
- **22 of 29 stations cover 2023-03-15** (start ≤ date ≤ end): N48, N303,
  N32, N306 (Musala), N221, N110, N155, N081, N164, N108, N058, N097, N122,
  N067, N142, N235 (Boboshevo), N096, N098, N072, N094, N27 (Semkovo), N349.
- **6 stations don't cover it** — all started after 2023-03-15 (never
  existed yet at test-date time): N313 (Jul 2023), N175 (Aug 2023), N323
  (Dec 2024), N141 (Aug 2025), N321 (Jan 2026), N330 (May 2026).
- **Flag for the hourly-aggregation step**: N096 (Lovna_Hut) has an average
  reporting interval of 103 minutes (vs. ~5-6 min for most others) despite
  nominally covering the date range — its 5-min reporting is very
  intermittent, so "covers the date range" does **not** guarantee it
  actually has data specifically on 2023-03-15. Must verify per-station,
  per-day data presence directly (not just start/end coverage) before
  finalizing the validation station list — this is the next step, along
  with building the actual 5-min→hourly min/max/mean aggregation.

### CORRECTION: per-day check on 2023-03-15 overturns 5 of the 22 "covering" stations
Checking overall start/end range is not the same as having actual data on
the specific test date — verified this directly (row counts specifically
within 2023-03-15 00:00–24:00 UTC, plus a wider Feb15–Apr15 2023 window to
see gap shape) and found real, substantial outages:
- **N306 (Musala, 2925m) — ZERO rows for the entire Feb15–Apr15 2023
  window.** This directly contradicts this file's own earlier note
  ("Musala (N306): 2019-08-23 → 2024-04-03" was based only on first/last
  record overall, not continuous coverage). Likely a winter
  power/connectivity outage at the summit — losing this station is a real
  loss for validating the cold/high-elevation end of the lapse rate, but
  the data simply isn't there.
- **N058 — ZERO rows for the entire Feb15–Apr15 2023 window.**
- **N096 — ZERO rows for the entire Feb15–Apr15 2023 window** (consistent
  with its already-flagged sparse ~103min average interval).
- **N27 (Semkovo) — has data, but stops 2023-03-12**, 3 days before the
  test date, doesn't resume within the checked window. Also contradicts
  this file's earlier "Semkovo (N27): 2022-10-25 → still live" note (again,
  that was overall range only).
- **N094 — has data, but only through 2023-02-21**, over 3 weeks before
  the test date.
- **The other 17 of the 22 candidates were confirmed to have real,
  complete hourly-ish coverage (259-288 5-min records) specifically on
  2023-03-15**: N48, N303, N32, N221, N110, N155, N081, N164, N108, N097,
  N122, N067, N142, N235 (Boboshevo), N098, N072, N349.

**Final validation station set for 2023-03-15: these 17 stations.**

### Hourly aggregation + station_list.csv — done
Wrote `bulgaria_rila_pirin/meteo_hourly_agg.py`: for each of the 17
confirmed stations, resamples the 2023-03-15 5-min `T [deg C]` readings to
hourly `min`/`max`/`mean`/`n_obs` (`<NodeID>_hourly_2023-03-15.csv`, plus a
combined `all_stations_hourly_2023-03-15.csv`, both in
`data/meterac/`, gitignored same as the raw data). Also builds
`bulgaria_rila_pirin/inputs/dem/station_list.csv` (tracked — small,
lat/lon/x/y/elevation only, no bulk data) in the Norway-example format
(`Name, stn_number, latitude, longitude, x, y` + an extra `elevation_m`
column), reprojecting lat/lon → EPSG:32634 via geopandas to match the DEM's
CRS, as required by `topoclass.py`'s `extract_pts_param()`.
- 15 of 17 stations: full 24/24 hourly coverage on 2023-03-15.
- N098 (Malyovitsa_Hut): 21/24 hours (gaps within the day, not a full
  outage — fine to use, just drop the missing hours during comparison).
- N349 (Bodrost): 14/24 hours (same — partial-day gaps, still usable).
- Verified all 17 stations' `x`/`y` fall correctly within the DEM's
  bounds (649842–769767 / 4575636–4702611 in EPSG:32634).

### Remaining steps for meteo validation (updated checklist)
- [x] Fetch all 29 in-AOI METER.AC nodes' metadata + raw history.
- [x] Full coverage-window check for all 29 (was 4/29) — see table above.
- [x] Verify actual per-day data presence on 2023-03-15 — corrected the
  candidate list from 22 down to 17 real usable stations (see above).
- [x] Aggregate 5-min → hourly `min`/`max`/`mean` temperature per station.
- [x] Build `station_list.csv` (METER.AC node IDs, lat/lon, DEM-projected
  x/y in EPSG:32634) in the format TopoPyScale's `sampling.points` expects.
- [ ] Create `config_point.yml` variant + run point-sampling pipeline to get
  downscaled values *at* station locations.
- [ ] Port Norway notebook's comparison methodology (resample-aligned
  scatter/regression + time series/bias plots) to METER.AC variables.

### QGIS comparison deliverables (input ERA5 vs. downscaled output)
Added for visual/quantitative comparison in QGIS, all in
`bulgaria_rila_pirin/outputs/`:
- `era5_t2m_2023-03-15_hourly.tif` — raw ERA5 2m air temperature (`t2m` from
  `inputs/climate/yearly/SURF_2023.nc`), 24-band (hourly), native ERA5 grid
  (8×10 cells, ~0.25°, EPSG:4326). Exported via `gdal_translate` from the
  `NETCDF:...:t2m` GDAL subdataset.
- **Sanity-checked the raw grid by hand** (band 1, 2023-03-15 00:00 UTC)
  after it looked like a "checkerboard" in QGIS's default symbology — the
  actual numbers are a smooth, physically coherent field (cold pocket down
  to -1.2°C right over the Rila-Pirin massif, surrounded by warmer
  lowland/coastal values up to +8.1°C toward the south). **Not a bug** — the
  checkerboard appearance was a symbology artifact (diverging ramp
  auto-stretched to a tight ~9°C range across only 8×10 giant cells makes
  small real steps look like strong color flips). No fix needed, just use a
  wider/continuous classification when eyeballing it.
- `era5_t2m_2023-03-15_resampled_25m.tif` — the above, `gdalwarp`'d (bilinear)
  onto the **exact same grid** as the downscaled output (same
  extent/resolution/CRS, EPSG:32634, 25m) — needed so ERA5 and the
  downscaled raster are pixel-for-pixel comparable, not just visually
  overlaid with on-the-fly reprojection.
- `era5_t2m_2023-03-15_resampled_25m_nearest.tif` — same as above but
  `gdalwarp -r near` instead of bilinear, so the original ~0.25° ERA5 cell
  boundaries stay visually sharp/blocky on the 25m grid (34 distinct values
  across the raster, confirmed) rather than being smoothed — useful for
  visually confirming "these are the real coarse ERA5 cells" rather than a
  continuous interpolation.
- Clarified a QGIS layer-ordering confusion (not a bug): the downscaled
  layer, checked and listed above ERA5 in the Layers panel, is fully opaque
  within the AOI and was visually hiding the ERA5 layer entirely — the fine
  ridge/valley detail the user was seeing was the downscaled layer, not
  ERA5. Verified independently that the resampled ERA5 raster's correlation
  with elevation is a moderate -0.50 (a real but broad regional trend),
  nowhere near the downscaled layer's -0.93 fine terrain-tracking —
  confirms ERA5 data itself is correctly coarse, no bug.
- `diff_downscaled_minus_era5_2023-03-15_hourly.tif` — `downscaled − era5`,
  computed band-by-band (`bulgaria_rila_pirin/diff_raster.py`), nodata
  -9999 outside the AOI mask (matching the downscaled raster). Range:
  **-12.1°C to +8.5°C, mean ~+0.14°C**. Physically sensible: negative
  (downscaled colder) concentrates on ridgelines/peaks where ERA5's coarse
  grid underestimates true elevation-driven cooling; positive (downscaled
  warmer) concentrates in valley floors where ERA5's blurred average
  overestimates elevation. This is the direct visual/quantitative
  demonstration of what the terrain-aware downscaling corrects versus the
  raw reanalysis input.

### Permissions note
Per explicit user instruction this session: proceeding autonomously without
pausing for per-command confirmation until the downscaled result is
obtained; permission scoping will be reconsidered together once results are
in. **Results are now in — revisit permission scoping with the user.**

## Session wrap-up — 2026-08-07: point-sampling setup, alternate data sources researched, git hygiene

### Point-sampling pipeline for station validation — prepared but NOT yet run
- Created `bulgaria_rila_pirin/outputs_points/` (separate from the main
  `outputs/`, so the toposub run's cached files are never touched) and
  seeded it with a copy of the validated `ds_param.nc` (confirmed via
  reading `topo_param.py`'s `extract_pts_param()` that point-sampling only
  reads `elevation/slope/aspect/aspect_cos/aspect_sin/svf` — **not** the
  corrupted `cluster_labels`/`mask`/`point_name` grid variables from bugs
  #7/#8, so reusing this file is safe).
- Wrote `bulgaria_rila_pirin/config_point.yml`: same project/climate/DEM
  settings as the main `config.yml`, but `sampling.method: points`,
  `sampling.points.csv_file: station_list.csv`,
  `sampling.points.name_column: stn_number` (so downscaled point IDs come
  out as the real METER.AC node IDs, e.g. "N48", not an arbitrary index),
  and `outputs.directory: outputs_points`.
- **Not yet executed** — paused before running to first do the full-history
  processing below. **Next session: run this** (`compute_dem_param` will
  cache-hit immediately via the copied `ds_param.nc`; `extract_topo_param`
  routes to `extract_pts_param` for the 17-or-so station points;
  `compute_solar_geometry`/`compute_horizon`/`downscale_climate` all need to
  actually run since these are new point locations, but should be much
  faster than the 500-cluster run given far fewer points — expect minutes,
  not the ~40min the toposub run took for `downscale_climate` alone).

### Full-history processing across all 28 METER.AC stations (superseding the single-day-only aggregation)
User asked to go further than just 2023-03-15: process every station's
**entire** history, not just the test day, and explicitly flag data gaps.
- `bulgaria_rila_pirin/meteo_full_history.py`: for all 28 stations with any
  data (excludes N211), resamples 5-min `T [deg C]` to hourly
  min/max/mean/n_obs across each station's full lifetime, classifying every
  hour as **complete** (≥10/12 nominal 5-min readings — confirmed via the
  metadata CSV that ALL METER.AC nodes share the same nominal 5-min
  `ReportingFrequency`, so 12/hour is a universal, not per-station, baseline),
  **partial** (1-9), or **gap** (0). Wrote per-station
  `<NodeID>_hourly_full_history.csv` + combined
  `all_stations_hourly_full_history.csv` (**1,075,312 rows** total) +
  `station_coverage_summary.csv` (lifetime % complete/partial/gap per
  station) — all in `data/meterac/` (gitignored).
- **Station reliability varies enormously** — this is the headline finding:
  N330 (98.4% complete), N048/N108 (97.4%), N142 (95.9%), N221 (95.5%) are
  excellent; N096 (94.8% *gap*, only 4.7% complete), N097 (78.6% gap), N175
  (88.7% gap), N058 (53.8% gap) are barely functional over their lifetimes.
- `bulgaria_rila_pirin/meteo_overlap_analysis.py`: built a day×station
  coverage matrix (`station_daily_status.csv`) and network-wide daily
  rollup (`network_daily_summary.csv`) classifying each station-day as
  `full` (24/24 complete hours), `usable` (no gap hours, some partial),
  `gappy` (≥1 gap hour), or `no_data` (edge of station's own range).
  **Key finding: across the entire 2018-11-24 to 2026-08-07 history (2,814
  days), no single day ever has more than 16 of the 28 stations
  simultaneously "full"** — and only 2 days ever hit that (2025-09-08,
  2026-07-31); median is 9/28. Best sustained windows: ≥10 stations full
  for 34 consecutive days (2026-02-23 to 2026-03-28); ≥12 for 17 days
  (2025-09-22 to 2025-10-08). **Our chosen test date, 2023-03-15, scores
  11/28 full, 15/28 usable-or-better** — a moderate day, well below what's
  achievable in 2025-2026 windows.
- **Open decision, not yet made**: whether to stick with 2023-03-15 (all
  pipeline debugging already solved for this date) or switch to a
  better-covered window (e.g. Feb-Mar 2026) for stronger station
  validation. Switching costs a new ERA5 download + pipeline rerun, but
  reruns are now fast/proven since every bug this session was fixed.

### Alternate data source research: stringmeteo.com and NIMH (Bulgaria's official met service) — assessed, not pursued for scraping
- Checked `stringmeteo.com` (a ~196-station Bulgarian amateur+official
  network aggregator, found via `other-scripts/stations_list.csv`,
  `scrape_stations.py`, `check_one_station.py` — pre-existing scripts from
  before this session). **Decided against scraping it**: its `robots.txt`
  explicitly disallows `/stations/archive/`, `/stations/archive_key/`,
  `/stations/graphs/` (exactly the historical-data pages needed), and its
  own terms state data is "intended for personal use only" and
  "unsuitable for... scientific research." The pre-existing scripts in
  `other-scripts/` were already scoped compliantly (only the allowed
  `/stations/index.php` listing page and a single station's *current*
  page) — confirms this boundary was already correctly respected before
  this session, nothing to walk back.
  - Manually identified (for the user to browse by hand, which is fine —
    the robots.txt/ToS concern is about automated scraping, not human
    browsing) which stations in/near the AOI are active vs. dead: active —
    Bansko, Boboshevo, Rila Monastery (`rilamon`, standard category, elevation
    ~1147m, **not** covered by any METER.AC node), Samokov-2, Kostenets,
    Pastra, Blagoevgrad-center. **Every genuinely high-alpine hut/peak
    station overlapping the AOI is marked permanently dead**: Bezbog Hut,
    Gotse Delchev Hut, Kartala-1/2, Borovets-Alpin/Ela. Two dead entries
    (`ortsevo`, `semkovo`) share names with still-active METER.AC nodes
    (N164, N27) — likely the same physical sites migrated networks, not a
    genuinely independent second source.
- Checked NIMH (Bulgaria's National Institute of Meteorology and
  Hydrology, official government service) as a "request data directly"
  option per user's suggestion:
  - Official experimental open-data portal (`info.meteo.bg/openData`):
    currently publishes only **daily** precipitation/river-discharge/snow
    and **monthly** mean/min/max temperature — no hourly temperature yet,
    too coarse for direct validation (useful at most as a rough monthly
    sanity check).
  - **Musala** (2925m, our DEM's highest point) has been an official NIMH
    station since **1932** — third-party resellers (meteoblue) sell hourly
    historical data "since 1940" for it commercially, strongly implying
    NIMH holds hourly archives internally even though not yet published.
    This would directly fill the gap left by METER.AC's own Musala node
    (N306) being offline on 2023-03-15.
  - **Bansko** (917m) is also an official NIMH climatic station, almost
    exactly co-located with METER.AC's active N48 Bansko (931.4m) — a
    genuine official-vs-citizen-station cross-check opportunity.
  - No documented online data-request form or dedicated researcher contact
    found — only a general MOEW administrative email
    (`edno_gishe@moew.government.bg`). Government press material states
    NIMH provides "free access to primary hydrometeorological data," which
    is a good sign for a direct request, but there's no self-service path
    — **direct outreach (email) is confirmed as the actual/only path**,
    consistent with the user's own instinct.
  - **Recommendation given to user**: if emailing NIMH, ask specifically
    for hourly air temperature at **Musala and Bansko** for whichever
    date(s) get finalized — not Rila/Kresna (lower elevation, redundant
    with existing METER.AC coverage nearby).

### Git hygiene check before today's push
Before pushing, audited every new/modified file today for size and
`.gitignore` coverage (this repo already had one past incident of a large
geodata push failure, per the 2026-08-06 entry above — wanted to avoid a
repeat).
- All of today's `outputs/`, `outputs_points/`, and `data/meterac/` files
  (multi-GB `.nc`/`.tif`/raw station `.txt` dumps) confirmed already
  correctly caught by existing blanket rules (`*.nc`, `*.tif`,
  `bulgaria_rila_pirin/data/`, `bulgaria_rila_pirin/outputs/`) — verified
  explicitly with `git check-ignore -v` on every known heavy file.
- **Found two real gaps**, both fixed:
  1. `bulgaria_rila_pirin/inputs/dem/mask.tif.bak` (18MB, a temporary backup
     from the mask-bounds fix earlier this session, no longer needed now
     that the fix is thoroughly verified) — **deleted**.
  2. `ex1_norway_finse/inputs/obs/*.pckl` (4 files, **331MB total**, MET
     Norway observation pickles) — **not** covered by any existing rule
     (unlike that same folder's `.nc`/`.tif` climate/DEM files, which
     already were). Added `*.pkl` and `*.pckl` to `.gitignore`, plus
     `bulgaria_rila_pirin/outputs_points/` explicitly (redundant with the
     `*.nc` rule for now, but matches the existing `outputs/` pattern for
     future-proofing).
- Re-verified after the fix: nothing untracked/modified today exceeds
  ~1.8MB (the Norway example notebook, which has embedded plot images).
  Safe to push.

### Remaining checklist (updated)
- [ ] Run the point-sampling pipeline (`config_point.yml`, prepared above).
- [ ] Port Norway notebook's comparison methodology (resample-aligned
  scatter/regression + time series/bias plots) to METER.AC variables, using
  the point-sampling output once run.
- [ ] Decide: keep 2023-03-15 or switch to a better-covered validation
  window (see overlap analysis above) — open discussion with user.
- [ ] (Optional, pending user follow-up) Email NIMH for Musala + Bansko
  hourly temperature at the finalized validation date(s).

## Session update — 2026-08-13: full-history pipeline upgrade, repo reorganization, coverage/temperature plots

### `meteo_full_history.py` upgraded: station names, N211 included, new daily-aggregation output
- Added `station_name` (joined from `selected_stations_metadata.csv`) to every
  per-station and combined output file — previously only the bare NodeID.
- **N211 (Makedonia_Hut) is now explicitly processed**, not silently skipped:
  it has zero raw records ever, so it gets a `no_data` row in
  `station_coverage_summary.csv` (all 29 in-AOI nodes now accounted for,
  instead of only the 28 with any data).
- **New**: daily aggregation, not just hourly. For each station, the hourly
  series is resampled to one row per day (`temp_c_min/max/mean`,
  `n_hours_with_data`, `coverage` = complete/partial/gap based on
  ≥20/24 hours present). Per-station `<NodeID>_daily_full_history.csv` plus a
  combined `stations_daily_full_history.csv` (44,831 rows).
- `overall_status` per station (`good`/`partial`/`mostly_gap`/`no_data`) added
  to `station_coverage_summary.csv`, thresholded on lifetime %complete
  (≥80% good, ≥30% partial, else mostly_gap).
- **Permissions snag**: `data/` and `data/meterac/` were root-owned (written
  by earlier container runs), blocking the host user from writing new output
  files. Fixed by starting the container (`docker compose up -d`; confirmed
  via the Dockerfile's missing `USER` directive that it runs as root, matching
  the existing file ownership) and running the script via
  `docker compose exec -T toposcale python3 ...` instead of the host venv.
  User separately ran `sudo chown -R $USER:$USER bulgaria_rila_pirin` to fix
  ownership going forward, so future runs don't need the container just for
  file permissions.

### Repo reorganization
- Moved the 4 combined/summary files out of `data/meterac/` up into `data/`
  directly (`stations_hourly_full_history.csv`,
  `stations_daily_full_history.csv`, `station_coverage_summary.csv`,
  `selected_stations_metadata.csv`) — per-station files stay in
  `data/meterac/`. `meteo_full_history.py` updated accordingly
  (`COMBINED_DIR` vs `OUT_DIR`).
- **All `.py` scripts moved from `bulgaria_rila_pirin/` into
  `bulgaria_rila_pirin/scripts/`** (via `git mv`, history preserved):
  `diff_raster.py`, `fix_mask.py`, `meteo-data.py`, `meteo_data_convert.py`,
  `meteo_full_history.py`, `meteo_hourly_agg.py`,
  `meteo_overlap_analysis.py`, `paint_back.py`, `pipeline.py`. Only one path
  fix was needed (`meteo_full_history.py`'s host-execution fallback, now
  `.parent.parent`) — every other script uses absolute `/app/...` container
  paths, unaffected by the script's own location on disk.

### New: coverage and per-station temperature plots (`bulgaria_rila_pirin/outputs/meteo_plots/`)
Two new scripts in `scripts/`, both runnable from the host venv directly (no
Docker needed — pandas/matplotlib only):
- **`plot_station_coverage.py`** → `station_coverage_timeline.png` (Gantt-style
  bar per station, colored by `overall_status`, labeled with name + altitude)
  and `station_data_quality.png` (stacked %complete/partial/gap per station,
  sorted worst-to-best, station labels annotated with the **exact hour count
  of real (non-gap) readings**, e.g. `Bansko (41,101 h)`).
- **`plot_station_daily.py`** → one PNG per station (28 with data + a
  `N211_Makedonia_Hut.png` placeholder noting it never reported), each with:
  daily min/max/mean temperature (line + shaded band) over the station's full
  record, a coverage strip (complete/partial/gap) below the time axis, and a
  day-count-by-category panel on the side — every plot is self-contained
  context (station name, altitude, exact date range, how much of the curve is
  actually backed by real readings).
- Colors follow the Claude Code `dataviz` skill's validated status palette
  (good/warning/serious/critical-equivalent hues, CVD-checked) rather than
  ad-hoc colors — `good`→green `#0ca30c`, `partial`→amber `#fab219`,
  `mostly_gap`/`gap`→red `#d03b3b`, `no_data`→gray `#898781`.
- Fixed one round of legend-overlapping-data issues on both summary plots by
  moving legends outside the axes (`bbox_to_anchor`) instead of `loc="lower/upper right"`.

### Opened: discussion on the actual validation methodology (ML residual bias-correction)
Carried over detailed reasoning from a prior thesis-planning chat (not done in
this Claude Code session, but directly informing the next implementation
step): the plan is to run TopoPyScale in **point mode** at station locations
(already scaffolded — see `config_point.yml` and the 2026-08-07 entries above
— but currently scoped to the single test day 2023-03-15 only), compute the
**residual** (`T_obs - T_topo`) at each station/timestamp, then train a
Random Forest / XGBoost model to **predict that residual** from terrain
features (elevation difference, slope, aspect, SVF) and time features
(hour, month), validated with **leave-one-station-out cross-validation**
(spatial generalization to unmonitored locations is the actual deliverable,
not temporal accuracy at known stations — pooling timestamps across stations
into a random split would leak each station's own bias into training).
Grounded in Ben Bouallègue et al. 2023 (MWR, ECMWF forecast-error
post-processing with RF, same bias-then-residual decomposition) plus several
terrain-aware RF/XGBoost downscaling precedents.

**Discussed this session:**
1. **Time range for the point-mode run — RESOLVED, see below** (was left open
   earlier in this same session; decided a few hours later after a dedicated
   best-day search).
2. **Which stations count as usable for training/validation — still open.**
   Leaning towards `good`-status stations only, possibly adding
   `partial`-status ones "if they bring real value" (user's phrasing) —
   excluding `mostly_gap` (Breznitsa, Lovna_Hut, Tevno_ezero_Hut) and
   `no_data` (Makedonia_Hut) — but not finalized. `station_coverage_summary.csv`'s
   `overall_status` column is ready to filter on whenever this is settled.

### Validation date decided: 2022-11-27 / 2022-11-28
Built `bulgaria_rila_pirin/scripts/find_best_coverage_day.py` to search
`stations_daily_full_history.csv` for the day(s) with the most stations at
`coverage == complete` (≥20/24 hours) network-wide — the direct answer to
"which single day has the best station coverage," reusing this session's own
daily-aggregation output rather than the older (differently-thresholded)
`meteo_overlap_analysis.py` output from 2026-08-07.
- **Best raw coverage** (no elevation constraint): 21/28 stations complete,
  tied across four 2026 days (07-05, 07-06, 07-31, 08-01) — but all of them
  fall within ERA5's ~2-3 month final-release lag from today (2026-08-13), so
  only the preliminary/revisable ERA5T product would exist for them. Rejected
  on those grounds alone, before even considering elevation.
- **Added the real deciding constraint**: elevation matters most for
  validating the lapse-rate correction, so **Musala (2925m, the DEM's highest
  point) must be complete**, and as many of the other high-elevation stations
  (≥1400m: Musala, Tevno_ezero_Hut, Rilski_ezera_Hut, Malyovitsa_Hut,
  Lovna_Hut, Semkovo, Ortsevo, Medeni_polyani, Popovi_livadi) as possible
  should be complete too. Musala has been unreliable since early 2023 (see
  the 2026-08-07 entries above) and its `complete` days only exist
  2019-08-24 to 2024-02-28 — this alone rules out any 2024+ candidate.
- Filtering to Musala-complete days and ranking by count of high-elevation
  stations complete found a clear top tier: **7 of 9 high-elevation stations
  complete (incl. Musala) + 19/28 total**, tied across six days in three
  back-to-back pairs: **2022-10-29/30-31 and 11-01/02, 11-20, 11-26,
  11-27/28, 12-06/07, 12-10/11** (top tier specifically: 11-27, 11-28, 12-06,
  12-07, 12-10, 12-11). On every one of these, the only missing
  high-elevation stations are Tevno_ezero_Hut and Lovna_Hut — both
  `mostly_gap` overall (8.7% and 4.7% lifetime complete), so they were never
  going to be available on any candidate day regardless of which one is
  picked.
- **Decision: 2022-11-27 and 2022-11-28.** Safely inside final-release ERA5
  (almost 4 years old), high-elevation-prioritized, and it's a genuine
  2-day window rather than a single day, giving the residual model
  slightly more temporal spread within the same well-covered stretch.
  Complete that day: Musala, Rilski_ezera_Hut, Malyovitsa_Hut, Semkovo,
  Ortsevo, Medeni_polyani, Popovi_livadi, Selishte, Obidim, Beli_Iskar,
  Bansko, Dolno_Draglishte, Velingrad_Zayche_blato, Breznitsa, Velingrad,
  Mosomishte, Garmen, Riltsi, Boboshevo (19 stations). Missing:
  Tevno_ezero_Hut, Lovna_Hut, Bodrost, Sarnitsa, Grashevo, Pletena,
  Dabnitsa, Golemo_selo, Balanovo (9 stations).

### Archived the 2023-03-15 toposub trial before starting the new run
To avoid mixing the old single-day full-grid trial with the new
Nov-2022 point-mode validation run, moved everything date-specific into
`bulgaria_rila_pirin/archive/2023-03-15_trial/` (~6GB):
- `outputs/`: `output.nc`, `ds_solar.nc`, `df_centroids.pck(.bak)`,
  `downscaled/` (all 500 per-cluster files), the 3 ERA5 comparison rasters,
  the diff raster, `t_2023-03-15_hourly_25m.tif`, `tmp/`.
- `inputs_climate/`: `PLEV_2023.nc`/`SURF_2023.nc` (relative symlinks,
  verified they still resolve after the move) + `daily/`, `yearly/`, `tmp/`
  — confirmed this was genuinely only ever one day's data despite the
  "yearly" folder name (~1.5MB total).
- `configs/`: snapshots of `config.yml` and `config_point.yml` exactly as
  they were for this trial (`start`/`end: 2023-03-15`), for reproducibility.
- **Deliberately left in place, not archived** — verified by reading
  `compute_horizon()`'s actual source (`Topoclass.compute_horizon`), which
  depends only on the DEM and azimuth increments, never on the configured
  date range: `outputs/da_horizon.nc` (7GB) and `outputs/ds_param.nc`
  (2.3GB), both terrain-only and fully reusable for the new date. Also left
  `outputs_points/ds_param.nc` (pre-seeded copy for point mode) and
  `outputs/meteo_plots/` (unrelated to the downscaling trial) untouched.
- **`.gitignore` gap found and fixed**: `df_centroids.pck`/`.pck.bak` (small,
  ~217KB each) weren't covered by any existing rule once moved out of the
  blanket-ignored `outputs/` path — added `*.pck` and `*.pck.bak` to
  `.gitignore`, matching the same reasoning as the existing `*.pkl`/`*.pckl`
  rules (cache/intermediate binary artifacts, not meant for version control).

### Point-mode pipeline run — started, live log
User gave full autonomy to run this unattended for hours: launch, diagnose,
patch, and relaunch on failure without stopping to ask, logging every fix
here as it happens.
- Regenerated `inputs/dem/station_list.csv` for **all 28 stations** (was 17,
  missing Musala/N306 among others) via new `scripts/build_station_list.py`
  (same lat/lon→EPSG:32634 reprojection approach as the original 17-station
  version). Downscaling all 28 costs almost nothing extra at this scale and
  fully defers the still-open "which stations count for training" question
  to the ML stage — no rerun risk later regardless of how that's decided.
- Copied (not hardlinked — deliberately, see discussion below)
  `outputs/da_horizon.nc` (7GB) into `outputs_points/` alongside the
  already-present `ds_param.nc`, since both are terrain-only and verified
  (by reading `Topoclass.compute_horizon`'s actual source) to never depend
  on the configured date range.
- Wrote `bulgaria_rila_pirin/scripts/pipeline_point.py` (adapted from
  `ex1_norway_finse/pipeline_point.py`, ending in `to_netcdf()` instead of
  `to_cryogrid()` to match this project's convention) and launched it in the
  background against the updated `config_point.yml`
  (`start`/`end: 2022-11-27`/`2022-11-28`).

**Bug #9 (new, found on first launch): `fetch_era5.py`'s own
`time_step_dict` has the exact same uppercase-only case-sensitivity issue as
Bug #6, in a different function.** `retrieve_era5()` (used by `get_era5()`)
hardcodes `time_step_dict = {'1H': [...], '3H': [...], '6H': [...]}`; with
`climate.era5.timestep: 1h` (lowercase, required since Bug #3),
`time_step_dict.get('1h')` silently returned `None` for every day, which
became the CDS API request's `time` field →
`400 Bad Request: request['time'][0]: None is not of type 'string'`. **This
never surfaced during the original 2023-03-15 download** because that
ERA5 data was fetched successfully *before* Bug #3's fix switched the config
to lowercase — later pipeline attempts just found the cached daily files and
skipped re-downloading, so this exact code path was never actually exercised
with a lowercase timestep until this run (the first fresh, uncached
`get_era5()` call since Bug #3). **Fix** (container-image-only, same caveat
as Bugs #6/#7 — lost on image rebuild, not just restart):
`sed`-appended a line right after the dict literal in
`/usr/local/lib/python3.13/site-packages/TopoPyScale/fetch_era5.py`:
`time_step_dict.update({'1h': time_step_dict['1H'], '3h': time_step_dict['3H'], '6h': time_step_dict['6H']})`.
Confirmed no leftover partial daily files from the failed attempt before
relaunching (clean retry, no stale-cache risk).
- Relaunched (2nd attempt) — **Bug #9's fix confirmed working**: ERA5 SURF
  daily files for both 2022-11-27 and 2022-11-28 downloaded and unzipped
  successfully this time (24 hourly timesteps each, correct 8×10 grid,
  verified by opening both with xarray). Got further, then hit a new,
  unrelated failure:

**Bug #10: `retrieve_era5()` auto-creates `inputs/climate/daily/` but never
`inputs/climate/yearly/`**, and this session's earlier archiving step (run as
the host user, not root, via plain `mv`) moved the *entire* `yearly/`
directory into `archive/2023-03-15_trial/` along with its contents — so the
directory didn't exist at all anymore for `cdo mergetime` to write into.
Manifested as a misleading `Error (cdf__create): .../SURF_2022.nc: Permission
denied` (netCDF/cdo's generic error for "parent directory doesn't exist" is
worded like a permissions failure, not a missing-path one — easy to
misdiagnose as another root-ownership issue like Bugs #1/#8's category, but
confirmed by direct inspection this was a genuinely absent directory, not a
permissions mismatch). **Fix**: recreated
`inputs/climate/yearly/` via `docker compose exec ... mkdir -p` (as root,
matching the container-created `daily/`/`tmp/` siblings' ownership, rather
than as the host user, to keep the whole `climate/` tree's ownership
consistent going forward). Not a code patch this time, just a missing
directory — nothing to lose on image rebuild.
- Relaunched (3rd attempt) — **Bugs #9 and #10's fixes both confirmed
  working**: ERA5 SURF+PLEV daily files downloaded (SURF was skipped, found
  already-downloaded from attempt #2 — the `file_exist` cache check works
  correctly across relaunches, as expected) and merged into yearly files with
  no errors. `compute_dem_param()` cache-hit on `ds_param.nc` correctly.
  Got further, into `extract_topo_param()`, then hit a third new bug:

**Bug #11: another pandas-3.0 strict-dtype issue, same family as Bugs
#7/#8** (chained-assignment / Arrow-string-dtype), this time in
`topo_param.py`'s `extract_pts_param()` — the function used specifically by
point-mode (never exercised by the earlier toposub full-grid run, which is
why this is only surfacing now on the very first real point-mode attempt).
`df_pts[[...]] = 0` initializes the elevation/slope/aspect/svf columns with
integer `0`, so pandas infers `int64` dtype; a later per-point
`.loc[i, [...]] = np.array((float values))` assignment (e.g. writing a real
elevation like `1626.048`) then hits pandas 3.0's strict type-checking and
raises `TypeError: Invalid value '1626.0481247738776' for dtype 'int64'`
instead of silently upcasting like older pandas did. **Fix**
(container-image-only, same caveat as every other patch this session):
changed `= 0` → `= 0.0` at line 87, so the columns are float64 from
creation. One-character diff.
- Relaunched (4th attempt) — **Bug #11's fix confirmed working**: got all the
  way through `extract_topo_param()` (all 28 stations reprojected/sampled
  fine, `df_centroids.pck` saved), `compute_solar_geometry()`, and
  `compute_horizon()` (cache-hit on the copied `da_horizon.nc` — confirms
  that copy-not-hardlink decision paid off, zero recompute) into
  `downscale_climate()` — furthest yet. Hit a **repeat of Bug #5** from the
  original toposub run: `downscale_climate()` globs for `PLEV*.nc`/`SURF*.nc`
  directly in `inputs/climate/`, but the merge step puts them in
  `inputs/climate/yearly/` → `OSError: no files to open`. Same fix as before,
  just for the new year: symlinked
  `inputs/climate/{PLEV,SURF}_2022.nc -> yearly/{PLEV,SURF}_2022.nc`. Not a
  code patch — a per-year setup step that will need repeating for any future
  year's climate data (worth automating in `pipeline_point.py` itself if this
  project ends up running many different date windows).
- Relaunched (5th attempt) — **SUCCESS.** All remaining cached steps
  (`extract_topo_param`, `compute_solar_geometry`, `compute_horizon`)
  loaded from cache correctly, confirming attempt #4's work wasn't wasted.
  `downscale_climate()` ran clean for all 28 stations (`t,q,p,tp,ws,wd` +
  `LW,SW` radiation) in **242 seconds** — vastly faster than the 500-cluster
  full-grid run's ~37 minutes for `downscale_climate()` alone, as expected at
  this much smaller point count. `to_netcdf()` wrote the merged result to
  **`bulgaria_rila_pirin/outputs_points/output.nc`**. `Pipeline finished`,
  no traceback.

**Output structure**: `xarray.Dataset`, dims `(point_name: 28, time: 48)` —
28 stations × 48 hourly timesteps (2022-11-27 00:00 through 2022-11-28
23:00), confirming the earlier product-count correction (28 stations → 28
files, not 56 or 38 — the time dimension just grows to cover both days
inside each file). All 28 individual `down_pt_*.nc` files also present in
`outputs_points/downscaled/`.

**Sanity check performed** (same methodology as the original 2023-03-15
toposub validation):
- Temperature range across all 28 points × 48 hours: **-11.89°C to +6.69°C**,
  mean **-0.32°C** — physically plausible for late November in the
  Rila-Pirin mountains.
- **Elevation vs. mean-temperature correlation: -0.987** — near-identical to
  the original toposub run's -0.98 for a completely different date, strong
  independent confirmation the lapse-rate physics is behaving consistently
  across runs.
- Coldest: **Musala (N306, 2925m) at -9.54°C mean** — highest station,
  coldest result, as expected, and specifically the station this whole
  validation window was chosen to include.
- Warmest: **Riltsi (N110, 378m) at +4.94°C mean** and **Boboshevo (N235,
  375m) at +4.62°C mean** — lowest-elevation stations, warmest results.
- Monotonic cold-with-elevation trend holds throughout the full station list
  (see run log / re-run the pandas snippet above for the full table), with
  only minor (~0.2-0.5°C) local noise between adjacent-elevation stations —
  expected from real aspect/terrain differences, not a red flag.

**All 5 bugs found this session (#7 reused from before this session,
#9/#10/#11 new) are now documented above with fixes.**

**Follow-up, same day: `docker/Dockerfile` updated to bake in every patch**
(Bugs #6, #7, #9, #11 — the two originally in the Dockerfile from
2026-08-06 were already covered) so none of them are lost on a future image
rebuild. Also pinned `TopoPyScale==0.3.3` (was unpinned) — every patch
targets exact line text specific to that version, so an unpinned upgrade
could silently stop matching (patch no-ops) or match something unintended.
**Verified each new patch before trusting it**: downloaded a pristine copy of
`TopoPyScale==0.3.3` via `pip download` into a scratch dir inside the
container, applied the exact same sed/python commands the Dockerfile now
uses, confirmed all 4 patterns matched exactly once, and `py_compile`'d the
resulting files clean — so this isn't just "looks right," it's confirmed to
actually apply correctly against a fresh install, not just the
already-patched live container. Bug #10 (missing `yearly/` directory) needed
no Dockerfile change — it wasn't a code bug, just a directory that a fresh
container will create automatically via `docker compose exec ... mkdir -p`
the same way this session did.

### Remaining checklist (updated again)
- [x] Run the point-mode pipeline for all 28 stations at 2022-11-27/28 —
  **done, see above.** `output.nc` and 28 `down_pt_*.nc` files ready in
  `outputs_points/`.
- [ ] Decide final station filter for training/validation (good-only vs
  good+useful-partial) — still open, see above. The **19 stations complete
  on both 2022-11-27 and 2022-11-28** (listed in "Validation date decided"
  above) are the ones that can actually be validated against real
  observations from this run; the other 9 were downscaled too but have no
  usable ground truth for this specific window.
- [x] Update `config_point.yml`: `start`/`end` → `2022-11-27`/`2022-11-28`.
- [x] Run the point-mode pipeline for all 28 stations — **done, see above.**
- [x] Compute `T_obs - T_topo` residuals for the 19 validated stations —
  **done, see "Residuals computed" below.**

(Superseded note: this used to say not to start the point-mode run until both
open items above were resolved. The time-range question was resolved later
the same session — see "Validation date decided" below — and the run was
started with the user's explicit go-ahead to downscale all 28 stations
regardless of the still-open training-filter question, since that filter can
be applied after the fact with no rerun needed.)

## Session update — 2026-08-14: Dockerfile patches verified, residuals computed, QGIS export, roadmap notes

### `docker/Dockerfile` patch verification
Before trusting yesterday's Dockerfile additions (Bugs #6/#7/#9/#11 baked in,
`TopoPyScale` pinned to `0.3.3`), verified them properly rather than just
eyeballing the diff: downloaded a **pristine** copy of `TopoPyScale==0.3.3`
via `pip download` into a scratch dir inside the container (not the
already-patched live install), applied the exact sed/python commands now in
the Dockerfile against it, confirmed all 4 patterns matched exactly once,
and `py_compile`'d the results clean. Confirms the Dockerfile will actually
reproduce a working setup on a real rebuild, not just "looks plausible."
Cleaned up the scratch dir afterward.

### Point-mode run timing
`downscale_climate()` itself: **242.0 seconds** (printed by the pipeline's
own log) for all 28 stations × 48 hourly timesteps. Cross-checked against
file timestamps (`df_centroids.pck` cache-hit at 18:49:42 → `output.nc`
written 18:53:45, a 243s gap) — confirms the **entire successful run (attempt
5), start to finish, took essentially just those ~4 minutes**, since every
other step (ERA5 fetch, DEM params, solar geometry, horizon) was already
cached from earlier failed attempts. For comparison, the original 500-cluster
full-grid run's `downscale_climate()` alone took ~37 minutes — point mode at
28 stations is ~9x faster on that step.

### Residuals computed
Wrote `bulgaria_rila_pirin/scripts/compute_residuals.py`: joins `output.nc`
(T_topo), `stations_hourly_full_history.csv` (T_obs, filtered to the 19
stations confirmed `complete` on both 2022-11-27 and 2022-11-28), and
`df_centroids.pck` (terrain features: DEM elevation, station's own reported
elevation, slope, aspect_cos/sin, svf) into one table,
`bulgaria_rila_pirin/data/residuals_2022-11-27_28.csv` (912 rows — 19
stations × ~48 hours, two stations short a few hours from their own partial
coverage: N097 has 47, N098 has 42).

**Raw TopoScale baseline (no ML correction) — the number any bias-correction
model needs to beat**:
- Overall: mean residual **+0.01°C** (unbiased in aggregate), **RMSE 2.21°C**,
  **MAE 1.59°C**.
- Per-station bias ranges from **-2.3°C (Riltsi, 378m)** to **+2.3°C (Obidim,
  1213m)** — real, non-random station-specific offsets survive even though
  the network-wide aggregate is unbiased.
- Correlation of residual with elevation-difference (DEM vs. station's real
  elevation): **0.01** (negligible — the differences are only a few to ~20m on
  a 25m grid, too small to explain much on their own). With hour-of-day:
  **0.30** (a real diurnal pattern in the error, useful ML signal). With SVF:
  **-0.07** (weak alone, may still interact with other features nonlinearly).
- **Musala (highest station, 2925m): TopoScale runs 1.65°C too warm on
  average**, RMSE 2.41°C — the hardest point, as expected for the most
  extreme/exposed terrain.

### QGIS spatial export
User needed to visualize `output.nc` in QGIS but it opened positioned near
"Africa" (Null Island) — diagnosed as `output.nc` having **no spatial
coordinates at all** (`point_name`, `time`, `reference_time` only; no x/y/
lat/lon), so QGIS's netCDF importer was falling back to plotting raw indices
as if they were degrees. `output.nc` was never going to render correctly as
a raster — it's a site/time-series table, not a spatial grid.

**Fix**: wrote `bulgaria_rila_pirin/scripts/export_output_to_gpkg.py`,
joining each station's real `station_list.csv` coordinates (EPSG:32634) onto
its `output.nc` values in long format (one row per station × hour, so QGIS's
Temporal Controller can animate through the 48 hours using the `time`
field). Output: `bulgaria_rila_pirin/outputs_points/output_points_for_qgis.gpkg`
(28 stations × 48 hours = 1,344 point features; verified CRS and bounds fall
correctly inside the known DEM extent this time). Columns: `node`, `Name`,
`elevation_m`, `time`, `t` (K), `t_celsius` (°C, added for convenience),
`ws`, `wd`, `tp`, `q`, `p`, `LW`, `SW`.
- Also confirmed **`landform.tif` does not exist anywhere in this project**
  and was never generated — it's a toposub-mode-only output (per-pixel
  cluster-ID raster), and neither `pipeline.py` nor `pipeline_point.py` ever
  called the method that writes it. It's also the exact thing Bugs #7/#8
  broke in the original run, which is why `paint_back.py` exists as a
  workaround (though that produces a temperature raster for 2023-03-15
  specifically, now archived, not a landform/cluster-ID grid).

### Roadmap notes for next steps (recorded here, not yet acted on)
User named four things to tackle next, in no particular stated order:
1. **Decide the ML bias-correction methodology properly.** The Phase
   1 (this 2-day dataset, methodology validation) vs. Phase 2 (multi-year
   run, real training) framing was proposed this session — see "Opened:
   discussion..." above — model recommendation for Phase 1 was Ridge
   regression + a small regularized Random Forest, evaluated by
   leave-one-station-out CV against the 2.21°C raw-RMSE baseline above.
   **Not yet built or agreed on** — paused here to record the other three
   items first.
2. **Set up a NOAA option for automated meteo station data.** User's own
   words — likely to get an automated/API-based station data feed rather
   than the manual scraping approach used for METER.AC, either as a
   supplement or an alternative source. **No details worked out yet** — needs
   its own scoping session (which NOAA product/API, which stations near the
   AOI, how it'd combine with or replace METER.AC data).
3. **Formulate a proposal/concept document + proof of concept**, to present
   for feedback from the user's teacher/advisor. Not started.
4. **After the roadmap above is finalized**, create a separate `STATUS.md`
   — explicitly **not** a full historical log like this file, but a
   current-state-focused document ("more on point data," per the user's
   phrasing) — deferred until the roadmap itself is settled, so it reflects
   final decisions rather than in-progress ones.

**Small near-term to-do (reminder for next session)**: extend
`export_output_to_gpkg.py` (or add a sibling script) to also pull in the
observed METER.AC values for the 19 validated stations, so the GeoPackage
has T_obs alongside T_topo per station/hour — lets the comparison be done
visually in QGIS (e.g. two symbol layers, or a computed-field showing the
residual directly) instead of only in the residuals CSV. User asked for this
right after the QGIS export above; explicitly deferred to next time.

## Session update — 2026-09-29: multi-year (1980-2025) downscaling wrapper — design, build, pilot

### Goal and research first
User wants the full 1980-2025 downscaled record eventually, plus a
resumable, self-debugging, well-logged tool to produce it (rather than
hand-running `pipeline_point.py` once per year) — explicitly asked to
consult TopoPyScale's own docs/source first rather than inventing something,
matching this project's "use the original creators' code" instinct.

**Findings from reading the actual installed source** (more reliable than
the sparse official docs, which barely mention any of this):
- `pipeline.py` (root) is confirmed to be exactly the canonical 6-step
  template from the readthedocs quickstart.
- The docs' use-cases page cites a real precedent at this scale: the
  "Genesis" project used TopoPyScale for **30 years of ERA5 hourly data**
  across several archipelagos — 45 years is within the tool's proven range.
- Real Zarr support exists: `fetch_era5.py`'s `convert_netcdf_stack_to_zarr()`
  (merges yearly NetCDF into one `ERA5.zarr`) and `topo_scale_zarr.py`'s
  `ClimateDownscaler` class (dask/zarr downscaling path, activated via
  `climate.era5.zarr_store` in config — exactly `ex2_romania_retezat`'s own
  reference config). **Two gotchas found by reading this code before ever
  running it**: (1) `ClimateDownscaler.__init__` has its own THIRD copy of
  the uppercase-only `tstep_dict` bug (Bugs #6/#9's twin) — patched
  preemptively as **Bug #12**, both live and in the Dockerfile, verified
  against a pristine `pip download` the same way as every other patch this
  project has made; (2) neither `convert_netcdf_stack_to_zarr()` nor the
  output-side `setup_output_store()` are actually resumable — both call
  `.to_zarr(mode='w')` unconditionally, so re-running either to "resume"
  would silently wipe prior progress, not continue it.
- The library's own built-in time-chunking feature (`project.split.IO`) is
  explicitly flagged by its own source comment as *"prone to bugs... Not
  compatible with Zarr system"* — deliberately not built on.

### Design decision: proven multicore path per year, Zarr as final export only
Chose **not** to route the live multi-year compute through the dask/zarr
`ClimateDownscaler` path, despite it being the "fancier" built-in option.
Reasoning: this project has hit a new bug on essentially every
first-time-exercised code path so far (6 bugs in the original toposub run,
3 more in the first point-mode run) — deliberately introducing a
barely-exercised, non-resumable-by-default code path into a job meant to run
unattended for days-to-weeks is the wrong risk tradeoff. Instead: reuse the
**already-validated** classic multicore/NetCDF path (proven at 242s for 28
stations × 2 days) once per year, and treat Zarr purely as a **final export
format** — a simple, low-risk `xr.concat` + `.to_zarr()` over completed
years' `output.nc` files, using plain xarray rather than the library's more
complex internals. This still delivers exactly what the user asked for
("obtain them in zarr format at the end") without adding compute-path risk.

### Built: `era5_batch_fetch.py` + `run_downscaling.py`
- **`bulgaria_rila_pirin/scripts/era5_batch_fetch.py`**: custom
  monthly-batched CDS fetch (reuses TopoPyScale's own `unzip_file()` helper
  and variable/dataset conventions directly, only the request-batching logic
  is new). Solves the real structural bottleneck: TopoPyScale's own
  `fetch_era5.py` requests one CDS API call **per day** — at 1980-2025 scale
  that's ~16,800 days × 2 datasets ≈ **33,600 requests**, risking CDS
  rate-limiting independent of actual compute cost. Monthly batching cuts
  this to ~1,104 requests (~30x fewer), each month's data merged into the
  same `PLEV_{year}.nc`/`SURF_{year}.nc` yearly format the rest of the
  pipeline already expects (via the same `cdo.mergetime` call
  `retrieve_era5()` already uses), so nothing downstream changes.
- **`bulgaria_rila_pirin/scripts/run_downscaling.py`**: the per-year
  orchestrator. For each year: (1) ensure that year's ERA5 exists (fetch if
  not), (2) symlink the three terrain-only shared caches (`ds_param.nc`,
  `da_horizon.nc`, `df_centroids.pck` — confirmed date-independent, reused
  as-is) into a per-year `outputs_points/yearly/<year>/` directory, (3) run
  the proven 6-step pipeline for just that year's date range, writing its
  own `output.nc`. **Resumability is filesystem-based** (does
  `<year>/output.nc` already exist and open validly?) rather than a separate
  state file that could drift from reality — matches TopoPyScale's own
  caching philosophy. CLI: `--config`, `--start-year`/`--end-year` or
  `--years`, `--phase {fetch,downscale,export,all}`, `--dry-run`, `--force`.
  Logs every phase/year transition to both console and
  **`bulgaria_rila_pirin/logs.md`** (new file — a dedicated, append-only
  operational log for this specific tool, kept separate from this file since
  a multi-week unattended run would otherwise flood `PROGRESS.md`; every
  command actually run gets logged verbatim there per explicit user
  instruction).
- Also added a tutorial-style comment block to the root `pipeline.py`
  explaining the 6-step flow in more depth and pointing at
  `run_downscaling.py` for any multi-year run.

### Testing so far (see `logs.md` for exact commands + live status)
- **Dry-run smoke test**: caught and fixed a real bug on the first try — the
  per-year `mkdir` wasn't guarded, so `--dry-run` was still creating an empty
  directory. Fixed (`year_dir.mkdir`, `yearly_dir.mkdir`, and
  `run_export`'s directory check all now guarded), re-ran, confirmed truly
  zero filesystem side effects.
- **Isolated 2-month fetch pilot** (separate scratch climate directory, not
  the real one): found a real, important constraint — CDS rejects a
  full-month PLEV request (6 vars x 14 pressure levels x ~744h ≈ 62,500
  fields) with `403 cost limits exceeded`. SURF (no pressure-level
  dimension) is fine at monthly granularity. Also learned the earlier
  "proven 2-day PLEV request" from the 2022-11-27/28 run was actually two
  separate 1-day requests (2,016 fields each), never tested combined — so
  the true safe ceiling was unknown, not just "bigger than 2 days" as
  assumed. A 7-day chunk (~14,112 fields) also failed. **3-day chunks
  (~6,048 fields) confirmed working** (session picked back up 2026-09-30
  after the prior session/container ended mid-pilot — file-existence checks
  meant resuming just skipped the already-completed chunks, no lost work).
  **Also added concurrency**: the fetch was running strictly sequentially
  (one CDS request at a time, each waiting out its full queue turn before
  the next was even submitted) — user flagged the wait directly, so added a
  thread pool (`multiprocessing.dummy.Pool`, matching TopoPyScale's own
  `retrieve_era5()` concurrency pattern — these are I/O-bound CDS-queue
  waits, not CPU-bound work) to `era5_batch_fetch.py`. `ensure_year_climate()`
  now takes `num_threads` (default 4) and submits that many chunks/months at
  once. **Full blow-by-blow with exact commands in `logs.md`** — kept there
  rather than duplicated here since this is exactly the kind of iterative
  back-and-forth `logs.md` exists to hold without flooding this file.
  **Status at time of this commit: pilot still in progress, not yet
  complete** — the real 1980-2025 run has not started.

### Time estimate given to the user (important — set expectations before launch)
Fetch phase: ~1,104 sequential requests; even at a conservative 3-4 min/request
average (month-sized multi-variable requests likely queue longer than the
20-150s seen for the tiny 2-day test), that's **~2.5-3 days just for
fetching**, currently unthreaded. Downscale phase: naive linear
extrapolation from the 242s/2-days data point puts a single year around
~12 hours, ×46 years → potentially **multiple weeks** — genuinely unknown
until the pilot proves it out. **Told the user explicitly this will not
finish today or even this week** — it's a multi-day-to-multi-week background
job by nature, not something to expect completed on return.

### Explicit standing instruction from user (record here so it isn't lost)
"Don't wait for my confirmation, act responsibly, log everything you do...
if the pilot needs edits, do them responsibly, update, try again until
satisfactory, then start the real 1980-2025 pipeline." Proceeding on this
basis: fix issues autonomously as they arise (same pattern as every prior
debugging session here), verify satisfactory (not just "no traceback") before
calling the pilot good, then launch the full range unattended. User will
`git commit`/push themselves afterward, once back.
