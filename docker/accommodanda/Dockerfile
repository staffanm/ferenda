# syntax=docker/dockerfile:1
# accommodanda (the rebuilt lagen.nu) -- serve AND run the pipeline.
#
# One image both serves the site+API (`lagen all serve`, the CMD) and carries
# the full download/rebuild toolchain, so maintenance runs in-container:
#     docker compose exec accommodanda lagen all rebuild
#     docker compose exec accommodanda lagen all index
# The code is baked in; the corpus (data_root, default /app/site/data) and the
# wiki content repo (WIKI_ROOT) are read-write bind-mounts -- see
# docker-compose.yml.
#
# Build context is the repo root (docker-compose.yml sets `context: .`), so the
# COPY paths below are repo-relative. .dockerignore trims the context.
FROM python:3.12-slim

# System tools the rebuild pipeline shells out to (installed at build time):
#   poppler-utils          pdftotext / pdftohtml -- forarbete, eurlex, DV namedcases
#   tesseract-ocr(+swe)    OCR language data for scanned eurlex PDFs
#   ocrmypdf               eurlex OCR (pulls ghostscript/qpdf/unpaper)
#   raptor2-utils          `rapper` -- eurlex RDF/XML -> n-triples notice stream
#   default-jre-headless   JVM for the .docx path (POI via jpype; needs Java 9+)
#   antiword               the .doc path -- forarbete's legacy Word bodies
#   git                    pull updates in the mounted wiki content repo
#   curl, ca-certificates  fetch the POI jar stack at build time
# antiword is NOT redundant with POI: the proptrips-era .doc bodies are mostly
# Word 6/95 binaries, which POI's HWPF refuses outright (OldWordFileFormatException)
# and its Word6Extractor mangles -- so `legacy_formats.word_paras` reads .doc with
# antiword and keeps POI for .docx only. (ImageMagick/libreoffice stay out:
# `convert` in eurlex is Pillow's method.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr tesseract-ocr-swe \
        ocrmypdf \
        raptor2-utils \
        default-jre-headless \
        antiword \
        git \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv provides dependency resolution + install; copy just its static binaries in.
COPY --from=ghcr.io/astral-sh/uv:0.9.0 /uv /uvx /bin/

# Build the venv against the image's own Python (python:3.12-slim), NOT a
# uv-managed interpreter: left to itself uv downloads a standalone CPython under
# /root/.local, which the unprivileged `app` user (uid 1000) can't traverse at
# runtime -> "Failed to import encodings module" crash loop. Pin uv to the
# system python3.12 (stdlib under /usr/local, world-readable) instead.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=python3.12

# Run as an unprivileged user, not root: the pipeline shells out to a lot of
# tools and serves the public internet, so drop root for the runtime process.
# UID/GID are build args so they can be pinned to the host `ferenda` user that
# owns the bind-mounted corpus/wiki (default 1000, matching the VPS user); the
# uids must line up or the mounted trees are unwritable to the download/rebuild.
ARG UID=1000
ARG GID=1000
RUN groupadd -g "$GID" app && useradd -m -u "$UID" -g "$GID" -s /bin/bash app \
    && mkdir -p /app && chown app:app /app
# The editor commits to the bind-mounted /wiki repo as `app`; git refuses to
# operate in a repo owned by a different uid unless it is marked safe. System
# config, so it lands in an early, rarely-invalidated layer.
RUN git config --system --add safe.directory /wiki \
    && git config --system --add safe.directory /app

WORKDIR /app

# POI jar stack in its OWN layer -- only tools/fetch_poi.sh is needed, so a code
# change never re-downloads the jars (they're uncommitted, fetched from Maven).
# They land in /app/vendor/poi (where dv/word.py globs) and survive `COPY . .`.
COPY --chown=app:app tools/fetch_poi.sh tools/fetch_poi.sh
RUN bash tools/fetch_poi.sh && chown -R app:app /app/vendor

# 1) dependencies only -- cached until pyproject.toml/uv.lock change (README.rst
#    is copied because pyproject points its readme at it). The venv is chowned to
#    app HERE, in this cached layer, and uv's download cache is a BuildKit cache
#    mount -- so a code change re-runs neither the chown nor any re-download, and
#    even a dependency bump re-resolves without re-fetching everything.
COPY --chown=app:app pyproject.toml uv.lock README.rst ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project \
    && chown -R app:app /app/.venv

# 2) the project itself. `COPY --chown` keeps the code app-owned; the editable
#    install only adds a few console-script/.pth entries to the (already app-
#    owned) venv, world-readable so `app` runs `lagen` fine -- so NO whole-tree
#    `chown -R /app` is needed on every build. This layer changes each release.
COPY --chown=app:app . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# venv console scripts (incl. `lagen`) on PATH; point search + wiki at the
# sidecar cluster / mounted content repo (config.py reads these env vars).
ENV PATH="/app/.venv/bin:$PATH" \
    OPENSEARCH_URL="http://opensearch:9200" \
    WIKI_ROOT="/wiki"

USER app

EXPOSE 8000
# Bind all interfaces so the nginx ferenda.lagen.nu vhost can proxy in.
CMD ["lagen", "all", "serve", "--host", "0.0.0.0", "--port", "8000"]
