"""
Re-evaluate the hourly QA of finished months with the current bounds, without recomputing anything.

    python zarr_workflow/scripts/recheck_qa.py zarr_workflow/config/config_2025.yml [--months 6]

Used when a QA bound was wrong (2026-10-13: wind direction 2*pi rounds to 6.28319 with the authors'
5-decimal rounding and exceeded a bound of 2*pi + 1e-6). Reads the month's hourly values from down.zarr,
reruns workflow.qa_hourly, keeps the month's GeoTIFF checks, and updates months/<YYYY-MM>.json
(the previous QA and status are kept under 'previous') and year_summary.json.
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow as wf  # noqa: E402

from TopoPyScale import topo_scale_zarr as tz  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument('config')
    p.add_argument('--months', type=int, nargs='+')
    a = p.parse_args()
    cfg, out, _ = wf.load_config(a.config)
    store = out / 'downscaled' / cfg['outputs']['file']['zarr_store']
    summary_f = out / 'year_summary.json'
    summary = json.loads(summary_f.read_text()) if summary_f.exists() else {'months': {}}
    for f in sorted((out / 'months').glob('*.json')):
        rec = json.loads(f.read_text())
        m0, m1 = pd.Timestamp(rec['start']), pd.Timestamp(rec['end'])
        if a.months and m0.month not in a.months:
            continue
        hourly = tz.open_downscaled_store(store).sel(time=slice(m0, m1 + pd.Timedelta('23h'))).load()
        qa_ok, qa = wf.qa_hourly(hourly)
        n_expected = int((m1 - m0) / pd.Timedelta('1h')) + 24
        qa_ok &= hourly.sizes['time'] == n_expected
        chk_ok = bool(rec.get('geotiff_checks')) and all(c['ok'] for c in rec['geotiff_checks'].values())
        new = 'ok' if (qa_ok and chk_ok and not rec.get('daily_nan')) else 'failed checks'
        if new != rec['status'] or rec['status'] != 'ok':
            rec['previous'] = {'status': rec['status'], 'qa_hourly': rec.get('qa_hourly')}
            rec['rechecked'] = f'{pd.Timestamp.now():%Y-%m-%d %H:%M} with recheck_qa.py (current QA bounds)'
        rec['qa_hourly'], rec['status'] = qa, new
        wf.write_json(f, rec)
        summary['months'][rec['month']] = new
        print(f'{rec["month"]}: {new}' + (f' (was {rec["previous"]["status"]})' if 'previous' in rec else ''), flush=True)
    wf.write_json(summary_f, summary)
    all_ok = all(str(v).startswith('ok') for v in summary['months'].values())
    print('ALL MONTHS OK' if all_ok else 'SOME MONTHS NOT OK', flush=True)
    sys.exit(0 if all_ok else 1)


if __name__ == '__main__':
    main()
