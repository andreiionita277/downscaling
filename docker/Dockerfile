FROM python:3.13-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    cdo \
    gdal-bin \
    libnetcdf-dev \
    libhdf5-dev \
    && rm -rf /var/lib/apt/lists/*

RUN cdo --version
RUN gdalinfo --version

# Exact package set frozen from the previous working container (TopoPyScale 0.3.3
# image, 2026-09-30) minus TopoPyScale itself. Includes ArcticSnow's topocalc fork
# pinned by commit, and zarr>=3 (needed for Earth Data Hub's Zarr v3 stores).
COPY docker/requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-cache-dir -r /tmp/requirements.lock.txt && \
    pip install --no-cache-dir pytest seaborn python-dotenv

# TopoPyScale is NOT installed from PyPI any more. The fork
# (TopoPyScale_Bulgaria_Rila_Pirin_Mountains) is bind-mounted at /opt/TopoPyScale by
# docker-compose.yml and put on PYTHONPATH, so edits to the fork are live without a
# rebuild. The pandas-3 / lowercase-timestep bugs previously sed-patched into 0.3.3
# here are fixed in the fork's source instead.
ENV PYTHONPATH=/opt/TopoPyScale

# Run as the host user (uid/gid 1000) so files written to bind mounts aren't root-owned.
RUN groupadd -g 1000 icam && useradd -m -u 1000 -g 1000 -s /bin/bash icam
USER icam
ENV HOME=/home/icam
ENV MPLCONFIGDIR=/tmp/matplotlib

# Shell history persisted inside the project bind mount (git-ignored)
ENV HISTFILE=/app/docker/.bash_history
ENV HISTSIZE=10000
ENV HISTFILESIZE=20000
ENV HDF5_USE_FILE_LOCKING=FALSE

WORKDIR /app
CMD ["/bin/bash"]
