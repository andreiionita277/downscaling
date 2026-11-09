"""
Download NOAA NCEI Global Hourly (ISD) station-year files from the public open-data bucket
https://noaa-global-hourly-pds.s3.amazonaws.com/<year>/<USAF><WBAN>.csv for every station in a list, for all years
between the station's BEGIN and END (from isd-history). One request at a time with a short pause; existing files are
skipped (resumable); years without a file (HTTP 403/404) are recorded in missing.csv and not retried.

    python meridionali_zarr/scripts/isd_download.py meridionali_zarr/data/stations/noaa_isd_stations_in_domain.csv
           [--out meridionali_zarr/data/stations/noaa_isd_hourly] [--pause 0.5]
Output: <out>/<year>/<USAF><WBAN>.csv, <out>/missing.csv, <out>/download_log.csv
"""
import argparse
import csv
import time
import urllib.error
import urllib.request
from pathlib import Path

BUCKET = 'https://noaa-global-hourly-pds.s3.amazonaws.com'
UA = 'research script (ERA5 downscaling validation, Southern Carpathians)'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('stations')
    p.add_argument('--out', default='meridionali_zarr/data/stations/noaa_isd_hourly')
    p.add_argument('--pause', type=float, default=0.5)
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    miss_f = out / 'missing.csv'
    missing = set()
    if miss_f.exists():
        missing = {(r['file'], r['year']) for r in csv.DictReader(open(miss_f))}
    jobs = []
    for s in csv.DictReader(open(a.stations)):
        sid = s['usaf'] + s['wban']
        for y in range(int(s['begin'][:4]), int(s['end'][:4]) + 1):
            jobs.append((sid, s['name'], y))
    print(f'{len(jobs)} station-years for {len({j[0] for j in jobs})} stations', flush=True)
    n_new = n_skip = n_miss = n_err = 0
    nbytes = 0
    t0 = time.time()
    log_f = open(out / 'download_log.csv', 'a', newline='')
    log_w = csv.writer(log_f)
    for i, (sid, name, y) in enumerate(jobs, 1):
        f = out / str(y) / f'{sid}.csv'
        if f.exists() or (f'{sid}.csv', str(y)) in missing:
            n_skip += 1
            continue
        url = f'{BUCKET}/{y}/{sid}.csv'
        for attempt in range(1, 5):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': UA})
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                f.parent.mkdir(exist_ok=True)
                tmp = f.with_suffix('.part')
                tmp.write_bytes(data)
                tmp.rename(f)
                n_new += 1
                nbytes += len(data)
                log_w.writerow([time.strftime('%Y-%m-%d %H:%M:%S'), sid, y, 'ok', len(data)])
                break
            except urllib.error.HTTPError as e:
                if e.code in (403, 404):
                    n_miss += 1
                    new = not miss_f.exists()
                    with open(miss_f, 'a', newline='') as m:
                        w = csv.writer(m)
                        if new:
                            w.writerow(['file', 'year', 'name', 'http'])
                        w.writerow([f'{sid}.csv', y, name, e.code])
                    break
                err = f'HTTP {e.code}'
            except Exception as e:  # noqa: BLE001
                err = str(e)[:100]
            time.sleep(10 * attempt)
        else:
            n_err += 1
            log_w.writerow([time.strftime('%Y-%m-%d %H:%M:%S'), sid, y, f'failed: {err}', 0])
        log_f.flush()
        if i % 50 == 0:
            print(f'{time.strftime("%H:%M:%S")} {i}/{len(jobs)}: {n_new} new ({nbytes / 1e6:.0f} MB), {n_skip} skipped, '
                  f'{n_miss} not on NOAA, {n_err} failed ({(time.time() - t0) / 60:.1f} min)', flush=True)
        time.sleep(a.pause)
    print(f'done: {n_new} new ({nbytes / 1e6:.0f} MB), {n_skip} skipped, {n_miss} not on NOAA, {n_err} failed '
          f'in {(time.time() - t0) / 60:.1f} min', flush=True)


if __name__ == '__main__':
    main()
