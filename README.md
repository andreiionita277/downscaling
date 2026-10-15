# Rila-Pirin ERA5 Downscaling

Master's thesis: spatial downscaling of ERA5 air temperature (2m) for the
Rila-Pirin mountains, Bulgaria, using TopoPyScale, containerized with Docker.

- Original library: https://github.com/ArcticSnow/TopoPyScale
- Fork with local patches: https://github.com/Nandhni03/TopoPyScale_Bulgaria_Rila_Pirin_Mountains
  (sibling folder on disk, installed in editable mode — see SETUP.md)

## Environment

Uses `uv` for Python environment and dependency management. See
`SETUP.md` for the full install sequence, system-level dependencies,
and known quirks. Developed across two machines (personal + faculty lab) —
both details tracked there too.

## Data sources

- DEM: EU-DEM v1.1, 25m (archived Copernicus dataset)
- Climate forcing: ERA5 (single-levels + pressure-levels), via CDS API
- Validation: METER.AC open station network (CC0), stations filtered to
  the Rila-Pirin AOI

## Contents

- `config.yml` — active project configuration
- `pipeline.py`, `test_step*.py` — pipeline entry points, staged for testing
- `inputs/`, `outputs/` — data (gitignored where large)
- `docker/`, `docker-compose.yml` — containerized run setup
- `ex2_romania_retezat/` — reference example, used to validate config structure

## Status

See git tags in this repo for milestone results.
