# Log 2026-10-27: number of clusters with TopoPyScale's `search_number_of_clusters` (no downscaling)

## What was run
- New script `scripts/k_search_helper.py`: calls `TopoPyScale.topo_sub.search_number_of_clusters` (fork,
  `topo_sub.py:215`) on the 2025 terrain (`data/meridionali_2025/outputs/ds_param.nc`, read only), pixels inside
  `mask.tif`, config features `x, y, elevation x4, slope, aspect_cos, aspect_sin, svf`, mini-batch k-means, seed 2.
- Not `Topoclass.search_optimum_number_of_clusters`: that wrapper ignores the mask and the seed.
- Random 10 M-pixel sample (21.75 % of 45,976,091 masked px), k scaled to the sample (same pixels per cluster as
  the real run), as `k_quick.py` (LOG.md §17, §25). Results reported for the real k.
- Command (from `downscaling/`):
  ```
  ../venv/bin/python meridionali_zarr/scripts/k_search_helper.py meridionali_zarr/config/config_meridionali_2025.yml
  ```
- 2 h 34 min, peak 6.8 GB RAM (Agisoft was running on Windows at the same time, so it was slower).
- Output: `data/meridionali_2025/outputs/k_search/{results.csv, summary.json, k_search.png}`,
  log `data/logs/k_search_20261027.log`.

## Result
| k | WCSS | Davies-Bouldin | Calinski-Harabasz | elev. RMSE (m) |
|---|---|---|---|---|
| 250 | 2.18e7 | 29.9 | 398,109 | 77.2 |
| 500 | 1.61e7 | 20.8 | 278,680 | 63.8 |
| 750 | 1.36e7 | 24.8 | 244,388 | 59.0 |
| 1000 | 1.19e7 | 33.8 | 221,585 | 55.9 |
| **1500** | 1.00e7 | 32.9 | 178,564 | 50.4 |
| 2000 | 8.89e6 | 30.2 | 158,272 | 47.5 |
| 2500 | 8.15e6 | 36.4 | 138,874 | 46.0 |
| 3000 | 7.56e6 | 35.6 | 124,261 | 43.7 |
| 4000 | 6.76e6 | 34.8 | 108,586 | 41.1 |
| 5000 | 6.12e6 | 37.2 | 99,013 | 39.1 |

- Elbow of WCSS: **k = 1500**; elbow of elevation RMSE: **k = 1500** (point farthest below the straight line
  from the first to the last point; depends on the tested range 250-5000).
- Davies-Bouldin min at 500 and Calinski-Harabasz max at 250: **not usable here**. The helper computes both on
  the unscaled, unweighted columns, where x/y in metres (range ~285 km) outweigh elevation; CH also falls
  steadily with k for terrain, which has no natural clusters.
- Elevation RMSE at 2000 (47.5 m) matches the earlier k test (47.2 m, LOG.md §25).
- 1500 -> 2000 gains 2.9 m RMSE (~0.02 K at 6.5 K/km). The 2000 used in the 2006-2025 runs is just past the elbow.
- Nothing in the downscaling outputs or configs was changed.
