# ismail MCP server over stdio: docker run -i --rm ismail
# Runs as root by default; for a bind-mounted songs folder, run as your own
# uid/gid to avoid root-owned files on the host:
#   docker run -i --rm --user "$(id -u):$(id -g)" -v <songs-dir>:/work ismail
# NUMBA_CACHE_DIR and ISMAIL_SONGS below, plus /work's permissions, make that
# work under any uid without extra flags.
FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 ffmpeg espeak-ng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY ismail ./ismail
RUN pip install --no-cache-dir .
ENV NUMBA_CACHE_DIR=/tmp/numba_cache
ENV ISMAIL_SONGS=/work
RUN mkdir -p /work && chmod 777 /work
WORKDIR /work
ENTRYPOINT ["ismail", "mcp"]
