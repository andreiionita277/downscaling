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

Version 5 (9 Nov, user: "download from cds and ncar in parallel and separate them in different directories ... 3 lanes
for ncar ... even if they fail, go on"): NCAR runs in separate processes (scripts/ncar_lane.py, 3 of them) and writes its
own stores in data/era5_600-1000hPa_ncar/<year>; the CDS lane keeps data/era5_600-1000hPa/<year>. Years are taken through
claim files in data/era5_claims (<year>.cds / <year>.ncar, under a file lock, so no year is taken twice; a claim whose
process is gone is free again). A store that is complete gets a COMPLETE marker (after verify); the downscaling lane
downscales every year with a marker, from whichever source finished first. When no year is free, the CDS lane helps with
the newest year an NCAR lane is still on (into its own store). The NCAR lane of this driver is no longer used.
Version 5b (9 Nov, user: "can't you download both at the same time?"): the CDS lane ignores NCAR claims and goes through
every year in order (2012, 2011, 2010, ...), racing the NCAR lanes on the same years in separate stores; the first
complete store is downscaled, an NCAR lane stops when CDS completes its year first, and the CDS lane skips years NCAR
has already completed.
Version 5c (9 Nov, user: "make our download have the same setup as hers", i.e. rila_pirin_zarr/run_era5_queue.sh):
NCAR stores get 12 requests at a time per lane, 60 s backoff base, and their single levels from the CDS in one request
per year (max_items_surf 121000, like rila_pirin_zarr/config/era5); pressure levels stay on NCAR. ncar_lane.py runs
download twice back to back (second pass retries failed units), like her lanes.
Version 5d (9 Nov, user: "take as much as you can from ncar also the single levels and keep cds running just as
backup"): NCAR stores take single levels from NCAR OPeNDAP again; rest of 5c unchanged; CDS lane unchanged (backup).
"""
import fcntl
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
ERA_DIRS = {'cds': MZ / 'data' / 'era5_600-1000hPa', 'ncar': MZ / 'data' / 'era5_600-1000hPa_ncar'}
CLAIMS = MZ / 'data' / 'era5_claims'
LEVELS = [600, 650, 700, 750, 775, 800, 825, 850, 875, 900, 925, 950, 975, 1000]

START_DOWNSCALING = time.time() + max(0, 60 * 60 - (time.time() - 1793127420))   # Agisoft: not before 21:57 (user)
NCAR_WORKERS = 12                # version 5c: as rila_pirin_zarr (version 4: 2)
NCAR_BACKOFF_S = 60              # version 5c: as rila_pirin_zarr, 1, 2, 4, 8, 16 min (version 4: 120 s base)
NCAR_STALL_S = 45 * 60
NCAR_RETRY_PAUSE_S = 15 * 60     # version 5: pause before an NCAR lane retries the same year

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
            fcntl.flock(f, fcntl.LOCK_EX)                       # several processes write this log (version 5)
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
    base = ERA_DIRS[source] / str(year)
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
    else:                                       # 5d (user): everything from NCAR; the CDS lane is the backup
        cfg['source'] = {'plev': 'ncar', 'surf': 'ncar'}
    if source == 'cds':
        f = MZ / 'config' / 'era5' / f'config_era5_meridionali_{year}_cds.yml'
    else:                                       # own folder and name: never matched by other_writer() of the CDS lane
        f = MZ / 'config' / 'era5_ncar' / f'config_era5_meridionali_ncar_{year}.yml'
        f.parent.mkdir(exist_ok=True)
    f.write_text(f'# written by scripts/overnight_E.py from {ERA_TEMPLATE.name} ({source}); see '
                 f'LOG_downscalingTest_Overnight.md\n' + yaml.safe_dump(cfg, sort_keys=False))
    return f, cfg


def run_cfg(year, cores, source='cds'):
    s = RUN_TEMPLATE.read_text()
    rep = [(r'(?m)^  name: .*', f'  name: Meridionali E {year}'),
           (r'(?m)^  description: .*', f'  description: Southern Carpathians {year}, setup E (3001 clusters, 4 elevation '
                                       f'groups), ERA5 600-1000 hPa ({source.upper()}), raw Zarr'),
           (r'(?m)^  directory: /.*', f'  directory: {MZ}/data/meridionali_E_{year}'),
           (r'(?m)^  start: .*', f'  start: {year}-01-01'),
           (r'(?m)^  end: .*', f'  end: {year}-12-31'),
           (r'(?m)^    path: .*era5_600-1000hPa/2025', f'    path: {ERA_DIRS[source] / str(year)}'),
           (r'(?m)^        CPU_cores: .*', f'        CPU_cores: {cores}                     # set by overnight_E.py')]
    for a, b in rep:
        s, n = re.subn(a, b, s)
        assert n == 1, a
    s = f'# written by scripts/overnight_E.py from {RUN_TEMPLATE.name}; see LOG_downscalingTest_Overnight.md\n' + s
    f = MZ / 'config' / f'config_meridionali_E_{year}.yml'
    f.write_text(s)
    return f


# ------------------------------------------------------------------------------------------------ ERA5 checks
def store_complete(year, source='cds'):
    """Every hour of the year present without NaN, 14 levels (same check run_year_public.py does)."""
    p = ERA_DIRS[source] / str(year) / 'ERA5.zarr'
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


def incomplete_months(year, source='cds'):
    ok, why = store_complete(year, source)
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


def marker(year, source):
    return ERA_DIRS[source] / str(year) / 'COMPLETE'


def complete_source(year):
    """'cds' / 'ncar' if that source's store of the year is complete and verified (marker), else None."""
    for s in ('cds', 'ncar'):
        if marker(year, s).exists():
            return s
    return None


def _claim_alive(f):
    """A claim holds while its process lives; claims of a dead process are free again."""
    try:
        pid = int(f.read_text().split('pid ')[1].split()[0])
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, IndexError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True


def claim_year(years, kind, who, helping=False):
    """Newest year with no complete store and no live claim -> claimed for `kind` ('cds' / 'ncar'). With helping=True
    (CDS lane, nothing free): the newest year an NCAR lane is still on and no CDS process has. Returns (year, helped)."""
    CLAIMS.mkdir(parents=True, exist_ok=True)
    with open(CLAIMS / '.lock', 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        stamp = f'{who} pid {os.getpid()} {time.strftime("%Y-%m-%d %H:%M:%S")}\n'
        for y in years:
            if complete_source(y) or (CLAIMS / f'{y}.failed').exists():
                continue
            # 5b: CDS races NCAR (ignores NCAR claims); NCAR lanes avoid every live claim
            if any(_claim_alive(CLAIMS / f'{y}.{k}') for k in (('cds',) if kind == 'cds' else ('cds', 'ncar'))):
                continue
            if kind == 'cds' and _claim_alive(CLAIMS / f'{y}.ncar'):
                (CLAIMS / f'{y}.{kind}').write_text(stamp)
                return y, True
            (CLAIMS / f'{y}.{kind}').write_text(stamp)
            return y, False
        if helping:
            for y in years:
                if complete_source(y) or (CLAIMS / f'{y}.failed').exists():
                    continue
                if _claim_alive(CLAIMS / f'{y}.ncar') and not _claim_alive(CLAIMS / f'{y}.cds'):
                    (CLAIMS / f'{y}.cds').write_text(stamp)
                    return y, True
    return None, False


def verify_and_mark(year, source, cfg_file, who=''):
    """Downloader verify of a complete store, then the COMPLETE marker (the downscaling lane waits for it)."""
    vout = LOGDIR / f'era5_{year}_{source}_verify.log'
    v = subprocess.run([PY, DL, str(cfg_file), 'verify'], cwd=ROOT, stdout=open(vout, 'w'), stderr=subprocess.STDOUT)
    txt = vout.read_text(errors='replace')
    n_ok = txt.count('OK hours')
    tmax = re.findall(r"EDH vs store t: \{'max_abs_diff': ([0-9.]+)", txt)
    marker(year, source).write_text(f'{time.strftime("%Y-%m-%d %H:%M:%S")} verify exit {v.returncode}, {n_ok}/12 months OK\n')
    log(f'{who}{year} ERA5 {source.upper()} verify: exit {v.returncode}, {n_ok}/12 months OK'
        f'{f", EDH t max diff {float(tmax[0]):.3f} K" if tmax else ""} -> `{vout.relative_to(MZ)}`; marked COMPLETE')


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
        y, helped = claim_year(years, 'cds', 'cds lane', helping=True)
        if y is None:
            if all(complete_source(x) or (CLAIMS / f'{x}.failed').exists() for x in years):
                break
            time.sleep(300)                                       # every remaining year is with a CDS process
            continue
        if helped:
            log(f'{y} ERA5: the CDS lane also downloads {y}, which an NCAR lane is on (separate stores, the first '
                f'complete one is downscaled)')
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
            if not helped:
                (CLAIMS / f'{y}.failed').write_text(f'cds {time.strftime("%Y-%m-%d %H:%M:%S")} months {months}\n')
            log(f'**{y} ERA5 CDS FAILED**: months {months} still incomplete after 3 CDS attempts'
                f'{"" if helped else "; year skipped"}')
        else:
            log(f'**{y} ERA5 complete** (CDS)')
            verify_and_mark(y, 'cds', f)
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
    for y in years:                                              # downscaled by an earlier driver run
        st = year_result(y)
        if sum(1 for s in st.values() if s and s.startswith('ok')) == 12:
            done.add(y)
    while not stop.is_set():
        todo = [(y, complete_source(y)) for y in years if y not in done]
        todo = [t for t in todo if t[1]]
        if not todo:
            if all(y in done or (CLAIMS / f'{y}.failed').exists() for y in years):
                log('Downscaling lane: nothing left to do')
                return
            time.sleep(300)
            continue
        y, src = todo[0]
        busy = agisoft_busy()
        cores = 6 if busy else 14
        cfg = run_cfg(y, cores, src)
        out_dir = prepare_terrain(y)
        log(f'**{y} downscaling** started ({cores} workers{", Agisoft busy" if busy else ""}); ERA5 from '
            f'{src.upper()}; terrain linked from meridionali_F_2025; config `{cfg.relative_to(MZ)}`')
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
    log('version 5: CDS lane + downscaling lane here; NCAR in scripts/ncar_lane.py (separate processes, own stores)')
    tb = threading.Thread(target=lane_cds, args=(years,), name='cds', daemon=True)
    tc = threading.Thread(target=lane_downscale, args=(years,), name='downscale', daemon=True)
    for t in (tb, tc):
        t.start()
        time.sleep(5)
    try:
        while tc.is_alive():
            tc.join(60)
    except KeyboardInterrupt:
        stop.set()
    log('driver finished')


if __name__ == '__main__':
    main()
