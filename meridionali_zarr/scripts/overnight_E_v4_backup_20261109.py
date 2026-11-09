"""
Overnight driver, setup E (3001 clusters in 4 elevation groups, LOG_27Oct.md), years 2025 -> 1990, one year at a time.
Raw Zarr only (hourly down.zarr + daily.zarr), NO GeoTIFF, no SURFEX. Started 2026-10-27 (user away; log in
LOG_downscalingTest_Overnight.md).

    cd ~/TopoPyScale-repo-github/downscaling
    setsid nohup ../venv/bin/python meridionali_zarr/scripts/overnight_E.py 2025 2024 ... 1990 \
        > meridionali_zarr/data/logs/overnight_E/driver.out 2>&1 < /dev/null &
    stop:  pkill -f overnight_E.py; pkill -f run_year_public.py; pkill -f era5_download.py
Version 2 (23:05): a year's NCAR download stops as soon as ONE unit has failed its 5 tries (before: only when the
whole download command ended, which with NCAR this slow would have taken days); its incomplete months go to the CDS.
Version 4 (29 Oct, after the PC restarted at 03:41): NCAR checked again, gently (user: "check again ncar, but without
being aggressive"): 2 NCAR requests at a time, longer backoff. Lanes A and B each claim the newest unsettled year nobody
works on. A failed NCAR try no longer queues the year for the CDS: it releases it, so the CDS lane takes it when free;
otherwise NCAR retries it after NCAR_RETRY_PAUSE_S. CDS_ONLY=1 still switches NCAR off.

Three lanes (threads), each running its steps as subprocesses:
  A  ERA5 from NCAR, one year after the other (Nandhni's era5_download.py, 8 parallel requests). A unit is retried
     5 times by the downloader; if units still fail (exit 1), or no unit arrives for 45 min (NCAR down), the year's
     incomplete months go to lane B. After such a fallback lane A pauses 30 min before the next year.
  B  ERA5 from the CDS for those months only (same store; a CDS unit rewrites its whole month), gently: 2 jobs at a
     time (the CDS runs ONE per user; the 2nd waits in the queue), <= 60,000 values per request.
  C  Downscaling of every year whose store is complete (newest first): terrain linked/copied from
     meridionali_F_2025 (no reclustering), run_year_public.py --no-geotiff, up to 3 attempts (resumable).
     Not before START_DOWNSCALING (Agisoft); 6 workers while Agisoft is busy, else 14.
Every step is resumable, so running the same command again continues where it stopped.
"""
import filecmp
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
import yaml

ROOT = Path(__file__).resolve().parents[2]                     # .../downscaling
MZ = ROOT / 'meridionali_zarr'
PY = str(ROOT.parent / 'venv' / 'bin' / 'python')
DL = str(ROOT / 'zarr_workflow' / 'era5_download' / 'era5_download.py')
RUN = str(MZ / 'scripts' / 'run_year_public.py')
LOG = MZ / os.environ.get('OVERNIGHT_LOG', 'LOG_downscalingTest_Overnight.md')   # 28 Oct: LOG_28Oct2026.md
LOGDIR = MZ / 'data' / 'logs' / 'overnight_E'
TERRAIN = MZ / 'data' / 'meridionali_F_2025' / 'outputs'       # setup E clusters (computed 2026-10-27 18:15-18:59)
ERA_TEMPLATE = MZ / 'config' / 'era5' / 'config_era5_meridionali_2025.yml'
RUN_TEMPLATE = MZ / 'config' / 'config_meridionali_F_2025-01.yml'
EDH_STORE = MZ / 'data' / 'era5_meridionali' / 'ERA5.zarr'
LEVELS = [600, 650, 700, 750, 775, 800, 825, 850, 875, 900, 925, 950, 975, 1000]

START_DOWNSCALING = time.time() + max(0, 60 * 60 - (time.time() - 1793127420))   # Agisoft: not before 21:57 (user)
NCAR_WORKERS = 2                 # version 4: gentle (was 8)
NCAR_BACKOFF_S = 120             # downloader waits 2, 4, 8, 16 min between tries of a unit (was 60 s base)
NCAR_STALL_S = 45 * 60
NCAR_RETRY_PAUSE_S = 60 * 60     # after a failed NCAR try, before NCAR claims a year again

