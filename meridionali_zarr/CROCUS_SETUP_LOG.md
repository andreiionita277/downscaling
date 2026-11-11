# How SURFEX-Crocus was made to work on this computer (9 Nov 2026)

Computer: Windows with WSL2, Ubuntu 24.04, gfortran 13.3, 24 cores, 23 GB RAM for WSL.
Result: SURFEX V8.1 (Crocus branch `cen`) compiled, snowtools runs it, a full year on 3001 points in under 3 minutes,
and the snowtools GUI shows the results.

The short version: **three things blocked the build** (one cosmetic, two real), and **one thing blocked snowtools**.
All four are fixed by the commands in section 6.

---

## 1. What was on the computer before

| What | Where | State |
|---|---|---|
| SURFEX source code | `~/git/surfex` (git branch `cen`, the Crocus development version; SFX-V8-1-1) | present |
| Build configuration ("profile"), made by `./configure` in May 2025 | `~/git/surfex/conf/profile_surfex-LXgfortran-SFX-V8-1-1-NOMPI-OMP-O2-X0` | present |
| Compiled SURFEX objects | `~/git/surfex/src/dir_obj-LXgfortran-SFX-V8-1-1-NOMPI-OMP-O2-X0/` | **0 compiled files**: the May attempt never got that far |
| SURFEX programs | `~/git/surfex/exe/` | **empty** |
| Links to the programs | `~/SURFEX/PGD`, `PREP`, `OFFLINE`, `SODA` | **broken**: pointed to `~/git/surfex/OFFLINE-LXgfortran-...-NOMPI-O2-X0`, which never existed |
| Libraries bundled with SURFEX (zlib, szip, curl 7.50.1, HDF5 1.12.0, netCDF-C 4.7.4) | `~/git/surfex/src/LIB/netcdf4/*-LXgfortran-NOMPI` | built in May |
| netCDF-Fortran 4.5.3 (bundled) | same place | **never built**: this is where the May attempt stopped |
| snowtools (Météo-France tools to run Crocus) | `~/git/snowtools` | present; `~/.bashrc` sets `SNOWTOOLS_CEN` and `PYTHONPATH` |
| Python for snowtools | conda environment `surfex` (`~/miniconda3/envs/surfex`) | works (`footprints` is not needed for research runs) |
| System netCDF (Ubuntu packages) | `/usr/include/netcdf.mod`, `/usr/lib/x86_64-linux-gnu/libnetcdf(f).so` | netCDF-C 4.9.2, netCDF-Fortran 4.5.4 |

---

## 2. Problem 1 (cosmetic): the profile "fails" when loaded

**What happened.** The build command was `. ../conf/profile_surfex-... && make ...`. It stopped immediately.

**Why.** The last line of the profile is `[ -f ${SRC_SURFEX}/conf/conf_${ARCH} ] && . ...`, an optional file that does
not exist here. That test returns "false", so loading the profile ends with exit code 1, and `&&` stops. The profile
itself was loaded correctly.

**Fix.** Load the profile with `;` after it, not `&&`.

---

## 3. Problem 2 (real): the bundled netCDF-Fortran does not build

**Error** (in the build log):

```
checking for library containing nc_open... no
configure: error: Could not link to netcdf C library. Please set LDFLAGS; for static builds set LIBS to the results of nc-config --libs.
make: *** [Makefile:510: .../netcdf-fortran-4.5.3-LXgfortran-NOMPI/include/netcdf.mod] Error 1
```

**Why** (found in `~/git/surfex/src/LIB/netcdf4/netcdf-fortran-4.5.3/config.log`):

