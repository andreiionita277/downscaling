"""
One NCAR download lane (driver version 5, overnight_E.py). Run it 3 times (lanes ncar1, ncar2, ncar3) next to the
driver; each lane takes the newest year that no other process has (claim files in data/era5_claims) and downloads it
from NCAR into its own store data/era5_600-1000hPa_ncar/<year> (never the CDS store). User 9 Nov: "even if they fail,
go on": a year is never given up; after an incomplete try the lane waits NCAR_RETRY_PAUSE_S and resumes the same year
(units already written are kept). A complete store is verified and marked COMPLETE; the driver's downscaling lane then
downscales it. If the CDS lane completes the same year first, the lane stops it and takes the next year.

    cd ~/TopoPyScale-repo-github/downscaling
    OVERNIGHT_LOG=LOG_28Oct2026.md setsid nohup ../venv/bin/python meridionali_zarr/scripts/ncar_lane.py ncar1 \
        $(seq 2012 -1 1990) > meridionali_zarr/data/logs/overnight_E/ncar1.out 2>&1 < /dev/null &
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import overnight_E as ov  # noqa: E402


def main():
    who, years = sys.argv[1], [int(a) for a in sys.argv[2:]]
    tag = f'[{who}] '
    ov.LOGDIR.mkdir(parents=True, exist_ok=True)
    ov.log(f'{tag}NCAR lane started (pid {ov.os.getpid()}); years {years[0]}..{years[-1]}; '
           f'{ov.NCAR_WORKERS} requests at a time, never gives a year up')
    while True:
        y, _ = ov.claim_year(years, 'ncar', who)
        if y is None:
            ov.log(f'{tag}NCAR lane: no year left, finished')
            return
        attempt, t_year = 0, time.time()
        while True:
            other = ov.complete_source(y)
            if other:
                ov.log(f'{tag}{y}: already complete from {other.upper()}, next year')
                break
            attempt += 1
            f, _ = ov.era_cfg(y, 'ncar')
            out = ov.LOGDIR / f'era5_{y}_ncar_{who}_try{attempt}_{time.strftime("%Y%m%d_%H%M")}.log'
            ov.log(f'{tag}**{y} ERA5 NCAR** try {attempt} -> `{out.relative_to(ov.MZ)}`')
            t0 = time.time()
            p = ov.sh([ov.PY, ov.DL, str(f), 'download'], out)
            while p.poll() is None:
                time.sleep(60)
                if ov.marker(y, 'cds').exists():                  # the CDS lane finished this year first
                    p.terminate()
                    p.wait(120)
                    break
            n_ok, n_fail = ov.tail_count(out, 'written and verified'), ov.tail_count(out, 'FAILED:')
            ov.log(f'{tag}{y} ERA5 NCAR try {attempt} ended after {(time.time() - t0) / 60:.0f} min (exit {p.returncode}): '
                   f'{n_ok} units written, {n_fail} given up this try, {ov.tail_count(out, "attempt ")} retried requests')
            if ov.marker(y, 'cds').exists():
                continue                                            # -> "already complete from CDS"
            months = ov.incomplete_months(y, 'ncar')
            if not months:
                ov.log(f'{tag}**{y} ERA5 complete** (NCAR, {attempt} tries, {(time.time() - t_year) / 3600:.1f} h)')
                ov.verify_and_mark(y, 'ncar', f, tag)
                break
            ov.log(f'{tag}{y}: months {months} still incomplete; retry in {ov.NCAR_RETRY_PAUSE_S // 60} min')
            time.sleep(ov.NCAR_RETRY_PAUSE_S)


if __name__ == '__main__':
    main()