_lock = threading.Lock()
ready = {}            # year -> 'ok' when the store is complete (lane A or B), 'failed' when given up
done = set()          # years downscaled
claimed = {}          # year -> 'ncar' / 'cds' while a lane works on it
cds_years = set()     # years the CDS lane has taken this run (NCAR leaves them alone)
stop = threading.Event()


def log(msg):
    line = f'- {time.strftime("%H:%M:%S")} {msg}'
    with _lock:
        with open(LOG, 'a') as f:
            f.write(line + '\n')
        print(line, flush=True)


def sh(cmd, out, **kw):
    """Run a command, stdout+stderr to file `out`; return the Popen."""
    out.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(cmd, cwd=ROOT, stdout=open(out, 'w'), stderr=subprocess.STDOUT, **kw)


# ------------------------------------------------------------------------------------------------ configs
def era_cfg(year, source):
    cfg = yaml.safe_load(ERA_TEMPLATE.read_text())
    cfg['period'] = {'start': f'{year}-01-01', 'end': f'{year}-12-31'}
    base = MZ / 'data' / 'era5_600-1000hPa' / str(year)
    cfg['paths'] = {k: str(base / k) for k in ('raw', 'state', 'logs')}
    cfg['paths']['store'] = str(base / 'ERA5.zarr')
    t = xr.open_zarr(EDH_STORE).time.values
    if t.min() <= np.datetime64(f'{year}-01-01') and t.max() >= np.datetime64(f'{year}-12-31T23'):
        cfg['paths']['edh_store'] = str(EDH_STORE)                  # cross-check at the 5 shared levels
    cfg['ncar']['workers'] = NCAR_WORKERS
    cfg['ncar']['backoff_seconds'] = NCAR_BACKOFF_S
    cfg['cds'].update(workers=2, max_items_plev=60000, max_items_surf=60000)
    if source == 'cds':
        cfg['source'] = {'plev': 'cds', 'surf': 'cds'}
    f = MZ / 'config' / 'era5' / f'config_era5_meridionali_{year}{"_cds" if source == "cds" else ""}.yml'
    f.write_text(f'# written by scripts/overnight_E.py from {ERA_TEMPLATE.name} ({source}); see '
                 f'LOG_downscalingTest_Overnight.md\n' + yaml.safe_dump(cfg, sort_keys=False))
    return f, cfg


def run_cfg(year, cores):
    s = RUN_TEMPLATE.read_text()
    rep = [(r'(?m)^  name: .*', f'  name: Meridionali E {year}'),
           (r'(?m)^  description: .*', f'  description: Southern Carpathians {year}, setup E (3001 clusters, 4 elevation '
                                       f'groups), ERA5 600-1000 hPa (NCAR/CDS), raw Zarr'),
           (r'(?m)^  directory: /.*', f'  directory: {MZ}/data/meridionali_E_{year}'),
           (r'(?m)^  start: .*', f'  start: {year}-01-01'),
           (r'(?m)^  end: .*', f'  end: {year}-12-31'),
           (r'(?m)^    path: .*era5_600-1000hPa/2025', f'    path: {MZ}/data/era5_600-1000hPa/{year}'),
           (r'(?m)^        CPU_cores: .*', f'        CPU_cores: {cores}                     # set by overnight_E.py')]
    for a, b in rep:
        s, n = re.subn(a, b, s)
        assert n == 1, a
    s = f'# written by scripts/overnight_E.py from {RUN_TEMPLATE.name}; see LOG_downscalingTest_Overnight.md\n' + s
    f = MZ / 'config' / f'config_meridionali_E_{year}.yml'
    f.write_text(s)
    return f


# ------------------------------------------------------------------------------------------------ ERA5 checks
def store_complete(year):
    """Every hour of the year present without NaN, 14 levels (same check run_year_public.py does)."""
    p = MZ / 'data' / 'era5_600-1000hPa' / str(year) / 'ERA5.zarr'
    if not p.exists():
        return False, 'no store'
    ds = xr.open_zarr(p)
    tvec = pd.date_range(f'{year}-01-01', f'{year}-12-31 23:00', freq='1h')
    if len(tvec.difference(pd.DatetimeIndex(ds.time.values))):
        return False, 'hours missing'
    if ds.level.values.astype(float).tolist() != [float(x) for x in LEVELS]:
        return False, 'levels differ'
    sub = ds.sel(time=tvec)
    bad = {}
    for v in sub.data_vars:
        a = sub[v].isnull()
        n = int(a.sum())
        if n:
            months = sorted({int(m) for m in pd.DatetimeIndex(sub.time.values[a.any(
                dim=[d for d in a.dims if d != 'time']).values]).month})
            bad[v] = months
    if bad:
        return False, bad
    return True, 'complete'


