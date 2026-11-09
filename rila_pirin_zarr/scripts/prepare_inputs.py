"""
Inputs of the FABDEM run into rila_pirin_zarr/data/dem/ (real copies: ~/fabdem is not mounted in the container).

    python rila_pirin_zarr/scripts/prepare_inputs.py <fabdem_32634_30m.tif> <mask_30m.tif> rila_pirin_zarr/data/dem

Writes dem.tif, mask.tif (copies), groups.tif (elevation groups 1 <800, 2 800-1500, 3 1500-2000, 4 >=2000 m,
on the DEM grid itself, because the fork compares bounds with ==) and group_weights.csv (weight_g ~ pixels in
the mask x density coefficient 0.5/1/1.5/2, multiples of 1/1024 so that the fork's sum == 1 holds exactly).
"""
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parent))
from k_design_test import group_weights  # noqa: E402

EDGES = [800, 1500, 2000]          # group g+1 = number of edges <= z
COEF = [0.5, 1, 1.5, 2]            # user decision 2026-11-05 (LOG.md)


def main():
    dem_src, mask_src, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dem_src, out / 'dem.tif')
    shutil.copy2(mask_src, out / 'mask.tif')
    with rasterio.open(out / 'dem.tif') as d, rasterio.open(out / 'mask.tif') as m:
        if d.bounds != m.bounds or d.res != m.res or d.crs != m.crs:
            raise ValueError('DEM and mask grids differ')
        z = d.read(1)
        inside = m.read(1) == 1
        profile = d.profile | dict(dtype='uint8', nodata=0, compress='deflate')
    if not np.isfinite(z[inside]).all():
        raise ValueError('nodata inside the mask')
    groups = (np.digitize(z, EDGES) + 1).astype('uint8')
    with rasterio.open(out / 'groups.tif', 'w', **profile) as w:
        w.write(groups, 1)
    n = np.bincount(groups[inside], minlength=5)[1:]
    w = group_weights(n, COEF)
    gw = pd.DataFrame({'group': [1, 2, 3, 4], 'weight': w})
    gw.to_csv(out / 'group_weights.csv', index=False)
    check = pd.read_csv(out / 'group_weights.csv')
    assert check.weight.sum() == 1, check.weight.sum()
    print(pd.DataFrame({'group': [1, 2, 3, 4], 'band': ['<800', '800-1500', '1500-2000', '>=2000'], 'px': n,
                        'share': (n / n.sum()).round(4), 'coef': COEF, 'weight': w,
                        'clusters_k1000': np.ceil(1000 * w).astype(int)}).to_string(index=False))


if __name__ == '__main__':
    main()
