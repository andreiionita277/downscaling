
## Environment
Python (pipeline, zarr_workflow) runs inside Docker: `docker compose up -d` in
this repo, then `docker exec -it toposcale-rila-pirin bash` and `cd /app`.
TopoPyScale comes from `../fork-original-code/TopoPyScale_Bulgaria_Rila_Pirin_Mountains`
(live-mounted at `/opt/TopoPyScale`, on PYTHONPATH); bulk data from `../rila-pirin-data`
(mounted at `/data`). Host-side notebooks use `../../venv-viewer`. There is no host
venv for TopoPyScale (the July 2026 `../../venv` was archived on 2026-10-16, see
`../../_archive/archived-2026-10-16_july-host-setup/README.md`).
