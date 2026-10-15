"""
Fetch METER.AC station metadata and raw historical data for stations
inside a given AOI (Rila-Pirin thesis project).

METER.AC (https://meter.ac) data is released under CC0 (public domain).
robots.txt confirms unrestricted access: User-agent: * / Disallow: (empty)
Cite: Terziyski et al., 2020, Data, 5(2), 36. https://doi.org/10.3390/data5020036

IMPORTANT: run this once with a single node first (see --test-node) and
inspect the raw file before trusting it in your pipeline -- the exact
column format of data.raw.php has not been verified here.

Usage:
    python fetch_meterac_stations.py --mask rila_pirin_rect_buffered_32634.geojson --outdir data/meterac
    python fetch_meterac_stations.py --test-node N306 --outdir data/meterac   # quick single-station check
"""
import argparse
import csv
import io
import time
from pathlib import Path

import requests
import geopandas as gpd
from shapely.geometry import Point

NODES_CSV_URL = "https://meter.ac/gs/metadata/nodes.csv"

# honest UA is good practice even
# though robots.txt places no restriction on this.
HEADERS = {
    "User-Agent": "RilaPirinThesisResearch/1.0 (academic use; contact: nandhni.singh03@e-uvt.ro)"
}
REQUEST_DELAY_S = 1.5  # will not be agressive, hopefully not trigger any rate limiting on the server


def fetch_nodes_table():
    resp = requests.get(NODES_CSV_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return list(csv.DictReader(io.StringIO(resp.text)))


def filter_nodes_in_aoi(nodes, mask_path):
    aoi = gpd.read_file(mask_path).to_crs("EPSG:4326")
    aoi_geom = aoi.union_all() if hasattr(aoi, "union_all") else aoi.unary_union
    selected = []
    for row in nodes:
        try:
            lat, lon = float(row["Latitude"]), float(row["Longitude"])
        except (KeyError, ValueError):
            continue
        if aoi_geom.contains(Point(lon, lat)):
            selected.append(row)
    return selected


def download_station_history(node_id, outdir):
    url = f"https://meter.ac/gs/nodes/{node_id}/data.raw.php"
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    outpath = Path(outdir) / f"{node_id}_history_raw.txt"
    outpath.write_bytes(resp.content)
    return outpath


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mask", help="Path to AOI mask (any CRS, will be reprojected to EPSG:4326)")
    ap.add_argument("--outdir", default="data/meterac")
    ap.add_argument("--test-node", help="Fetch a single node ID (e.g. N306) to inspect format before scaling up")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if args.test_node:
        print(f"Fetching single test node {args.test_node} -- inspect this file's format before proceeding.")
        path = download_station_history(args.test_node, outdir)
        print(f"Saved to {path}. Open it and check the column structure.")
        return

    if not args.mask:
        ap.error("--mask is required unless using --test-node")

    print("Fetching node metadata...")
    nodes = fetch_nodes_table()
    print(f"  {len(nodes)} total nodes in network")

    selected = filter_nodes_in_aoi(nodes, args.mask)
    print(f"  {len(selected)} nodes fall inside AOI:")
    for row in selected:
        print(f"    {row['NodeID']}: {row['Location']} @ {row['Altitude']} m")

    if not selected:
        print("No nodes found inside the given mask -- check CRS/extent.")
        return

    # Save filtered metadata for provenance / your methods section
    meta_path = outdir / "selected_stations_metadata.csv"
    with open(meta_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=selected[0].keys())
        writer.writeheader()
        writer.writerows(selected)
    print(f"Saved selected station metadata to {meta_path}")

    for row in selected:
        node_id = row["NodeID"]
        print(f"Downloading history for {node_id} ({row['Location']})...")
        try:
            path = download_station_history(node_id, outdir)
            print(f"  saved to {path}")
        except requests.HTTPError as e:
            print(f"  failed: {e}")
        time.sleep(REQUEST_DELAY_S)

    print("Done. Inspect one raw file's actual columns before writing any parsing/validation code.")


if __name__ == "__main__":
    main()