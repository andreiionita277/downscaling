"""
Helper download lane for a SECOND computer (19 Nov 2026, user: "clone ... on another computer ... from 1940 upward and
meet somewhere in the middle"). Downloads ERA5 years exactly like this computer's NCAR lanes (ncar_lane.py /
overnight_E.era_cfg, version 5g): pressure levels 600-1000 hPa (NCSS) and single levels (OPeNDAP) both from NCAR,
12 NCAR requests at a time, same timeouts. Never gives a year up: after an incomplete try it waits 15 min and resumes
(units already written are kept). A complete year is verified and marked COMPLETE, same marker text as here.

It does NOT use the claim files of overnight_E.py (the two computers cannot see each other's claims): the split of the
years between the computers is agreed by hand. The finished folders <out>/<year>/ are then copied to the main computer
into meridionali_zarr/data/era5_600-1000hPa_ncar/<year>/ and verified there again before downscaling.

Needs only 3 files (same relative layout as in the repo, or pass --template/--downloader):
    zarr_workflow/era5_download/era5_download.py
    meridionali_zarr/config/era5/config_era5_meridionali_2025.yml
    meridionali_zarr/scripts/helper_download.py              (this file)
No CDS key needed (everything from NCAR).

    python helper_download.py 1940 1941 1942 ...              # years in the order to do them
    python helper_download.py --test-minutes 20 1940           # speed test: stop after 20 min, print units/h
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent                       # .../meridionali_zarr/scripts
REPO = HERE.parents[1]                                       # .../downscaling
NCAR = dict(workers=12, backoff_seconds=60, timeout_seconds=450, connect_timeout_seconds=20, max_backoff_seconds=240)
RETRY_PAUSE_S = 15 * 60


def log(msg, logfile):
    line = f'- {time.strftime("%Y-%m-%d %H:%M:%S")} {msg}'
    print(line, flush=True)
    with open(logfile, 'a') as f:
        f.write(line + '\n')


def make_cfg(year, template, out):
    """Same settings as overnight_E.era_cfg(year, 'ncar'), with this computer's paths and no EDH cross-check."""
    cfg = yaml.safe_load(Path(template).read_text())
    cfg['period'] = {'start': f'{year}-01-01', 'end': f'{year}-12-31'}
    base = out / str(year)
    cfg['paths'] = {k: str(base / k) for k in ('raw', 'state', 'logs')}
    cfg['paths']['store'] = str(base / 'ERA5.zarr')
    cfg['ncar'].update(NCAR)
    cfg['cds'].update(workers=2, max_items_plev=60000, max_items_surf=60000)
    cfg['source'] = {'plev': 'ncar', 'surf': 'ncar'}       # user 19 Nov: both from NCAR (as overnight_E 5g)
    f = out / 'configs' / f'config_era5_meridionali_ncar_{year}.yml'
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f'# written by helper_download.py from {Path(template).name}\n' + yaml.safe_dump(cfg, sort_keys=False))
    return f


def count(path, pattern):
    try:
        return path.read_text(errors='replace').count(pattern)
    except FileNotFoundError:
        return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('years', type=int, nargs='+')
    ap.add_argument('--out', default=str(REPO / 'meridionali_zarr' / 'data' / 'era5_600-1000hPa_ncar'))
    ap.add_argument('--template', default=str(REPO / 'meridionali_zarr' / 'config' / 'era5' /
                                              'config_era5_meridionali_2025.yml'))
    ap.add_argument('--downloader', default=str(REPO / 'zarr_workflow' / 'era5_download' / 'era5_download.py'))
    ap.add_argument('--test-minutes', type=float, default=0, help='speed test: stop after this many minutes')
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    logfile = out / 'helper_log.md'
    log(f'helper started; years {a.years}; out {out}; python {sys.executable}', logfile)
    t_start = time.time()
    for y in a.years:
        if (out / str(y) / 'COMPLETE').exists():
            log(f'{y}: already COMPLETE, next year', logfile)
            continue
        cfg = make_cfg(y, a.template, out)
        attempt, t_year = 0, time.time()
        while True:
            attempt += 1
            rcs = []
            for n_pass in (1, 2):                               # as ncar_lane.py: a second pass right away
                plog = out / str(y) / f'helper_{y}_try{attempt}_pass{n_pass}_{time.strftime("%Y%m%d_%H%M")}.log'
                plog.parent.mkdir(parents=True, exist_ok=True)
                log(f'**{y}** try {attempt} pass {n_pass} -> {plog}', logfile)
                t0 = time.time()
                with open(plog, 'w') as fo:
                    p = subprocess.Popen([sys.executable, a.downloader, str(cfg), 'download'],
                                         stdout=fo, stderr=subprocess.STDOUT)
                    while p.poll() is None:
                        time.sleep(30)
                        if a.test_minutes and time.time() - t_start > a.test_minutes * 60:
                            p.terminate()
                            p.wait(120)
                            n, h = count(plog, 'written and verified'), (time.time() - t0) / 3600
                            log(f'TEST END after {a.test_minutes:.0f} min: {n} units written '
                                f'({n / h:.0f} units/h; this computer\'s lanes do 400-500/h each when NCAR is fast), '
                                f'{count(plog, "attempt ")} retried requests, {count(plog, "FAILED:")} given up', logfile)
                            return
                rcs.append(p.returncode)
                log(f'{y} try {attempt} pass {n_pass}: exit {p.returncode} after {(time.time() - t0) / 60:.0f} min, '
                    f'{count(plog, "written and verified")} units written, {count(plog, "FAILED:")} given up', logfile)
                if p.returncode == 0:
                    break
            vlog = out / str(y) / f'helper_{y}_verify.log'
            with open(vlog, 'w') as fo:
                v = subprocess.run([sys.executable, a.downloader, str(cfg), 'verify'], stdout=fo, stderr=subprocess.STDOUT)
            n_ok = count(vlog, 'OK hours')
            if v.returncode == 0 and n_ok == 12:
                (out / str(y) / 'COMPLETE').write_text(
                    f'{time.strftime("%Y-%m-%d %H:%M:%S")} verify exit 0, 12/12 months OK (helper computer)\n')
                log(f'**{y} COMPLETE** ({attempt} tries, {(time.time() - t_year) / 3600:.1f} h), verify 12/12 months OK',
                    logfile)
                break
            log(f'{y}: verify {n_ok}/12 months OK (exit {v.returncode}); retry in {RETRY_PAUSE_S // 60} min', logfile)
            time.sleep(RETRY_PAUSE_S)
    log('helper: all years done', logfile)


if __name__ == '__main__':
    main()
