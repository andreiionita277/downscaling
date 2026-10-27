"""Throughput from s3://nsf-ncar-era5 to this machine: one 53 MB hourly chunk, then 8 and 16 in parallel."""
import time
from concurrent.futures import ThreadPoolExecutor
import s3fs, h5py
fs = s3fs.S3FileSystem(anon=True)
f0 = 'nsf-ncar-era5/e5.oper.an.pl/202501/e5.oper.an.pl.128_130_t.ll025sc.2025010100_2025010123.nc'
with fs.open(f0, 'rb') as fh, h5py.File(fh, 'r') as h:
    ds = h['T']; info = [ds.id.get_chunk_info(i) for i in range(ds.id.get_num_chunks())]
def get(i):
    return len(fs.cat_file(f0, start=info[i].byte_offset, end=info[i].byte_offset + info[i].size))
for n in (1, 8, 16, 24):
    t = time.time()
    with ThreadPoolExecutor(n) as ex: nbytes = sum(ex.map(get, range(n)))
    dt = time.time() - t
    print(f'{n:2d} chunks parallel: {nbytes/1e6:.0f} MB in {dt:.1f}s = {nbytes/1e6/dt:.1f} MB/s', flush=True)
