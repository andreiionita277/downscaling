# Downscaling tool run log

Dedicated, append-only operational log for the multi-year (1980-2025)
TopoPyScale downscaling wrapper (`bulgaria_rila_pirin/scripts/run_downscaling.py`).
Separate from `PROGRESS.md` (the broader project narrative/decisions log)
because this tool can run unattended for days-to-weeks and would otherwise
flood that file. Every entry below `---` is either auto-appended live by the
tool itself (`logger` writes here on every phase/year transition) or a
manual note when a human/Claude decision was made outside the tool's own
run (e.g. a bug fix, a design change). **Every command actually run is
logged verbatim** — not paraphrased — per explicit user instruction.

---

## 2026-09-29 — Manual entry: wrapper built, pilot test in progress

Built `bulgaria_rila_pirin/scripts/era5_batch_fetch.py` (monthly-batched CDS
fetch, ~30x fewer requests than TopoPyScale's own day-by-day fetch at
multi-year scale) and `bulgaria_rila_pirin/scripts/run_downscaling.py` (the
per-year fetch→downscale→export orchestrator, filesystem-based
resumability). Full design rationale in `PROGRESS.md`.

Preemptively patched a 4th occurrence of the known uppercase-only
`tstep_dict` case-sensitivity bug (Bugs #6/#9's twin), this time in
`topo_scale_zarr.py`, found via reading the source before ever exercising
that code path — baked into `docker/Dockerfile` too, verified against a
pristine `pip download` of `TopoPyScale==0.3.3` the same way as the other
patches.

**Commands run so far:**

Dry-run smoke test (no side effects, confirmed after a fix — see below):
```
docker compose exec -T toposcale python3 /app/bulgaria_rila_pirin/scripts/run_downscaling.py \
  --config /app/bulgaria_rila_pirin/config_point.yml \
  --years 2023 \
  --dry-run
```
First attempt at this left an empty `outputs_points/yearly/2023/` directory
behind despite `--dry-run` (the per-year `mkdir` wasn't guarded). Fixed in
`run_downscaling.py` (guarded `year_dir.mkdir`, `yearly_dir.mkdir`, and
`run_export`'s directory check behind `if not args.dry_run` / existence
checks) and re-ran — confirmed zero filesystem changes on the second run
(only `logs.md` gained a line, which is intended).

Isolated pilot test of the fetch mechanics alone (2 months, separate scratch
climate directory so it can't collide with real data), launched in the
background and **still queued at CDS as of this entry** (no files back yet
after several minutes — consistent with the expectation that month-sized
multi-variable requests queue longer than the tiny 2-day requests from the
2022-11-27/28 validation run):
```
docker compose exec -T toposcale python3 -c "
from pathlib import Path
import sys
sys.path.insert(0, '/app/bulgaria_rila_pirin/scripts')
from era5_batch_fetch import ensure_year_climate
bbox = [42.42891940797414, 22.790290781890487, 41.31828832214469, 24.279323407431068]
plevels = [600,650,700,750,775,800,825,850,875,900,925,950,975,1000]
plev, surf = ensure_year_climate(2024, '/app/bulgaria_rila_pirin/inputs/climate_pilot_test', bbox, plevels, months=[1,2])
print('RESULT:', plev, surf)
"
```

**Result: FAILED.** SURF succeeded for both months (monthly batching works
fine there — 7 vars x 1 level x ~30 days x 24h ≈ 5,200 fields), but the
first PLEV request (a full month: 6 vars x 14 levels x 31 days x 24h ≈
62,500 fields) got rejected outright:
```
requests.exceptions.HTTPError: 403 Client Error: Forbidden for url:
https://cds.climate.copernicus.eu/api/retrieve/v1/processes/reanalysis-era5-pressure-levels/execution
cost limits exceeded
Your request is too large, please reduce your selection.
```
CDS has a per-request "cost" ceiling that PLEV's pressure-level dimension
blows through at monthly granularity, even though SURF (no pressure levels)
is fine at the same granularity. Our only actually-proven-safe PLEV data
point is the original single-day request from the 2022-11-27/28 validation
run (2,016 fields) — the "2-day combined request" I'd assumed was proven
earlier was actually always two separate 1-day requests, never tested
combined.

**Fix attempt 1**: rewrote `era5_batch_fetch.py` to keep SURF at monthly
batching (proven) but split PLEV into weekly (7-day) chunks (~14,112
fields — 3x margin under the failed 62,500). Re-ran the same command
(SURF skipped automatically, already fetched):
```
docker compose exec -T toposcale python3 -c "
from pathlib import Path
import sys
sys.path.insert(0, '/app/bulgaria_rila_pirin/scripts')
from era5_batch_fetch import ensure_year_climate
bbox = [42.42891940797414, 22.790290781890487, 41.31828832214469, 24.279323407431068]
plevels = [600,650,700,750,775,800,825,850,875,900,925,950,975,1000]
plev, surf = ensure_year_climate(2024, '/app/bulgaria_rila_pirin/inputs/climate_pilot_test', bbox, plevels, months=[1,2])
print('RESULT:', plev, surf)
"
```
**Also failed**, same error, on the very first 7-day chunk. Notably this
rejection came back *instantly* (no CDS queue wait at all), confirming it's
a pre-submission cost check, not a capacity/queue issue — which at least
makes iterating on the right chunk size fast, not slow.

**Fix attempt 2**: dropped PLEV chunk size further, to 3 days (~6,048
fields — between the proven-safe 2,016 and the failed 14,112). Changed
`_week_chunks_for_month`'s default `chunk_days` 7 → 3 in
`era5_batch_fetch.py`. Confirmed no stale/partial file was left behind by
the failed request (it fails before any download starts) so the retry is a
clean rerun, not a resume-from-partial. Re-ran the identical command above
— **result pending, see next entry**.

**Plan from here**: once this resolves (success or failure), fix anything
broken and retry until it's genuinely correct (verified data, not just "no
traceback"), then launch the real run:
```
docker compose exec -T toposcale python3 /app/bulgaria_rila_pirin/scripts/run_downscaling.py \
  --config /app/bulgaria_rila_pirin/config_point.yml \
  --start-year 1980 --end-year 2025
```
Expected to take **days to weeks**, not hours — see `PROGRESS.md` for the
full timing reasoning. Proceeding without further check-ins per explicit
user instruction ("don't wait for my confirmation, act responsibly, log
everything").

---

2026-09-29 12:23:17  
2026-09-29 12:23:17  ## run_downscaling.py started — years=2023-2023 phase=all dry_run=True force=False
2026-09-29 12:23:17  
2026-09-29 12:23:17  ## Year 2023
2026-09-29 12:23:17    [dry-run] would write config to /app/bulgaria_rila_pirin/outputs_points/yearly/2023/config_point.yml
2026-09-29 12:23:17    [dry-run] would symlink shared caches into /app/bulgaria_rila_pirin/outputs_points/yearly/2023
2026-09-29 12:23:18  
2026-09-29 12:23:18  ## Export to Zarr
2026-09-29 12:23:18    no completed years found, nothing to export
2026-09-29 12:23:18  
2026-09-29 12:23:18  ## run_downscaling.py finished
2026-09-29 12:26:50  
2026-09-29 12:26:50  ## run_downscaling.py started — years=2023-2023 phase=all dry_run=True force=False
2026-09-29 12:26:50  
2026-09-29 12:26:50  ## Year 2023
2026-09-29 12:26:50    [dry-run] would write config to /app/bulgaria_rila_pirin/outputs_points/yearly/2023/config_point.yml
2026-09-29 12:26:50    [dry-run] would symlink shared caches into /app/bulgaria_rila_pirin/outputs_points/yearly/2023
2026-09-29 12:26:51  
2026-09-29 12:26:51  ## Export to Zarr
2026-09-29 12:26:51    no completed years found, nothing to export
2026-09-29 12:26:51  
2026-09-29 12:26:51  ## run_downscaling.py finished

---

## 2026-09-30 — New session: picking back up mid-pilot

Previous session ended (Docker container exited, `Exited (255)`) while the
3-day-chunk PLEV retry was still running in the background — no completion
record, so the task showed as `stopped` rather than succeeded/failed on
resume. Restarted the container:
```
docker compose up -d
```
Checked the scratch pilot directory before assuming anything:
```
docker compose exec -T toposcale bash -c "find /app/bulgaria_rila_pirin/inputs/climate_pilot_test -type f 2>/dev/null"
```
**Result: the 3-day chunk fix works.** 8 PLEV chunk files for January 2024
(`d01-03` through `d22-24`) had already downloaded successfully before the
container died — zero `cost limits exceeded` errors this time. Confirms the
fix from the previous entry; just needs to finish the remaining chunks.
Also confirmed the real project climate data
(`inputs/climate/yearly/{PLEV,SURF}_2022.nc`, from the actual
2022-11-27/28 validation run) is untouched — the pilot only ever wrote to
the separate `climate_pilot_test` scratch directory.

Updated `era5_batch_fetch.py`'s module docstring to reflect the real
findings (3-day chunks proven, not the originally-guessed weekly) since it
still described the failed weekly attempt.

Resumed the pilot fetch with the identical command as before — each
already-completed chunk is skipped automatically (file-existence check),
so this continues from `d25-27` of January rather than restarting:
```
docker compose exec -T toposcale python3 -c "
from pathlib import Path
import sys
sys.path.insert(0, '/app/bulgaria_rila_pirin/scripts')
from era5_batch_fetch import ensure_year_climate
bbox = [42.42891940797414, 22.790290781890487, 41.31828832214469, 24.279323407431068]
plevels = [600,650,700,750,775,800,825,850,875,900,925,950,975,1000]
plev, surf = ensure_year_climate(2024, '/app/bulgaria_rila_pirin/inputs/climate_pilot_test', bbox, plevels, months=[1,2])
print('RESULT:', plev, surf)
"
```
Confirmed progressing (12 files on disk, one more PLEV chunk landed) but
running strictly sequentially — one CDS request at a time, each waiting out
its full queue turn before the next is even submitted. With ~44 more years
still ahead, user flagged this waiting experience directly. Real fix, not
just reassurance: added concurrency to `era5_batch_fetch.py` via
`multiprocessing.dummy.Pool` (a thread pool — matches TopoPyScale's own
`retrieve_era5()` concurrency pattern exactly, since these are I/O-bound
CDS-queue waits, not CPU-bound work). `ensure_year_climate()` now takes
`num_threads` (default 4) and submits that many chunks/months at once via
`pool.map`, rather than one at a time.

Killed the running sequential fetch (found and `kill -TERM`'d the
in-container Python process — same technique as the original 2026-08-06
session, `/proc/[0-9]*/cmdline` grep, no `ps`/`pgrep` in this image) and
relaunched with the new threaded code:
```
docker compose exec -T toposcale bash -c "
for p in /proc/[0-9]*; do
  if grep -q 'ensure_year_climate' \$p/cmdline 2>/dev/null; then
    echo \"killing \$(basename \$p)\"
    kill -TERM \$(basename \$p) 2>/dev/null
  fi
done
"
```
```
docker compose exec -T toposcale python3 -c "
from pathlib import Path
import sys
sys.path.insert(0, '/app/bulgaria_rila_pirin/scripts')
from era5_batch_fetch import ensure_year_climate
bbox = [42.42891940797414, 22.790290781890487, 41.31828832214469, 24.279323407431068]
plevels = [600,650,700,750,775,800,825,850,875,900,925,950,975,1000]
plev, surf = ensure_year_climate(2024, '/app/bulgaria_rila_pirin/inputs/climate_pilot_test', bbox, plevels, months=[1,2], num_threads=4)
print('RESULT:', plev, surf)
"
```
Already-completed chunks are skipped automatically (file-existence check),
so this resumes rather than redoing the ~9 chunks already fetched. Result
pending.
