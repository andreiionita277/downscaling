# Environment Setup Log

This project is developed across two machines. This file tracks the
baseline state of each and every change made, so the setup is reproducible.

## Machine 1 — Personal (home)

- OS: Ubuntu 24.04.4 LTS (native, not WSL)

## Machine 2 — Faculty lab machine (ICAM-B-104)

### Baseline (as found, 2026-07-30)

- Windows build: 10.0.26100 (Windows 11 24H2 — OsName/WindowsProductName
  strings disagree due to an upgrade-registry quirk; build number is
  the authoritative value)
- CPU: Intel i9-13900KF (13th gen, 8P+16E cores, no iGPU — irrelevant here)
- VS Code: 1.109.5
- QGIS: three versions present — 3.40.0, 3.40.4, 3.40.10
  → standardizing on 3.40.10 for plugin/processing-script development
- WSL: two distros present
  - Ubuntu-24.04 (24.04.4 LTS) — set as default, matches home machine exactly
  - Ubuntu (unidentified) — not used, left untouched (shared machine, not mine to remove)

### Changes made

- **Claude Code, native Windows**: installed via
  `irm https://claude.ai/install.ps1 | iex`
  - Fix: added `%USERPROFILE%\.local\bin` to User PATH
```powershell
    $currentPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
    [Environment]::SetEnvironmentVariable('PATH', "$currentPath;$env:USERPROFILE\.local\bin", 'User')
```
- **Claude Code, WSL (Ubuntu-24.04)**: installed via
  `curl -fsSL https://claude.ai/install.sh | bash`
  - Fix: added `~/.local/bin` to PATH
```bash
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```
- Verified both installs: `claude --version` → 2.1.220 (native Windows and WSL)
- Project working directory chosen: `~/nandhni/downscaling-topopyscale-project/`
  inside WSL (native Linux filesystem, not `/mnt/c/...`, for GDAL/IO performance)
- Cloned both repos here (see README.md for what each is)
- Installed TopoPyScale fork in editable mode: `pip install -e .`
  from inside `TopoPyScale_Bulgaria_Rila_Pirin_Mountains/`
- Added `upstream` remote to the fork:
  `git remote add upstream https://github.com/ArcticSnow/TopoPyScale.git`

### Verification checks

```bash
claude --version              # 2.1.220
python3 -c "import TopoPyScale; print(TopoPyScale.__file__)"
                               # should point INTO TopoPyScale_Bulgaria_Rila_Pirin_Mountains/,
                               # not a site-packages copy
```

## Environment build — resolved 2026-07-30

> **Archived 2026-10-16.** This host `venv` and the fork clone it was installed from
> (`TopoPyScale_Bulgaria_Rila_Pirin_Mountains` at 8668a5b, project root) were moved to
> `~/nandhni/downscaling-topopyscale-project/_archive/archived-2026-10-16_july-host-setup/`.
> Replaced by the Docker setup (`docker-compose.yml`, `docker/requirements.lock.txt`, fork in
> `repo-github/fork-original-code/`). The section below is kept as history.

Switched from plain pip/venv to `uv` mid-setup (see rationale: faster,
manages Python versions itself, better lockfile story for reproducibility).

### Final working sequence
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.13 venv
source venv/bin/activate

sudo apt install -y python3.13-dev   # required: topocalc compiles a C extension,
                                       # needs Python.h headers

cd TopoPyScale_Bulgaria_Rila_Pirin_Mountains
uv pip install xarray matplotlib pandas scikit-learn netcdf4 h5netcdf pyproj dask cdsapi zarr
uv pip install git+https://github.com/ArcticSnow/topocalc   # must be this fork,
                                                               # NOT plain PyPI topocalc —
                                                               # TopoPyScale needs
                                                               # ArcticSnow's C-extension version
uv pip install -e .
```

### Known quirks in this exact working set
- `numpy` was auto-downgraded by uv's resolver from 2.5.1 → 2.4.6 to satisfy
  `numba`'s compatibility range. Working combination locked in
  `requirements.lock.txt` — do not manually bump numpy without re-resolving
  the whole set.
- `topocalc` throws a harmless `SyntaxWarning: invalid escape sequence '\p'`
  on import (an unescaped backslash in a LaTeX docstring in their `viewf.py`)
  — cosmetic, not a functional issue.

### Verification (passed)
```bash
python3 -c "import TopoPyScale; print(TopoPyScale.__file__)"
python3 -c "from TopoPyScale import topo_param; print('topo_param ok')"
python3 -c "import topocalc; print(topocalc.__file__)"
```
All three point into the local fork / venv correctly, confirming the
editable install and the topocalc C-extension dependency both resolve.

### Reproducing this environment elsewhere
```bash
uv venv --python 3.13 venv
source venv/bin/activate
uv pip install -r TopoPyScale_Bulgaria_Rila_Pirin_Mountains/requirements.lock.txt
```