```
.../netcdf-c-4.7.4-LXgfortran-NOMPI/lib/libnetcdf.so: undefined reference to `curl_easy_init@CURL_OPENSSL_4'
```

The bundled netCDF-C was built against Ubuntu's own libcurl (symbols tagged `CURL_OPENSSL_4`), but the
netCDF-Fortran configure test links without `-lcurl`. So the test fails, and with it the whole build.
**This is the same error that stopped the May 2025 attempt.**

**Fix.** Do not use the bundled netCDF at all: use Ubuntu's netCDF, which is built properly. SURFEX's build already has
an option for a system netCDF in `/usr`: `VER_CDF=CDFCTI` (in `src/Makefile.SURFEX.mk`: `-lnetcdff -lnetcdf -lhdf5_hl
-lhdf5 -lsz -lz`, with `-L/usr/lib/x86_64-linux-gnu/hdf5/serial/`).

Checked first with a tiny Fortran program (it printed ` 4.9.2 of Mar 31 2024`):

```bash
echo 'program t; use netcdf; print *, trim(nf90_inq_libvers()); end program' > t.f90
gfortran t.f90 -I/usr/include -L/usr/lib/x86_64-linux-gnu/hdf5/serial -lnetcdff -lnetcdf -lhdf5_hl -lhdf5 -lsz -lz -o t && ./t
```

---

## 4. Problem 3 (real): the programs do not link

With `VER_CDF=CDFCTI` all of SURFEX compiled (5277 object files, about 2 minutes with `make -j 8`), but making the
programs failed:

```
/usr/bin/ld: /usr/lib/x86_64-linux-gnu/hdf5/serial//libhdf5.so: undefined reference to `curl_easy_cleanup@CURL_OPENSSL_4'
collect2: error: ld returned 1 exit status
make[1]: *** [Makefile:637: PGD] Error 1
```

**Why.** Ubuntu's HDF5 needs Ubuntu's libcurl. But the profile puts the **bundled** libraries on `LD_LIBRARY_PATH`,
including the old bundled curl 7.50.1:

```
~/git/surfex/src/LIB/netcdf4/netcdf-fortran-4.5.3-LXgfortran-NOMPI/lib
~/git/surfex/src/LIB/netcdf4/netcdf-c-4.7.4-LXgfortran-NOMPI/lib
~/git/surfex/src/LIB/netcdf4/hdf5-1.12.0-LXgfortran-NOMPI/lib
~/git/surfex/src/LIB/netcdf4/szip-2.1-LXgfortran-NOMPI/lib
~/git/surfex/src/LIB/netcdf4/zlib-1.2.8-LXgfortran-NOMPI/lib
~/git/surfex/src/LIB/netcdf4/curl-7.50.1-LXgfortran-NOMPI/lib      <- this one
```

The linker uses `LD_LIBRARY_PATH` to find the libraries that other libraries need, so it took the old curl, which does
not have the `CURL_OPENSSL_4` symbols.

**Fix.** After loading the profile, `unset LD_LIBRARY_PATH` (with the system netCDF, none of the bundled libraries are
needed). The already compiled files are kept, so the rerun only links.

Check that the programs find all their libraries (no "not found", netCDF from the system):

```bash
ldd ~/git/surfex/exe/OFFLINE-LXgfortran-SFX-V8-1-1-NOMPI-OMP-O2-X0 | grep -E 'not found|netcdf'
```

---

## 5. Problem 4 (snowtools): "Unknown file ... ecoclimapI_covers_param.bin"

**Error** (running snowtools' `s2m`, first with `-s ~/SURFEX`):

```
snowtools.utils.FileException.FileNameException: Unknown file : /home/andreiionita27/SURFEX/../MY_RUN/ECOCLIMAP/ecoclimapI_covers_param.bin
```

**Why.** snowtools looks for SURFEX's data files in `MY_RUN/` **next to the folder of the programs** (`<exe folder>/../MY_RUN`).
With `-s ~/SURFEX` that is `~/MY_RUN`, which does not exist. The files are in `~/git/surfex/MY_RUN/ECOCLIMAP/`
(the `.bin` files are made by the build).

**Fix.** Give snowtools `-s ~/git/surfex/exe`. In that folder the programs have long names
(`OFFLINE-LXgfortran-SFX-V8-1-1-NOMPI-OMP-O2-X0`), so short links were added: `PGD`, `PREP`, `OFFLINE`, `SODA`.

---

## 6. The commands that work

### 6.1 Build (or rebuild) SURFEX

```bash
cd ~/git/surfex/src
env -u SRC_SURFEX bash -c '. ../conf/profile_surfex-LXgfortran-SFX-V8-1-1-NOMPI-OMP-O2-X0; export VER_CDF=CDFCTI; unset LD_LIBRARY_PATH; make -j 8 && make installmaster'
cd ~/git/surfex/exe && for p in PGD PREP OFFLINE SODA; do ln -sfn $p-LXgfortran-SFX-V8-1-1-NOMPI-OMP-O2-X0 $p; done
```

- `env -u SRC_SURFEX`: start without an old SURFEX setting (the profile warns otherwise).
- `;` after the profile: problem 1. `VER_CDF=CDFCTI`: problem 2. `unset LD_LIBRARY_PATH`: problem 3.
- `make installmaster` puts the programs (links) in `~/git/surfex/exe/`; the last line adds the short names (problem 4).
- Build logs of 9 Nov: `~/git/surfex/build_20261109_*.log`.
- `~/SURFEX/PGD`, `PREP`, `OFFLINE`, `SODA` were also repointed to the new programs.

### 6.2 Make the Crocus forcing from the downscaling (TopoPyScale)

```bash
cd ~/TopoPyScale-repo-github/downscaling
../venv/bin/python meridionali_zarr/scripts/export_surfex.py meridionali_zarr/config/config_meridionali_E_2024.yml \
    meridionali_zarr/config/config_meridionali_E_2025.yml --start 2024-09-01 --end 2025-08-31 --out meridionali_zarr/data/surfex_E
