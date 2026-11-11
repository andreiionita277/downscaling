"""
Progress bars for the aspect-fix rerun (rerun_fixed_years.sh), one line per year.
    cd ~/TopoPyScale-repo-github/downscaling && ../venv/bin/python meridionali_zarr/scripts/rerun_progress.py
Stages of one year (rough share of its ~25 min): preparation (terrain, solar geometry, horizon, ERA5) 0-30 %,
downscaling 3001 points 30-85 %, daily statistics 12 months 85-95 %, checks 95-100 %.
"""
import re
from pathlib import Path

L = Path(__file__).resolve().parents[1] / 'data' / 'logs' / 'rerun_fix'
drv = (L / 'driver.out').read_text() if (L / 'driver.out').exists() else ''
m = re.search(r'started for years ([\d ]+)', drv)
years = m.group(1).split() if m else []
STAGES = ['compute_dem_param', 'extract_topo_param', 'compute_solar_geometry', 'compute_horizon', 'get_era5']


def bar(p, w=30):
    n = int(round(p / 100 * w))
    return '█' * n + '░' * (w - n)


done_info = {}
if (L / 'progress.tsv').exists():
    for line in (L / 'progress.tsv').read_text().splitlines()[1:]:
        f = line.split('\t')
        done_info[f[0]] = f
rows = []
for y in years:
    if y in done_info:
        f = done_info[y]
        p, txt = (100, f'{f[4]} in {f[3]} min; {f[5]}; old outputs {f[6]}') if f[4] == 'ALL PASS' else (0, 'FAILED, see check_' + y + '.out')
    else:
        logs = sorted(L.glob(f'run_{y}_try*.log'))
        if not logs:
            p, txt = 0, 'waiting'
        else:
            t = logs[-1].read_text(errors='replace')
            pts = re.findall(r'---> (\d+)/3001 points', t)
            months = len(re.findall(r'#################### \d{4}-\d\d:', t))
            if (L / f'check_{y}.out').exists() or ('exit' in drv and f'{y} re-downscaling' in drv and months == 12 and 'write_daily done' in t.split('#' * 20)[-1]):
                p, txt = 96, 'checks running (NaN, time axis, DJF SW order)'
            elif months:
                p, txt = 85 + 10 * (months - 1) / 12, f'daily statistics, month {months}/12'
            elif pts:
                k = int(pts[-1]); eta = re.findall(r'ETA (\d+)s', t)
                p, txt = 30 + 55 * k / 3001, f'downscaling {k}/3001 points' + (f', ~{int(eta[-1]) // 60} min left' if eta else '')
            else:
                s = [i for i, st in enumerate(STAGES) if f'========== {st}' in t]
                k = (s[-1] + 1) if s else 0
                p, txt = 30 * k / (len(STAGES) + 1), f'preparation: {STAGES[k - 1] if k else "starting"}'
    rows.append(f'{y}  {bar(p)} {p:5.1f} %  {txt}')
n_done = sum(1 for y in years if y in done_info and done_info[y][4] == 'ALL PASS')
print(f'Overall: {n_done}/{len(years)} years  {bar(100 * n_done / max(len(years), 1))}')
print('\n'.join(rows))
