"""Probe NCAR ERA5 on AWS (s3://nsf-ncar-era5, anonymous): file naming, sizes, HDF5 chunking and compression."""
import sys, time
import s3fs, h5py
fs = s3fs.S3FileSystem(anon=True)
for prefix in ['nsf-ncar-era5/e5.oper.an.pl/197901/', 'nsf-ncar-era5/e5.oper.an.pl/202501/']:
    try:
        files = fs.ls(prefix, detail=True)
        print(prefix, len(files), 'files'); [print('  ', f['name'].split('/')[-1], round(f['size']/1e6), 'MB') for f in files[:12]]
    except Exception as e:
        print(prefix, 'ERR', e)
names = [f for f in fs.ls('nsf-ncar-era5/e5.oper.an.pl/202501/') if '_t.' in f or '130_t' in f]
f0 = sorted(names)[0]; print('probe file', f0)
t = time.time()
with fs.open(f0, 'rb', block_size=2**20) as fh, h5py.File(fh, 'r') as h:
    def show(name, obj):
        if isinstance(obj, h5py.Dataset):
            print(f'{name:12s} shape={obj.shape} dtype={obj.dtype} chunks={obj.chunks} comp={obj.compression} '
                  f'opts={obj.compression_opts} shuffle={obj.shuffle} scaleoffset={obj.scaleoffset}')
    h.visititems(show)
    ds = h[[k for k in h if h[k].ndim == 4][0]]
    print('n stored chunks', ds.id.get_num_chunks())
    print('chunk 0 info', ds.id.get_chunk_info(0))
    for k in ('level', 'latitude', 'longitude'):
        if k in h: v = h[k][:]; print(k, v[:3], v[-3:])
print('metadata read', round(time.time()-t, 1), 's')