```

Result: `data/surfex_E/FORCING_2024090100_2025083123.nc` (3001 points × 8760 h, 2.7 GB, 8 min). The script checks
every hour, no missing values, temperature = downscaled temperature, rain + snow = precipitation, elevations = clusters.
It has every variable of snowtools' reference forcing (`~/git/snowtools/snowtools/DATA/FORCING_test_base.nc`).

### 6.3 Run Crocus with snowtools

One forcing file (e.g. a few points):

```bash
cd <run folder>
PYTHONPATH=$HOME/git/snowtools ~/miniconda3/envs/surfex/bin/python -m snowtools.tasks.s2m_command research \
    -b 2024090100 -e 2025083123 -f <FORCING file> -o output -w work -s $HOME/git/surfex/exe -g
```

- `-g`: the starting ground temperature is computed from the mean air temperature of the period (needed when there is
  no earlier PREP file), then the simulation runs. Options: snowtools' default `OPTIONS_V8.1_NEW_OUTPUTS_NC.nam`.
- Success = the log ends with `OFFLINE ENDS CORRECTLY`.
- Output: `output/pro/PRO_<begin>_<end>.nc` (snow every 6 h, 50 layers), `output/prep/PGD.nc`, `PREP_*.nc`.

All points in parallel (the build is single-process, so the points are split into chunks):

```bash
cd ~/TopoPyScale-repo-github/downscaling
../venv/bin/python meridionali_zarr/scripts/crocus_parallel.py meridionali_zarr/data/surfex_E/FORCING_2024090100_2025083123.nc \
    --out meridionali_zarr/data/crocus_E/2024-2025 --chunks 12 --parallel 12
```

Result: 12 × `chunk_NN/output/pro/PRO_2024090100_2025083123.nc` (about 2 GB each, 26 GB in all), 2.8 minutes;
`chunk_NN/points.csv` gives the cluster (`point_ind`) of each point.

### 6.4 Look at the results

GUI (opens as a window on Windows through WSLg):

```bash
PYTHONPATH=~/git/snowtools ~/miniconda3/envs/surfex/bin/python -m snowtools.plots.stratiprofile.proplotter <PRO file>
```

In Python (xarray): open PRO files with `decode_times=False` (the variable `SAG_VEG` has the unit "days since snowfall",
which xarray mistakes for a date), then convert `time` with `netCDF4.num2date`.

---

## 7. Quick troubleshooting

| Message | Cause | Fix |
|---|---|---|
| build stops right after loading the profile, no `make` output | profile ends with exit code 1 | `;` instead of `&&` after `. profile` |
| `Could not link to netcdf C library` (netcdf-fortran) | bundled netCDF-C needs libcurl, test links without it | `export VER_CDF=CDFCTI` (system netCDF) |
| `undefined reference to curl_...@CURL_OPENSSL_4`, `collect2: error`, at PGD/PREP | bundled old curl on `LD_LIBRARY_PATH` | `unset LD_LIBRARY_PATH` after the profile |
| snowtools: `Unknown file : .../MY_RUN/ECOCLIMAP/ecoclimapI_covers_param.bin` | snowtools looks for `MY_RUN` next to the program folder | `-s ~/git/surfex/exe` + short links PGD/PREP/OFFLINE/SODA |
| xarray: `unable to decode time units 'days since snowfall'` | snow age variable `SAG_VEG` | `xr.open_dataset(..., decode_times=False)` |

The project log (`LOG_28Oct2026.md`, 9 Nov) has the same steps in time order.