def incomplete_months(year):
    ok, why = store_complete(year)
    if ok:
        return []
    if isinstance(why, dict):
        return sorted({m for ms in why.values() for m in ms})
    return list(range(1, 13))


def claim(years, lane):
    """Newest year not settled and not worked on by the other lane (NCAR skips the CDS lane's years); None if none."""
    with _lock:
        for y in years:
            if ready.get(y) is None and y not in claimed and not (lane == 'ncar' and y in cds_years):
                claimed[y] = lane
                if lane == 'cds':
                    cds_years.add(y)
                return y
    return None


def tail_count(path, pattern):
    try:
        return path.read_text(errors='replace').count(pattern)
    except FileNotFoundError:
        return 0


# ------------------------------------------------------------------------------------------------ lane A: NCAR
CDS_ONLY = os.environ.get('CDS_ONLY') == '1'    # user 28 Oct 11:10: NCAR failed every year overnight -> CDS only


def lane_ncar(years):
    if CDS_ONLY:
        log('NCAR lane: off (CDS_ONLY=1)')
        return
    while not stop.is_set():
        y = claim(years, 'ncar')
        if y is None:
            break
        ok, why = store_complete(y)
        if ok:
            log(f'**{y} ERA5**: store already complete')
            ready[y] = 'ok'
            claimed.pop(y, None)
            continue
        f, _ = era_cfg(y, 'ncar')
        out = LOGDIR / f'era5_{y}_ncar_{time.strftime("%Y%m%d_%H%M")}.log'
        log(f'**{y} ERA5 NCAR** started ({NCAR_WORKERS} requests at a time) -> `{out.relative_to(MZ)}`')
        t0 = time.time()
        p = sh([PY, DL, str(f), 'download'], out)
        last_n, last_t, stalled = 0, time.time(), False
        while p.poll() is None:
            time.sleep(60)
            n = tail_count(out, 'written and verified')
            if n > last_n:
                last_n, last_t = n, time.time()
            n_gave_up = tail_count(out, 'FAILED:')
            if n_gave_up:
                # user's rule: a unit that failed 5 tries -> stop NCAR for this year (released: CDS lane or a later NCAR try)
                stalled = True
                log(f'{y} ERA5 NCAR: {n_gave_up} unit(s) failed 5 tries ({n} written so far, '
                    f'{tail_count(out, "attempt ")} retried attempts): NCAR too slow/unreliable, download stopped')
                p.terminate()
                p.wait(120)
                break
            if time.time() - last_t > NCAR_STALL_S:
                stalled = True
                log(f'{y} ERA5 NCAR: no unit written for {NCAR_STALL_S // 60} min ({n} written so far, '
                    f'{tail_count(out, "failed")} failed attempts): NCAR treated as down, download stopped')
                p.terminate()
                p.wait(120)
                break
            if stop.is_set():
                p.terminate()
                return
        n_ok, n_fail = tail_count(out, 'written and verified'), tail_count(out, 'FAILED:')
        log(f'{y} ERA5 NCAR finished in {(time.time() - t0) / 60:.0f} min (exit {p.returncode}): {n_ok} units written '
            f'this run, {n_fail} given up after 5 attempts, {tail_count(out, "attempt ")} retried attempts')
        months = incomplete_months(y)
        if not months:
            ready[y] = 'ok'
            log(f'**{y} ERA5 complete** (all 8760/8784 h, 14 levels, 13 variables, no NaN)')
            vout = LOGDIR / f'era5_{y}_verify.log'
            v = subprocess.run([PY, DL, str(f), 'verify'], cwd=ROOT, stdout=open(vout, 'w'), stderr=subprocess.STDOUT)
            log(f'{y} ERA5 verify (12 month validations, + EDH comparison at 5 levels for 2006-2025): '
                f'{"OK" if v.returncode == 0 else "FAILED"} -> `{vout.relative_to(MZ)}`')
            claimed.pop(y, None)
        else:
            claimed.pop(y, None)
            log(f'{y} ERA5: months {months} incomplete after NCAR -> released (the CDS lane takes it when free); '
                f'NCAR pauses {NCAR_RETRY_PAUSE_S // 60} min')
            for _ in range(NCAR_RETRY_PAUSE_S // 30):
                if stop.is_set():
                    return
                time.sleep(30)
    log('NCAR lane: all years handled')


# ------------------------------------------------------------------------------------------------ lane B: CDS
def other_writer(year):
    """PIDs of an era5_download.py still writing this year's store (e.g. left running by an earlier driver)."""
    r = subprocess.run(['pgrep', '-f', rf'era5_download\.py .*config_era5_meridionali_{year}(_cds)?\.yml'],
                       capture_output=True, text=True)
    return r.stdout.split()


def lane_cds(years):
    while not stop.is_set():
        y = claim(years, 'cds')
        if y is None:
            if all(ready.get(x) for x in years):
                break
            time.sleep(60)                                        # NCAR still on the last years
            continue
        pids = other_writer(y)
        if pids:
            # one writer per store: let it finish (its CDS jobs are in flight), then check what is still missing
            log(f'{y} ERA5: downloader from the previous driver still running (pid {" ".join(pids)}); waiting for it')
            while other_writer(y) and not stop.is_set():
                time.sleep(60)
            log(f'{y} ERA5: previous downloader finished')
        f, _ = era_cfg(y, 'cds')
        for attempt in range(1, 4):
            months = incomplete_months(y)
            if not months:
                break
            out = LOGDIR / f'era5_{y}_cds_try{attempt}_{time.strftime("%Y%m%d_%H%M")}.log'
            log(f'**{y} ERA5 CDS** attempt {attempt}, months {months} (2 jobs at a time, <= 60,000 values per request) '
                f'-> `{out.relative_to(MZ)}`')
            t0 = time.time()
            r = subprocess.run([PY, DL, str(f), 'download', '--months', *map(str, months)], cwd=ROOT,
                               stdout=open(out, 'w'), stderr=subprocess.STDOUT)
            log(f'{y} ERA5 CDS attempt {attempt} finished in {(time.time() - t0) / 60:.0f} min (exit {r.returncode}): '
                f'{tail_count(out, "written and verified")} units written, {tail_count(out, "FAILED:")} failed')
        months = incomplete_months(y)
        if months:
            ready[y] = 'failed'
            log(f'**{y} ERA5 FAILED**: months {months} still incomplete after NCAR + 3 CDS attempts; year skipped')
        else:
            ready[y] = 'ok'
            log(f'**{y} ERA5 complete** (NCAR + CDS)')
        claimed.pop(y, None)
    log('CDS lane: all years handled')


# ------------------------------------------------------------------------------------------------ lane C: downscaling
def agisoft_busy():
    """True if Metashape used more than one core on average over 2 min (Windows process list via PowerShell)."""
    ps = '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
    q = "(Get-Process | Where-Object { $_.ProcessName -match 'metashape' } | Measure-Object CPU -Sum).Sum"
    try:
        a = float(subprocess.run([ps, '-NoProfile', '-Command', q], capture_output=True, text=True, timeout=60).stdout or 0)
        time.sleep(120)
        b = float(subprocess.run([ps, '-NoProfile', '-Command', q], capture_output=True, text=True, timeout=60).stdout or 0)
    except Exception as e:  # noqa: BLE001
        log(f'Agisoft check failed ({e}); assuming busy')
        return True
    return (b - a) / 120 > 1.0


def prepare_terrain(year):
    out = MZ / 'data' / f'meridionali_E_{year}' / 'outputs'
    out.mkdir(parents=True, exist_ok=True)
    for name in ('ds_param.nc', 'da_horizon.nc'):
        if not (out / name).exists():
            os.link(TERRAIN / name, out / name)                 # same file, no space; TopoPyScale replaces, not edits
    if not (out / 'df_centroids.pck').exists():
        shutil.copy2(TERRAIN / 'df_centroids.pck', out / 'df_centroids.pck')   # copied: compute_horizon rewrites it
    return out


def year_result(year):
    out = MZ / 'data' / f'meridionali_E_{year}' / 'outputs'
    import json
    st = {}
    for f in sorted((out / 'months').glob('*.json')):
        st[f.stem] = json.loads(f.read_text()).get('status')
    return st


def lane_downscale(years):
    while time.time() < START_DOWNSCALING and not stop.is_set():
        time.sleep(30)
    while not stop.is_set():
        todo = [y for y in years if ready.get(y) == 'ok' and y not in done]
        pending = [y for y in years if ready.get(y) is None]
        if not todo:
            if not pending and all(ready.get(y) for y in years):
                log('Downscaling lane: nothing left to do')
                return
            time.sleep(300)
            continue
        y = todo[0]
        busy = agisoft_busy()
        cores = 6 if busy else 14
        cfg = run_cfg(y, cores)
        out_dir = prepare_terrain(y)
        log(f'**{y} downscaling** started ({cores} workers{", Agisoft busy" if busy else ""}); terrain linked from '
            f'meridionali_F_2025; config `{cfg.relative_to(MZ)}`')
        t0 = time.time()
        for attempt in range(1, 4):
            out = LOGDIR / f'run_{y}_try{attempt}_{time.strftime("%Y%m%d_%H%M")}.log'
            r = subprocess.run(['/usr/bin/time', '-v', PY, RUN, str(cfg), '--no-geotiff'], cwd=ROOT,
                               stdout=open(out, 'w'), stderr=subprocess.STDOUT)
            txt = out.read_text(errors='replace')
            dsl = re.findall(r'Downscaling: .*', txt)
            peak = re.findall(r'Maximum resident set size \(kbytes\): (\d+)', txt)
            st = year_result(y)
            n_ok = sum(1 for s in st.values() if s and s.startswith('ok'))
            log(f'{y} downscaling attempt {attempt}: exit {r.returncode}, {(time.time() - t0) / 60:.0f} min, '
                f'peak {int(peak[-1]) / 1e6 if peak else 0:.1f} GB; {dsl[-1] if dsl else "no downscaling line"}; '
                f'months ok {n_ok}/12 -> `{out.relative_to(MZ)}`')
            if r.returncode == 0 and n_ok == 12:
                break
            if r.returncode != 0:
                err = [l for l in txt.splitlines() if 'Error' in l or 'Killed' in l or 'signal' in l][-3:]
                log(f'{y} downscaling attempt {attempt} FAILED: {" | ".join(err)[:400]}')
                for tmp in out_dir.glob('tmp*'):
                    if tmp.is_dir():
                        shutil.rmtree(tmp, ignore_errors=True)
        st = year_result(y)
        if r.returncode == 0 and sum(1 for s in st.values() if s and s.startswith('ok')) == 12:
            # ds_param.nc was re-saved by TopoPyScale (new 4.7 GB file): relink if byte-identical
            f = out_dir / 'ds_param.nc'
            if os.stat(f).st_ino != os.stat(TERRAIN / 'ds_param.nc').st_ino and filecmp.cmp(f, TERRAIN / 'ds_param.nc', shallow=False):
                f.unlink()
                os.link(TERRAIN / 'ds_param.nc', f)
                relink = 'ds_param.nc relinked (identical)'
            else:
                relink = 'ds_param.nc kept as is'
            size = sum(p.stat().st_size for p in (out_dir / 'downscaled').rglob('*') if p.is_file()) / 1e9
            log(f'**{y} DONE** in {(time.time() - t0) / 60:.0f} min: 12 months ok (hourly QA + daily statistics), '
                f'stores {size:.1f} GB in `data/meridionali_E_{y}/outputs/downscaled/`; {relink}')
        else:
            log(f'**{y} downscaling FAILED** after 3 attempts; months: {st}; year skipped')
        done.add(y)


def main():
    years = [int(a) for a in sys.argv[1:]] or list(range(2025, 1989, -1))
    LOGDIR.mkdir(parents=True, exist_ok=True)
    log(f'driver started (pid {os.getpid()}); years {years[0]}..{years[-1]} ({len(years)}); downscaling not before '
        f'{time.strftime("%H:%M", time.localtime(START_DOWNSCALING))} (Agisoft)')
    ta = threading.Thread(target=lane_ncar, args=(years,), name='ncar', daemon=True)
    tb = threading.Thread(target=lane_cds, args=(years,), name='cds', daemon=True)
    tc = threading.Thread(target=lane_downscale, args=(years,), name='downscale', daemon=True)
    for t in (tb, ta, tc):                                       # CDS first: it keeps the newest year (2024 half done)
        t.start()
        time.sleep(5)
    try:
        while tc.is_alive():
            tc.join(60)
            if not ta.is_alive() and not tb.is_alive() and all(ready.get(y) for y in years) and not tc.is_alive():
                break
    except KeyboardInterrupt:
        stop.set()
    log('driver finished')


if __name__ == '__main__':
    main()
