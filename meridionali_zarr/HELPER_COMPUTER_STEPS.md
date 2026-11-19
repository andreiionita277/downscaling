# Second computer (Nandhni's): download Meridionali ERA5 years from 1940 upward

Written 19 Nov 2026. Purpose: download older years in parallel with Andrei's computer, which goes from 1990 downward.
Nandhni's computer only **downloads**. Downscaling and Crocus stay on Andrei's computer; they need the terrain,
SURFEX and D:, and they take only ~40 min per year.

**Nothing in `rila_pirin_zarr/` is touched.** Everything is written under `meridionali_zarr/` of Nandhni's clone:
- the data go to `meridionali_zarr/data/`, which is git-ignored;
- Nandhni's Rila-Pirin box (40.75–43° N) and the Meridionali box (44.25–46.5° N) do not overlap, so nothing is reused
  or overwritten.

## What runs

`meridionali_zarr/scripts/helper_download.py` downloads exactly like Andrei's NCAR lanes:
- pressure levels 600–1000 hPa from NCAR NCSS (12 requests at a time),
- 7 single-level variables also from NCAR (OPeNDAP, one request at a time, ~3 h per year alongside the 5 h of
  pressure levels). No CDS: user decision 19 Nov, "if ncar works we want both from ncar".

The helper never gives a year up: after an incomplete
try it waits 15 min and retries, keeping what it already has. Each finished year is verified (12/12 months) and gets a
`COMPLETE` file.

## 1. Get the script (Andrei)

Andrei commits and pushes the two new files:
- `meridionali_zarr/scripts/helper_download.py`
- `meridionali_zarr/HELPER_COMPUTER_STEPS.md`

## 2. Update the clone (Nandhni, on her computer)

```bash
cd <Nandhni's downscaling clone>        # the folder that contains rila_pirin_zarr/ and meridionali_zarr/
git pull
```

Nothing else to install: her Docker container `toposcale-rila-pirin` already has Python and all packages,
and sees the clone as `/app`.

## 3. Speed test (20 min)

```bash
docker exec -it toposcale-rila-pirin bash -c 'cd /app && python meridionali_zarr/scripts/helper_download.py --test-minutes 20 1940'
```

The last line prints e.g. `TEST END after 20 min: 140 units written (420 units/h ...)`. Send that line to Andrei.
The units downloaded in the test are kept: the real run continues 1940 from there.

## 4. Real run (days; independent of the terminal)

Years agreed with Andrei, oldest first, e.g. 1940 to 1965. Three helpers in parallel = 3 lanes, like Nandhni's own A/B/C.
Each helper gets its own years: never give the same year to two helpers.

```bash
docker exec -d toposcale-rila-pirin bash -c 'cd /app && python meridionali_zarr/scripts/helper_download.py $(seq 1940 1948) >> meridionali_zarr/data/helper_A.out 2>&1'
docker exec -d toposcale-rila-pirin bash -c 'cd /app && python meridionali_zarr/scripts/helper_download.py $(seq 1949 1957) >> meridionali_zarr/data/helper_B.out 2>&1'
docker exec -d toposcale-rila-pirin bash -c 'cd /app && python meridionali_zarr/scripts/helper_download.py $(seq 1958 1965) >> meridionali_zarr/data/helper_C.out 2>&1'
```

Progress:

```bash
tail -5 meridionali_zarr/data/era5_600-1000hPa_ncar/helper_log.md
```

Stop all helpers:

```bash
docker exec toposcale-rila-pirin pkill -f helper_download.py
```

Starting again resumes; finished years are skipped.

## 5. Bring the finished years back

Each finished year is a folder of ~315 MB with a `COMPLETE` file:
`meridionali_zarr/data/era5_600-1000hPa_ncar/<year>/`

Copy whole year folders, only those with `COMPLETE`, to Andrei's computer (USB stick or shared drive, e.g.
`D:\Downscaling\from_nandhni\`). Then Claude on Andrei's computer:
1. copies them into `meridionali_zarr/data/era5_600-1000hPa_ncar/<year>/`;
2. re-runs the downloader's verify (12/12 months);
3. only then lets the year be downscaled.

## Fallback without Docker

Use this only if the container is not available. Ubuntu/WSL, Python ≥ 3.11:

```bash
python3 -m venv ~/venv_helper
~/venv_helper/bin/pip install numpy pandas xarray zarr dask pyyaml scipy netCDF4 h5netcdf cfgrib eccodes ecmwf-datastores-client
```

Then run the same commands with `~/venv_helper/bin/python` from the clone root. A
copy of the 3 needed files is in `D:\Downscaling\helper_for_nandhni\` on Andrei's computer.
