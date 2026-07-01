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
#   default-jre-headless   JVM for the DV legacy .doc/.docx path (POI via jpype)
#   git                    pull updates in the mounted wiki content repo
#   curl, ca-certificates  fetch the POI jar stack at build time
# (ImageMagick/antiword/libreoffice are NOT needed: `convert` in eurlex is
# Pillow's method, and the .doc/.docx path is POI, not antiword.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr tesseract-ocr-swe \
        ocrmypdf \
        raptor2-utils \
        default-jre-headless \
        git \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv provides dependency resolution + install; copy just its static binaries in.
COPY --from=ghcr.io/astral-sh/uv:0.9.0 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# 1) dependencies only -- cached until pyproject.toml/uv.lock change.
#    README.rst is copied because pyproject points its readme at it.
COPY pyproject.toml uv.lock README.rst ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) the project itself, then fetch the POI jars into /app/vendor/poi (where
#    dv/word.py globs for them). This layer changes each release.
COPY . .
RUN uv sync --frozen --no-dev \
    && bash tools/fetch_poi.sh

# venv console scripts (incl. `lagen`) on PATH; point search + wiki at the
# sidecar cluster / mounted content repo (config.py reads these env vars).
ENV PATH="/app/.venv/bin:$PATH" \
    OPENSEARCH_URL="http://opensearch:9200" \
    WIKI_ROOT="/wiki"

EXPOSE 8000
# Bind all interfaces so the nginx ferenda.lagen.nu vhost can proxy in.
CMD ["lagen", "all", "serve", "--host", "0.0.0.0", "--port", "8000"]
